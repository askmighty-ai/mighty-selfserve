"""Tests for Provider Orchestrator goal evaluation, prioritization, and WorkQueue."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape as html_escape

import pytest

from mighty.access_state_publication import serialize_access_state
from mighty.access_timeline import (
    build_provider_operations_details,
    render_provider_operations_details_html,
)
from mighty.provider_orchestrator import (
    ACTION_KEEPALIVE,
    ACTION_RECOVERY,
    ACTION_SNAPSHOT,
    ACTION_VERIFY,
    ProviderGoal,
    ProviderOrchestrator,
    WorkItem,
    WorkQueue,
    build_work_queue_for_provider,
    evaluate_provider_goal,
    normalize_access_observation,
)
from mighty.provider_registry import (
    ManagedProvider,
    ProviderPlatformCapabilities,
    ProviderRegistry,
    build_amex_provider,
    reset_provider_registry_for_tests,
)
from mighty.provider_runtime_control_center import (
    ACCESS_HEALTH_DEGRADED,
    ACCESS_HEALTH_HEALTHY,
    BROWSER_STATUS_HEALTHY,
    RECOVERY_STATUS_AWAITING_USER,
    RECOVERY_STATUS_IDLE,
    RUNTIME_STATUS_RUNNING,
    AccessState,
)


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_provider_registry_for_tests(include_amex=True)
    yield
    reset_provider_registry_for_tests(include_amex=True)


def _delta_provider() -> ManagedProvider:
    return ManagedProvider(
        provider_id="delta",
        display_name="Delta",
        capabilities=ProviderPlatformCapabilities(
            verification=True,
            keepalive=False,
            recovery=False,
            snapshots=True,
            connector_readiness=False,
        ),
        open_url="https://www.delta.com/",
        sort_order=20,
    )


def _fresh_state(**overrides) -> AccessState:
    base = AccessState(
        provider="amex",
        runtime_status=RUNTIME_STATUS_RUNNING,
        browser_status=BROWSER_STATUS_HEALTHY,
        recovery_planner_status=RECOVERY_STATUS_IDLE,
        authentication_state="SIGNED_IN",
        access_health=ACCESS_HEALTH_HEALTHY,
        session_started_at="2026-07-20T10:00:00+00:00",
        last_verification_at="2026-07-20T11:59:00+00:00",
        last_verification_result="SIGNED_IN",
        last_keepalive_at="2026-07-20T11:58:00+00:00",
        last_keepalive_result="ok",
        ready_for_extraction=True,
        ready_for_connector=True,
        updated_at="2026-07-20T11:59:30+00:00",
    )
    if not overrides:
        return base
    from dataclasses import replace

    return replace(base, **overrides)


def _payload_from_state(state: AccessState, **overrides):
    payload = serialize_access_state(state, runtime_instance_id="inst-1")
    payload.update(overrides)
    return payload


# --- Goal evaluation ---------------------------------------------------------


def test_goal_met_when_access_state_matches_provider_goal():
    state = _fresh_state()
    orch = ProviderOrchestrator()
    evaluation = orch.evaluate_provider("amex", state, now=NOW)
    assert evaluation.meets_goal is True
    assert evaluation.gaps == ()
    assert evaluation.work_items == ()


def test_goal_gaps_for_signed_out_require_recovery_then_verify():
    state = _fresh_state(
        authentication_state="SIGNED_OUT",
        access_health=ACCESS_HEALTH_DEGRADED,
        ready_for_connector=False,
        ready_for_extraction=False,
    )
    evaluation = ProviderOrchestrator().evaluate_provider("amex", state, now=NOW)
    assert evaluation.meets_goal is False
    assert "not_authenticated" in evaluation.gaps
    actions = [item.action for item in evaluation.work_items]
    assert ACTION_RECOVERY in actions
    assert ACTION_VERIFY in actions
    assert actions.index(ACTION_RECOVERY) < actions.index(ACTION_VERIFY)


def test_goal_pending_user_action_enqueues_recovery():
    state = _fresh_state(
        authentication_state="SIGNED_OUT",
        access_health=ACCESS_HEALTH_DEGRADED,
        recovery_planner_status=RECOVERY_STATUS_AWAITING_USER,
        escalation_reason="safe_recovery_exhausted",
        ready_for_connector=False,
    )
    evaluation = ProviderOrchestrator().evaluate_provider("amex", state, now=NOW)
    assert "pending_user_action" in evaluation.gaps
    recovery_items = [i for i in evaluation.work_items if i.action == ACTION_RECOVERY]
    assert recovery_items
    assert recovery_items[0].details.get("escalation_reason") == "safe_recovery_exhausted"


def test_goal_connector_readiness_gap_enqueues_verify():
    state = _fresh_state(ready_for_connector=False, ready_for_extraction=False)
    evaluation = ProviderOrchestrator().evaluate_provider("amex", state, now=NOW)
    assert "connector_not_ready" in evaluation.gaps
    assert any(item.action == ACTION_VERIFY for item in evaluation.work_items)


def test_goal_stale_snapshot_enqueues_snapshot():
    state = _fresh_state(updated_at="2026-07-20T11:00:00+00:00")
    evaluation = ProviderOrchestrator().evaluate_provider("amex", state, now=NOW)
    assert "snapshot_stale" in evaluation.gaps
    assert any(item.action == ACTION_SNAPSHOT for item in evaluation.work_items)


def test_goal_keepalive_due_when_interval_elapsed():
    state = _fresh_state(last_keepalive_at="2026-07-20T11:00:00+00:00")
    evaluation = ProviderOrchestrator().evaluate_provider("amex", state, now=NOW)
    assert "keepalive_due" in evaluation.gaps
    assert any(item.action == ACTION_KEEPALIVE for item in evaluation.work_items)


def test_goal_accepts_published_payload_dict():
    payload = _payload_from_state(_fresh_state(authentication_state="LOGIN_UNKNOWN"))
    evaluation = ProviderOrchestrator().evaluate_provider("amex", payload, now=NOW)
    assert "not_authenticated" in evaluation.gaps
    assert normalize_access_observation(payload, provider="amex")["present"] is True


def test_missing_access_state_enqueues_bootstrap_work():
    evaluation = ProviderOrchestrator().evaluate_provider("amex", None, now=NOW)
    assert evaluation.meets_goal is False
    assert "access_state_missing" in evaluation.gaps
    actions = {item.action for item in evaluation.work_items}
    assert ACTION_VERIFY in actions
    assert ACTION_SNAPSHOT in actions


# --- Prioritization ----------------------------------------------------------


def test_action_priority_recovery_before_verify_before_keepalive_before_snapshot():
    items = [
        WorkItem("amex", ACTION_SNAPSHOT, 40, "snap"),
        WorkItem("amex", ACTION_KEEPALIVE, 30, "keep"),
        WorkItem("amex", ACTION_VERIFY, 20, "verify"),
        WorkItem("amex", ACTION_RECOVERY, 10, "recover"),
    ]
    queue = WorkQueue(items=tuple(sorted(items, key=lambda i: (i.priority, i.action))))
    assert [i.action for i in queue] == [
        ACTION_RECOVERY,
        ACTION_VERIFY,
        ACTION_KEEPALIVE,
        ACTION_SNAPSHOT,
    ]


def test_evaluate_provider_dedupes_same_action():
    state = _fresh_state(
        authentication_state="SIGNED_OUT",
        access_health=ACCESS_HEALTH_DEGRADED,
        ready_for_connector=False,
        recovery_planner_status=RECOVERY_STATUS_AWAITING_USER,
        escalation_reason="needs_login",
    )
    evaluation = ProviderOrchestrator().evaluate_provider("amex", state, now=NOW)
    actions = [item.action for item in evaluation.work_items]
    assert actions.count(ACTION_RECOVERY) == 1
    assert actions.count(ACTION_VERIFY) == 1


def test_custom_provider_goal_thresholds():
    state = _fresh_state(
        last_keepalive_at="2026-07-20T11:55:00+00:00",
        updated_at="2026-07-20T11:59:00+00:00",
        last_verification_at="2026-07-20T11:59:00+00:00",
    )
    tight = ProviderGoal(max_keepalive_age_seconds=60.0)
    evaluation = evaluate_provider_goal(
        normalize_access_observation(state, provider="amex"),
        tight,
        capabilities=build_amex_provider().capabilities,
        now=NOW,
    )
    assert "keepalive_due" in evaluation.gaps


# --- Queue generation --------------------------------------------------------


def test_work_queue_to_dict_and_actions_for():
    queue = WorkQueue(
        items=(
            WorkItem("amex", ACTION_VERIFY, 20, "verify amex"),
            WorkItem("delta", ACTION_SNAPSHOT, 40, "snap delta"),
        ),
        evaluated_at="2026-07-20T12:00:00+00:00",
    )
    payload = queue.to_dict()
    assert payload["count"] == 2
    assert payload["items"][0]["provider"] == "amex"
    assert queue.actions_for("delta")[0].action == ACTION_SNAPSHOT


def test_build_work_queue_for_provider_convenience():
    state = _fresh_state(updated_at="2026-07-20T10:00:00+00:00")
    queue = build_work_queue_for_provider("amex", state, now=NOW)
    assert len(queue) >= 1
    assert all(item.provider == "amex" for item in queue)


def test_capabilities_suppress_unsupported_actions():
    registry = ProviderRegistry()
    registry.register(build_amex_provider())
    registry.register(_delta_provider())
    reset_provider_registry_for_tests(providers=registry.list_providers())

    delta_state = _fresh_state(
        provider="delta",
        authentication_state="SIGNED_OUT",
        access_health=ACCESS_HEALTH_DEGRADED,
        ready_for_connector=False,
        last_keepalive_at=None,
        updated_at="2026-07-20T10:00:00+00:00",
    )
    evaluation = ProviderOrchestrator().evaluate_provider("delta", delta_state, now=NOW)
    actions = {item.action for item in evaluation.work_items}
    assert ACTION_VERIFY in actions
    assert ACTION_SNAPSHOT in actions
    assert ACTION_KEEPALIVE not in actions
    assert ACTION_RECOVERY not in actions


# --- Multi-provider behavior -------------------------------------------------


def test_evaluate_all_merges_and_prioritizes_across_providers():
    registry = ProviderRegistry()
    registry.register(build_amex_provider())
    registry.register(_delta_provider())
    reset_provider_registry_for_tests(providers=registry.list_providers())

    amex = _fresh_state(
        provider="amex",
        authentication_state="SIGNED_OUT",
        access_health=ACCESS_HEALTH_DEGRADED,
        ready_for_connector=False,
    )
    delta = _fresh_state(
        provider="delta",
        updated_at="2026-07-20T10:00:00+00:00",
        last_verification_at="2026-07-20T10:00:00+00:00",
    )
    queue = ProviderOrchestrator().evaluate_all(
        {"amex": amex, "delta": delta},
        now=NOW,
        evaluated_at="2026-07-20T12:00:00+00:00",
    )
    assert len(queue) >= 2
    # Recovery (amex) must outrank snapshot/verify freshness work.
    assert queue.items[0].action == ACTION_RECOVERY
    assert queue.items[0].provider == "amex"
    providers_seen = {item.provider for item in queue}
    assert providers_seen == {"amex", "delta"}


def test_evaluate_all_includes_never_reported_registered_providers():
    registry = ProviderRegistry()
    registry.register(build_amex_provider())
    registry.register(_delta_provider())
    reset_provider_registry_for_tests(providers=registry.list_providers())

    queue = ProviderOrchestrator().evaluate_all(
        {"amex": _fresh_state()},
        now=NOW,
    )
    delta_items = queue.actions_for("delta")
    assert delta_items
    assert any(item.action == ACTION_VERIFY for item in delta_items)


def test_evaluate_all_detailed_returns_per_provider_evaluations():
    evaluations, queue = ProviderOrchestrator().evaluate_all_detailed(
        {"amex": _fresh_state()},
        now=NOW,
    )
    assert len(evaluations) == 1
    assert evaluations[0].provider == "amex"
    assert evaluations[0].meets_goal is True
    assert len(queue) == 0


# --- Operations dashboard exposure -------------------------------------------


def test_operations_details_includes_work_queue():
    payload = _payload_from_state(
        _fresh_state(authentication_state="SIGNED_OUT", ready_for_connector=False)
    )
    details = build_provider_operations_details(payload, provider="amex", now=NOW)
    assert details.work_queue
    assert details.meets_goal is False
    assert "not_authenticated" in details.orchestration_gaps
    ops = details.to_dict()
    assert ops["work_queue"]
    assert ops["work_queue"][0]["action"] in {
        ACTION_RECOVERY,
        ACTION_VERIFY,
        ACTION_KEEPALIVE,
        ACTION_SNAPSHOT,
    }

    html = render_provider_operations_details_html(details, escape=html_escape)
    assert "Intended work" in html
    assert 'data-work-queue="1"' in html
    assert "recovery" in html or "verify" in html


def test_operations_details_empty_queue_when_goal_met():
    payload = _payload_from_state(_fresh_state())
    details = build_provider_operations_details(payload, provider="amex", now=NOW)
    assert details.meets_goal is True
    assert details.work_queue == ()
    html = render_provider_operations_details_html(details, escape=html_escape)
    assert "Intended work" in html
    assert "Goal met" in html
    assert 'data-work-queue-empty="1"' in html
