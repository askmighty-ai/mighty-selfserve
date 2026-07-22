"""Recovery Executor — maps capabilities to Access Manager actions (M6).

No recovery policy. Failures return outcomes; never raise into Home/Worker.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from mighty.recovery_planner import (
    AttemptOutcome,
    RecoveryCapability,
    RecoveryDecision,
    RecoveryFacts,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionResult:
    outcome: AttemptOutcome
    detail: dict[str, Any]


def execute_recovery_capability(
    db: Any,
    *,
    facts: RecoveryFacts,
    decision: RecoveryDecision,
    now: datetime,
) -> ExecutionResult:
    """Execute one planned capability. Never raises."""
    try:
        capability = decision.capability
        if capability is None:
            return ExecutionResult(AttemptOutcome.FAILED, {"error": "no_capability"})
        if capability is RecoveryCapability.ASK_HUMAN:
            return ExecutionResult(
                AttemptOutcome.SUCCEEDED,
                {"escalation": decision.reason or "ask_human"},
            )
        if capability is RecoveryCapability.BOUNDED_WAIT:
            return ExecutionResult(
                AttemptOutcome.SUCCEEDED,
                {"wait_seconds": decision.wait_seconds or 60},
            )
        if capability is RecoveryCapability.SILENT_REAUTH:
            if not facts.supports_silent_reauth:
                return ExecutionResult(AttemptOutcome.SKIPPED, {"reason": "unavailable"})
            return ExecutionResult(AttemptOutcome.SKIPPED, {"reason": "not_implemented"})
        if capability is RecoveryCapability.NAVIGATION_GAP_FILL:
            if not facts.supports_navigation_gap_fill:
                return ExecutionResult(AttemptOutcome.SKIPPED, {"reason": "unavailable"})
            return ExecutionResult(AttemptOutcome.SKIPPED, {"reason": "not_implemented"})
        if capability is RecoveryCapability.SESSION_VERIFY:
            return _run_session_verify(db, facts=facts, deep=False)
        if capability is RecoveryCapability.DEEP_PROBE:
            if not facts.supports_deep_probe:
                return ExecutionResult(AttemptOutcome.SKIPPED, {"reason": "unavailable"})
            return _run_session_verify(db, facts=facts, deep=True)
        if capability is RecoveryCapability.ACCOUNT_RESYNC:
            if not facts.supports_account_resync:
                return ExecutionResult(AttemptOutcome.SKIPPED, {"reason": "unavailable"})
            return _run_account_resync(db, facts=facts)
        return ExecutionResult(
            AttemptOutcome.SKIPPED, {"reason": f"unknown:{capability}"}
        )
    except Exception as exc:
        logger.exception(
            "recovery_execute_failed provider=%s capability=%s",
            facts.provider,
            getattr(decision.capability, "value", decision.capability),
        )
        return ExecutionResult(
            AttemptOutcome.FAILED, {"error": type(exc).__name__}
        )


def _run_session_verify(
    db: Any, *, facts: RecoveryFacts, deep: bool
) -> ExecutionResult:
    if not facts.supports_session_verify:
        return ExecutionResult(AttemptOutcome.SKIPPED, {"reason": "unavailable"})
    try:
        from mighty.provider_access_manager import request_provider_verification
    except Exception as exc:
        return ExecutionResult(
            AttemptOutcome.FAILED, {"error": f"import:{type(exc).__name__}"}
        )
    try:
        result = request_provider_verification(
            db,
            facts.user_id,
            facts.provider,
            trigger_source="internal_recovery",
        )
        return ExecutionResult(
            AttemptOutcome.SUCCEEDED,
            {
                "deep": deep,
                "verification": _safe_result(result),
            },
        )
    except Exception as exc:
        logger.exception(
            "recovery_session_verify_failed user=%s provider=%s",
            facts.user_id,
            facts.provider,
        )
        return ExecutionResult(
            AttemptOutcome.FAILED, {"error": type(exc).__name__, "deep": deep}
        )


def _run_account_resync(db: Any, *, facts: RecoveryFacts) -> ExecutionResult:
    try:
        from mighty.provider_access_manager import ensure_provider_access_check_if_stale
    except Exception as exc:
        return ExecutionResult(
            AttemptOutcome.FAILED, {"error": f"import:{type(exc).__name__}"}
        )
    try:
        result = ensure_provider_access_check_if_stale(
            db,
            facts.user_id,
            facts.provider,
            trigger_source="internal_recovery",
        )
        return ExecutionResult(
            AttemptOutcome.SUCCEEDED,
            {"resync": _safe_result(result)},
        )
    except Exception as exc:
        logger.exception(
            "recovery_account_resync_failed user=%s provider=%s",
            facts.user_id,
            facts.provider,
        )
        return ExecutionResult(AttemptOutcome.FAILED, {"error": type(exc).__name__})


def _safe_result(result: Any) -> Any:
    if result is None:
        return None
    if isinstance(result, (str, int, float, bool)):
        return result
    if isinstance(result, dict):
        return {str(k): _safe_result(v) for k, v in list(result.items())[:20]}
    return str(result)[:200]
