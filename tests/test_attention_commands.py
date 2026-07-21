"""Tests for Attention command helpers (M4)."""

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
from mighty.attention import AttentionClass
from mighty.attention_commands import (
    build_view_payload,
    command_cta,
    command_dismiss,
    command_snooze,
)
from mighty.attention_engine import read_attention
from mighty.attention_overlay import OverlayStatus
from mighty.attention_store import (
    AttentionStoreCommandError,
    ensure_attention_overlay_tables,
    get_attention_overlay,
)
from mighty.provider_session_state import (
    SessionEvidence,
    ensure_provider_session_state_tables,
    upsert_provider_session_state,
)

FIXED_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
USER_ID = "user-1"


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "attention_commands.db"))
    conn.row_factory = sqlite3.Row
    ensure_account_state_tables(conn)
    ensure_provider_session_state_tables(conn)
    ensure_attention_overlay_tables(conn)
    persist_account_state(
        conn,
        AccountState(
            user_id=USER_ID,
            provider="amex",
            display_name="Amex",
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
    upsert_provider_session_state(
        conn,
        USER_ID,
        SessionEvidence(
            provider="amex",
            state="signed_out",
            evidence_type="login_form",
            evidence_summary="login form",
            observed_at=FIXED_NOW - timedelta(minutes=5),
            source="access_manager",
            confidence="high",
        ),
    )
    conn.commit()
    yield conn
    conn.close()


def _primary_id(db) -> str:
    state = read_attention(db, USER_ID, now=FIXED_NOW)
    assert state.primary is not None
    return state.primary.attention_id


class TestAttentionCommands:
    def test_view_payload(self, db):
        payload = build_view_payload(db, USER_ID, "home", now=FIXED_NOW)
        assert payload["ok"] is True
        assert payload["view"]["primary"]["attention_class"] == (
            AttentionClass.AUTH_BLOCKER.value
        )

    def test_snooze_and_cta(self, db):
        aid = _primary_id(db)
        snoozed = command_snooze(db, USER_ID, aid, now=FIXED_NOW)
        assert snoozed["overlay"]["status"] == OverlayStatus.SNOOZED.value
        # Still a candidate — CTA can restart in_flight
        cta = command_cta(db, USER_ID, aid, now=FIXED_NOW + timedelta(minutes=1))
        assert cta["overlay"]["status"] == OverlayStatus.IN_FLIGHT.value
        assert get_attention_overlay(db, USER_ID, aid).status is OverlayStatus.IN_FLIGHT

    def test_dismiss_rejects_blocker(self, db):
        aid = _primary_id(db)
        with pytest.raises(AttentionStoreCommandError):
            command_dismiss(db, USER_ID, aid, now=FIXED_NOW)

    def test_cta_side_effect_hook(self, db):
        aid = _primary_id(db)
        called = []

        def req(db, user_id, provider):
            called.append((user_id, provider))
            return "ok"

        result = command_cta(
            db, USER_ID, aid, now=FIXED_NOW, request_verification=req
        )
        assert result["side_effects"]["verification_requested"] is True
        assert called == [(USER_ID, "amex")]
