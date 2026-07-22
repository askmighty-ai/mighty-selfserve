"""Natural Session coordinator tests (Milestone 8)."""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.natural_session import (
    observe_natural_session,
    run_natural_session_ensure_due,
)
from mighty.natural_session_policy import NaturalSessionAction
from mighty.provider_session_state import (
    SessionEvidence,
    ensure_provider_session_state_tables,
    upsert_provider_session_state,
)
from mighty.recovery_planner import AttemptOutcome, RecoveryCapability
from mighty.recovery_store import (
    append_attempt,
    claim_or_get_active_case,
    ensure_recovery_tables,
)
from mighty.session_verification import ensure_session_verification_tables

FIXED_NOW = datetime(2026, 7, 22, 16, 0, 0, tzinfo=timezone.utc)
USER_ID = "user-1"


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "natural_session.db"))
    conn.row_factory = sqlite3.Row
    ensure_session_verification_tables(conn)
    ensure_provider_session_state_tables(conn)
    ensure_recovery_tables(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS account_credentials (
            user_id TEXT NOT NULL,
            source TEXT NOT NULL,
            username_enc TEXT,
            password_enc TEXT,
            extra_enc TEXT,
            created_at TEXT,
            updated_at TEXT,
            PRIMARY KEY (user_id, source)
        )
        """
    )
    conn.execute(
        "INSERT INTO account_credentials "
        "(user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
        "VALUES (?, 'amex', '', '', '', ?, ?)",
        (USER_ID, FIXED_NOW.isoformat(), FIXED_NOW.isoformat()),
    )
    conn.commit()
    yield conn
    conn.close()


def _pss(db, *, state: str, observed_at: datetime):
    upsert_provider_session_state(
        db,
        USER_ID,
        SessionEvidence(
            provider="amex",
            state=state,
            evidence_type="dom",
            evidence_summary="test",
            observed_at=observed_at,
            source="access_manager",
            confidence="high",
        ),
    )


class TestNaturalSessionCoordinator:
    def test_skip_fresh_connected(self, db):
        _pss(db, state="connected", observed_at=FIXED_NOW - timedelta(seconds=30))
        # Mark recent ready so revalidation interval suppresses.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_session_ready (
                user_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                ready_at TEXT NOT NULL,
                PRIMARY KEY (user_id, provider)
            )
            """
        )
        # Use PAM path: get_last_confirmed_ready_at — check what table it uses
        result = observe_natural_session(
            db,
            USER_ID,
            "amex",
            trigger_source="provider_page_observed",
            now=FIXED_NOW,
        )
        # Fresh 30s connected evidence → skip (even without ready row,
        # CURRENT_SESSION_FRESHNESS is 120s).
        assert result.action == NaturalSessionAction.SKIP_FRESH.value
        assert result.enqueued is False

    def test_enqueue_when_stale(self, db, monkeypatch):
        _pss(db, state="connected", observed_at=FIXED_NOW - timedelta(hours=2))
        calls = []

        def fake_ensure(db, user_id, provider, **kwargs):
            calls.append((user_id, provider, kwargs.get("trigger_source")))
            return type("V", (), {"verification_id": "v1"})()

        monkeypatch.setattr(
            "mighty.provider_access_manager.ensure_provider_access_check_if_stale",
            fake_ensure,
        )
        result = observe_natural_session(
            db,
            USER_ID,
            "amex",
            trigger_source="provider_page_observed",
            now=FIXED_NOW,
        )
        assert result.action == NaturalSessionAction.ENQUEUE_VERIFY.value
        assert result.enqueued is True
        assert calls and calls[0][2] == "provider_page_observed"

    def test_defer_to_recovery(self, db, monkeypatch):
        _pss(db, state="signed_out", observed_at=FIXED_NOW - timedelta(hours=2))
        case = claim_or_get_active_case(
            db,
            user_id=USER_ID,
            provider="amex",
            root_cause="login",
            now=FIXED_NOW,
        )
        append_attempt(
            db,
            case.case_id,
            capability=RecoveryCapability.SESSION_VERIFY,
            outcome=AttemptOutcome.FAILED,
            now=FIXED_NOW,
        )
        called = []

        def boom(*a, **k):
            called.append(1)
            raise AssertionError("must not enqueue while recovery active")

        monkeypatch.setattr(
            "mighty.provider_access_manager.ensure_provider_access_check_if_stale",
            boom,
        )
        result = observe_natural_session(
            db,
            USER_ID,
            "amex",
            trigger_source="provider_page_observed",
            now=FIXED_NOW,
        )
        assert result.action == NaturalSessionAction.DEFER_RECOVERY.value
        assert called == []

    def test_unsupported_provider(self, db):
        result = observe_natural_session(
            db,
            USER_ID,
            "delta",
            trigger_source="provider_page_observed",
            now=FIXED_NOW,
        )
        assert result.action == NaturalSessionAction.UNSUPPORTED.value

    def test_ensure_due_sweep_counts(self, db, monkeypatch):
        _pss(db, state="connected", observed_at=FIXED_NOW - timedelta(hours=3))

        def fake_ensure(db, user_id, provider, **kwargs):
            return type("V", (), {"verification_id": "v2"})()

        monkeypatch.setattr(
            "mighty.provider_access_manager.ensure_provider_access_check_if_stale",
            fake_ensure,
        )
        monkeypatch.setattr(
            "mighty.provider_access_manager.run_verification_maintenance",
            lambda *a, **k: 0,
        )
        sweep = run_natural_session_ensure_due(
            db,
            USER_ID,
            trigger_source="scheduled_recheck",
            now=FIXED_NOW,
        )
        assert sweep.detections >= 1
        assert sweep.enqueued >= 1
        assert sweep.errors == 0

    def test_never_raises(self, db, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(
            "mighty.natural_session.get_provider_session_states",
            boom,
        )
        result = observe_natural_session(
            db, USER_ID, "amex", now=FIXED_NOW
        )
        assert result.action in {"error", "unsupported", "enqueue_verify", "skip_fresh", "defer_recovery"}
