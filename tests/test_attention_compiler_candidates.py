"""Tests for compile_attention_candidates gather (PR 2H)."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.attention import AttentionClass
from mighty.attention_compiler import (
    AuthorizeRow,
    compile_attention_candidates,
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

USER_ID = "user-1"
OBSERVED = "2026-07-21T12:00:00+00:00"
PROJECTED = "2026-07-21T12:05:00+00:00"


def _truth(**overrides) -> AuthTruth:
    payload = {
        "schema_version": AUTH_TRUTH_SCHEMA_VERSION,
        "user_id": USER_ID,
        "provider": "amex",
        "state": AuthenticationState.SIGNED_IN,
        "access_method": ACCESS_BROWSER_SESSION,
        "evidence_class": EvidenceClass.DEFINITIVE,
        "evidence_source": EVIDENCE_SOURCE_ACCESS_MANAGER,
        "evidence_id": "ev-1",
        "observed_at": OBSERVED,
        "projected_at": PROJECTED,
        "interruption": AuthInterruption.NONE,
        "interruption_expected": False,
        "needs_human": False,
        "needs_human_reason": None,
        "evidence_age_seconds": 300.0,
        "stale": False,
    }
    payload.update(overrides)
    return AuthTruth(**payload)


def test_empty_inputs():
    assert compile_attention_candidates() == ()


def test_gathers_blocker_degraded_and_authorize():
    truths = [
        _truth(
            provider="amex",
            needs_human=True,
            needs_human_reason="login",
            interruption=AuthInterruption.LOGIN,
            state=AuthenticationState.SIGNED_OUT,
        ),
        _truth(provider="chase", stale=True),
        _truth(provider="united"),  # healthy → nothing
    ]
    rows = [
        AuthorizeRow(
            action_id="42",
            user_id=USER_ID,
            status="pending",
            created_at=OBSERVED,
        ),
        AuthorizeRow(
            action_id="99",
            user_id=USER_ID,
            status="approved",
        ),
    ]
    items = compile_attention_candidates(auth_truths=truths, authorize_rows=rows)
    classes = [item.attention_class for item in items]
    assert classes == [
        AttentionClass.AUTH_BLOCKER,
        AttentionClass.ACCESS_DEGRADED,
        AttentionClass.AGENT_AUTHORIZATION,
    ]
    assert items[0].provider == "amex"
    assert items[1].provider == "chase"
    assert items[2].source_ref == "authorize:42"


def test_needs_human_does_not_also_emit_degraded():
    truth = _truth(
        needs_human=True,
        needs_human_reason="mfa",
        interruption=AuthInterruption.MFA,
        stale=True,
        state=AuthenticationState.SIGNED_OUT,
    )
    items = compile_attention_candidates(auth_truths=[truth])
    assert len(items) == 1
    assert items[0].attention_class == AttentionClass.AUTH_BLOCKER


def test_replay_stable():
    truths = [_truth(provider="amex", stale=True)]
    rows = [
        AuthorizeRow(action_id="1", user_id=USER_ID, status="pending"),
    ]
    first = compile_attention_candidates(auth_truths=truths, authorize_rows=rows)
    second = compile_attention_candidates(auth_truths=truths, authorize_rows=rows)
    assert [i.to_dict() for i in first] == [i.to_dict() for i in second]
