"""Provider Orchestrator — observational reconciliation over managed providers.

Compares each registered provider's current AccessState against a ProviderGoal
and produces a prioritized WorkQueue of intended actions (verify, keepalive,
snapshot, recovery).

Phase 1 is observational only: the orchestrator never executes work and does
not alter AccessSupervisor loops, publication, timeline, or the Provider Registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from mighty.provider_registry import (
    ManagedProvider,
    ProviderPlatformCapabilities,
    ProviderRegistry,
    get_provider_registry,
)
from mighty.provider_runtime_control_center import (
    ACCESS_HEALTH_HEALTHY,
    DEFAULT_KEEPALIVE_INTERVAL_SECONDS,
    DEFAULT_SUPERVISOR_INTERVAL_SECONDS,
    RECOVERY_STATUS_AWAITING_USER,
    RECOVERY_STATUS_FAILED,
    RECOVERY_STATUS_RECOVERING,
    AccessState,
    iso_now,
    parse_iso,
)

# Align with Railway AccessState stale presentation (3× default 60s heartbeat).
DEFAULT_SNAPSHOT_MAX_AGE_SECONDS = 180.0

ACTION_RECOVERY = "recovery"
ACTION_VERIFY = "verify"
ACTION_KEEPALIVE = "keepalive"
ACTION_SNAPSHOT = "snapshot"

# Lower number = higher priority.
ACTION_PRIORITY: dict[str, int] = {
    ACTION_RECOVERY: 10,
    ACTION_VERIFY: 20,
    ACTION_KEEPALIVE: 30,
    ACTION_SNAPSHOT: 40,
}

WORK_ACTIONS: frozenset[str] = frozenset(ACTION_PRIORITY)


@dataclass(frozen=True)
class ProviderGoal:
    """Desired steady-state for a managed provider.

    The orchestrator treats these as the platform's intended end-state. Gaps
    versus current AccessState become WorkQueue items.
    """

    authenticated: bool = True
    healthy: bool = True
    connector_ready: bool = True
    no_pending_user_action: bool = True
    max_snapshot_age_seconds: float = DEFAULT_SNAPSHOT_MAX_AGE_SECONDS
    max_keepalive_age_seconds: float = DEFAULT_KEEPALIVE_INTERVAL_SECONDS
    max_verification_age_seconds: float = DEFAULT_SUPERVISOR_INTERVAL_SECONDS * 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "authenticated": self.authenticated,
            "healthy": self.healthy,
            "connector_ready": self.connector_ready,
            "no_pending_user_action": self.no_pending_user_action,
            "max_snapshot_age_seconds": self.max_snapshot_age_seconds,
            "max_keepalive_age_seconds": self.max_keepalive_age_seconds,
            "max_verification_age_seconds": self.max_verification_age_seconds,
        }


@dataclass(frozen=True)
class WorkItem:
    """One intended (not yet executed) reconciliation action."""

    provider: str
    action: str
    priority: int
    reason: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        action = str(self.action or "").strip().lower()
        if action not in WORK_ACTIONS:
            raise ValueError(f"unsupported work action: {self.action!r}")
        object.__setattr__(self, "provider", str(self.provider or "").strip().lower())
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "priority", int(self.priority))
        object.__setattr__(self, "reason", str(self.reason or ""))
        object.__setattr__(self, "details", dict(self.details or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "action": self.action,
            "priority": self.priority,
            "reason": self.reason,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class WorkQueue:
    """Prioritized queue of intended provider actions (observational)."""

    items: tuple[WorkItem, ...] = ()
    evaluated_at: str = field(default_factory=iso_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluated_at": self.evaluated_at,
            "count": len(self.items),
            "items": [item.to_dict() for item in self.items],
        }

    def actions_for(self, provider: str) -> tuple[WorkItem, ...]:
        pid = str(provider or "").strip().lower()
        return tuple(item for item in self.items if item.provider == pid)

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self):
        return iter(self.items)


@dataclass(frozen=True)
class ProviderEvaluation:
    """Goal comparison result for one managed provider."""

    provider: str
    goal: ProviderGoal
    meets_goal: bool
    gaps: tuple[str, ...] = ()
    work_items: tuple[WorkItem, ...] = ()
    observed: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "goal": self.goal.to_dict(),
            "meets_goal": self.meets_goal,
            "gaps": list(self.gaps),
            "work_items": [item.to_dict() for item in self.work_items],
            "observed": dict(self.observed),
        }


def _age_seconds(iso_timestamp: str | None, *, now: datetime) -> float | None:
    parsed = parse_iso(iso_timestamp)
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds())


def normalize_access_observation(
    state: AccessState | Mapping[str, Any] | None,
    *,
    provider: str,
) -> dict[str, Any]:
    """Normalize AccessState or published payload into a common observation dict."""
    if state is None:
        return {
            "provider": str(provider).strip().lower(),
            "present": False,
            "authentication_state": None,
            "access_health": None,
            "runtime_state": None,
            "browser_state": None,
            "recovery_state": None,
            "escalation_reason": None,
            "ready_for_connector": False,
            "ready_for_extraction": False,
            "last_verified_at": None,
            "last_verification_result": None,
            "last_keepalive_at": None,
            "last_keepalive_result": None,
            "updated_at": None,
            "user_action_required": False,
        }

    if isinstance(state, AccessState):
        recovery = state.recovery_planner_status
        return {
            "provider": str(state.provider or provider).strip().lower(),
            "present": True,
            "authentication_state": state.authentication_state,
            "access_health": state.access_health,
            "runtime_state": state.runtime_status,
            "browser_state": state.browser_status,
            "recovery_state": recovery,
            "escalation_reason": state.escalation_reason,
            "ready_for_connector": bool(state.ready_for_connector),
            "ready_for_extraction": bool(state.ready_for_extraction),
            "last_verified_at": state.last_verification_at,
            "last_verification_result": state.last_verification_result,
            "last_keepalive_at": state.last_keepalive_at,
            "last_keepalive_result": state.last_keepalive_result,
            "updated_at": state.updated_at,
            "user_action_required": recovery == RECOVERY_STATUS_AWAITING_USER
            or bool(state.escalation_reason),
        }

    payload = dict(state)
    recovery = str(
        payload.get("recovery_state")
        or payload.get("recovery_planner_status")
        or ""
    )
    escalation = payload.get("escalation_reason")
    return {
        "provider": str(payload.get("provider") or provider).strip().lower(),
        "present": True,
        "authentication_state": payload.get("authentication_state"),
        "access_health": payload.get("access_health"),
        "runtime_state": payload.get("runtime_state") or payload.get("runtime_status"),
        "browser_state": payload.get("browser_state") or payload.get("browser_status"),
        "recovery_state": recovery or None,
        "escalation_reason": escalation,
        "ready_for_connector": bool(payload.get("ready_for_connector")),
        "ready_for_extraction": bool(payload.get("ready_for_extraction")),
        "last_verified_at": payload.get("last_verified_at")
        or payload.get("last_verification_at"),
        "last_verification_result": payload.get("last_verification_result"),
        "last_keepalive_at": payload.get("last_keepalive_at"),
        "last_keepalive_result": payload.get("last_keepalive_result"),
        "updated_at": payload.get("updated_at"),
        "user_action_required": recovery == RECOVERY_STATUS_AWAITING_USER
        or bool(escalation),
    }


def default_provider_goal() -> ProviderGoal:
    return ProviderGoal()


def _capability_allows(action: str, capabilities: ProviderPlatformCapabilities) -> bool:
    if action == ACTION_VERIFY:
        return bool(capabilities.verification)
    if action == ACTION_KEEPALIVE:
        return bool(capabilities.keepalive)
    if action == ACTION_SNAPSHOT:
        return bool(capabilities.snapshots)
    if action == ACTION_RECOVERY:
        return bool(capabilities.recovery)
    return False


def evaluate_provider_goal(
    observation: Mapping[str, Any],
    goal: ProviderGoal,
    *,
    capabilities: ProviderPlatformCapabilities | None = None,
    now: datetime | None = None,
) -> ProviderEvaluation:
    """Compare one provider observation to ProviderGoal and emit work items."""
    current = now or datetime.now(timezone.utc)
    caps = capabilities or ProviderPlatformCapabilities(
        verification=True,
        keepalive=True,
        recovery=True,
        snapshots=True,
        connector_readiness=True,
    )
    provider = str(observation.get("provider") or "").strip().lower()
    gaps: list[str] = []
    items: list[WorkItem] = []

    def enqueue(action: str, reason: str, **details: Any) -> None:
        if not _capability_allows(action, caps):
            return
        items.append(
            WorkItem(
                provider=provider,
                action=action,
                priority=ACTION_PRIORITY[action],
                reason=reason,
                details=details,
            )
        )

    present = bool(observation.get("present"))
    auth = str(observation.get("authentication_state") or "") or None
    health = str(observation.get("access_health") or "") or None
    recovery = str(observation.get("recovery_state") or "") or None
    user_action = bool(observation.get("user_action_required"))
    connector_ready = bool(observation.get("ready_for_connector"))

    if not present:
        gaps.append("access_state_missing")
        enqueue(ACTION_VERIFY, "no AccessState reported yet")
        enqueue(ACTION_SNAPSHOT, "no AccessState snapshot available")
        # Stable priority order among distinct actions.
        items.sort(key=lambda item: (item.priority, item.action))
        return ProviderEvaluation(
            provider=provider,
            goal=goal,
            meets_goal=False,
            gaps=tuple(gaps),
            work_items=tuple(items),
            observed=dict(observation),
        )

    if goal.no_pending_user_action and user_action:
        gaps.append("pending_user_action")
        enqueue(
            ACTION_RECOVERY,
            "user action required before autonomous progress",
            recovery_state=recovery,
            escalation_reason=observation.get("escalation_reason"),
        )

    if goal.authenticated and auth != "SIGNED_IN":
        gaps.append("not_authenticated")
        enqueue(
            ACTION_RECOVERY,
            f"authentication_state is {auth or 'unknown'}",
            authentication_state=auth,
        )
        enqueue(
            ACTION_VERIFY,
            f"verify required; authentication_state is {auth or 'unknown'}",
            authentication_state=auth,
        )

    if goal.healthy and health != ACCESS_HEALTH_HEALTHY:
        gaps.append("not_healthy")
        if recovery in {
            RECOVERY_STATUS_RECOVERING,
            RECOVERY_STATUS_FAILED,
            RECOVERY_STATUS_AWAITING_USER,
        } or auth != "SIGNED_IN":
            enqueue(
                ACTION_RECOVERY,
                f"access_health is {health or 'unknown'}",
                access_health=health,
                recovery_state=recovery,
            )
        else:
            enqueue(
                ACTION_VERIFY,
                f"access_health is {health or 'unknown'}; re-verify",
                access_health=health,
            )

    if goal.connector_ready and caps.connector_readiness and not connector_ready:
        gaps.append("connector_not_ready")
        enqueue(
            ACTION_VERIFY,
            "connector readiness not met",
            ready_for_connector=False,
        )

    verified_age = _age_seconds(
        observation.get("last_verified_at"),  # type: ignore[arg-type]
        now=current,
    )
    if caps.verification and auth == "SIGNED_IN":
        if verified_age is None or verified_age >= goal.max_verification_age_seconds:
            gaps.append("verification_stale")
            enqueue(
                ACTION_VERIFY,
                "verification is due",
                last_verified_at=observation.get("last_verified_at"),
                age_seconds=verified_age,
                max_age_seconds=goal.max_verification_age_seconds,
            )

    keepalive_age = _age_seconds(
        observation.get("last_keepalive_at"),  # type: ignore[arg-type]
        now=current,
    )
    if caps.keepalive and auth == "SIGNED_IN" and not user_action:
        if keepalive_age is None or keepalive_age >= goal.max_keepalive_age_seconds:
            gaps.append("keepalive_due")
            enqueue(
                ACTION_KEEPALIVE,
                "keepalive is due",
                last_keepalive_at=observation.get("last_keepalive_at"),
                age_seconds=keepalive_age,
                max_age_seconds=goal.max_keepalive_age_seconds,
            )

    snapshot_age = _age_seconds(
        observation.get("updated_at"),  # type: ignore[arg-type]
        now=current,
    )
    if caps.snapshots:
        if snapshot_age is None or snapshot_age >= goal.max_snapshot_age_seconds:
            gaps.append("snapshot_stale")
            enqueue(
                ACTION_SNAPSHOT,
                "AccessState snapshot is stale or missing",
                updated_at=observation.get("updated_at"),
                age_seconds=snapshot_age,
                max_age_seconds=goal.max_snapshot_age_seconds,
            )

    # Deduplicate by action (keep highest-priority / first reason).
    deduped: dict[str, WorkItem] = {}
    for item in sorted(items, key=lambda w: (w.priority, w.action)):
        if item.action not in deduped:
            deduped[item.action] = item
    ordered = tuple(sorted(deduped.values(), key=lambda w: (w.priority, w.action)))
    unique_gaps = tuple(dict.fromkeys(gaps))
    return ProviderEvaluation(
        provider=provider,
        goal=goal,
        meets_goal=not unique_gaps,
        gaps=unique_gaps,
        work_items=ordered,
        observed=dict(observation),
    )


class ProviderOrchestrator:
    """Evaluate every registered provider against ProviderGoal (observational)."""

    def __init__(
        self,
        registry: ProviderRegistry | None = None,
        *,
        goal: ProviderGoal | None = None,
    ) -> None:
        self._registry = registry
        self.goal = goal or default_provider_goal()

    @property
    def registry(self) -> ProviderRegistry:
        return self._registry if self._registry is not None else get_provider_registry()

    def list_providers(self) -> tuple[ManagedProvider, ...]:
        return self.registry.list_providers()

    def evaluate_provider(
        self,
        provider_id: str,
        state: AccessState | Mapping[str, Any] | None,
        *,
        now: datetime | None = None,
        goal: ProviderGoal | None = None,
    ) -> ProviderEvaluation:
        managed = self.registry.get(provider_id)
        pid = str(provider_id or "").strip().lower()
        if managed is None:
            # Still evaluate with empty capabilities so callers see a clear gap.
            observation = normalize_access_observation(state, provider=pid)
            evaluation = evaluate_provider_goal(
                observation,
                goal or self.goal,
                capabilities=ProviderPlatformCapabilities(),
                now=now,
            )
            return ProviderEvaluation(
                provider=pid,
                goal=evaluation.goal,
                meets_goal=False,
                gaps=("provider_not_registered",) + evaluation.gaps,
                work_items=(),
                observed=evaluation.observed,
            )
        observation = normalize_access_observation(state, provider=managed.provider_id)
        return evaluate_provider_goal(
            observation,
            goal or self.goal,
            capabilities=managed.capabilities,
            now=now,
        )

    def evaluate_all(
        self,
        states: Mapping[str, AccessState | Mapping[str, Any] | None] | None = None,
        *,
        now: datetime | None = None,
        goal: ProviderGoal | None = None,
        evaluated_at: str | None = None,
    ) -> WorkQueue:
        """Evaluate every registered provider; return a prioritized WorkQueue.

        ``states`` maps provider_id → AccessState, published payload, or None.
        Missing keys are treated as never-reported.
        """
        current = now or datetime.now(timezone.utc)
        state_map = {
            str(key).strip().lower(): value for key, value in dict(states or {}).items()
        }
        sort_index = {
            provider.provider_id: (provider.sort_order, provider.display_name.lower())
            for provider in self.list_providers()
        }
        evaluations: list[ProviderEvaluation] = []
        for managed in self.list_providers():
            evaluations.append(
                self.evaluate_provider(
                    managed.provider_id,
                    state_map.get(managed.provider_id),
                    now=current,
                    goal=goal,
                )
            )

        items: list[WorkItem] = []
        for evaluation in evaluations:
            items.extend(evaluation.work_items)

        items.sort(
            key=lambda item: (
                item.priority,
                sort_index.get(item.provider, (10_000, item.provider)),
                item.provider,
                item.action,
            )
        )
        return WorkQueue(
            items=tuple(items),
            evaluated_at=evaluated_at or iso_now(),
        )

    def evaluate_all_detailed(
        self,
        states: Mapping[str, AccessState | Mapping[str, Any] | None] | None = None,
        *,
        now: datetime | None = None,
        goal: ProviderGoal | None = None,
    ) -> tuple[tuple[ProviderEvaluation, ...], WorkQueue]:
        """Return per-provider evaluations plus the merged WorkQueue."""
        current = now or datetime.now(timezone.utc)
        state_map = {
            str(key).strip().lower(): value for key, value in dict(states or {}).items()
        }
        evaluations = tuple(
            self.evaluate_provider(
                managed.provider_id,
                state_map.get(managed.provider_id),
                now=current,
                goal=goal,
            )
            for managed in self.list_providers()
        )
        queue = self.evaluate_all(states, now=current, goal=goal)
        return evaluations, queue


def build_work_queue_for_provider(
    provider_id: str,
    state: AccessState | Mapping[str, Any] | None,
    *,
    registry: ProviderRegistry | None = None,
    goal: ProviderGoal | None = None,
    now: datetime | None = None,
) -> WorkQueue:
    """Convenience: WorkQueue containing only one provider's intended work."""
    orchestrator = ProviderOrchestrator(registry, goal=goal)
    evaluation = orchestrator.evaluate_provider(provider_id, state, now=now)
    return WorkQueue(items=evaluation.work_items, evaluated_at=iso_now())
