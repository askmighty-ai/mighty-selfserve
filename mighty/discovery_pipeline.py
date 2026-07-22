"""Discovery pipeline — scan results → reconcile → auto-enroll (Milestone 7).

Never raises into Home/Worker callers. Failures are counted and logged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from mighty.discovery_enrollment import (
    EnrollmentResult,
    enroll_from_discovery,
    is_provider_enrolled,
)
from mighty.discovery_policy import (
    DISPOSITION_ELIGIBLE,
    suggestion_to_decision,
)
from mighty.discovery_store import (
    ensure_discovery_tables,
    is_dismissed,
    reconcile_discovery_hits,
)

logger = logging.getLogger(__name__)

RegisterFn = Callable[[str, str, Any], None]


@dataclass
class DiscoveryProcessResult:
    decisions: int = 0
    reconciled: dict[str, int] = field(default_factory=dict)
    auto_enrolled: list[str] = field(default_factory=list)
    already_enrolled: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    dismissed: list[str] = field(default_factory=list)
    rejected: int = 0
    errors: int = 0


def process_email_scan(
    db: Any,
    user_id: str,
    suggestions: Sequence[dict],
    *,
    source_type: str,
    source_ref: str | None,
    auto_enroll_providers: frozenset[str],
    register_fn: RegisterFn | None = None,
    now: datetime | None = None,
    auto_enroll: bool = True,
) -> DiscoveryProcessResult:
    """Reconcile scan suggestions and optionally auto-enroll eligible providers.

    ``suggestions`` should include registry matches even if already enrolled so
    dispositions can mark ``already_enrolled``. Prefer calling scan with an
    empty ``already_connected`` set and let this pipeline classify.
    """
    result = DiscoveryProcessResult()
    try:
        now = now or datetime.now(timezone.utc)
        ensure_discovery_tables(db, commit=False)
        uid = str(user_id).strip()
        decisions = []
        for raw in suggestions:
            provider = str(raw.get("site_key") or "").strip().lower()
            enrolled = is_provider_enrolled(db, uid, provider) if provider else False
            dismissed = is_dismissed(db, uid, provider) if provider else False
            decision = suggestion_to_decision(
                raw,
                is_enrolled=enrolled,
                is_dismissed=dismissed,
                auto_enroll_providers=auto_enroll_providers,
            )
            if decision is None:
                result.rejected += 1
                continue
            decisions.append(decision)
            if decision.disposition == "ambiguous":
                result.ambiguous.append(decision.provider)
            elif decision.disposition == "dismissed":
                result.dismissed.append(decision.provider)
            elif decision.disposition == "already_enrolled":
                result.already_enrolled.append(decision.provider)

        result.decisions = len(decisions)
        result.reconciled = reconcile_discovery_hits(
            db,
            uid,
            decisions,
            source_type=source_type,
            source_ref=source_ref,
            now=now,
        )

        if auto_enroll and register_fn is not None:
            for decision in decisions:
                if decision.disposition != DISPOSITION_ELIGIBLE:
                    continue
                if decision.provider not in auto_enroll_providers:
                    continue
                enroll_result = _safe_enroll(
                    db,
                    uid,
                    decision.provider,
                    register_fn=register_fn,
                    now=now,
                )
                if enroll_result.enrolled:
                    result.auto_enrolled.append(decision.provider)
                elif enroll_result.already_enrolled:
                    if decision.provider not in result.already_enrolled:
                        result.already_enrolled.append(decision.provider)
    except Exception:
        result.errors += 1
        logger.exception("discovery_process_email_scan_failed user_id=%s", user_id)
    return result


def _safe_enroll(
    db: Any,
    user_id: str,
    provider: str,
    *,
    register_fn: RegisterFn,
    now: datetime,
) -> EnrollmentResult:
    try:
        return enroll_from_discovery(
            db,
            user_id,
            provider,
            register_fn=register_fn,
            now=now,
            require_eligible=True,
        )
    except Exception:
        logger.exception(
            "discovery_auto_enroll_failed user=%s provider=%s", user_id, provider
        )
        return EnrollmentResult(provider, False, False, True, "exception")
