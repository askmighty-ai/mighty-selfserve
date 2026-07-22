"""Recovery Supervisor integration tests (Milestone 6)."""

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
from mighty.attention_engine import read_attention
from mighty.attention_store import ensure_attention_overlay_tables
from mighty.provider_session_state import (
    SessionEvidence,
    ensure_provider_session_state_tables,
    upsert_provider_session_state,
)
from mighty.recovery_metrics import compute_recovery_metrics
from mighty.recovery_store import (
    ensure_recovery_tables,
    get_active_case,
    list_escalated_providers,
    load_history,
)
from mighty.recovery_supervisor import run_recovery_supervisor

FIXED_NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
USER_ID = "user-1"


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "recovery_supervisor.db"))
    conn.row_factory = sqlite3.Row
    ensure_account_state_tables(conn)
    ensure_provider_session_state_tables(conn)
    ensure_attention_overlay_tables(conn)
    ensure_recovery_tables(conn)
    yield conn
    conn.close()


def _persist(db, *, provider: str = "amex"):
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


def _pss(db, *, state: str, evidence_type: str = "login_form"):
    upsert_provider_session_state(
        db,
        USER_ID,
        SessionEvidence(
            provider="amex",
            state=state,
            evidence_type=evidence_type,
            evidence_summary=evidence_type,
            observed_at=FIXED_NOW - timedelta(minutes=5),
            source="access_manager",
            confidence="high",
        ),
    )


class TestRecoverySupervisor:
    def test_signed_out_starts_recovery_without_attention(self, db, monkeypatch):
        calls = []

        def fake_verify(db, user_id, provider, trigger_source, **kwargs):
            calls.append((user_id, provider, trigger_source))
            return None

        monkeypatch.setattr(
            "mighty.provider_access_manager.request_provider_verification",
            fake_verify,
        )
        monkeypatch.setattr(
            "mighty.provider_access_manager.ensure_provider_access_check_if_stale",
            lambda *a, **k: None,
        )

        _persist(db)
        _pss(db, state="signed_out")

        before = read_attention(db, USER_ID, now=FIXED_NOW)
        assert before.primary is None

        result = run_recovery_supervisor(db, now=FIXED_NOW, user_ids=[USER_ID])
        assert result.errors == 0
        assert result.attempts >= 1
        assert calls and calls[0][2] == "internal_recovery"
        assert get_active_case(
            db, user_id=USER_ID, provider="amex", root_cause="login"
        ) is not None

        mid = read_attention(db, USER_ID, now=FIXED_NOW)
        assert mid.primary is None  # still recovering

    def test_mfa_escalates_to_attention(self, db, monkeypatch):
        monkeypatch.setattr(
            "mighty.provider_access_manager.request_provider_verification",
            lambda *a, **k: None,
        )
        _persist(db)
        # signed_out with needs_human_reason projected as login from PSS;
        # use AuthTruth path via login — for MFA we need Runtime or project.
        # Simulate by writing PSS signed_out then patching load_auth_truths.
        _pss(db, state="signed_out")

        from mighty.auth_truth import (
            AuthInterruption,
            AuthTruth,
            EvidenceClass,
        )
        from mighty.authentication_state import AuthenticationState

        truth = AuthTruth(
            schema_version=1,
            user_id=USER_ID,
            provider="amex",
            state=AuthenticationState.SIGNED_OUT,
            access_method=ACCESS_BROWSER_SESSION,
            evidence_class=EvidenceClass.DEFINITIVE,
            evidence_source="access_manager",
            evidence_id=None,
            observed_at=FIXED_NOW.isoformat(),
            projected_at=FIXED_NOW.isoformat(),
            interruption=AuthInterruption.MFA,
            interruption_expected=False,
            needs_human=True,
            needs_human_reason="mfa",
            evidence_age_seconds=10.0,
            stale=False,
        )
        monkeypatch.setattr(
            "mighty.recovery_supervisor.load_auth_truths",
            lambda *a, **k: [truth],
        )
        monkeypatch.setattr(
            "mighty.recovery_supervisor.load_trust_signals",
            lambda *a, **k: [],
        )

        result = run_recovery_supervisor(db, now=FIXED_NOW, user_ids=[USER_ID])
        assert result.escalated == 1
        assert list_escalated_providers(db, USER_ID) == {"amex"}

        # Engine loaders still see real PSS login — seed Attention via engine
        # using escalated gate with real AuthTruth (login). Escalate root was mfa.
        # list_escalated_providers is per provider, so login auth_blocker may emit.
        state = read_attention(db, USER_ID, now=FIXED_NOW)
        assert state.primary is not None
        assert state.primary.attention_class == AttentionClass.AUTH_BLOCKER

    def test_success_clears_without_attention(self, db, monkeypatch):
        monkeypatch.setattr(
            "mighty.provider_access_manager.request_provider_verification",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "mighty.provider_access_manager.ensure_provider_access_check_if_stale",
            lambda *a, **k: None,
        )
        _persist(db)
        _pss(db, state="signed_out")
        run_recovery_supervisor(db, now=FIXED_NOW, user_ids=[USER_ID])
        assert get_active_case(
            db, user_id=USER_ID, provider="amex", root_cause="login"
        ) is not None

        later = FIXED_NOW + timedelta(minutes=1)
        upsert_provider_session_state(
            db,
            USER_ID,
            SessionEvidence(
                provider="amex",
                state="signed_in",
                evidence_type="dom",
                evidence_summary="dom",
                observed_at=later,
                source="access_manager",
                confidence="high",
            ),
        )
        result = run_recovery_supervisor(db, now=later, user_ids=[USER_ID])
        assert result.succeeded >= 1 or get_active_case(
            db, user_id=USER_ID, provider="amex", root_cause="login"
        ) is None
        assert get_active_case(
            db, user_id=USER_ID, provider="amex", root_cause="login"
        ) is None
        state = read_attention(db, USER_ID, now=later)
        assert state.primary is None

    def test_never_raises_on_executor_failure(self, db, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("pam down")

        monkeypatch.setattr(
            "mighty.provider_access_manager.request_provider_verification",
            boom,
        )
        monkeypatch.setattr(
            "mighty.provider_access_manager.ensure_provider_access_check_if_stale",
            boom,
        )
        _persist(db)
        _pss(db, state="signed_out")
        result = run_recovery_supervisor(db, now=FIXED_NOW, user_ids=[USER_ID])
        assert result.errors == 0
        case = get_active_case(
            db, user_id=USER_ID, provider="amex", root_cause="login"
        )
        assert case is not None
        history = load_history(db, case.case_id)
        assert any(a.outcome.value == "failed" for a in history.attempts)

    def test_metrics_compute(self, db, monkeypatch):
        monkeypatch.setattr(
            "mighty.provider_access_manager.request_provider_verification",
            lambda *a, **k: None,
        )
        _persist(db)
        _pss(db, state="signed_out")
        run_recovery_supervisor(db, now=FIXED_NOW, user_ids=[USER_ID])
        snap = compute_recovery_metrics(db, now=FIXED_NOW)
        assert snap.cases_active >= 1
        assert 0.0 <= snap.autonomous_recovery_coverage <= 1.0
