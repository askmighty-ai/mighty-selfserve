"""Natural Session coordinator (Milestone 8).

Observes natural browse / ensure-due triggers, applies pure policy, executes
only through Provider Access Manager. Never raises to Home/Worker callers.

See docs/NATURAL_SESSION.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from mighty.natural_session_policy import (
    NaturalSessionAction,
    NaturalSessionDecision,
    plan_natural_session,
)
from mighty.provider_session_state import get_provider_session_states
from mighty.recovery_store import has_active_recovery_for_provider
from mighty.session_verification import (
    SESSION_VERIFICATION_ENTRY_URLS,
    get_last_confirmed_ready_at,
    session_state_needs_verification,
    verification_entry_url,
)

logger = logging.getLogger(__name__)


@dataclass
class NaturalSessionResult:
    provider: str
    action: str
    reason: str
    enqueued: bool = False
    verification_id: str | None = None
    verification: Any = None


@dataclass
class NaturalSessionSweepResult:
    results: list[NaturalSessionResult] = field(default_factory=list)
    detections: int = 0
    enqueued: int = 0
    skipped_fresh: int = 0
    deferred_recovery: int = 0
    unsupported: int = 0
    errors: int = 0


def observe_natural_session(
    db: Any,
    user_id: str,
    provider: str,
    *,
    trigger_source: str = "provider_page_observed",
    now: datetime | None = None,
    requested_by: str | None = None,
) -> NaturalSessionResult:
    """Handle one natural provider observation. Never raises."""
    try:
        now = _ensure_aware(now or datetime.now(timezone.utc))
        decision = _decide(db, user_id, provider, now=now)
        return _execute(
            db,
            user_id,
            decision,
            trigger_source=trigger_source,
            now=now,
            requested_by=requested_by or f"natural_session:{user_id}",
        )
    except Exception:
        logger.exception(
            "natural_session_observe_failed user=%s provider=%s",
            user_id,
            provider,
        )
        return NaturalSessionResult(
            str(provider or "").strip().lower(),
            "error",
            "exception",
            enqueued=False,
        )


def run_natural_session_ensure_due(
    db: Any,
    user_id: str,
    *,
    trigger_source: str = "scheduled_recheck",
    now: datetime | None = None,
    requested_by: str | None = None,
    providers: list[str] | None = None,
) -> NaturalSessionSweepResult:
    """Ensure-due sweep through Natural Session policy. Never raises."""
    sweep = NaturalSessionSweepResult()
    try:
        now = _ensure_aware(now or datetime.now(timezone.utc))
        from mighty.provider_access_manager import run_verification_maintenance

        run_verification_maintenance(db, user_id, now=now)
        targets = providers or sorted(SESSION_VERIFICATION_ENTRY_URLS.keys())
        for provider in targets:
            sweep.detections += 1
            try:
                decision = _decide(db, user_id, provider, now=now)
                result = _execute(
                    db,
                    user_id,
                    decision,
                    trigger_source=trigger_source,
                    now=now,
                    requested_by=requested_by or f"extension:{user_id}",
                )
                sweep.results.append(result)
                _tally(sweep, result)
            except Exception:
                sweep.errors += 1
                logger.exception(
                    "natural_session_ensure_due_provider_failed user=%s provider=%s",
                    user_id,
                    provider,
                )
    except Exception:
        sweep.errors += 1
        logger.exception(
            "natural_session_ensure_due_failed user=%s", user_id
        )
    return sweep


def _decide(
    db: Any, user_id: str, provider: str, *, now: datetime
) -> NaturalSessionDecision:
    prov = str(provider or "").strip().lower()
    capable = verification_entry_url(prov) is not None
    recovery_active = False
    try:
        recovery_active = has_active_recovery_for_provider(db, user_id, prov)
    except Exception:
        recovery_active = False

    needs = False
    if capable and not recovery_active:
        states = get_provider_session_states(db, user_id, providers=[prov])
        session_state = states.get(prov)
        last_ready = get_last_confirmed_ready_at(db, user_id, prov)
        needs = session_state_needs_verification(
            session_state,
            prov,
            now=now,
            last_ready_at=last_ready,
        )

    return plan_natural_session(
        provider=prov,
        has_verification_capability=capable,
        recovery_active=recovery_active,
        needs_verification=needs,
    )


def _execute(
    db: Any,
    user_id: str,
    decision: NaturalSessionDecision,
    *,
    trigger_source: str,
    now: datetime,
    requested_by: str,
) -> NaturalSessionResult:
    action = decision.action.value
    if decision.action is NaturalSessionAction.ENQUEUE_VERIFY:
        from mighty.provider_access_manager import ensure_provider_access_check_if_stale

        ver = ensure_provider_access_check_if_stale(
            db,
            user_id,
            decision.provider,
            now=now,
            trigger_source=trigger_source,
            requested_by=requested_by,
        )
        enqueued = ver is not None
        logger.info(
            "natural_session.enqueue provider=%s trigger=%s enqueued=%s reason=%s",
            decision.provider,
            trigger_source,
            enqueued,
            decision.reason,
        )
        return NaturalSessionResult(
            decision.provider,
            action,
            decision.reason,
            enqueued=enqueued,
            verification_id=getattr(ver, "verification_id", None) if ver else None,
            verification=ver,
        )

    logger.info(
        "natural_session.%s provider=%s trigger=%s reason=%s",
        action,
        decision.provider,
        trigger_source,
        decision.reason,
    )
    return NaturalSessionResult(
        decision.provider, action, decision.reason, enqueued=False
    )


def _tally(sweep: NaturalSessionSweepResult, result: NaturalSessionResult) -> None:
    if result.action == NaturalSessionAction.ENQUEUE_VERIFY.value:
        if result.enqueued:
            sweep.enqueued += 1
        else:
            # Policy said enqueue but PAM no-op'd (throttle/active) — count as skip.
            sweep.skipped_fresh += 1
    elif result.action == NaturalSessionAction.SKIP_FRESH.value:
        sweep.skipped_fresh += 1
    elif result.action == NaturalSessionAction.DEFER_RECOVERY.value:
        sweep.deferred_recovery += 1
    elif result.action == NaturalSessionAction.UNSUPPORTED.value:
        sweep.unsupported += 1


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
