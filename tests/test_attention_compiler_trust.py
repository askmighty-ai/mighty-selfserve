"""Golden / replay tests for TrustSignal → trust compiler (M5)."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    REASON_AWAITING_USER,
    AttentionClass,
    AttentionCtaKey,
    AttentionSourceKind,
    AttentionUrgency,
)
from mighty.attention_compiler import (
    TrustSignal,
    compile_attention_candidates,
    compile_trust_attention,
    trust_fingerprint,
)
from mighty.auth_truth import ACCESS_BROWSER_SESSION, ACCESS_MANAGED_RUNTIME

USER_ID = "user-1"
PROVIDER = "amex"
OBSERVED = "2026-07-21T12:00:00+00:00"


def _signal(**overrides) -> TrustSignal:
    payload = {
        "user_id": USER_ID,
        "provider": PROVIDER,
        "access_method": ACCESS_MANAGED_RUNTIME,
        "presentation_status": "awaiting_user",
        "authentication_state": "SIGNED_IN",
        "access_health": "degraded",
        "recovery_state": "awaiting_user",
        "runtime_state": "running",
        "escalation_reason": "mfa",
        "observed_at": OBSERVED,
        "needs_human": False,
        "interruption_expected": False,
    }
    payload.update(overrides)
    return TrustSignal(**payload)


class TestTrustCompiler:
    def test_awaiting_user_golden(self):
        item = compile_trust_attention(_signal())
        assert item is not None
        assert item.to_dict() == {
            "schema_version": ATTENTION_ITEM_SCHEMA_VERSION,
            "attention_id": "att_user-1_trust_amex",
            "user_id": USER_ID,
            "attention_class": AttentionClass.TRUST.value,
            "urgency": AttentionUrgency.BLOCKER.value,
            "provider": PROVIDER,
            "fingerprint": "trust:amex:runtime",
            "reason": {"code": REASON_AWAITING_USER},
            "cta_key": AttentionCtaKey.FOCUS_MANAGED_RUNTIME.value,
            "source_kind": AttentionSourceKind.TRUST.value,
            "source_ref": "runtime_access_state:user-1:amex",
            "observed_at": OBSERVED,
            "becomes_stale_at": None,
            "interruption_expected": False,
        }

    def test_needs_human_defers_to_auth(self):
        assert compile_trust_attention(_signal(needs_human=True)) is None

    def test_browser_session_does_not_emit(self):
        assert (
            compile_trust_attention(_signal(access_method=ACCESS_BROWSER_SESSION))
            is None
        )

    def test_healthy_does_not_emit(self):
        assert (
            compile_trust_attention(_signal(presentation_status="healthy")) is None
        )

    def test_stale_signed_in_healthy_does_not_emit(self):
        assert (
            compile_trust_attention(
                _signal(
                    presentation_status="stale",
                    authentication_state="SIGNED_IN",
                    access_health="healthy",
                )
            )
            is None
        )

    def test_runtime_offline_emits(self):
        item = compile_trust_attention(
            _signal(presentation_status="runtime_offline")
        )
        assert item is not None
        assert item.fingerprint == trust_fingerprint(PROVIDER)
        assert item.reason.code == "runtime_offline"


def test_gather_includes_trust_before_worker():
    from mighty.attention_compiler import WorkerSignal

    items = compile_attention_candidates(
        trust_signals=[_signal()],
        worker_signal=WorkerSignal(
            user_id=USER_ID,
            installed=False,
            reachable=False,
            enrolled_account_count=1,
        ),
    )
    assert [i.attention_class for i in items] == [
        AttentionClass.TRUST,
        AttentionClass.SYSTEM,
    ]
