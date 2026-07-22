"""Recovery Supervisor — observe failures, drive planner, execute (Milestone 6).

Heartbeat-only. Failures never raise to Home/Worker/sync callers.

See docs/ATTENTION_AUTONOMOUS_RECOVERY.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from mighty.admin_local_time import parse_admin_timestamp
from mighty.attention_loaders import (
    load_account_states_for_attention,
    load_auth_truths,
    load_trust_signals,
)
from mighty.auth_truth import AuthTruth
from mighty.recovery_executor import execute_recovery_capability
from mighty.recovery_planner import (
    AttemptOutcome,
    RecoveryCapability,
    RecoveryDecisionKind,
    RecoveryFacts,
    capabilities_to_skip,
    plan_recovery,
    root_cause_for_interruption,
)
from mighty.recovery_store import (
    append_attempt,
    claim_or_get_active_case,
    ensure_recovery_tables,
    get_active_case,
    list_active_cases_for_user,
    list_recovery_user_ids,
    load_history,
    transition_case,
)

logger = logging.getLogger(__name__)

# Cap how long a running capability may stay without progress.
RUNNING_TIMEOUT_SECONDS = 15 * 60


@dataclass(frozen=True)
class RecoverySupervisorResult:
    users_scanned: int
    cases_touched: int
    attempts: int
    escalated: int
    succeeded: int
    errors: int


def run_recovery_supervisor(
    db: Any,
    *,
    now: datetime,
    user_ids: list[str] | None = None,
) -> RecoverySupervisorResult:
    """Drive recovery for known users. Never raises."""
    try:
        now = _ensure_aware(now)
        ensure_recovery_tables(db, commit=False)
        if user_ids is None:
            user_ids = _discover_user_ids(db)
    except Exception:
        logger.exception("recovery_supervisor_init_failed")
        return RecoverySupervisorResult(0, 0, 0, 0, 0, 1)

    users_scanned = 0
    cases_touched = 0
    attempts = 0
    escalated = 0
    succeeded = 0
    errors = 0

    for raw_uid in user_ids:
        uid = str(raw_uid or "").strip()
        if not uid:
            continue
        users_scanned += 1
        try:
            touched, att, esc, suc = _supervise_user(db, uid, now=now)
            cases_touched += touched
            attempts += att
            escalated += esc
            succeeded += suc
        except Exception:
            errors += 1
            logger.exception("recovery_supervisor_user_failed user_id=%s", uid)

    return RecoverySupervisorResult(
        users_scanned=users_scanned,
        cases_touched=cases_touched,
        attempts=attempts,
        escalated=escalated,
        succeeded=succeeded,
        errors=errors,
    )


def _discover_user_ids(db: Any) -> list[str]:
    ids: set[str] = set(list_recovery_user_ids(db))
    for table in ("account_state", "auth_truth", "provider_session_state"):
        try:
            rows = db.execute(
                f"SELECT DISTINCT user_id FROM {table} ORDER BY user_id"
            ).fetchall()
        except Exception:
            continue
        for row in rows:
            try:
                uid = str(row["user_id"] if hasattr(row, "keys") else row[0]).strip()
            except Exception:
                uid = ""
            if uid:
                ids.add(uid)
    return sorted(ids)


def _supervise_user(
    db: Any, user_id: str, *, now: datetime
) -> tuple[int, int, int, int]:
    generated_at = now.replace(microsecond=0).isoformat()
    accounts = load_account_states_for_attention(db, user_id)
    truths = load_auth_truths(
        db,
        user_id,
        now=now,
        projected_at=generated_at,
        accounts=accounts,
    )
    trust_signals = load_trust_signals(db, user_id, now=now, accounts=accounts)

    failure_facts = _collect_failure_facts(truths, trust_signals)
    touched = 0
    attempts = 0
    escalated = 0
    succeeded = 0

    # Advance existing active cases first.
    for case in list_active_cases_for_user(db, user_id):
        key = (case.provider, case.root_cause)
        facts = failure_facts.get(key)
        if facts is None:
            # Failure cleared while recovering.
            transition_case(
                db, case.case_id, status="succeeded", now=now, clear_next_attempt=True
            )
            touched += 1
            succeeded += 1
            logger.info(
                "recovery.succeeded case_id=%s provider=%s reason=failure_cleared",
                case.case_id,
                case.provider,
            )
            continue
        t, a, e, s = _advance_case(db, case.case_id, facts=facts, now=now)
        touched += t
        attempts += a
        escalated += e
        succeeded += s

    # Open cases for new failures (not already active; not already escalated).
    for key, facts in failure_facts.items():
        provider, root_cause = key
        if get_active_case(
            db, user_id=user_id, provider=provider, root_cause=root_cause
        ):
            continue
        if _latest_terminal_blocks_reopen(db, user_id, provider, root_cause):
            continue
        case = claim_or_get_active_case(
            db,
            user_id=user_id,
            provider=provider,
            root_cause=root_cause,
            now=now,
        )
        t, a, e, s = _advance_case(db, case.case_id, facts=facts, now=now)
        touched += t
        attempts += a
        escalated += e
        succeeded += s

    return touched, attempts, escalated, succeeded


def _latest_terminal_blocks_reopen(
    db: Any, user_id: str, provider: str, root_cause: str
) -> bool:
    """While escalated for the same root_cause, do not start a parallel episode."""
    row = db.execute(
        """
        SELECT status, root_cause FROM recovery_case
        WHERE user_id = ? AND provider = ?
          AND status IN ('escalated', 'succeeded', 'cancelled')
        ORDER BY updated_at DESC, case_id DESC
        LIMIT 1
        """,
        (user_id, provider),
    ).fetchone()
    if not row:
        return False
    try:
        status = str(row["status"])
        cause = str(row["root_cause"])
    except Exception:
        status = str(row[0])
        cause = str(row[1])
    return status == "escalated" and cause == root_cause


def _collect_failure_facts(
    truths: list[AuthTruth],
    trust_signals: list[Any],
) -> dict[tuple[str, str], RecoveryFacts]:
    facts: dict[tuple[str, str], RecoveryFacts] = {}

    for truth in truths:
        provider = str(truth.provider or "").strip().lower()
        if not provider:
            continue
        interruption = None
        if truth.needs_human:
            interruption = (
                str(truth.needs_human_reason or truth.interruption.value or "login")
                .strip()
                .lower()
            )
        elif truth.stale:
            interruption = "stale"
        elif str(getattr(truth.state, "value", truth.state)).lower() == "login_unknown":
            interruption = "login_unknown"
        else:
            continue
        root = root_cause_for_interruption(interruption)
        key = (provider, root)
        facts[key] = RecoveryFacts(
            user_id=str(truth.user_id).strip(),
            provider=provider,
            root_cause=root,
            interruption=interruption,
            failure_cleared=False,
            supports_silent_reauth=False,
            supports_navigation_gap_fill=False,
            supports_account_resync=True,
            supports_deep_probe=True,
            supports_session_verify=str(truth.access_method).lower()
            != "managed_runtime",
        )

    for signal in trust_signals:
        if getattr(signal, "needs_human", False):
            continue
        status = str(getattr(signal, "presentation_status", "") or "").strip().lower()
        if status not in {"awaiting_user", "runtime_offline", "never_reported", "stale"}:
            continue
        provider = str(getattr(signal, "provider", "") or "").strip().lower()
        if not provider:
            continue
        root = root_cause_for_interruption(status)
        key = (provider, root)
        if key in facts:
            continue
        facts[key] = RecoveryFacts(
            user_id=str(getattr(signal, "user_id", "") or "").strip(),
            provider=provider,
            root_cause=root,
            interruption=status,
            failure_cleared=False,
            supports_silent_reauth=False,
            supports_navigation_gap_fill=False,
            supports_account_resync=False,
            supports_deep_probe=False,
            supports_session_verify=False,
        )

    return facts


def _advance_case(
    db: Any,
    case_id: str,
    *,
    facts: RecoveryFacts,
    now: datetime,
) -> tuple[int, int, int, int]:
    from mighty.recovery_store import get_case

    case = get_case(db, case_id)
    if case is None:
        return 0, 0, 0, 0

    if case.status == "waiting" and case.next_attempt_at:
        due = parse_admin_timestamp(case.next_attempt_at)
        if due is not None and _ensure_aware(due) > now:
            return 0, 0, 0, 0

    if case.status == "running":
        updated = parse_admin_timestamp(case.updated_at)
        if updated is not None:
            age = (now - _ensure_aware(updated)).total_seconds()
            if age < RUNNING_TIMEOUT_SECONDS:
                return 0, 0, 0, 0
        # Timeout the stuck running state as a failed attempt marker.
        append_attempt(
            db,
            case_id,
            capability=RecoveryCapability.SESSION_VERIFY,
            outcome=AttemptOutcome.TIMEOUT,
            now=now,
            detail={"reason": "running_timeout"},
        )
        transition_case(
            db, case_id, status="open", now=now, clear_next_attempt=True
        )

    if facts.failure_cleared:
        transition_case(
            db, case_id, status="succeeded", now=now, clear_next_attempt=True
        )
        logger.info("recovery.succeeded case_id=%s", case_id)
        return 1, 0, 0, 1

    history = load_history(db, case_id)

    # Record unavailable capabilities as skipped once.
    for capability in capabilities_to_skip(facts):
        if any(a.capability is capability for a in history.attempts):
            continue
        append_attempt(
            db,
            case_id,
            capability=capability,
            outcome=AttemptOutcome.SKIPPED,
            now=now,
            detail={"reason": "unavailable"},
        )
    history = load_history(db, case_id)
    decision = plan_recovery(facts, history)

    if decision.kind is RecoveryDecisionKind.SUCCEED:
        transition_case(
            db, case_id, status="succeeded", now=now, clear_next_attempt=True
        )
        logger.info("recovery.succeeded case_id=%s reason=%s", case_id, decision.reason)
        return 1, 0, 0, 1

    if decision.kind is RecoveryDecisionKind.ESCALATE:
        append_attempt(
            db,
            case_id,
            capability=RecoveryCapability.ASK_HUMAN,
            outcome=AttemptOutcome.SUCCEEDED,
            now=now,
            detail={"reason": decision.reason},
        )
        transition_case(
            db,
            case_id,
            status="escalated",
            now=now,
            escalation_reason=decision.reason,
            clear_next_attempt=True,
        )
        logger.info(
            "recovery.escalated case_id=%s provider=%s reason=%s",
            case_id,
            facts.provider,
            decision.reason,
        )
        return 1, 1, 1, 0

    # ATTEMPT
    assert decision.capability is not None
    transition_case(db, case_id, status="running", now=now, clear_next_attempt=True)
    result = execute_recovery_capability(
        db, facts=facts, decision=decision, now=now
    )
    append_attempt(
        db,
        case_id,
        capability=decision.capability,
        outcome=result.outcome,
        now=now,
        detail=result.detail,
    )

    if decision.capability is RecoveryCapability.BOUNDED_WAIT:
        wait_s = decision.wait_seconds or 60
        next_at = _iso(now + timedelta(seconds=wait_s))
        transition_case(
            db,
            case_id,
            status="waiting",
            now=now,
            next_attempt_at=next_at,
        )
        return 1, 1, 0, 0

    # Re-check whether failure cleared after verify/resync (best-effort).
    # Full clear is detected on subsequent supervisor ticks via AuthTruth.
    transition_case(db, case_id, status="open", now=now, clear_next_attempt=True)
    return 1, 1, 0, 0


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()
