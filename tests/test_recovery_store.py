"""Recovery Store lifecycle + single-owner tests (Milestone 6)."""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.recovery_planner import AttemptOutcome, RecoveryCapability
from mighty.recovery_store import (
    append_attempt,
    claim_or_get_active_case,
    ensure_recovery_tables,
    get_active_case,
    list_escalated_providers,
    load_history,
    provider_allows_attention,
    transition_case,
)

FIXED_NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
USER_ID = "user-1"


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "recovery_store.db"))
    conn.row_factory = sqlite3.Row
    ensure_recovery_tables(conn)
    yield conn
    conn.close()


class TestRecoveryStore:
    def test_single_active_case_idempotent(self, db):
        a = claim_or_get_active_case(
            db, user_id=USER_ID, provider="amex", root_cause="login", now=FIXED_NOW
        )
        b = claim_or_get_active_case(
            db, user_id=USER_ID, provider="amex", root_cause="login", now=FIXED_NOW
        )
        assert a.case_id == b.case_id
        assert get_active_case(
            db, user_id=USER_ID, provider="amex", root_cause="login"
        ).case_id == a.case_id

    def test_attention_gate_active_suppresses(self, db):
        claim_or_get_active_case(
            db, user_id=USER_ID, provider="amex", root_cause="login", now=FIXED_NOW
        )
        assert provider_allows_attention(db, USER_ID, "amex") is False
        assert list_escalated_providers(db, USER_ID) == set()

    def test_attention_gate_escalated_allows(self, db):
        case = claim_or_get_active_case(
            db, user_id=USER_ID, provider="amex", root_cause="login", now=FIXED_NOW
        )
        append_attempt(
            db,
            case.case_id,
            capability=RecoveryCapability.ASK_HUMAN,
            outcome=AttemptOutcome.SUCCEEDED,
            now=FIXED_NOW,
        )
        transition_case(
            db,
            case.case_id,
            status="escalated",
            now=FIXED_NOW,
            escalation_reason="exhausted",
            clear_next_attempt=True,
        )
        assert provider_allows_attention(db, USER_ID, "amex") is True
        assert list_escalated_providers(db, USER_ID) == {"amex"}

    def test_history_order(self, db):
        case = claim_or_get_active_case(
            db, user_id=USER_ID, provider="amex", root_cause="login", now=FIXED_NOW
        )
        append_attempt(
            db,
            case.case_id,
            capability=RecoveryCapability.SESSION_VERIFY,
            outcome=AttemptOutcome.SUCCEEDED,
            now=FIXED_NOW,
        )
        append_attempt(
            db,
            case.case_id,
            capability=RecoveryCapability.BOUNDED_WAIT,
            outcome=AttemptOutcome.SUCCEEDED,
            now=FIXED_NOW.replace(minute=1),
        )
        history = load_history(db, case.case_id)
        assert [a.capability for a in history.attempts] == [
            RecoveryCapability.SESSION_VERIFY,
            RecoveryCapability.BOUNDED_WAIT,
        ]
