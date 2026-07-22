"""Pure authorization policy tests (Milestone 11)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mighty.agent_capability_registry import action_type_is_executable
from mighty.authorization_policy import (
    AUTH_AUTO_AUTHORIZE,
    AUTH_DENY,
    AUTH_REQUIRE_HUMAN,
    evaluate_authorization_policy,
)

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def test_routine_requires_human():
    d = evaluate_authorization_policy(
        action_type="book",
        consequence_level="routine",
        now=NOW,
    )
    assert d.outcome == AUTH_REQUIRE_HUMAN
    assert d.requires_human is True


def test_informational_auto_authorize():
    d = evaluate_authorization_policy(
        action_type="record",
        consequence_level="informational",
        now=NOW,
        record_only=True,
    )
    assert d.outcome == AUTH_AUTO_AUTHORIZE


def test_expired_denied():
    d = evaluate_authorization_policy(
        action_type="book",
        consequence_level="consequential",
        expires_at=(NOW - timedelta(seconds=1)).isoformat(),
        now=NOW,
    )
    assert d.outcome == AUTH_DENY
    assert d.reason == "expired"


def test_duplicate_denied():
    d = evaluate_authorization_policy(
        action_type="book",
        consequence_level="routine",
        now=NOW,
        duplicate_open=True,
    )
    assert d.outcome == AUTH_DENY
    assert d.reason == "duplicate_open_action"


def test_provider_capability_default():
    assert action_type_is_executable("book", provider="amex")
    assert action_type_is_executable("redeem", provider=None)


def test_replay_deterministic():
    a = evaluate_authorization_policy(
        action_type="pay", consequence_level="critical", now=NOW
    )
    b = evaluate_authorization_policy(
        action_type="pay", consequence_level="critical", now=NOW
    )
    assert a == b
