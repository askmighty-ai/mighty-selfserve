"""Shared helpers for Milestone 6 recovery / Attention-gate tests."""

from __future__ import annotations

from datetime import datetime, timezone

from mighty.recovery_planner import AttemptOutcome, RecoveryCapability
from mighty.recovery_store import (
    append_attempt,
    claim_or_get_active_case,
    ensure_recovery_tables,
    transition_case,
)


def escalate_recovery(
    db,
    user_id: str,
    provider: str = "amex",
    *,
    root_cause: str = "login",
    reason: str = "exhausted",
    now: datetime | None = None,
) -> str:
    """Mark a provider as Recovery-escalated so Attention may interrupt."""
    now = now or datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
    ensure_recovery_tables(db, commit=False)
    case = claim_or_get_active_case(
        db,
        user_id=user_id,
        provider=provider,
        root_cause=root_cause,
        now=now,
    )
    append_attempt(
        db,
        case.case_id,
        capability=RecoveryCapability.ASK_HUMAN,
        outcome=AttemptOutcome.SUCCEEDED,
        now=now,
        detail={"reason": reason},
    )
    transition_case(
        db,
        case.case_id,
        status="escalated",
        now=now,
        escalation_reason=reason,
        clear_next_attempt=True,
    )
    return case.case_id
