"""ProviderPolicy — configurable platform thresholds for provider orchestration.

Separates tunable intervals/budgets from ProviderGoal (desired state) and from
ProviderOrchestrator planning logic. Policies are associated with ManagedProvider
entries in the Provider Registry.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from mighty.access_state_publication import DEFAULT_HEARTBEAT_SECONDS
from mighty.provider_runtime_control_center import (
    DEFAULT_KEEPALIVE_INTERVAL_SECONDS,
    DEFAULT_MAX_SAFE_AUTH_RECOVERY_ATTEMPTS,
    DEFAULT_SUPERVISOR_INTERVAL_SECONDS,
)

# Snapshot freshness target matches Railway stale presentation (3× heartbeat).
DEFAULT_SNAPSHOT_FRESHNESS_TARGET_SECONDS = DEFAULT_HEARTBEAT_SECONDS * 3.0
DEFAULT_VERIFICATION_INTERVAL_SECONDS = DEFAULT_SUPERVISOR_INTERVAL_SECONDS * 2.0


@dataclass(frozen=True)
class ProviderPolicy:
    """Configurable thresholds that drive observational WorkItem planning.

    Amex defaults mirror current AccessSupervisor / publication behavior so
    attaching a policy does not change existing Amex cadence.
    """

    policy_id: str = "default"
    verification_interval_seconds: float = DEFAULT_VERIFICATION_INTERVAL_SECONDS
    keepalive_interval_seconds: float = DEFAULT_KEEPALIVE_INTERVAL_SECONDS
    snapshot_freshness_target_seconds: float = DEFAULT_SNAPSHOT_FRESHNESS_TARGET_SECONDS
    recovery_budget: int = DEFAULT_MAX_SAFE_AUTH_RECOVERY_ATTEMPTS
    heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_SECONDS

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", str(self.policy_id or "default").strip() or "default")
        object.__setattr__(
            self,
            "verification_interval_seconds",
            max(0.0, float(self.verification_interval_seconds)),
        )
        object.__setattr__(
            self,
            "keepalive_interval_seconds",
            max(0.0, float(self.keepalive_interval_seconds)),
        )
        object.__setattr__(
            self,
            "snapshot_freshness_target_seconds",
            max(0.0, float(self.snapshot_freshness_target_seconds)),
        )
        object.__setattr__(self, "recovery_budget", max(0, int(self.recovery_budget)))
        object.__setattr__(
            self,
            "heartbeat_interval_seconds",
            max(0.0, float(self.heartbeat_interval_seconds)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "verification_interval_seconds": self.verification_interval_seconds,
            "keepalive_interval_seconds": self.keepalive_interval_seconds,
            "snapshot_freshness_target_seconds": self.snapshot_freshness_target_seconds,
            "recovery_budget": self.recovery_budget,
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
        }

    def with_overrides(self, **overrides: Any) -> ProviderPolicy:
        """Return a copy with selected fields replaced (tests / per-provider tuning)."""
        return replace(self, **overrides)

    def format_trigger(self, policy_key: str) -> str:
        """Human-readable policy trigger label for WorkItem explainability."""
        key = str(policy_key or "").strip()
        if not key:
            return ""
        value = getattr(self, key, None)
        if value is None:
            return f"{key} ({self.policy_id})"
        if key.endswith("_seconds"):
            short = key.removesuffix("_seconds")
            return f"{short}={_format_seconds(float(value))} ({self.policy_id})"
        return f"{key}={value} ({self.policy_id})"


def _format_seconds(seconds: float) -> str:
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m" if secs == 0 else f"{minutes}m{secs}s"
    hours, rem_m = divmod(minutes, 60)
    return f"{hours}h" if rem_m == 0 else f"{hours}h{rem_m}m"


def default_provider_policy() -> ProviderPolicy:
    """Platform-wide default thresholds (matches historical Amex cadence)."""
    return ProviderPolicy(policy_id="default")


def amex_provider_policy() -> ProviderPolicy:
    """Amex policy — identical numeric defaults, distinct policy_id for explainability."""
    return ProviderPolicy(policy_id="amex")


# Stable policy keys referenced by WorkItem.policy_key.
POLICY_KEY_VERIFICATION_INTERVAL = "verification_interval_seconds"
POLICY_KEY_KEEPALIVE_INTERVAL = "keepalive_interval_seconds"
POLICY_KEY_SNAPSHOT_FRESHNESS = "snapshot_freshness_target_seconds"
POLICY_KEY_RECOVERY_BUDGET = "recovery_budget"
POLICY_KEY_HEARTBEAT_INTERVAL = "heartbeat_interval_seconds"
POLICY_KEY_GOAL_AUTHENTICATED = "goal.authenticated"
POLICY_KEY_GOAL_HEALTHY = "goal.healthy"
POLICY_KEY_GOAL_CONNECTOR_READY = "goal.connector_ready"
POLICY_KEY_GOAL_NO_PENDING_USER_ACTION = "goal.no_pending_user_action"
POLICY_KEY_ACCESS_STATE_MISSING = "access_state_missing"
