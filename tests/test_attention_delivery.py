"""Tests for AttentionDelivery receipts + primary push (M4)."""

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
from mighty.attention_delivery import (
    deliver_attention_primary,
    get_delivery_receipt,
    run_attention_delivery_sweep,
)
from mighty.attention_store import ensure_attention_overlay_tables
from mighty.provider_session_state import (
    SessionEvidence,
    ensure_provider_session_state_tables,
    upsert_provider_session_state,
)

FIXED_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
USER_ID = "user-1"


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "attention_delivery.db"))
    conn.row_factory = sqlite3.Row
    ensure_account_state_tables(conn)
    ensure_provider_session_state_tables(conn)
    ensure_attention_overlay_tables(conn)
    conn.commit()
    yield conn
    conn.close()


def _seed_auth_blocker(db):
    persist_account_state(
        db,
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
        db,
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


class TestAttentionDelivery:
    def test_delivers_blocker_primary_once(self, db):
        _seed_auth_blocker(db)
        sent: list[tuple] = []

        def send_push(uid, title, body, url):
            sent.append((uid, title, body, url))
            return True

        first = deliver_attention_primary(
            db, USER_ID, now=FIXED_NOW, send_push=send_push
        )
        assert first is not None
        assert first.status == "delivered"
        assert len(sent) == 1
        assert first.attention_id
        receipt = get_delivery_receipt(db, USER_ID, first.attention_id)
        assert receipt is not None
        assert receipt["status"] == "delivered"

        second = deliver_attention_primary(
            db, USER_ID, now=FIXED_NOW, send_push=send_push
        )
        assert second is not None
        assert second.status == "skipped"
        assert len(sent) == 1

    def test_skips_when_no_primary(self, db):
        persist_account_state(
            db,
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
        result = deliver_attention_primary(
            db, USER_ID, now=FIXED_NOW, send_push=lambda *a: True
        )
        assert result is None

    def test_records_failure_without_raising(self, db):
        _seed_auth_blocker(db)

        def boom(*_a):
            raise RuntimeError("push down")

        result = deliver_attention_primary(
            db, USER_ID, now=FIXED_NOW, send_push=boom
        )
        assert result is not None
        assert result.status == "failed"
        receipt = get_delivery_receipt(db, USER_ID, result.attention_id)
        assert receipt["status"] == "failed"

    def test_sweep_counts_attempts(self, db):
        _seed_auth_blocker(db)
        n = run_attention_delivery_sweep(
            db,
            now=FIXED_NOW,
            user_ids=[USER_ID],
            send_push=lambda *a: True,
        )
        assert n == 1
