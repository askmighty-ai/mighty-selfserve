"""Milestone 4 end-to-end replay: multi-producer ranking + lifecycle."""

from __future__ import annotations

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
from mighty.attention_commands import command_cta, command_snooze
from mighty.attention_delivery import deliver_attention_primary, get_delivery_receipt
from mighty.attention_engine import read_attention, read_attention_snapshot
from mighty.attention_overlay import IN_FLIGHT_TIMEOUT_SECONDS, OverlayStatus
from mighty.attention_state import SilenceVerdict
from mighty.attention_store import (
    ensure_attention_overlay_tables,
    get_attention_overlay,
)
from mighty.attention_supervisor import run_attention_supervisor
from mighty.attention_view import build_attention_view
from tests.recovery_test_helpers import escalate_recovery
from mighty.provider_session_state import (
    SessionEvidence,
    ensure_provider_session_state_tables,
    upsert_provider_session_state,
)

FIXED_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
USER_ID = "user-1"


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "attention_m4.db"))
    conn.row_factory = sqlite3.Row
    ensure_account_state_tables(conn)
    ensure_provider_session_state_tables(conn)
    ensure_attention_overlay_tables(conn)
    conn.execute(
        """
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            extension_version TEXT,
            extension_last_seen_at TEXT,
            notify_push INTEGER DEFAULT 1
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE action_items (
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
    conn.execute(
        """
        CREATE TABLE actions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            label TEXT NOT NULL,
            fields TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT
        )
        """
    )
    conn.commit()
    yield conn
    conn.close()


def _account(db, provider: str, *, data_status: str = DATA_COMPLETE):
    persist_account_state(
        db,
        AccountState(
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
                level="high", score=90, factors=ConfidenceFactors()
            ),
            status_line="",
            is_actionable=False,
            updated_at=FIXED_NOW.isoformat(),
            version=ACCOUNT_STATE_VERSION,
        ),
    )


def _pss(db, provider: str, state: str, evidence_type: str):
    upsert_provider_session_state(
        db,
        USER_ID,
        SessionEvidence(
            provider=provider,
            state=state,
            evidence_type=evidence_type,
            evidence_summary=evidence_type,
            observed_at=FIXED_NOW - timedelta(minutes=5),
            source="access_manager",
            confidence="high",
        ),
    )


class TestM4Replay:
    def test_ranking_prefers_authorize_over_auth_over_benefit(self, db):
        db.execute("INSERT INTO users (id) VALUES (?)", (USER_ID,))
        # Fresh worker heartbeat so SYSTEM does not win.
        db.execute(
            "UPDATE users SET extension_version=?, extension_last_seen_at=? WHERE id=?",
            ("1.0.0", FIXED_NOW.isoformat(), USER_ID),
        )
        _account(db, "amex", data_status=DATA_NONE)
        _pss(db, "amex", "signed_out", "login_form")
        escalate_recovery(db, USER_ID, "amex", root_cause="login", now=FIXED_NOW)
        db.execute(
            """
            INSERT INTO actions
            (id, user_id, action_type, label, status, created_at, expires_at)
            VALUES ('42', ?, 'transfer', 'Send', 'pending', ?, ?)
            """,
            (USER_ID, FIXED_NOW.isoformat(), (FIXED_NOW + timedelta(hours=1)).isoformat()),
        )
        db.execute(
            """
            INSERT INTO action_items
            (user_id, source, field_key, label, value, btype, urgency,
             days_left, exp_date, created_at)
            VALUES (?, 'amex', 'cert', 'Cert', '1', 'certificate', 'urgent',
                    3, ?, ?)
            """,
            (USER_ID, (FIXED_NOW + timedelta(days=3)).isoformat(), FIXED_NOW.isoformat()),
        )
        db.commit()

        snap = read_attention_snapshot(db, USER_ID, now=FIXED_NOW)
        classes = [c.attention_class for c in snap.candidates]
        assert AttentionClass.AGENT_AUTHORIZATION in classes
        assert AttentionClass.AUTH_BLOCKER in classes
        assert AttentionClass.VALUE_AT_RISK in classes
        assert AttentionClass.DATA_GAP in classes

        state = snap.state
        assert state.primary is not None
        assert state.primary.attention_class == AttentionClass.AGENT_AUTHORIZATION
        assert state.silence is None

        view = build_attention_view(state, surface="home")
        assert view.primary is not None
        assert view.primary.attention_id == state.primary.attention_id

    def test_lifecycle_cta_timeout_delivery_replay(self, db):
        db.execute(
            "INSERT INTO users (id, extension_version, extension_last_seen_at) VALUES (?,?,?)",
            (USER_ID, "1.0.0", FIXED_NOW.isoformat()),
        )
        _account(db, "amex", data_status=DATA_COMPLETE)
        _pss(db, "amex", "signed_out", "login_form")
        escalate_recovery(db, USER_ID, "amex", root_cause="login", now=FIXED_NOW)
        db.commit()

        state = read_attention(db, USER_ID, now=FIXED_NOW)
        assert state.primary is not None
        assert state.primary.attention_class == AttentionClass.AUTH_BLOCKER
        aid = state.primary.attention_id

        command_cta(db, USER_ID, aid, now=FIXED_NOW)
        overlay = get_attention_overlay(db, USER_ID, aid)
        assert overlay is not None
        assert overlay.status is OverlayStatus.IN_FLIGHT

        # Still visible while in_flight (compose does not hide).
        mid = read_attention(db, USER_ID, now=FIXED_NOW + timedelta(minutes=5))
        assert mid.primary is not None
        assert mid.primary.attention_id == aid

        # Supervisor clears after timeout.
        later = FIXED_NOW + timedelta(seconds=IN_FLIGHT_TIMEOUT_SECONDS + 1)
        result = run_attention_supervisor(db, now=later, user_ids=[USER_ID])
        assert result.in_flight_cleared == 1
        assert get_attention_overlay(db, USER_ID, aid) is None

        # Delivery of primary blocker.
        sent = []
        attempt = deliver_attention_primary(
            db,
            USER_ID,
            now=later,
            send_push=lambda *a: sent.append(a) or True,
        )
        assert attempt is not None
        assert attempt.status == "delivered"
        assert get_delivery_receipt(db, USER_ID, aid)["status"] == "delivered"

        # Snooze → suppressed when no other blockers.
        command_snooze(db, USER_ID, aid, now=later)
        silenced = read_attention(db, USER_ID, now=later)
        assert silenced.silence == SilenceVerdict.SUPPRESSED
        assert silenced.primary is None

    def test_replay_determinism_with_all_producers(self, db):
        db.execute(
            "INSERT INTO users (id, extension_version, extension_last_seen_at) VALUES (?,?,?)",
            (USER_ID, "1.0.0", FIXED_NOW.isoformat()),
        )
        _account(db, "amex", data_status=DATA_NONE)
        _account(db, "chase", data_status=DATA_COMPLETE)
        _pss(db, "amex", "signed_in", "dom")
        _pss(db, "chase", "signed_in", "dom")
        db.execute(
            """
            INSERT INTO action_items
            (user_id, source, field_key, label, value, btype, urgency,
             days_left, exp_date, created_at)
            VALUES (?, 'chase', 'credit', 'Credit', '$50', 'cash_credit', 'info',
                    60, NULL, ?)
            """,
            (USER_ID, FIXED_NOW.isoformat()),
        )
        db.commit()

        first = read_attention_snapshot(db, USER_ID, now=FIXED_NOW)
        second = read_attention_snapshot(db, USER_ID, now=FIXED_NOW)
        assert first.state.to_dict() == second.state.to_dict()
        assert [c.to_dict() for c in first.candidates] == [
            c.to_dict() for c in second.candidates
        ]
        # Opportunity may be primary under all_clear silence, or data_gap under awaiting_data.
        assert first.state.primary is not None
        assert first.state.primary.attention_class in {
            AttentionClass.OPPORTUNITY,
            AttentionClass.DATA_GAP,
        }
