"""Tests for AttentionSupervisor in_flight timeout + orphan GC (M4)."""

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
    AccountState,
    Confidence,
    ConfidenceFactors,
    ensure_account_state_tables,
    persist_account_state,
)
from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    AttentionClass,
    AttentionCtaKey,
    AttentionItem,
    AttentionReason,
    AttentionSourceKind,
    AttentionUrgency,
)
from mighty.attention_overlay import IN_FLIGHT_TIMEOUT_SECONDS, OverlayStatus
from mighty.attention_store import (
    ensure_attention_overlay_tables,
    get_attention_overlay,
    list_attention_overlay_user_ids,
    start_attention_cta,
    upsert_attention_overlay,
)
from mighty.attention_supervisor import run_attention_supervisor
from mighty.authentication_state import AuthenticationState
from mighty.provider_session_state import (
    SessionEvidence,
    ensure_provider_session_state_tables,
    upsert_provider_session_state,
)
from tests.recovery_test_helpers import escalate_recovery

FIXED_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
USER_ID = "user-1"


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "attention_supervisor.db"))
    conn.row_factory = sqlite3.Row
    ensure_account_state_tables(conn)
    ensure_provider_session_state_tables(conn)
    ensure_attention_overlay_tables(conn)
    conn.commit()
    yield conn
    conn.close()


def _persist_account(db, provider: str = "amex"):
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
        ),
    )


def _write_signed_out(db, provider: str = "amex"):
    upsert_provider_session_state(
        db,
        USER_ID,
        SessionEvidence(
            provider=provider,
            state="signed_out",
            evidence_type="login_form",
            evidence_summary="login form",
            observed_at=FIXED_NOW - timedelta(minutes=5),
            source="access_manager",
            confidence="high",
        ),
    )


def _auth_item(provider: str = "amex") -> AttentionItem:
    return AttentionItem(
        schema_version=ATTENTION_ITEM_SCHEMA_VERSION,
        attention_id=f"att_{USER_ID}_auth_blocker_{provider}_needs_human",
        user_id=USER_ID,
        attention_class=AttentionClass.AUTH_BLOCKER,
        urgency=AttentionUrgency.BLOCKER,
        provider=provider,
        fingerprint=f"auth:{provider}:needs_human",
        reason=AttentionReason(code="login"),
        cta_key=AttentionCtaKey.START_PROVIDER_LOGIN,
        source_kind=AttentionSourceKind.AUTH,
        source_ref=f"auth_truth:{USER_ID}:{provider}",
        observed_at=FIXED_NOW.isoformat(),
        becomes_stale_at=None,
        interruption_expected=False,
    )


class TestAttentionSupervisor:
    def test_clears_timed_out_in_flight(self, db):
        _persist_account(db)
        _write_signed_out(db)
        escalate_recovery(db, USER_ID, "amex", root_cause="login", now=FIXED_NOW)
        item = _auth_item()
        started = FIXED_NOW - timedelta(seconds=IN_FLIGHT_TIMEOUT_SECONDS + 60)
        start_attention_cta(db, item, now=started)
        assert get_attention_overlay(db, USER_ID, item.attention_id) is not None

        result = run_attention_supervisor(db, now=FIXED_NOW)
        assert result.in_flight_cleared == 1
        assert result.reopened == 1
        assert get_attention_overlay(db, USER_ID, item.attention_id) is None

    def test_keeps_fresh_in_flight(self, db):
        _persist_account(db)
        _write_signed_out(db)
        escalate_recovery(db, USER_ID, "amex", root_cause="login", now=FIXED_NOW)
        item = _auth_item()
        start_attention_cta(db, item, now=FIXED_NOW - timedelta(minutes=5))
        result = run_attention_supervisor(db, now=FIXED_NOW)
        assert result.in_flight_cleared == 0
        overlay = get_attention_overlay(db, USER_ID, item.attention_id)
        assert overlay is not None
        assert overlay.status is OverlayStatus.IN_FLIGHT

    def test_deletes_orphan_overlay_when_candidate_gone(self, db):
        _persist_account(db)
        # signed_in → no auth_blocker candidate
        upsert_provider_session_state(
            db,
            USER_ID,
            SessionEvidence(
                provider="amex",
                state="signed_in",
                evidence_type="dom",
                evidence_summary="dom",
                observed_at=FIXED_NOW - timedelta(minutes=5),
                source="access_manager",
                confidence="high",
            ),
        )
        item = _auth_item()
        start_attention_cta(db, item, now=FIXED_NOW)
        assert list_attention_overlay_user_ids(db) == [USER_ID]

        result = run_attention_supervisor(db, now=FIXED_NOW)
        assert result.orphans_deleted == 1
        assert get_attention_overlay(db, USER_ID, item.attention_id) is None

    def test_never_raises_on_bad_user(self, db):
        result = run_attention_supervisor(
            db, now=FIXED_NOW, user_ids=["", "missing-user"]
        )
        assert result.errors == 0
        assert result.users_scanned == 1
