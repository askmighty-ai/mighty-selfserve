"""Discovery enrollment — canonical enroll path from discovery (Milestone 7).

Wraps the existing credential/stub registration. Does not invent session or
extraction truth. Idempotent under repeated calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from mighty.discovery_policy import DISPOSITION_ELIGIBLE, DISPOSITION_ENROLLED
from mighty.discovery_store import (
    get_discovery_fact,
    mark_enrolled,
)

logger = logging.getLogger(__name__)

RegisterFn = Callable[[str, str, Any], None]


@dataclass(frozen=True)
class EnrollmentResult:
    provider: str
    enrolled: bool
    already_enrolled: bool
    skipped: bool
    reason: str


def is_provider_enrolled(db: Any, user_id: str, provider: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM account_credentials WHERE user_id=? AND source=?",
        (str(user_id).strip(), str(provider).strip().lower()),
    ).fetchone()
    return row is not None


def enroll_from_discovery(
    db: Any,
    user_id: str,
    provider: str,
    *,
    register_fn: RegisterFn,
    now: datetime | None = None,
    require_eligible: bool = True,
) -> EnrollmentResult:
    """Enroll a provider through the canonical register path.

    ``register_fn(uid, source, db)`` must be the app's ``_register_account_source``
    (or a test double with the same contract).
    """
    now = now or datetime.now(timezone.utc)
    uid = str(user_id).strip()
    prov = str(provider).strip().lower()
    if not uid or not prov:
        return EnrollmentResult(prov, False, False, True, "invalid_identity")

    if is_provider_enrolled(db, uid, prov):
        mark_enrolled(db, uid, prov, now=now)
        return EnrollmentResult(prov, False, True, False, "already_enrolled")

    fact = get_discovery_fact(db, uid, prov)
    if require_eligible:
        if fact is None:
            return EnrollmentResult(prov, False, False, True, "no_discovery_fact")
        if fact.disposition not in {DISPOSITION_ELIGIBLE, DISPOSITION_ENROLLED}:
            if fact.disposition == "dismissed":
                return EnrollmentResult(prov, False, False, True, "dismissed")
            return EnrollmentResult(
                prov, False, False, True, f"not_eligible:{fact.disposition}"
            )

    try:
        register_fn(uid, prov, db)
    except Exception:
        logger.exception(
            "discovery_enroll_register_failed user=%s provider=%s", uid, prov
        )
        return EnrollmentResult(prov, False, False, True, "register_failed")

    if not is_provider_enrolled(db, uid, prov):
        return EnrollmentResult(prov, False, False, True, "register_noop")

    mark_enrolled(db, uid, prov, now=now)
    logger.info(
        "discovery.enrolled user_id=%s provider=%s provenance=%s",
        uid,
        prov,
        (fact.source_type if fact else "unknown"),
    )
    return EnrollmentResult(prov, True, False, False, "enrolled")
