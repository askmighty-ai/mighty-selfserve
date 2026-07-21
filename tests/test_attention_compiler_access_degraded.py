"""Golden / replay tests for AuthTruth → access_degraded compiler (PR 2G)."""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    REASON_LOGIN_UNKNOWN,
    REASON_STALE,
    AttentionClass,
    AttentionCtaKey,
    AttentionSourceKind,
    AttentionUrgency,
)
from mighty.attention_compiler import (
    access_degraded_attention_id,
    access_degraded_fingerprint,
    auth_truth_source_ref,
    compile_access_degraded_attention,
    compile_auth_attention,
)
from mighty.auth_truth import (
    ACCESS_BROWSER_SESSION,
    AUTH_TRUTH_SCHEMA_VERSION,
    AuthInterruption,
    AuthTruth,
    EvidenceClass,
    EVIDENCE_SOURCE_ACCESS_MANAGER,
)
from mighty.authentication_state import AuthenticationState

FIXED_OBSERVED_AT = "2026-07-21T12:00:00+00:00"
FIXED_PROJECTED_AT = "2026-07-21T12:05:00+00:00"
USER_ID = "user-1"
PROVIDER = "amex"


def _truth(**overrides) -> AuthTruth:
    payload = {
        "schema_version": AUTH_TRUTH_SCHEMA_VERSION,
        "user_id": USER_ID,
        "provider": PROVIDER,
        "state": AuthenticationState.SIGNED_IN,
        "access_method": ACCESS_BROWSER_SESSION,
        "evidence_class": EvidenceClass.DEFINITIVE,
        "evidence_source": EVIDENCE_SOURCE_ACCESS_MANAGER,
        "evidence_id": "ev-1",
        "observed_at": FIXED_OBSERVED_AT,
        "projected_at": FIXED_PROJECTED_AT,
        "interruption": AuthInterruption.NONE,
        "interruption_expected": False,
        "needs_human": False,
        "needs_human_reason": None,
        "evidence_age_seconds": 300.0,
        "stale": False,
    }
    payload.update(overrides)
    return AuthTruth(**payload)


class TestAccessDegraded:
    def test_stale_signed_in_golden(self):
        item = compile_access_degraded_attention(_truth(stale=True))
        assert item is not None
        assert item.to_dict() == {
            "schema_version": ATTENTION_ITEM_SCHEMA_VERSION,
            "attention_id": "att_user-1_access_degraded_amex",
            "user_id": USER_ID,
            "attention_class": AttentionClass.ACCESS_DEGRADED.value,
            "urgency": AttentionUrgency.INFORMATIONAL.value,
            "provider": PROVIDER,
            "fingerprint": "auth:amex:access_degraded",
            "reason": {"code": REASON_STALE},
            "cta_key": AttentionCtaKey.OPEN_ACCOUNT_DETAIL.value,
            "source_kind": AttentionSourceKind.AUTH.value,
            "source_ref": "auth_truth:user-1:amex",
            "observed_at": FIXED_OBSERVED_AT,
            "becomes_stale_at": None,
            "interruption_expected": False,
        }

    def test_login_unknown_without_stale(self):
        item = compile_access_degraded_attention(
            _truth(
                state=AuthenticationState.LOGIN_UNKNOWN,
                evidence_class=EvidenceClass.WEAK,
                stale=False,
            )
        )
        assert item is not None
        assert item.reason.code == REASON_LOGIN_UNKNOWN
        assert item.fingerprint == access_degraded_fingerprint(PROVIDER)

    def test_stale_wins_reason_over_login_unknown(self):
        item = compile_access_degraded_attention(
            _truth(
                state=AuthenticationState.LOGIN_UNKNOWN,
                stale=True,
            )
        )
        assert item is not None
        assert item.reason.code == REASON_STALE

    def test_needs_human_defers_to_blocker(self):
        truth = _truth(
            needs_human=True,
            needs_human_reason="login",
            interruption=AuthInterruption.LOGIN,
            state=AuthenticationState.SIGNED_OUT,
            stale=True,
        )
        assert compile_access_degraded_attention(truth) is None
        blocker = compile_auth_attention(truth)
        assert blocker is not None
        assert blocker.attention_class == AttentionClass.AUTH_BLOCKER

    def test_healthy_signed_in_emits_nothing(self):
        assert compile_access_degraded_attention(_truth()) is None

    def test_identity_helpers(self):
        assert access_degraded_fingerprint("AmEx") == "auth:amex:access_degraded"
        assert (
            access_degraded_attention_id(USER_ID, "amex")
            == "att_user-1_access_degraded_amex"
        )
        assert auth_truth_source_ref(USER_ID, "amex") == "auth_truth:user-1:amex"

    def test_replay_stability(self):
        truth = _truth(stale=True)
        snaps = [compile_access_degraded_attention(truth).to_dict() for _ in range(5)]
        assert all(s == snaps[0] for s in snaps)

    def test_scenario_7_phone_stale_is_degraded_not_blocker(self):
        """Part XIV #7 — stale ≠ signed_out; candidate is access_degraded."""
        truth = _truth(stale=True, state=AuthenticationState.SIGNED_IN)
        assert compile_auth_attention(truth) is None
        item = compile_access_degraded_attention(truth)
        assert item is not None
        assert item.attention_class == AttentionClass.ACCESS_DEGRADED
        assert item.cta_key == AttentionCtaKey.OPEN_ACCOUNT_DETAIL
