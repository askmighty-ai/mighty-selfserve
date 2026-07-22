"""Recovery Planner — pure deterministic recovery policy (Milestone 6).

Chooses the next autonomous recovery capability from failure facts and attempt
history. No I/O, ranking of user attention, or provider-identity branching
unless a documented capability flag requires it.

See docs/ATTENTION_AUTONOMOUS_RECOVERY.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence


class RecoveryCapability(str, Enum):
    """Ordered autonomous capabilities; ask_human is always last."""

    SESSION_VERIFY = "session_verify"
    SILENT_REAUTH = "silent_reauth"
    ACCOUNT_RESYNC = "account_resync"
    NAVIGATION_GAP_FILL = "navigation_gap_fill"
    DEEP_PROBE = "deep_probe"
    BOUNDED_WAIT = "bounded_wait"
    ASK_HUMAN = "ask_human"


# Deterministic rank order (index 0 first).
CAPABILITY_ORDER: tuple[RecoveryCapability, ...] = (
    RecoveryCapability.SESSION_VERIFY,
    RecoveryCapability.SILENT_REAUTH,
    RecoveryCapability.ACCOUNT_RESYNC,
    RecoveryCapability.NAVIGATION_GAP_FILL,
    RecoveryCapability.DEEP_PROBE,
    RecoveryCapability.BOUNDED_WAIT,
    RecoveryCapability.ASK_HUMAN,
)

# Interruptions that are never autonomously recoverable.
HUMAN_ONLY_INTERRUPTIONS: frozenset[str] = frozenset(
    {"mfa", "captcha", "consent", "unknown_human"}
)

# Max times bounded_wait may appear before escalation.
MAX_BOUNDED_WAIT_ATTEMPTS = 2

# Default backoff for bounded_wait (seconds).
DEFAULT_BOUNDED_WAIT_SECONDS = 60


class RecoveryDecisionKind(str, Enum):
    ATTEMPT = "attempt"
    ESCALATE = "escalate"
    SUCCEED = "succeed"


class AttemptOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class RecoveryFacts:
    """Observable failure facts for one (user, provider) recovery case.

    Provider integrations report facts and capability flags; they do not own
    cross-platform policy.
    """

    user_id: str
    provider: str
    root_cause: str
    interruption: str | None = None
    failure_cleared: bool = False
    supports_silent_reauth: bool = False
    supports_navigation_gap_fill: bool = False
    supports_account_resync: bool = True
    supports_deep_probe: bool = True
    supports_session_verify: bool = True


@dataclass(frozen=True)
class RecoveryAttemptRecord:
    """One historical attempt used by the pure planner."""

    capability: RecoveryCapability
    outcome: AttemptOutcome


@dataclass(frozen=True)
class RecoveryHistory:
    attempts: tuple[RecoveryAttemptRecord, ...] = ()


@dataclass(frozen=True)
class RecoveryDecision:
    kind: RecoveryDecisionKind
    capability: RecoveryCapability | None = None
    reason: str | None = None
    wait_seconds: int | None = None


def root_cause_for_interruption(interruption: str | None, *, fallback: str = "access_failure") -> str:
    """Stable root-cause key fragment from an interruption code."""
    code = str(interruption or "").strip().lower()
    if code in HUMAN_ONLY_INTERRUPTIONS or code == "login":
        return code
    if code in {"stale", "login_unknown", "awaiting_user", "runtime_offline", "signed_out"}:
        return code
    return fallback


def plan_recovery(facts: RecoveryFacts, history: RecoveryHistory) -> RecoveryDecision:
    """Return the next recovery decision for identical facts+history.

    Pure and deterministic. Does not rank AttentionItems.
    """
    if facts.failure_cleared:
        return RecoveryDecision(kind=RecoveryDecisionKind.SUCCEED, reason="failure_cleared")

    interruption = str(facts.interruption or "").strip().lower() or None
    if interruption in HUMAN_ONLY_INTERRUPTIONS:
        return RecoveryDecision(
            kind=RecoveryDecisionKind.ESCALATE,
            capability=RecoveryCapability.ASK_HUMAN,
            reason=f"human_only:{interruption}",
        )

    tried = _attempted_capabilities(history)
    wait_count = sum(
        1
        for a in history.attempts
        if a.capability is RecoveryCapability.BOUNDED_WAIT
        and a.outcome is not AttemptOutcome.SKIPPED
    )

    for capability in CAPABILITY_ORDER:
        if capability is RecoveryCapability.ASK_HUMAN:
            continue
        if capability in tried and capability is not RecoveryCapability.BOUNDED_WAIT:
            continue
        if capability is RecoveryCapability.BOUNDED_WAIT and wait_count >= MAX_BOUNDED_WAIT_ATTEMPTS:
            continue
        if not _capability_available(capability, facts):
            # Caller should record skipped; planner advances conceptually by
            # treating unavailable as already tried when present in history.
            if capability not in tried:
                return RecoveryDecision(
                    kind=RecoveryDecisionKind.ATTEMPT,
                    capability=capability,
                    reason="probe_availability",
                )
            continue
        if capability is RecoveryCapability.BOUNDED_WAIT:
            return RecoveryDecision(
                kind=RecoveryDecisionKind.ATTEMPT,
                capability=capability,
                reason="backoff",
                wait_seconds=DEFAULT_BOUNDED_WAIT_SECONDS,
            )
        return RecoveryDecision(
            kind=RecoveryDecisionKind.ATTEMPT,
            capability=capability,
            reason="next_capability",
        )

    return RecoveryDecision(
        kind=RecoveryDecisionKind.ESCALATE,
        capability=RecoveryCapability.ASK_HUMAN,
        reason="exhausted",
    )


def _attempted_capabilities(history: RecoveryHistory) -> set[RecoveryCapability]:
    """Capabilities that have been tried (including skipped probes)."""
    return {a.capability for a in history.attempts}


def _capability_available(capability: RecoveryCapability, facts: RecoveryFacts) -> bool:
    if capability is RecoveryCapability.SESSION_VERIFY:
        return bool(facts.supports_session_verify)
    if capability is RecoveryCapability.SILENT_REAUTH:
        return bool(facts.supports_silent_reauth)
    if capability is RecoveryCapability.ACCOUNT_RESYNC:
        return bool(facts.supports_account_resync)
    if capability is RecoveryCapability.NAVIGATION_GAP_FILL:
        return bool(facts.supports_navigation_gap_fill)
    if capability is RecoveryCapability.DEEP_PROBE:
        return bool(facts.supports_deep_probe)
    if capability is RecoveryCapability.BOUNDED_WAIT:
        return True
    if capability is RecoveryCapability.ASK_HUMAN:
        return True
    return False


def capabilities_to_skip(facts: RecoveryFacts) -> tuple[RecoveryCapability, ...]:
    """Capabilities known unavailable from facts (for executor pre-skip)."""
    skipped: list[RecoveryCapability] = []
    for capability in CAPABILITY_ORDER:
        if capability in {
            RecoveryCapability.BOUNDED_WAIT,
            RecoveryCapability.ASK_HUMAN,
        }:
            continue
        if not _capability_available(capability, facts):
            skipped.append(capability)
    return tuple(skipped)
