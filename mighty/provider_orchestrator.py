"""Provider Orchestrator — observational reconciliation over managed providers.

Compares each registered provider's current AccessState against a ProviderGoal
under that provider's ProviderPolicy, and produces a prioritized WorkQueue of
intended actions (verify, keepalive, snapshot, recovery).

Phase 1 is observational only: the orchestrator never executes work and does
not alter AccessSupervisor loops, publication, timeline, or registry contracts
beyond reading ManagedProvider.policy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from mighty.provider_policy import (
    POLICY_KEY_ACCESS_STATE_MISSING,
    POLICY_KEY_GOAL_AUTHENTICATED,
    POLICY_KEY_GOAL_CONNECTOR_READY,
    POLICY_KEY_GOAL_HEALTHY,
    POLICY_KEY_GOAL_NO_PENDING_USER_ACTION,
    POLICY_KEY_KEEPALIVE_INTERVAL,
    POLICY_KEY_RECOVERY_BUDGET,
    POLICY_KEY_SNAPSHOT_FRESHNESS,
    POLICY_KEY_VERIFICATION_INTERVAL,
    ProviderPolicy,
    default_provider_policy,
)
from mighty.provider_registry import (
    ManagedProvider,
    ProviderPlatformCapabilities,
    ProviderRegistry,
    get_provider_registry,
)
from mighty.provider_runtime_control_center import (
    ACCESS_HEALTH_HEALTHY,
    RECOVERY_STATUS_AWAITING_USER,
    RECOVERY_STATUS_FAILED,
    RECOVERY_STATUS_RECOVERING,
    AccessState,
    iso_now,
    parse_iso,
)

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

# Deterministic planner confidence for rule-based WorkItems.
CONFIDENCE_DETERMINISTIC = 1.0


def _new_work_item_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class ProviderGoal:
    """Desired steady-state for a managed provider.

    Thresholds live on ProviderPolicy. Optional max_* fields remain as
    backward-compatible overrides when non-None.
    """

    authenticated: bool = True
    healthy: bool = True
    connector_ready: bool = True
    no_pending_user_action: bool = True
    # Backward-compatible overrides (None → use ProviderPolicy).
    max_snapshot_age_seconds: float | None = None
    max_keepalive_age_seconds: float | None = None
    max_verification_age_seconds: float | None = None

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
class EffectiveThresholds:
    """Resolved planning thresholds: policy values with optional goal overrides."""

    verification_interval_seconds: float
    keepalive_interval_seconds: float
    snapshot_freshness_target_seconds: float
    recovery_budget: int
    heartbeat_interval_seconds: float
    policy_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "verification_interval_seconds": self.verification_interval_seconds,
            "keepalive_interval_seconds": self.keepalive_interval_seconds,
            "snapshot_freshness_target_seconds": self.snapshot_freshness_target_seconds,
            "recovery_budget": self.recovery_budget,
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            "policy_id": self.policy_id,
        }


def resolve_thresholds(
    goal: ProviderGoal,
    policy: ProviderPolicy,
) -> EffectiveThresholds:
    """Derive effective thresholds from ProviderPolicy, applying goal overrides."""
    return EffectiveThresholds(
        verification_interval_seconds=(
            float(goal.max_verification_age_seconds)
            if goal.max_verification_age_seconds is not None
            else policy.verification_interval_seconds
        ),
        keepalive_interval_seconds=(
            float(goal.max_keepalive_age_seconds)
            if goal.max_keepalive_age_seconds is not None
            else policy.keepalive_interval_seconds
        ),
        snapshot_freshness_target_seconds=(
            float(goal.max_snapshot_age_seconds)
            if goal.max_snapshot_age_seconds is not None
            else policy.snapshot_freshness_target_seconds
        ),
        recovery_budget=policy.recovery_budget,
        heartbeat_interval_seconds=policy.heartbeat_interval_seconds,
        policy_id=policy.policy_id,
    )


@dataclass(frozen=True)
class WorkItem:
    """One intended (not yet executed) reconciliation action with explainability."""

    provider: str
    action: str
    priority: int
    reason: str
    work_item_id: str = field(default_factory=_new_work_item_id)
    created_at: str = field(default_factory=iso_now)
    policy_key: str | None = None
    policy_value: Any = None
    policy_id: str | None = None
    confidence: float = CONFIDENCE_DETERMINISTIC
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        action = str(self.action or "").strip().lower()
        if action not in WORK_ACTIONS:
            raise ValueError(f"unsupported work action: {self.action!r}")
        object.__setattr__(self, "provider", str(self.provider or "").strip().lower())
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "priority", int(self.priority))
        object.__setattr__(self, "reason", str(self.reason or ""))
        object.__setattr__(
            self,
            "work_item_id",
            str(self.work_item_id or _new_work_item_id()),
        )
        object.__setattr__(self, "created_at", str(self.created_at or iso_now()))
        object.__setattr__(
            self,
            "policy_key",
            str(self.policy_key) if self.policy_key is not None else None,
        )
        object.__setattr__(
            self,
            "policy_id",
            str(self.policy_id) if self.policy_id is not None else None,
        )
        object.__setattr__(self, "confidence", float(self.confidence))
        object.__setattr__(self, "details", dict(self.details or {}))

    @property
    def policy_trigger(self) -> str:
        """Human-readable policy that triggered this WorkItem."""
        if not self.policy_key:
            return ""
        if self.policy_value is None:
            base = self.policy_key
        elif str(self.policy_key).endswith("_seconds"):
            short = str(self.policy_key).removesuffix("_seconds")
            base = f"{short}={self.policy_value}"
        else:
            base = f"{self.policy_key}={self.policy_value}"
        if self.policy_id:
            return f"{base} ({self.policy_id})"
        return base

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_item_id": self.work_item_id,
            "created_at": self.created_at,
            "provider": self.provider,
            "action": self.action,
            "priority": self.priority,
            "reason": self.reason,
            "policy_key": self.policy_key,
            "policy_value": self.policy_value,
            "policy_id": self.policy_id,
            "policy_trigger": self.policy_trigger,
            "confidence": self.confidence,
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
    """Goal + policy comparison result for one managed provider."""

    provider: str
    goal: ProviderGoal
    policy: ProviderPolicy
    meets_goal: bool
    gaps: tuple[str, ...] = ()
    work_items: tuple[WorkItem, ...] = ()
    observed: dict[str, Any] = field(default_factory=dict)
    thresholds: EffectiveThresholds | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "goal": self.goal.to_dict(),
            "policy": self.policy.to_dict(),
            "thresholds": self.thresholds.to_dict() if self.thresholds else None,
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


def _format_age_for_reason(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s ago"
    minutes = total // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    rem_m = minutes % 60
    return f"{hours}h {rem_m}m ago" if rem_m else f"{hours}h ago"


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
            "recovery_attempts": 0,
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
            "recovery_attempts": int(state.recovery_attempt_count or 0),
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
        "recovery_attempts": int(
            payload.get("recovery_attempts")
            or payload.get("recovery_attempt_count")
            or 0
        ),
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
    policy: ProviderPolicy | None = None,
    capabilities: ProviderPlatformCapabilities | None = None,
    now: datetime | None = None,
    created_at: str | None = None,
) -> ProviderEvaluation:
    """Compare AccessState observation to ProviderGoal under ProviderPolicy."""
    current = now or datetime.now(timezone.utc)
    pol = policy or default_provider_policy()
    thresholds = resolve_thresholds(goal, pol)
    caps = capabilities or ProviderPlatformCapabilities(
        verification=True,
        keepalive=True,
        recovery=True,
        snapshots=True,
        connector_readiness=True,
    )
    provider = str(observation.get("provider") or "").strip().lower()
    stamp = created_at or iso_now()
    gaps: list[str] = []
    items: list[WorkItem] = []

    def enqueue(
        action: str,
        reason: str,
        *,
        policy_key: str | None = None,
        policy_value: Any = None,
        **details: Any,
    ) -> None:
        if not _capability_allows(action, caps):
            return
        items.append(
            WorkItem(
                provider=provider,
                action=action,
                priority=ACTION_PRIORITY[action],
                reason=reason,
                created_at=stamp,
                policy_key=policy_key,
                policy_value=policy_value,
                policy_id=pol.policy_id,
                confidence=CONFIDENCE_DETERMINISTIC,
                details=details,
            )
        )

    present = bool(observation.get("present"))
    auth = str(observation.get("authentication_state") or "") or None
    health = str(observation.get("access_health") or "") or None
    recovery = str(observation.get("recovery_state") or "") or None
    user_action = bool(observation.get("user_action_required"))
    connector_ready = bool(observation.get("ready_for_connector"))
    recovery_attempts = int(observation.get("recovery_attempts") or 0)

    if not present:
        gaps.append("access_state_missing")
        enqueue(
            ACTION_VERIFY,
            "No AccessState reported yet; verification required to establish baseline.",
            policy_key=POLICY_KEY_ACCESS_STATE_MISSING,
            policy_value=True,
        )
        enqueue(
            ACTION_SNAPSHOT,
            (
                "No AccessState snapshot available; freshness target is "
                f"{thresholds.snapshot_freshness_target_seconds:g}s "
                f"(heartbeat {thresholds.heartbeat_interval_seconds:g}s)."
            ),
            policy_key=POLICY_KEY_SNAPSHOT_FRESHNESS,
            policy_value=thresholds.snapshot_freshness_target_seconds,
            heartbeat_interval_seconds=thresholds.heartbeat_interval_seconds,
        )
        items.sort(key=lambda item: (item.priority, item.action))
        return ProviderEvaluation(
            provider=provider,
            goal=goal,
            policy=pol,
            meets_goal=False,
            gaps=tuple(gaps),
            work_items=tuple(items),
            observed=dict(observation),
            thresholds=thresholds,
        )

    if goal.no_pending_user_action and user_action:
        gaps.append("pending_user_action")
        budget = thresholds.recovery_budget
        enqueue(
            ACTION_RECOVERY,
            (
                "User action required before autonomous progress; "
                f"recovery attempts {recovery_attempts}/{budget} "
                f"(recovery_budget={budget})."
            ),
            policy_key=POLICY_KEY_GOAL_NO_PENDING_USER_ACTION,
            policy_value=True,
            recovery_state=recovery,
            escalation_reason=observation.get("escalation_reason"),
            recovery_attempts=recovery_attempts,
            recovery_budget=budget,
        )

    if goal.authenticated and auth != "SIGNED_IN":
        gaps.append("not_authenticated")
        budget = thresholds.recovery_budget
        within_budget = recovery_attempts < budget
        enqueue(
            ACTION_RECOVERY,
            (
                f"Authentication is {auth or 'unknown'}; "
                + (
                    f"within recovery_budget ({recovery_attempts}/{budget})."
                    if within_budget
                    else f"recovery_budget exhausted ({recovery_attempts}/{budget})."
                )
            ),
            policy_key=POLICY_KEY_RECOVERY_BUDGET,
            policy_value=budget,
            authentication_state=auth,
            recovery_attempts=recovery_attempts,
            within_budget=within_budget,
        )
        enqueue(
            ACTION_VERIFY,
            f"Verify required; authentication_state is {auth or 'unknown'}.",
            policy_key=POLICY_KEY_GOAL_AUTHENTICATED,
            policy_value=True,
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
                (
                    f"Access health is {health or 'unknown'} "
                    f"(recovery_state={recovery or '—'}); recovery intended."
                ),
                policy_key=POLICY_KEY_GOAL_HEALTHY,
                policy_value=True,
                access_health=health,
                recovery_state=recovery,
            )
        else:
            enqueue(
                ACTION_VERIFY,
                f"Access health is {health or 'unknown'}; re-verify to restore healthy.",
                policy_key=POLICY_KEY_GOAL_HEALTHY,
                policy_value=True,
                access_health=health,
            )

    if goal.connector_ready and caps.connector_readiness and not connector_ready:
        gaps.append("connector_not_ready")
        enqueue(
            ACTION_VERIFY,
            "Connector readiness not met; verification intended to restore readiness.",
            policy_key=POLICY_KEY_GOAL_CONNECTOR_READY,
            policy_value=True,
            ready_for_connector=False,
        )

    verified_age = _age_seconds(
        observation.get("last_verified_at"),  # type: ignore[arg-type]
        now=current,
    )
    verify_limit = thresholds.verification_interval_seconds
    if caps.verification and auth == "SIGNED_IN":
        if verified_age is None or verified_age >= verify_limit:
            gaps.append("verification_stale")
            enqueue(
                ACTION_VERIFY,
                (
                    "Verification is due: last verified "
                    f"{_format_age_for_reason(verified_age)}; "
                    f"verification_interval={verify_limit:g}s."
                ),
                policy_key=POLICY_KEY_VERIFICATION_INTERVAL,
                policy_value=verify_limit,
                last_verified_at=observation.get("last_verified_at"),
                age_seconds=verified_age,
            )

    keepalive_age = _age_seconds(
        observation.get("last_keepalive_at"),  # type: ignore[arg-type]
        now=current,
    )
    keepalive_limit = thresholds.keepalive_interval_seconds
    if caps.keepalive and auth == "SIGNED_IN" and not user_action:
        if keepalive_age is None or keepalive_age >= keepalive_limit:
            gaps.append("keepalive_due")
            enqueue(
                ACTION_KEEPALIVE,
                (
                    "Keepalive is due: last keepalive "
                    f"{_format_age_for_reason(keepalive_age)}; "
                    f"keepalive_interval={keepalive_limit:g}s."
                ),
                policy_key=POLICY_KEY_KEEPALIVE_INTERVAL,
                policy_value=keepalive_limit,
                last_keepalive_at=observation.get("last_keepalive_at"),
                age_seconds=keepalive_age,
            )

    snapshot_age = _age_seconds(
        observation.get("updated_at"),  # type: ignore[arg-type]
        now=current,
    )
    snapshot_limit = thresholds.snapshot_freshness_target_seconds
    if caps.snapshots:
        if snapshot_age is None or snapshot_age >= snapshot_limit:
            gaps.append("snapshot_stale")
            enqueue(
                ACTION_SNAPSHOT,
                (
                    "AccessState snapshot is stale or missing: updated "
                    f"{_format_age_for_reason(snapshot_age)}; "
                    f"snapshot_freshness_target={snapshot_limit:g}s "
                    f"(heartbeat={thresholds.heartbeat_interval_seconds:g}s)."
                ),
                policy_key=POLICY_KEY_SNAPSHOT_FRESHNESS,
                policy_value=snapshot_limit,
                updated_at=observation.get("updated_at"),
                age_seconds=snapshot_age,
                heartbeat_interval_seconds=thresholds.heartbeat_interval_seconds,
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
        policy=pol,
        meets_goal=not unique_gaps,
        gaps=unique_gaps,
        work_items=ordered,
        observed=dict(observation),
        thresholds=thresholds,
    )


class ProviderOrchestrator:
    """Evaluate every registered provider against ProviderGoal + ProviderPolicy."""

    def __init__(
        self,
        registry: ProviderRegistry | None = None,
        *,
        goal: ProviderGoal | None = None,
        default_policy: ProviderPolicy | None = None,
    ) -> None:
        self._registry = registry
        self.goal = goal or default_provider_goal()
        self.default_policy = default_policy or default_provider_policy()

    @property
    def registry(self) -> ProviderRegistry:
        return self._registry if self._registry is not None else get_provider_registry()

    def list_providers(self) -> tuple[ManagedProvider, ...]:
        return self.registry.list_providers()

    def policy_for(self, provider_id: str) -> ProviderPolicy:
        managed = self.registry.get(provider_id)
        if managed is not None:
            return managed.policy
        return self.default_policy

    def evaluate_provider(
        self,
        provider_id: str,
        state: AccessState | Mapping[str, Any] | None,
        *,
        now: datetime | None = None,
        goal: ProviderGoal | None = None,
        policy: ProviderPolicy | None = None,
    ) -> ProviderEvaluation:
        managed = self.registry.get(provider_id)
        pid = str(provider_id or "").strip().lower()
        active_goal = goal or self.goal
        if managed is None:
            observation = normalize_access_observation(state, provider=pid)
            evaluation = evaluate_provider_goal(
                observation,
                active_goal,
                policy=policy or self.default_policy,
                capabilities=ProviderPlatformCapabilities(),
                now=now,
            )
            return ProviderEvaluation(
                provider=pid,
                goal=evaluation.goal,
                policy=evaluation.policy,
                meets_goal=False,
                gaps=("provider_not_registered",) + evaluation.gaps,
                work_items=(),
                observed=evaluation.observed,
                thresholds=evaluation.thresholds,
            )
        observation = normalize_access_observation(state, provider=managed.provider_id)
        return evaluate_provider_goal(
            observation,
            active_goal,
            policy=policy or managed.policy,
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
                item.work_item_id,
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
    policy: ProviderPolicy | None = None,
    now: datetime | None = None,
) -> WorkQueue:
    """Convenience: WorkQueue containing only one provider's intended work."""
    orchestrator = ProviderOrchestrator(registry, goal=goal)
    evaluation = orchestrator.evaluate_provider(
        provider_id, state, now=now, policy=policy
    )
    return WorkQueue(items=evaluation.work_items, evaluated_at=iso_now())
