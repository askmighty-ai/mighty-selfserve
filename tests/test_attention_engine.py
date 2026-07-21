"""E2E / replay tests for Attention loaders + engine + shadow (Milestone 2)."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_state import (
    ACCESS_BROWSER_SESSION,
    ACCOUNT_STATE_VERSION,
    CONN_CONNECTED,
    DATA_COMPLETE,
    DATA_NONE,
    AccountState,
    Confidence,
    ConfidenceFactors,
    ensure_account_state_tables,
    persist_account_state,
)
from mighty.attention import AttentionClass
from mighty.attention_engine import read_attention, read_attention_snapshot
from mighty.attention_loaders import load_auth_truths, load_authorize_rows
from mighty.attention_shadow import (
    load_attention_shadow,
    record_attention_shadow,
)
from mighty.attention_state import SilenceVerdict
from mighty.attention_store import (
    ensure_attention_overlay_tables,
    snooze_attention,
)
from mighty.authentication_state import AuthenticationState
from mighty.provider_session_state import (
    SessionEvidence,
    ensure_provider_session_state_tables,
    upsert_provider_session_state,
)

FIXED_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
USER_ID = "user-1"


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "attention_engine.db"))
    conn.row_factory = sqlite3.Row
    ensure_account_state_tables(conn)
    ensure_provider_session_state_tables(conn)
    ensure_attention_overlay_tables(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS actions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            label TEXT NOT NULL,
            fields TEXT,
            status TEXT NOT NULL,
            outcome TEXT,
            approval_token TEXT UNIQUE,
            created_at TEXT NOT NULL,
            decided_at TEXT,
            expires_at TEXT
        )
        """
    )
    conn.commit()
    yield conn
    conn.close()


def _persist_account(
    db,
    provider: str = "amex",
    *,
    data_status: str = DATA_COMPLETE,
):
    state = AccountState(
        user_id=USER_ID,
        provider=provider,
        display_name=provider.title(),
        category="financial",
        access_method=ACCESS_BROWSER_SESSION,
        connection_state=CONN_CONNECTED,
        session_health="healthy",
        last_verified_at=None,
        data_status=data_status,
        last_data_refresh=None,
        observations_available=[],
        field_count=0,
        next_recommended_action=None,
        confidence=Confidence(
            level="high",
            score=90,
            factors=ConfidenceFactors(),
        ),
        status_line="",
        is_actionable=False,
        updated_at=FIXED_NOW.isoformat(),
        version=ACCOUNT_STATE_VERSION,
    )
    persist_account_state(db, state)


def _write_pss(db, *, provider: str, state: str, evidence_type: str = "dom"):
    upsert_provider_session_state(
        db,
        USER_ID,
        SessionEvidence(
            provider=provider,
            state=state,
            evidence_type=evidence_type,
            evidence_summary=f"{evidence_type} evidence",
            observed_at=FIXED_NOW - timedelta(minutes=5),
            source="access_manager",
            confidence="high",
        ),
    )


def _insert_action(db, *, action_id: str, status: str = "pending"):
    db.execute(
        """
        INSERT INTO actions
        (id, user_id, action_type, label, status, created_at, expires_at)
        VALUES (?, ?, 'transfer', 'Send money', ?, ?, ?)
        """,
        (
            action_id,
            USER_ID,
            status,
            "2026-07-21T11:00:00+00:00",
            "2026-07-21T18:00:00+00:00",
        ),
    )
    db.commit()


class TestLoaders:
    def test_load_authorize_rows_pending_only(self, db):
        _insert_action(db, action_id="a1", status="pending")
        _insert_action(db, action_id="a2", status="approved")
        rows = load_authorize_rows(db, USER_ID)
        assert [r.action_id for r in rows] == ["a1"]
        assert rows[0].status == "pending"

    def test_load_auth_truths_from_account_state(self, db):
        _persist_account(db, "amex")
        _write_pss(db, provider="amex", state="signed_out", evidence_type="login_form")
        truths = load_auth_truths(db, USER_ID, now=FIXED_NOW)
        assert len(truths) == 1
        assert truths[0].provider == "amex"
        assert truths[0].needs_human is True
        assert truths[0].state == AuthenticationState.SIGNED_OUT


class TestEnginePipeline:
    def test_signed_out_and_pending_authorize_ranks_agent_first(self, db):
        _persist_account(db, "amex")
        _write_pss(db, provider="amex", state="signed_out", evidence_type="login_form")
        _insert_action(db, action_id="42", status="pending")

        state = read_attention(db, USER_ID, now=FIXED_NOW)
        assert state.primary is not None
        assert state.primary.attention_class == AttentionClass.AGENT_AUTHORIZATION
        assert state.primary.source_ref == "authorize:42"
        assert state.silence is None
        assert any(
            item.attention_class == AttentionClass.AUTH_BLOCKER
            for item in state.remaining
        )

    def test_snooze_blocker_yields_suppressed(self, db):
        _persist_account(db, "amex")
        _write_pss(db, provider="amex", state="signed_out", evidence_type="login_form")
        snap = read_attention_snapshot(db, USER_ID, now=FIXED_NOW)
        blocker = next(
            item
            for item in snap.candidates
            if item.attention_class == AttentionClass.AUTH_BLOCKER
        )
        snooze_attention(
            db,
            blocker,
            now=FIXED_NOW,
            duration=timedelta(minutes=30),
        )
        state = read_attention(db, USER_ID, now=FIXED_NOW)
        assert state.silence == SilenceVerdict.SUPPRESSED
        assert state.primary is None

    def test_replay_determinism(self, db):
        _persist_account(db, "amex")
        _write_pss(db, provider="amex", state="signed_out", evidence_type="login_form")
        _insert_action(db, action_id="42", status="pending")
        first = read_attention_snapshot(db, USER_ID, now=FIXED_NOW)
        second = read_attention_snapshot(db, USER_ID, now=FIXED_NOW)
        assert first.state.to_dict() == second.state.to_dict()
        assert [c.to_dict() for c in first.candidates] == [
            c.to_dict() for c in second.candidates
        ]

    def test_healthy_account_all_clear(self, db):
        _persist_account(db, "amex", data_status=DATA_COMPLETE)
        _write_pss(db, provider="amex", state="signed_in", evidence_type="dom")
        state = read_attention(db, USER_ID, now=FIXED_NOW)
        assert state.primary is None
        assert state.silence == SilenceVerdict.ALL_CLEAR

    def test_connected_without_data_emits_data_gap(self, db):
        _persist_account(db, "amex", data_status=DATA_NONE)
        _write_pss(db, provider="amex", state="signed_in", evidence_type="dom")
        state = read_attention(db, USER_ID, now=FIXED_NOW)
        assert state.primary is not None
        assert state.primary.attention_class == AttentionClass.DATA_GAP
        assert state.primary.provider == "amex"
        assert state.silence == SilenceVerdict.AWAITING_DATA

    def test_missing_worker_is_system_primary(self, db):
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                extension_version TEXT,
                extension_last_seen_at TEXT
            )
            """
        )
        db.execute("INSERT INTO users (id) VALUES (?)", (USER_ID,))
        db.commit()
        _persist_account(db, "amex", data_status=DATA_COMPLETE)
        _write_pss(db, provider="amex", state="signed_in", evidence_type="dom")
        state = read_attention(db, USER_ID, now=FIXED_NOW)
        assert state.primary is not None
        assert state.primary.attention_class == AttentionClass.SYSTEM
        assert state.primary.cta_key.value == "install_worker"
        assert state.silence is None

    def test_managed_runtime_awaiting_user_is_trust_primary(self, db):
        from mighty.auth_truth import ACCESS_MANAGED_RUNTIME
        from mighty.runtime_access_state import (
            ensure_runtime_access_state_tables,
            upsert_runtime_access_state,
        )

        ensure_runtime_access_state_tables(db)
        state = AccountState(
            user_id=USER_ID,
            provider="amex",
            display_name="Amex",
            category="financial",
            access_method=ACCESS_MANAGED_RUNTIME,
            connection_state=CONN_CONNECTED,
            session_health="healthy",
            last_verified_at=None,
            data_status=DATA_COMPLETE,
            last_data_refresh=None,
            observations_available=[],
            field_count=0,
            next_recommended_action=None,
            confidence=Confidence(
                level="high", score=90, factors=ConfidenceFactors()
            ),
            status_line="",
            is_actionable=False,
            updated_at=FIXED_NOW.isoformat(),
            version=ACCOUNT_STATE_VERSION,
        )
        persist_account_state(db, state)
        upsert_runtime_access_state(
            db,
            USER_ID,
            {
                "schema_version": 2,
                "provider": "amex",
                "runtime_instance_id": "rt-1",
                "updated_at": FIXED_NOW.isoformat(),
                "authentication_state": "SIGNED_IN",
                "access_health": "degraded",
                "runtime_state": "running",
                "browser_state": "healthy",
                "recovery_state": "awaiting_user",
                "escalation_reason": "mfa",
            },
        )
        result = read_attention(db, USER_ID, now=FIXED_NOW)
        assert result.primary is not None
        assert result.primary.attention_class == AttentionClass.TRUST
        assert result.primary.cta_key.value == "focus_managed_runtime"
        assert result.silence is None

    def test_expiring_benefit_is_value_at_risk_primary(self, db):
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS action_items (
                id INTEGER PRIMARY KEY,
                user_id TEXT NOT NULL,
                source TEXT NOT NULL,
                field_key TEXT NOT NULL,
                label TEXT,
                value TEXT,
                btype TEXT,
                urgency TEXT,
                days_left INTEGER,
                exp_date TEXT,
                created_at TEXT,
                dismissed_at TEXT,
                snoozed_until TEXT,
                completed_at TEXT
            )
            """
        )
        db.execute(
            """
            INSERT INTO action_items
            (user_id, source, field_key, label, value, btype, urgency,
             days_left, exp_date, created_at)
            VALUES (?, 'amex', 'companion_cert', 'Companion Certificate', '1',
                    'certificate', 'urgent', 5, '2026-07-28T00:00:00', ?)
            """,
            (USER_ID, FIXED_NOW.isoformat()),
        )
        db.commit()
        _persist_account(db, "amex", data_status=DATA_COMPLETE)
        _write_pss(db, provider="amex", state="signed_in", evidence_type="dom")
        state = read_attention(db, USER_ID, now=FIXED_NOW)
        assert state.primary is not None
        assert state.primary.attention_class == AttentionClass.VALUE_AT_RISK
        assert state.primary.provider == "amex"
        assert state.silence is None


class TestShadow:
    def test_record_and_load_shadow(self, db):
        _persist_account(db, "amex")
        _write_pss(db, provider="amex", state="signed_out", evidence_type="login_form")
        snap = record_attention_shadow(
            db, USER_ID, "home", now=FIXED_NOW
        )
        assert snap is not None
        loaded = load_attention_shadow(db, USER_ID, "home")
        assert loaded is not None
        assert loaded["primary_attention_id"] == snap.state.primary.attention_id
        assert loaded["candidate_count"] == len(snap.candidates)
        assert loaded["state"]["schema_version"] == 1
        # Worker surface is independent key.
        record_attention_shadow(db, USER_ID, "worker", now=FIXED_NOW)
        worker = load_attention_shadow(db, USER_ID, "worker")
        assert worker is not None
        assert worker["surface"] == "worker"
        assert json.dumps(loaded["state"], sort_keys=True)
