"""Pure authorization policy for Trusted Agent Actions (Milestone 11).

Provider-independent. Decides whether an Action needs human authorization,
can auto-authorize, or must be denied — without ranking Attention.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from mighty.agent_capability_registry import action_type_is_executable

# Consequence levels (provider-independent).
LEVEL_CRITICAL = "critical"
LEVEL_CONSEQUENTIAL = "consequential"
LEVEL_ROUTINE = "routine"
LEVEL_INFORMATIONAL = "informational"

# Policy outcomes.
AUTH_REQUIRE_HUMAN = "require_human"
AUTH_AUTO_AUTHORIZE = "auto_authorize"
AUTH_DENY = "deny"

HUMAN_REQUIRED_LEVELS = frozenset(
    {LEVEL_CRITICAL, LEVEL_CONSEQUENTIAL, LEVEL_ROUTINE}
)


@dataclass(frozen=True)
class AuthorizationDecision:
    outcome: str
    reason: str
    consequence_level: str
    requires_human: bool
    executable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "consequence_level": self.consequence_level,
            "requires_human": self.requires_human,
            "executable": self.executable,
        }


def normalize_consequence_level(level: str | None) -> str:
    text = str(level or LEVEL_ROUTINE).strip().lower()
    if text in {
        LEVEL_CRITICAL,
        LEVEL_CONSEQUENTIAL,
        LEVEL_ROUTINE,
        LEVEL_INFORMATIONAL,
    }:
        return text
    return LEVEL_ROUTINE


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


def evaluate_authorization_policy(
    *,
    action_type: str,
    consequence_level: str | None = None,
    provider: str | None = None,
    expires_at: str | None = None,
    now: datetime | None = None,
    duplicate_open: bool = False,
    record_only: bool = False,
) -> AuthorizationDecision:
    """Evaluate whether an Action may proceed, needs human, or is denied."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    level = normalize_consequence_level(consequence_level)
    executable = action_type_is_executable(action_type, provider=provider)

    exp = _parse_iso(expires_at)
    if exp is not None and now >= exp:
        return AuthorizationDecision(
            outcome=AUTH_DENY,
            reason="expired",
            consequence_level=level,
            requires_human=False,
            executable=executable,
        )

    if duplicate_open:
        return AuthorizationDecision(
            outcome=AUTH_DENY,
            reason="duplicate_open_action",
            consequence_level=level,
            requires_human=False,
            executable=executable,
        )

    if record_only or level == LEVEL_INFORMATIONAL:
        return AuthorizationDecision(
            outcome=AUTH_AUTO_AUTHORIZE,
            reason="informational_or_record_only",
            consequence_level=level,
            requires_human=False,
            executable=executable,
        )

    if level in HUMAN_REQUIRED_LEVELS:
        return AuthorizationDecision(
            outcome=AUTH_REQUIRE_HUMAN,
            reason="consequence_requires_human",
            consequence_level=level,
            requires_human=True,
            executable=executable,
        )

    return AuthorizationDecision(
        outcome=AUTH_REQUIRE_HUMAN,
        reason="default_require_human",
        consequence_level=level,
        requires_human=True,
        executable=executable,
    )
