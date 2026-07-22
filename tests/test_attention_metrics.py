"""Tests for Attention production metrics (M5)."""

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
from mighty.attention_delivery import (
    ensure_attention_delivery_tables,
    record_delivery_receipt,
)
from mighty.attention_metrics import (
    compute_attention_metrics,
    load_attention_metric_snapshot,
    run_attention_metrics_sweep,
)
from mighty.attention_store import ensure_attention_overlay_tables
from mighty.auth_truth import ACCESS_MANAGED_RUNTIME
from mighty.provider_session_state import (
    SessionEvidence,
    ensure_provider_session_state_tables,
    upsert_provider_session_state,
)
from tests.recovery_test_helpers import escalate_recovery
from mighty.runtime_access_state import (
    ensure_runtime_access_state_tables,
    upsert_runtime_access_state,
)

FIXED_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
USER_ID = "user-1"


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "metrics.db"))
    conn.row_factory = sqlite3.Row
    ensure_account_state_tables(conn)
    ensure_provider_session_state_tables(conn)
    ensure_attention_overlay_tables(conn)
    ensure_attention_delivery_tables(conn)
    ensure_runtime_access_state_tables(conn)
    yield conn
    conn.close()


def _persist(db, *, access_method=ACCESS_BROWSER_SESSION, provider="amex"):
    persist_account_state(
        db,
        AccountState(
            user_id=USER_ID,
            provider=provider,
            display_name=provider.title(),
            category="financial",
            access_method=access_method,
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


class TestAttentionMetrics:
    def test_autonomous_coverage_healthy_runtime(self, db):
        _persist(db, access_method=ACCESS_MANAGED_RUNTIME)
        upsert_runtime_access_state(
            db,
            USER_ID,
            {
                "schema_version": 2,
                "provider": "amex",
                "runtime_instance_id": "rt-1",
                "updated_at": FIXED_NOW.isoformat(),
                "authentication_state": "SIGNED_IN",
                "access_health": "healthy",
                "runtime_state": "running",
                "browser_state": "healthy",
                "recovery_state": "idle",
            },
        )
        snap = compute_attention_metrics(db, now=FIXED_NOW, user_ids=[USER_ID])
        assert snap.autonomous_eligible == 1
        assert snap.autonomous_covered == 1
        assert snap.autonomous_coverage == 1.0

    def test_false_silence_when_blocker_undelivered(self, db):
        _persist(db)
        upsert_provider_session_state(
            db,
            USER_ID,
            SessionEvidence(
                provider="amex",
                state="signed_out",
                evidence_type="login_form",
                evidence_summary="login",
                observed_at=FIXED_NOW - timedelta(minutes=10),
                source="access_manager",
                confidence="high",
            ),
        )
        escalate_recovery(db, USER_ID, "amex", root_cause="login", now=FIXED_NOW)
        later = FIXED_NOW + timedelta(minutes=5)
        snap = compute_attention_metrics(db, now=later, user_ids=[USER_ID])
        assert snap.push_eligible_blockers == 1
        assert snap.false_silence_count == 1
        assert snap.false_silence_rate == 1.0
        assert snap.unexpected_interrupt_count == 1

    def test_delivery_sla_ok_when_receipt_timely(self, db):
        _persist(db)
        upsert_provider_session_state(
            db,
            USER_ID,
            SessionEvidence(
                provider="amex",
                state="signed_out",
                evidence_type="login_form",
                evidence_summary="login",
                observed_at=FIXED_NOW - timedelta(minutes=5),
                source="access_manager",
                confidence="high",
            ),
        )
        escalate_recovery(db, USER_ID, "amex", root_cause="login", now=FIXED_NOW)
        from mighty.attention_engine import read_attention

        state = read_attention(db, USER_ID, now=FIXED_NOW)
        assert state.primary is not None
        record_delivery_receipt(
            db,
            user_id=USER_ID,
            attention_id=state.primary.attention_id,
            channel="push",
            status="delivered",
            now=FIXED_NOW,
            attempt_count=1,
            first_attempted_at=FIXED_NOW.isoformat(),
        )
        snap = run_attention_metrics_sweep(db, now=FIXED_NOW, user_ids=[USER_ID])
        assert snap is not None
        assert snap.delivery_sla_rate == 1.0
        loaded = load_attention_metric_snapshot(db)
        assert loaded is not None
        assert loaded.delivery_sla_ok == 1
