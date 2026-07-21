"""Golden / replay tests for AuthTruth → AttentionItem compiler (PR 2B)."""

from __future__ import annotations

import json
import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    REASON_CAPTCHA,
    REASON_CONSENT,
    REASON_LOGIN,
    REASON_MFA,
    REASON_UNKNOWN_HUMAN,
    AttentionClass,
    AttentionCtaKey,
    AttentionItem,
    AttentionSourceKind,
    AttentionUrgency,
)
from mighty.attention_compiler import (
    auth_blocker_attention_id,
    auth_blocker_fingerprint,
    auth_truth_source_ref,
    compile_auth_attention,
)
from mighty.auth_truth import (
    ACCESS_API,
    ACCESS_BROWSER_SESSION,
    ACCESS_MANAGED_RUNTIME,
    ACCESS_MANUAL,
    AUTH_TRUTH_SCHEMA_VERSION,
    AuthInterruption,
    AuthTruth,
    EvidenceClass,
    EVIDENCE_SOURCE_ACCESS_MANAGER,
    EVIDENCE_SOURCE_RUNTIME,
)
from mighty.authentication_state import AuthenticationState

FIXED_OBSERVED_AT = "2026-07-21T12:00:00+00:00"
FIXED_PROJECTED_AT = "2026-07-21T12:05:00+00:00"
USER_ID = "user-1"
PROVIDER = "amex"


def _truth(**overrides) -> AuthTruth:
    """Build a minimal AuthTruth for compiler unit tests (no DB)."""
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


def _needs_human_truth(
    *,
    reason: str,
    access_method: str = ACCESS_BROWSER_SESSION,
    interruption_expected: bool = False,
    state: AuthenticationState = AuthenticationState.SIGNED_OUT,
    **overrides,
) -> AuthTruth:
    interruption = AuthInterruption(reason)
    defaults = {
        "state": state,
        "access_method": access_method,
        "interruption": interruption,
        "interruption_expected": interruption_expected,
        "needs_human": True,
        "needs_human_reason": reason,
    }
    if access_method == ACCESS_MANAGED_RUNTIME:
        defaults["evidence_source"] = EVIDENCE_SOURCE_RUNTIME
        defaults["state"] = AuthenticationState.LOGIN_UNKNOWN
    defaults.update(overrides)
    return _truth(**defaults)


# ---------------------------------------------------------------------------
# Golden expected AttentionItem payloads (exhaustive auth-blocker mapping)
# ---------------------------------------------------------------------------

_GOLDEN_AUTH_BLOCKER_BASE = {
    "schema_version": ATTENTION_ITEM_SCHEMA_VERSION,
    "attention_id": "att_user-1_auth_blocker_amex_needs_human",
    "user_id": USER_ID,
    "attention_class": AttentionClass.AUTH_BLOCKER.value,
    "urgency": AttentionUrgency.BLOCKER.value,
    "provider": PROVIDER,
    "fingerprint": "auth:amex:needs_human",
    "cta_key": AttentionCtaKey.START_PROVIDER_LOGIN.value,
    "source_kind": AttentionSourceKind.AUTH.value,
    "source_ref": "auth_truth:user-1:amex",
    "observed_at": FIXED_OBSERVED_AT,
    "becomes_stale_at": None,
    "interruption_expected": False,
}


def _golden(*, reason: str, **overrides) -> dict:
    payload = dict(_GOLDEN_AUTH_BLOCKER_BASE)
    payload["reason"] = {"code": reason}
    payload.update(overrides)
    return payload


# User brief vocabulary → AuthTruth interruption / needs_human_reason codes.
_AUTH_BLOCKER_CASES = (
    ("login_required", REASON_LOGIN),
    ("mfa_required", REASON_MFA),
    ("captcha_required", REASON_CAPTCHA),
    ("consent_required", REASON_CONSENT),
    ("unknown_human", REASON_UNKNOWN_HUMAN),
)


class TestCompileAuthAttentionMapping:
    def test_signed_in_emits_none(self):
        truth = _truth(
            state=AuthenticationState.SIGNED_IN,
            needs_human=False,
            interruption=AuthInterruption.NONE,
            needs_human_reason=None,
        )
        assert compile_auth_attention(truth) is None

    def test_needs_human_false_regardless_of_state_emits_none(self):
        for state in (
            AuthenticationState.SIGNED_IN,
            AuthenticationState.SIGNED_OUT,
            AuthenticationState.LOGIN_UNKNOWN,
        ):
            truth = _truth(state=state, needs_human=False, stale=True)
            assert compile_auth_attention(truth) is None

    @pytest.mark.parametrize(
        "brief_label,reason",
        _AUTH_BLOCKER_CASES,
        ids=[c[0] for c in _AUTH_BLOCKER_CASES],
    )
    def test_auth_blocker_golden(self, brief_label: str, reason: str):
        del brief_label  # documented alias only
        truth = _needs_human_truth(reason=reason)
        item = compile_auth_attention(truth)
        assert item is not None
        assert item.to_dict() == _golden(reason=reason)

    def test_runtime_mfa_uses_focus_managed_runtime_cta(self):
        truth = _needs_human_truth(
            reason=REASON_MFA,
            access_method=ACCESS_MANAGED_RUNTIME,
            interruption_expected=False,
        )
        item = compile_auth_attention(truth)
        assert item is not None
        assert item.to_dict() == _golden(
            reason=REASON_MFA,
            cta_key=AttentionCtaKey.FOCUS_MANAGED_RUNTIME.value,
        )

    def test_bootstrap_expected_mfa_passthrough(self):
        truth = _needs_human_truth(
            reason=REASON_MFA,
            access_method=ACCESS_MANAGED_RUNTIME,
            interruption_expected=True,
        )
        item = compile_auth_attention(truth)
        assert item is not None
        assert item.to_dict() == _golden(
            reason=REASON_MFA,
            cta_key=AttentionCtaKey.FOCUS_MANAGED_RUNTIME.value,
            interruption_expected=True,
        )

    def test_stale_without_needs_human_does_not_emit_access_degraded(self):
        """PR 2B scope: stale alone is not an auth_blocker (nor access_degraded)."""
        truth = _truth(
            state=AuthenticationState.SIGNED_IN,
            needs_human=False,
            stale=True,
        )
        assert compile_auth_attention(truth) is None

    def test_api_and_manual_without_needs_human_emit_none(self):
        for method in (ACCESS_API, ACCESS_MANUAL):
            truth = _truth(access_method=method, needs_human=False)
            assert compile_auth_attention(truth) is None

    def test_needs_human_with_missing_reason_falls_back_to_unknown_human(self):
        truth = _truth(
            state=AuthenticationState.SIGNED_OUT,
            needs_human=True,
            needs_human_reason=None,
            interruption=AuthInterruption.NONE,
        )
        item = compile_auth_attention(truth)
        assert item is not None
        assert item.reason.code == REASON_UNKNOWN_HUMAN
        assert item.to_dict() == _golden(reason=REASON_UNKNOWN_HUMAN)

    def test_null_observed_at_passthrough(self):
        truth = _needs_human_truth(reason=REASON_LOGIN, observed_at=None)
        item = compile_auth_attention(truth)
        assert item is not None
        assert item.to_dict() == _golden(reason=REASON_LOGIN, observed_at=None)


class TestFingerprintAndAttentionIdStability:
    def test_helpers_match_compiler_output(self):
        truth = _needs_human_truth(reason=REASON_LOGIN)
        item = compile_auth_attention(truth)
        assert item is not None
        assert item.fingerprint == auth_blocker_fingerprint(PROVIDER)
        assert item.attention_id == auth_blocker_attention_id(USER_ID, PROVIDER)
        assert item.source_ref == auth_truth_source_ref(USER_ID, PROVIDER)

    def test_provider_normalized_in_ids(self):
        truth = _needs_human_truth(reason=REASON_LOGIN, provider="AmEx")
        item = compile_auth_attention(truth)
        assert item is not None
        assert item.provider == "amex"
        assert item.fingerprint == "auth:amex:needs_human"
        assert item.attention_id == "att_user-1_auth_blocker_amex_needs_human"
        assert item.source_ref == "auth_truth:user-1:amex"

    def test_captcha_during_login_same_fingerprint_and_id(self):
        login = compile_auth_attention(_needs_human_truth(reason=REASON_LOGIN))
        captcha = compile_auth_attention(_needs_human_truth(reason=REASON_CAPTCHA))
        assert login is not None and captcha is not None
        assert login.fingerprint == captcha.fingerprint == "auth:amex:needs_human"
        assert login.attention_id == captcha.attention_id
        assert login.reason.code == REASON_LOGIN
        assert captcha.reason.code == REASON_CAPTCHA
        assert login.to_dict() != captcha.to_dict()

    def test_multi_provider_distinct_fingerprints(self):
        amex = compile_auth_attention(
            _needs_human_truth(reason=REASON_LOGIN, provider="amex")
        )
        chase = compile_auth_attention(
            _needs_human_truth(reason=REASON_LOGIN, provider="chase")
        )
        assert amex is not None and chase is not None
        assert amex.fingerprint == "auth:amex:needs_human"
        assert chase.fingerprint == "auth:chase:needs_human"
        assert amex.attention_id != chase.attention_id
        assert amex.source_ref == "auth_truth:user-1:amex"
        assert chase.source_ref == "auth_truth:user-1:chase"


class TestDeterminismAndReplayStability:
    def test_identical_truth_compiles_identically(self):
        truth = _needs_human_truth(reason=REASON_LOGIN)
        first = compile_auth_attention(truth)
        second = compile_auth_attention(truth)
        assert first is not None and second is not None
        assert first == second
        assert first.to_dict() == second.to_dict()

    def test_replay_across_fresh_truth_instances(self):
        """Same fields, new AuthTruth objects → identical AttentionItem dicts."""
        a = compile_auth_attention(_needs_human_truth(reason=REASON_CAPTCHA))
        b = compile_auth_attention(_needs_human_truth(reason=REASON_CAPTCHA))
        assert a is not None and b is not None
        assert a.to_dict() == b.to_dict()
        assert json.dumps(a.to_dict(), sort_keys=True) == json.dumps(
            b.to_dict(), sort_keys=True
        )

    @pytest.mark.parametrize(
        "brief_label,reason",
        _AUTH_BLOCKER_CASES,
        ids=[c[0] for c in _AUTH_BLOCKER_CASES],
    )
    def test_replay_stable_for_every_auth_blocker_reason(
        self, brief_label: str, reason: str
    ):
        del brief_label
        payloads = [
            compile_auth_attention(_needs_human_truth(reason=reason)).to_dict()
            for _ in range(3)
        ]
        assert payloads[0] == payloads[1] == payloads[2] == _golden(reason=reason)

    def test_none_path_is_replay_stable(self):
        results = [
            compile_auth_attention(
                _truth(state=AuthenticationState.SIGNED_IN, needs_human=False)
            )
            for _ in range(3)
        ]
        assert results == [None, None, None]

    def test_round_trip_through_attention_item_from_dict(self):
        item = compile_auth_attention(_needs_human_truth(reason=REASON_CONSENT))
        assert item is not None
        restored = AttentionItem.from_dict(item.to_dict())
        assert restored == item
        assert restored.to_dict() == _golden(reason=REASON_CONSENT)

    def test_projected_at_does_not_affect_output(self):
        """Compiler must not leak projection wall-clock into the candidate."""
        a = compile_auth_attention(
            _needs_human_truth(reason=REASON_LOGIN, projected_at="2026-01-01T00:00:00+00:00")
        )
        b = compile_auth_attention(
            _needs_human_truth(reason=REASON_LOGIN, projected_at="2026-12-31T23:59:59+00:00")
        )
        assert a is not None and b is not None
        assert a.to_dict() == b.to_dict()
        assert "projected_at" not in a.to_dict()
