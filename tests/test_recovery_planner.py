"""Pure Recovery Planner policy tests (Milestone 6)."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.recovery_planner import (
    AttemptOutcome,
    RecoveryAttemptRecord,
    RecoveryCapability,
    RecoveryDecisionKind,
    RecoveryFacts,
    RecoveryHistory,
    plan_recovery,
)


def _facts(**kwargs) -> RecoveryFacts:
    base = dict(
        user_id="u1",
        provider="amex",
        root_cause="login",
        interruption="login",
        supports_silent_reauth=False,
        supports_navigation_gap_fill=False,
    )
    base.update(kwargs)
    return RecoveryFacts(**base)


class TestPlanRecovery:
    def test_human_only_mfa_escalates_immediately(self):
        decision = plan_recovery(
            _facts(interruption="mfa", root_cause="mfa"),
            RecoveryHistory(),
        )
        assert decision.kind is RecoveryDecisionKind.ESCALATE
        assert decision.capability is RecoveryCapability.ASK_HUMAN
        assert decision.reason == "human_only:mfa"

    def test_captcha_and_consent_human_only(self):
        for code in ("captcha", "consent"):
            decision = plan_recovery(
                _facts(interruption=code, root_cause=code),
                RecoveryHistory(),
            )
            assert decision.kind is RecoveryDecisionKind.ESCALATE
            assert decision.reason == f"human_only:{code}"

    def test_login_starts_with_session_verify(self):
        decision = plan_recovery(_facts(), RecoveryHistory())
        assert decision.kind is RecoveryDecisionKind.ATTEMPT
        assert decision.capability is RecoveryCapability.SESSION_VERIFY

    def test_skips_unavailable_then_continues(self):
        history = RecoveryHistory(
            attempts=(
                RecoveryAttemptRecord(
                    RecoveryCapability.SESSION_VERIFY, AttemptOutcome.SUCCEEDED
                ),
                RecoveryAttemptRecord(
                    RecoveryCapability.SILENT_REAUTH, AttemptOutcome.SKIPPED
                ),
                RecoveryAttemptRecord(
                    RecoveryCapability.NAVIGATION_GAP_FILL, AttemptOutcome.SKIPPED
                ),
            )
        )
        decision = plan_recovery(_facts(), history)
        assert decision.capability is RecoveryCapability.ACCOUNT_RESYNC

    def test_exhaustion_escalates(self):
        attempts = []
        for cap in (
            RecoveryCapability.SESSION_VERIFY,
            RecoveryCapability.SILENT_REAUTH,
            RecoveryCapability.ACCOUNT_RESYNC,
            RecoveryCapability.NAVIGATION_GAP_FILL,
            RecoveryCapability.DEEP_PROBE,
            RecoveryCapability.BOUNDED_WAIT,
            RecoveryCapability.BOUNDED_WAIT,
        ):
            attempts.append(
                RecoveryAttemptRecord(cap, AttemptOutcome.FAILED)
            )
        decision = plan_recovery(_facts(), RecoveryHistory(tuple(attempts)))
        assert decision.kind is RecoveryDecisionKind.ESCALATE
        assert decision.reason == "exhausted"

    def test_failure_cleared_succeeds(self):
        decision = plan_recovery(
            _facts(failure_cleared=True),
            RecoveryHistory(),
        )
        assert decision.kind is RecoveryDecisionKind.SUCCEED

    def test_deterministic(self):
        facts = _facts()
        history = RecoveryHistory()
        assert plan_recovery(facts, history) == plan_recovery(facts, history)
