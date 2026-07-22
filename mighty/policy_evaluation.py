"""Pure Policy evaluation + explainability (Milestone 12).

Authorization consumes these decisions. Shared evaluation never branches on
provider id — provider_overrides are config lookups only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from mighty.agent_capability_registry import action_type_is_executable
from mighty.authorization_policy import (
    AUTH_AUTO_AUTHORIZE,
    AUTH_DENY,
    AUTH_REQUIRE_HUMAN,
    AuthorizationDecision,
    LEVEL_INFORMATIONAL,
    LEVEL_ROUTINE,
    normalize_consequence_level,
)
from mighty.user_policy import UserPolicy, default_user_policy, level_rank


@dataclass(frozen=True)
class ExplainedAuthorizationDecision:
    decision: AuthorizationDecision
    explanation: str
    policy_refs: tuple[str, ...] = ()
    overridden: bool = False
    conflict_resolution: str = ""
    suppressed_execution: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.to_dict(),
            "explanation": self.explanation,
            "policy_refs": list(self.policy_refs),
            "overridden": self.overridden,
            "conflict_resolution": self.conflict_resolution,
            "suppressed_execution": self.suppressed_execution,
        }


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _provider_override(policy: UserPolicy, provider: str | None) -> dict[str, Any]:
    key = str(provider or "").strip().lower()
    if not key:
        return {}
    raw = policy.provider_overrides.get(key) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _decide(
    outcome: str,
    reason: str,
    level: str,
    executable: bool,
    *,
    explanation: str,
    policy_refs: list[str],
    overridden: bool = False,
    conflict_resolution: str = "",
    suppressed_execution: bool = False,
) -> ExplainedAuthorizationDecision:
    refs = tuple(policy_refs)
    return ExplainedAuthorizationDecision(
        decision=AuthorizationDecision(
            outcome=outcome,
            reason=reason,
            consequence_level=level,
            requires_human=(outcome == AUTH_REQUIRE_HUMAN),
            executable=executable,
            explanation=explanation,
            policy_refs=refs,
        ),
        explanation=explanation,
        policy_refs=refs,
        overridden=overridden,
        conflict_resolution=conflict_resolution,
        suppressed_execution=suppressed_execution,
    )


def evaluate_authorization_with_policy(
    *,
    action_type: str,
    consequence_level: str | None = None,
    provider: str | None = None,
    expires_at: str | None = None,
    now: datetime | None = None,
    duplicate_open: bool = False,
    record_only: bool = False,
    policy: UserPolicy | None = None,
) -> ExplainedAuthorizationDecision:
    """Evaluate Authorization from Policy + Action facts with an explanation."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    pol = policy or default_user_policy("")
    level = normalize_consequence_level(consequence_level)
    executable = action_type_is_executable(action_type, provider=provider)
    refs: list[str] = ["policy.version"]
    override = _provider_override(pol, provider)

    # Fact gates first
    exp = _parse_iso(expires_at)
    if exp is not None and now >= exp:
        return _decide(
            AUTH_DENY,
            "expired",
            level,
            executable,
            explanation="Denied because the authorization request expired.",
            policy_refs=refs,
        )

    if duplicate_open:
        return _decide(
            AUTH_DENY,
            "duplicate_open_action",
            level,
            executable,
            explanation="Denied because an identical open Action already exists (duplicate suppression).",
            policy_refs=refs,
            conflict_resolution="deny_over_auto",
        )

    # Provider override: hard deny / suppress execution
    if override.get("deny_execution") or override.get("deny"):
        return _decide(
            AUTH_DENY,
            "provider_override_deny",
            level,
            executable,
            explanation=(
                f"Denied by provider override for '{provider}' "
                f"(deny_execution)."
            ),
            policy_refs=refs + [f"provider_overrides.{provider}.deny_execution"],
            overridden=True,
            conflict_resolution="deny_over_auto",
            suppressed_execution=True,
        )

    # Force human via override (more restrictive)
    force_human = bool(override.get("require_human"))
    if force_human:
        return _decide(
            AUTH_REQUIRE_HUMAN,
            "provider_override_require_human",
            level,
            executable,
            explanation=(
                f"Human approval required by provider override for '{provider}'."
            ),
            policy_refs=refs + [f"provider_overrides.{provider}.require_human"],
            overridden=True,
            conflict_resolution="require_human_over_auto",
        )

    # Record-only / informational shortcuts honor auto_execute_informational
    if record_only or level == LEVEL_INFORMATIONAL:
        if pol.auto_execute_informational:
            return _decide(
                AUTH_AUTO_AUTHORIZE,
                "policy_auto_informational",
                level,
                executable,
                explanation=(
                    "Auto-authorized: consequence is informational/record-only and "
                    "Policy.auto_execute_informational=true."
                ),
                policy_refs=refs + ["auto_execute_informational"],
            )
        return _decide(
            AUTH_REQUIRE_HUMAN,
            "policy_require_human_informational",
            level,
            executable,
            explanation=(
                "Human approval required: informational Action but "
                "Policy.auto_execute_informational=false."
            ),
            policy_refs=refs + ["auto_execute_informational"],
        )

    threshold = normalize_consequence_level(pol.require_human_at_or_above)
    refs.append("require_human_at_or_above")

    if level_rank(level) >= level_rank(threshold):
        # At or above approval threshold → human, unless routine auto-exec allowed
        if level == LEVEL_ROUTINE and pol.auto_execute_routine:
            # Conflict: threshold says human for routine, but auto_execute_routine
            # Resolution: require_human wins (safer) unless threshold is above routine
            if level_rank(threshold) > level_rank(LEVEL_ROUTINE):
                return _decide(
                    AUTH_AUTO_AUTHORIZE,
                    "policy_auto_routine_below_threshold",
                    level,
                    executable,
                    explanation=(
                        "Auto-authorized: routine is below "
                        f"require_human_at_or_above={threshold} and "
                        "auto_execute_routine=true."
                    ),
                    policy_refs=refs + ["auto_execute_routine"],
                    conflict_resolution="threshold_allows_auto",
                )
            return _decide(
                AUTH_REQUIRE_HUMAN,
                "policy_require_human_threshold",
                level,
                executable,
                explanation=(
                    f"Human approval required: consequence={level} is at/above "
                    f"require_human_at_or_above={threshold} "
                    "(require_human wins over auto_execute_routine)."
                ),
                policy_refs=refs + ["auto_execute_routine"],
                conflict_resolution="require_human_over_auto",
            )
        return _decide(
            AUTH_REQUIRE_HUMAN,
            "policy_require_human_threshold",
            level,
            executable,
            explanation=(
                f"Human approval required: consequence={level} is at/above "
                f"Policy.require_human_at_or_above={threshold}."
            ),
            policy_refs=refs,
        )

    # Below threshold
    if level == LEVEL_ROUTINE and pol.auto_execute_routine:
        return _decide(
            AUTH_AUTO_AUTHORIZE,
            "policy_auto_routine",
            level,
            executable,
            explanation=(
                "Auto-authorized: consequence=routine is below approval threshold "
                "and Policy.auto_execute_routine=true."
            ),
            policy_refs=refs + ["auto_execute_routine"],
        )

    return _decide(
        AUTH_REQUIRE_HUMAN,
        "policy_default_require_human",
        level,
        executable,
        explanation=(
            f"Human approval required by default for consequence={level} "
            f"(threshold={threshold}, auto_execute_routine="
            f"{pol.auto_execute_routine})."
        ),
        policy_refs=refs,
    )


def explain_coverage(explained: ExplainedAuthorizationDecision) -> bool:
    """True when a non-empty explanation and at least one policy ref exist."""
    return bool(explained.explanation.strip()) and bool(explained.policy_refs)
