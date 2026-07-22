"""Pure Natural Session policy tests (Milestone 8)."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.natural_session_policy import NaturalSessionAction, plan_natural_session


class TestPlanNaturalSession:
    def test_unsupported_without_capability(self):
        d = plan_natural_session(
            provider="delta",
            has_verification_capability=False,
            recovery_active=False,
            needs_verification=True,
        )
        assert d.action is NaturalSessionAction.UNSUPPORTED

    def test_defer_when_recovery_active(self):
        d = plan_natural_session(
            provider="amex",
            has_verification_capability=True,
            recovery_active=True,
            needs_verification=True,
        )
        assert d.action is NaturalSessionAction.DEFER_RECOVERY
        assert d.reason == "recovery_active"

    def test_skip_when_fresh(self):
        d = plan_natural_session(
            provider="amex",
            has_verification_capability=True,
            recovery_active=False,
            needs_verification=False,
        )
        assert d.action is NaturalSessionAction.SKIP_FRESH

    def test_enqueue_when_stale(self):
        d = plan_natural_session(
            provider="amex",
            has_verification_capability=True,
            recovery_active=False,
            needs_verification=True,
        )
        assert d.action is NaturalSessionAction.ENQUEUE_VERIFY

    def test_deterministic(self):
        kwargs = dict(
            provider="amex",
            has_verification_capability=True,
            recovery_active=False,
            needs_verification=True,
        )
        assert plan_natural_session(**kwargs) == plan_natural_session(**kwargs)
