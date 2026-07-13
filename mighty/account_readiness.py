"""Canonical account readiness — live access + private data.

Customer surfaces (Dashboard, Accounts, Account Center, extension popup,
/api/account-status) must use this result for positive “Connected” status.

Ready means both are true for a confirmed access cycle:
  1. Fresh authenticated-session evidence (existing live-session freshness window)
  2. Successful private account-data extraction correlated with that session

Cached data, sync_status, connection_status, and session_verified alone never
produce ready. Cached-data freshness stays secondary context only.

Stale-while-revalidate:
  Last confirmed ready is retained while a later routine verification runs, and
  through an explicit grace period after inconclusive/timeout rechecks. Active
  verification lifecycle must not overwrite customer-facing ready. Definitive
  signed_out replaces ready immediately. “Connected — awaiting data” is only
  valid before any successful correlated extraction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from mighty.provider_account import (
    EXTRACTION_COMPLETE,
    EXTRACTION_FAILED,
    EXTRACTION_PENDING,
    ProviderAccount,
    has_normalized_data,
    is_synced,
)
from mighty.session_access import ProductAccountState, ProductSessionState
from mighty.session_verification import (
    ACTIVE_VERIFICATION_LIFECYCLES,
    CURRENT_SESSION_FRESHNESS_SECONDS,
    READY_RESULT_GRACE_SECONDS,
)

ReadinessState = Literal["ready", "checking", "signed_out", "unverified"]

READY = "ready"
CHECKING = "checking"
SIGNED_OUT = "signed_out"
UNVERIFIED = "unverified"

READINESS_STATUS_LABELS: dict[ReadinessState, str] = {
    READY: "Connected",
    CHECKING: "Checking",
    SIGNED_OUT: "Sign in required",
    UNVERIFIED: "Unable to verify",
}

READINESS_STATUS_COPY: dict[ReadinessState, str] = {
    READY: "Mighty is connected to your logged-in account and can see your data.",
    CHECKING: "Mighty is verifying access and account data.",
    SIGNED_OUT: "Sign in so Mighty can access your account data.",
    UNVERIFIED: "Mighty could not confirm both account access and data.",
}

# Presentation keys shared with Access Loop / AccountStatus consumers.
READINESS_PRESENTATION_KEY: dict[ReadinessState, str] = {
    READY: "ready",
    CHECKING: "checking",
    SIGNED_OUT: "needs_sign_in",
    UNVERIFIED: "unknown",
}

# AccountStatus.status compatibility mapping for health / section buckets.
READINESS_CANONICAL_STATUS: dict[ReadinessState, str] = {
    READY: "up_to_date",
    CHECKING: "checking",
    SIGNED_OUT: "needs_login",
    UNVERIFIED: "unverified",
}

BACKGROUND_VERIFYING_LABEL = "Verifying in the background"
BACKGROUND_VERIFYING_WARNING = (
    "Connected — last confirmed access is still usable while Mighty rechecks."
)


@dataclass(frozen=True)
class AccountReadiness:
    """Single readiness result shared by all customer surfaces."""

    provider: str
    state: ReadinessState
    status_label: str
    status_copy: str
    presentation_key: str
    canonical_status: str
    login_required: bool
    session_state: ProductSessionState | None
    access_cycle_id: str | None
    session_evidence_at: str | None
    extraction_at: str | None
    extraction_ok: bool
    extraction_correlated: bool
    verification_id: str | None
    cached_data_label: str | None = None
    # Last confirmed ready (may differ from active verification lifecycle).
    last_confirmed_ready_at: str | None = None
    last_confirmed_access_cycle_id: str | None = None
    background_verification: bool = False
    secondary_label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "readiness": self.state,
            "status_label": self.status_label,
            "status_copy": self.status_copy,
            "presentation_key": self.presentation_key,
            "canonical_status": self.canonical_status,
            "login_required": self.login_required,
            "session_state": self.session_state,
            "access_cycle_id": self.access_cycle_id,
            "session_evidence_at": self.session_evidence_at,
            "extraction_at": self.extraction_at,
            "extraction_ok": self.extraction_ok,
            "extraction_correlated": self.extraction_correlated,
            "verification_id": self.verification_id,
            "cached_data_label": self.cached_data_label,
            "last_confirmed_ready_at": self.last_confirmed_ready_at,
            "last_confirmed_access_cycle_id": self.last_confirmed_access_cycle_id,
            "background_verification": self.background_verification,
            "secondary_label": self.secondary_label,
        }


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def format_cached_data_secondary(last_private_data_at: str | None, *, now: datetime | None = None) -> str | None:
    """Secondary context only — never implies Connected / ready."""
    when = _parse_iso(last_private_data_at)
    if when is None:
        return None
    now = now or datetime.now(timezone.utc)
    age = now - when
    seconds = max(0, int(age.total_seconds()))
    if seconds < 60:
        ago = "just now" if seconds < 10 else f"{seconds} seconds ago"
    elif seconds < 3600:
        minutes = seconds // 60
        ago = "1 minute ago" if minutes == 1 else f"{minutes} minutes ago"
    elif seconds < 86400:
        hours = seconds // 3600
        ago = "1 hour ago" if hours == 1 else f"{hours} hours ago"
    else:
        days = seconds // 86400
        ago = "1 day ago" if days == 1 else f"{days} days ago"
    return f"Last saved data: {ago}"


def make_access_cycle_id(
    *,
    provider: str,
    verification_id: str | None = None,
    session_evidence_at: str | None = None,
) -> str | None:
    """Stable access-cycle identifier for correlating verification and extraction."""
    if verification_id:
        return str(verification_id).strip() or None
    if session_evidence_at:
        return f"{provider}:{session_evidence_at}"
    return None


def extraction_correlates_with_access(
    *,
    session_evidence_at: str | None,
    extraction_at: str | None,
    access_cycle_id: str | None = None,
    extraction_access_cycle_id: str | None = None,
) -> bool:
    """True when extraction belongs to the winning authenticated access cycle.

    Prefer shared access-cycle / verification ids when both sides have them.
    Otherwise require extraction at-or-after the winning session evidence time.
    """
    if access_cycle_id and extraction_access_cycle_id:
        if access_cycle_id == extraction_access_cycle_id:
            return True
        # Explicit mismatch: do not fall back to timestamps from a prior cycle.
        return False

    session_at = _parse_iso(session_evidence_at)
    extracted_at = _parse_iso(extraction_at)
    if session_at is None or extracted_at is None:
        return False
    return extracted_at >= session_at


def has_successful_private_extraction(
    account: ProviderAccount | None,
    *,
    extraction_status: str | None = None,
) -> bool:
    """Successful private account-data extraction — not cached presence alone."""
    status = extraction_status
    if status is None and account is not None:
        status = account.extraction_status
    if status == EXTRACTION_FAILED:
        return False
    fields = account.normalized_fields if account else None
    if is_synced(fields, extraction_status=status):
        return True
    if status == EXTRACTION_COMPLETE and has_normalized_data(fields):
        return True
    return False


def is_extraction_in_progress(
    *,
    extraction_status: str | None = None,
    updating_this_source: bool = False,
) -> bool:
    if updating_this_source:
        return True
    return extraction_status == EXTRACTION_PENDING


def is_prior_ready_usable(
    *,
    extraction_ok: bool,
    extraction_at: str | None,
    now: datetime | None = None,
    grace_seconds: int = READY_RESULT_GRACE_SECONDS,
) -> bool:
    """True when a prior successful extraction remains within the ready grace window."""
    if not extraction_ok:
        return False
    when = _parse_iso(extraction_at)
    if when is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - when).total_seconds() <= grace_seconds


def resolve_account_readiness(
    *,
    provider: str,
    product: ProductAccountState | None = None,
    session_state: ProductSessionState | None = None,
    session_evidence_at: str | None = None,
    verification_id: str | None = None,
    verification_lifecycle: str | None = None,
    account: ProviderAccount | None = None,
    extraction_status: str | None = None,
    extraction_at: str | None = None,
    extraction_access_cycle_id: str | None = None,
    last_private_data_at: str | None = None,
    updating_this_source: bool = False,
    now: datetime | None = None,
    freshness_seconds: int = CURRENT_SESSION_FRESHNESS_SECONDS,
    grace_seconds: int = READY_RESULT_GRACE_SECONDS,
) -> AccountReadiness:
    """Resolve the canonical readiness state for one account.

    Rules:
      ready      — confirmed correlated extraction, including stale-while-revalidate
                   while a later routine cycle runs (within grace)
      checking   — verification/extraction in flight with no usable prior ready
      signed_out — fresh explicit signed-out / login_required evidence
      unverified — stale/incomplete/failed without usable prior ready; never
                   signed_out without definitive evidence
    """
    del freshness_seconds  # Session freshness is already applied in Current Access.
    now = now or datetime.now(timezone.utc)

    if product is not None:
        session_state = product.session_state
        provider = product.provider or provider

    session_state = session_state or "unknown"
    current_access = product.current_access if product is not None else None

    active_access_cycle_id = make_access_cycle_id(
        provider=provider,
        verification_id=verification_id,
        session_evidence_at=session_evidence_at,
    )

    status = extraction_status
    if status is None and account is not None:
        status = account.extraction_status
    if extraction_at is None and account is not None:
        extraction_at = account.synced_at

    extraction_ok = has_successful_private_extraction(
        account, extraction_status=status,
    )
    extraction_correlated = bool(
        extraction_ok
        and extraction_correlates_with_access(
            session_evidence_at=session_evidence_at,
            extraction_at=extraction_at,
            access_cycle_id=active_access_cycle_id,
            extraction_access_cycle_id=extraction_access_cycle_id,
        )
    )

    prior_ready_usable = is_prior_ready_usable(
        extraction_ok=extraction_ok,
        extraction_at=extraction_at,
        now=now,
        grace_seconds=grace_seconds,
    )
    last_confirmed_ready_at = extraction_at if extraction_ok else None
    last_confirmed_access_cycle_id = (
        extraction_access_cycle_id if extraction_ok else None
    )
    # Between-cycle retention requires an explicitly cycle-tagged prior ready.
    # Timestamp-only legacy extractions must not invent Connected when session
    # evidence is merely unknown/stale with no active revalidation.
    cycle_tagged_prior_ready = prior_ready_usable and bool(extraction_access_cycle_id)

    cached_label = format_cached_data_secondary(
        last_private_data_at or extraction_at, now=now,
    )

    verifying = verification_lifecycle in ACTIVE_VERIFICATION_LIFECYCLES
    extracting = is_extraction_in_progress(
        extraction_status=status,
        updating_this_source=updating_this_source,
    )
    inconclusive = (
        current_access == "error"
        or verification_lifecycle in {"failed", "timed_out"}
    )
    definitive_signed_out = (
        session_state == "signed_out" and current_access != "error"
    )

    # Stale-while-revalidate: retain last confirmed ready while a later routine
    # cycle runs, through inconclusive rechecks inside grace, or between cycles
    # when session evidence aged out but a cycle-tagged ready remains usable.
    swr_context = False
    if prior_ready_usable and not definitive_signed_out:
        if verifying or inconclusive:
            swr_context = True
        elif (
            cycle_tagged_prior_ready
            and session_state in {"checking", "unknown"}
        ):
            swr_context = True

    background_verification = False
    secondary_label: str | None = None
    # Customer-facing access_cycle_id prefers last confirmed over active lifecycle.
    customer_access_cycle_id = active_access_cycle_id

    if definitive_signed_out:
        # Definitive signed-out evidence replaces ready immediately.
        state: ReadinessState = SIGNED_OUT
    elif session_state == "connected" and extraction_correlated:
        # Fresh successful correlated extraction for the winning access cycle.
        state = READY
        customer_access_cycle_id = (
            extraction_access_cycle_id or active_access_cycle_id
        )
        last_confirmed_ready_at = extraction_at
        last_confirmed_access_cycle_id = customer_access_cycle_id
    elif swr_context and prior_ready_usable:
        # Retain last confirmed ready; do not let active lifecycle overwrite it.
        state = READY
        customer_access_cycle_id = (
            last_confirmed_access_cycle_id or active_access_cycle_id
        )
        if verifying:
            background_verification = True
            secondary_label = BACKGROUND_VERIFYING_LABEL
        elif inconclusive:
            background_verification = True
            secondary_label = BACKGROUND_VERIFYING_WARNING
    elif inconclusive:
        # Past grace (or never ready): unable to verify — never invent signed_out.
        state = UNVERIFIED
    elif session_state == "checking" or verifying:
        state = CHECKING
    elif session_state == "connected" and extracting and not extraction_correlated:
        # Fresh session, private-data pull still in flight for this cycle.
        # Once the access cycle is terminal without correlation, do not linger
        # in checking (e.g. extraction_status=pending after a failed cycle).
        if verification_lifecycle in {"failed", "timed_out", "completed"}:
            state = UNVERIFIED
        else:
            state = CHECKING
    else:
        # Fresh session without correlated extraction, stale/unknown session,
        # extraction failure, or incomplete evidence → unverified.
        # Do not ask the user to sign in unless session is definitively signed_out.
        state = UNVERIFIED

    login_required = state == SIGNED_OUT

    return AccountReadiness(
        provider=provider,
        state=state,
        status_label=READINESS_STATUS_LABELS[state],
        status_copy=READINESS_STATUS_COPY[state],
        presentation_key=READINESS_PRESENTATION_KEY[state],
        canonical_status=READINESS_CANONICAL_STATUS[state],
        login_required=login_required,
        session_state=session_state,
        access_cycle_id=customer_access_cycle_id,
        session_evidence_at=session_evidence_at,
        extraction_at=extraction_at,
        extraction_ok=extraction_ok,
        extraction_correlated=extraction_correlated,
        verification_id=verification_id,
        cached_data_label=cached_label if state != READY else None,
        last_confirmed_ready_at=last_confirmed_ready_at if extraction_ok else None,
        last_confirmed_access_cycle_id=(
            last_confirmed_access_cycle_id if extraction_ok else None
        ),
        background_verification=background_verification and state == READY,
        secondary_label=secondary_label if state == READY else None,
    )
