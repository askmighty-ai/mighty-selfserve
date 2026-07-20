"""Tests for ProviderPolicy defaults, overrides, and registry association."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from mighty.provider_orchestrator import (
    ACTION_KEEPALIVE,
    ACTION_RECOVERY,
    ACTION_SNAPSHOT,
    ACTION_VERIFY,
    ProviderGoal,
    ProviderOrchestrator,
    WorkItem,
    WorkQueue,
    evaluate_provider_goal,
    normalize_access_observation,
    resolve_thresholds,
)
from mighty.provider_policy import (
    DEFAULT_SNAPSHOT_FRESHNESS_TARGET_SECONDS,
    DEFAULT_VERIFICATION_INTERVAL_SECONDS,
    POLICY_KEY_KEEPALIVE_INTERVAL,
    POLICY_KEY_RECOVERY_BUDGET,
    POLICY_KEY_SNAPSHOT_FRESHNESS,
    POLICY_KEY_VERIFICATION_INTERVAL,
    ProviderPolicy,
    amex_provider_policy,
    default_provider_policy,
)
from mighty.provider_registry import (
    ManagedProvider,
    ProviderPlatformCapabilities,
    ProviderRegistry,
    build_amex_provider,
    get_provider_registry,
    reset_provider_registry_for_tests,
)
from mighty.provider_runtime_control_center import (
    ACCESS_HEALTH_DEGRADED,
    ACCESS_HEALTH_HEALTHY,
    BROWSER_STATUS_HEALTHY,
    DEFAULT_KEEPALIVE_INTERVAL_SECONDS,
    DEFAULT_MAX_SAFE_AUTH_RECOVERY_ATTEMPTS,
    RECOVERY_STATUS_IDLE,
    RUNTIME_STATUS_RUNNING,
    AccessState,
)
from mighty.access_state_publication import DEFAULT_HEARTBEAT_SECONDS


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_provider_registry_for_tests(include_amex=True)
    yield
    reset_provider_registry_for_tests(include_amex=True)


def _fresh_state(**overrides) -> AccessState:
    from dataclasses import replace

    base = AccessState(
        provider="amex",
        runtime_status=RUNTIME_STATUS_RUNNING,
        browser_status=BROWSER_STATUS_HEALTHY,
        recovery_planner_status=RECOVERY_STATUS_IDLE,
        authentication_state="SIGNED_IN",
        access_health=ACCESS_HEALTH_HEALTHY,
        runtime_started_at="2026-07-20T09:55:00+00:00",
        authenticated_session_started_at="2026-07-20T10:00:00+00:00",
        autonomous_since_at="2026-07-20T10:00:00+00:00",
        authentication_state_changed_at="2026-07-20T10:00:00+00:00",
        last_verification_at="2026-07-20T11:59:00+00:00",
        last_verification_result="SIGNED_IN",
        last_keepalive_at="2026-07-20T11:58:00+00:00",
        last_keepalive_result="ok",
        ready_for_extraction=True,
        ready_for_connector=True,
        updated_at="2026-07-20T11:59:30+00:00",
    )
    return replace(base, **overrides) if overrides else base


def test_default_and_amex_policy_match_current_amex_cadence():
    default = default_provider_policy()
    amex = amex_provider_policy()
    assert default.verification_interval_seconds == DEFAULT_VERIFICATION_INTERVAL_SECONDS
    assert default.keepalive_interval_seconds == DEFAULT_KEEPALIVE_INTERVAL_SECONDS
    assert default.snapshot_freshness_target_seconds == DEFAULT_SNAPSHOT_FRESHNESS_TARGET_SECONDS
    assert default.recovery_budget == DEFAULT_MAX_SAFE_AUTH_RECOVERY_ATTEMPTS
    assert default.heartbeat_interval_seconds == DEFAULT_HEARTBEAT_SECONDS
    # Amex keeps identical numeric behavior; only policy_id differs for explainability.
    assert amex.verification_interval_seconds == default.verification_interval_seconds
    assert amex.keepalive_interval_seconds == default.keepalive_interval_seconds
    assert amex.snapshot_freshness_target_seconds == default.snapshot_freshness_target_seconds
    assert amex.recovery_budget == default.recovery_budget
    assert amex.heartbeat_interval_seconds == default.heartbeat_interval_seconds
    assert amex.policy_id == "amex"
    assert default.policy_id == "default"


def test_amex_registry_entry_uses_amex_policy():
    provider = build_amex_provider()
    assert provider.policy.policy_id == "amex"
    assert provider.to_dict()["policy"]["keepalive_interval_seconds"] == 300.0
    registry = get_provider_registry()
    assert registry.policy_for("amex").policy_id == "amex"


def test_policy_overrides_change_work_queue_without_changing_goal():
    state = _fresh_state(
        last_keepalive_at="2026-07-20T11:55:00+00:00",  # 5m old
        last_verification_at="2026-07-20T11:59:00+00:00",
        updated_at="2026-07-20T11:59:00+00:00",
    )
    tight = ProviderPolicy(
        policy_id="tight-keepalive",
        keepalive_interval_seconds=60.0,
    )
    evaluation = ProviderOrchestrator().evaluate_provider(
        "amex",
        state,
        now=NOW,
        policy=tight,
    )
    assert "keepalive_due" in evaluation.gaps
    keepalive = next(i for i in evaluation.work_items if i.action == ACTION_KEEPALIVE)
    assert keepalive.policy_key == POLICY_KEY_KEEPALIVE_INTERVAL
    assert keepalive.policy_value == 60.0
    assert keepalive.policy_id == "tight-keepalive"
    assert "keepalive_interval=60" in keepalive.reason


def test_registered_provider_policy_override_via_registry():
    custom = ManagedProvider(
        provider_id="delta",
        display_name="Delta",
        capabilities=ProviderPlatformCapabilities(
            verification=True,
            keepalive=True,
            recovery=False,
            snapshots=True,
            connector_readiness=False,
        ),
        policy=ProviderPolicy(
            policy_id="delta-fast",
            verification_interval_seconds=30.0,
            snapshot_freshness_target_seconds=45.0,
            keepalive_interval_seconds=60.0,
        ),
        sort_order=20,
    )
    reset_provider_registry_for_tests(
        providers=(build_amex_provider(), custom),
    )
    state = _fresh_state(
        provider="delta",
        last_verification_at="2026-07-20T11:59:00+00:00",  # 60s ago > 30s
        last_keepalive_at="2026-07-20T11:59:00+00:00",
        updated_at="2026-07-20T11:59:00+00:00",
    )
    evaluation = ProviderOrchestrator().evaluate_provider("delta", state, now=NOW)
    assert evaluation.policy.policy_id == "delta-fast"
    assert any(i.action == ACTION_VERIFY for i in evaluation.work_items)
    verify = next(i for i in evaluation.work_items if i.action == ACTION_VERIFY)
    assert verify.policy_key == POLICY_KEY_VERIFICATION_INTERVAL
    assert verify.policy_value == 30.0


def test_work_item_explainability_fields():
    item = WorkItem(
        provider="amex",
        action=ACTION_SNAPSHOT,
        priority=40,
        reason="snapshot stale",
        policy_key=POLICY_KEY_SNAPSHOT_FRESHNESS,
        policy_value=180.0,
        policy_id="amex",
        confidence=1.0,
        created_at="2026-07-20T12:00:00+00:00",
        work_item_id="wi-test-1",
    )
    payload = item.to_dict()
    assert payload["work_item_id"] == "wi-test-1"
    assert payload["created_at"] == "2026-07-20T12:00:00+00:00"
    assert payload["priority"] == 40
    assert payload["reason"] == "snapshot stale"
    assert payload["policy_key"] == POLICY_KEY_SNAPSHOT_FRESHNESS
    assert payload["policy_value"] == 180.0
    assert payload["policy_id"] == "amex"
    assert "snapshot_freshness_target=180" in payload["policy_trigger"]
    assert payload["confidence"] == 1.0


def test_reason_generation_includes_policy_threshold():
    state = _fresh_state(updated_at="2026-07-20T11:00:00+00:00")
    evaluation = ProviderOrchestrator().evaluate_provider("amex", state, now=NOW)
    snap = next(i for i in evaluation.work_items if i.action == ACTION_SNAPSHOT)
    assert "snapshot_freshness_target=" in snap.reason
    assert "heartbeat=" in snap.reason
    assert snap.policy_key == POLICY_KEY_SNAPSHOT_FRESHNESS
    assert snap.confidence == 1.0
    assert snap.work_item_id
    assert snap.created_at


def test_recovery_budget_policy_on_auth_loss():
    state = _fresh_state(
        authentication_state="SIGNED_OUT",
        access_health=ACCESS_HEALTH_DEGRADED,
        ready_for_connector=False,
        recovery_attempt_count=1,
    )
    policy = ProviderPolicy(policy_id="amex", recovery_budget=1)
    evaluation = evaluate_provider_goal(
        normalize_access_observation(state, provider="amex"),
        ProviderGoal(),
        policy=policy,
        capabilities=build_amex_provider().capabilities,
        now=NOW,
    )
    recovery = next(i for i in evaluation.work_items if i.action == ACTION_RECOVERY)
    assert recovery.policy_key == POLICY_KEY_RECOVERY_BUDGET
    assert recovery.policy_value == 1
    assert "exhausted" in recovery.reason


def test_work_item_ordering_stable_by_priority_then_action():
    items = [
        WorkItem("amex", ACTION_SNAPSHOT, 40, "snap", work_item_id="d"),
        WorkItem("amex", ACTION_KEEPALIVE, 30, "keep", work_item_id="c"),
        WorkItem("amex", ACTION_VERIFY, 20, "verify", work_item_id="b"),
        WorkItem("amex", ACTION_RECOVERY, 10, "recover", work_item_id="a"),
    ]
    queue = WorkQueue(items=tuple(sorted(items, key=lambda i: (i.priority, i.action))))
    assert [i.action for i in queue] == [
        ACTION_RECOVERY,
        ACTION_VERIFY,
        ACTION_KEEPALIVE,
        ACTION_SNAPSHOT,
    ]


def test_backward_compatible_goal_threshold_override():
    """Legacy ProviderGoal max_* overrides still win over policy."""
    state = _fresh_state(
        last_keepalive_at="2026-07-20T11:55:00+00:00",
        updated_at="2026-07-20T11:59:00+00:00",
        last_verification_at="2026-07-20T11:59:00+00:00",
    )
    policy = default_provider_policy()  # keepalive 300s — would NOT fire
    goal = ProviderGoal(max_keepalive_age_seconds=60.0)
    thresholds = resolve_thresholds(goal, policy)
    assert thresholds.keepalive_interval_seconds == 60.0
    evaluation = evaluate_provider_goal(
        normalize_access_observation(state, provider="amex"),
        goal,
        policy=policy,
        capabilities=build_amex_provider().capabilities,
        now=NOW,
    )
    assert "keepalive_due" in evaluation.gaps


def test_amex_default_behavior_unchanged_when_fresh():
    """With Amex policy + fresh state, orchestrator still reports goal met."""
    evaluation = ProviderOrchestrator().evaluate_provider(
        "amex",
        _fresh_state(),
        now=NOW,
    )
    assert evaluation.meets_goal is True
    assert evaluation.work_items == ()
    assert evaluation.policy.policy_id == "amex"
    assert evaluation.thresholds is not None
    assert evaluation.thresholds.keepalive_interval_seconds == 300.0


def test_registry_policy_for_unknown_returns_default():
    registry = ProviderRegistry()
    policy = registry.policy_for("unknown")
    assert policy.policy_id == "default"
