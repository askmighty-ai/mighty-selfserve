"""Natural Session pure policy (Milestone 8).

Decides skip / enqueue / defer without I/O. Does not rank Attention or
mutate AuthTruth. Provider capability is a boolean input from the registry
(entry URL present) — not a provider-id branch inside shared policy.

See docs/NATURAL_SESSION.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class NaturalSessionAction(str, Enum):
    SKIP_FRESH = "skip_fresh"
    ENQUEUE_VERIFY = "enqueue_verify"
    DEFER_RECOVERY = "defer_recovery"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class NaturalSessionDecision:
    action: NaturalSessionAction
    reason: str
    provider: str


def plan_natural_session(
    *,
    provider: str,
    has_verification_capability: bool,
    recovery_active: bool,
    needs_verification: bool,
) -> NaturalSessionDecision:
    """Deterministic Natural Session decision for one provider observation."""
    prov = str(provider or "").strip().lower()
    if not prov:
        return NaturalSessionDecision(
            NaturalSessionAction.UNSUPPORTED, "missing_provider", ""
        )
    if not has_verification_capability:
        return NaturalSessionDecision(
            NaturalSessionAction.UNSUPPORTED, "no_verification_capability", prov
        )
    if recovery_active:
        return NaturalSessionDecision(
            NaturalSessionAction.DEFER_RECOVERY, "recovery_active", prov
        )
    if not needs_verification:
        return NaturalSessionDecision(
            NaturalSessionAction.SKIP_FRESH, "evidence_fresh", prov
        )
    return NaturalSessionDecision(
        NaturalSessionAction.ENQUEUE_VERIFY, "stale_or_due", prov
    )
