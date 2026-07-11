"""Canonical account readiness — live access + private data.

Customer surfaces (Dashboard, Accounts, Account Center, extension popup,
/api/account-status) must use this result for positive “Connected” status.

Ready means both are true for the current access cycle:
  1. Fresh authenticated-session evidence (existing live-session freshness window)
  2. Successful private account-data extraction correlated with that session

Cached data, sync_status, connection_status, and session_verified alone never
produce ready. Cached-data freshness stays secondary context only.
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
from mighty.session_verification import CURRENT_SESSION_FRESHNESS_SECONDS

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
) -> AccountReadiness:
    """Resolve the canonical readiness state for one account.

    Rules:
      ready      — fresh authenticated session + correlated successful extraction
      checking   — verification or extraction queued/running; no definitive failure
      signed_out — fresh explicit signed-out / login_required evidence
      unverified — anything else (stale, incomplete, extraction failed, network, …)
    """
    del freshness_seconds  # Session freshness is already applied in Current Access.
    now = now or datetime.now(timezone.utc)

    if product is not None:
        session_state = product.session_state
        provider = product.provider or provider

    session_state = session_state or "unknown"
    login_required = session_state == "signed_out"

    access_cycle_id = make_access_cycle_id(
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
            access_cycle_id=access_cycle_id,
            extraction_access_cycle_id=extraction_access_cycle_id,
        )
    )

    cached_label = format_cached_data_secondary(
        last_private_data_at or extraction_at, now=now,
    )

    verifying = verification_lifecycle in {"requested", "running"}
    extracting = is_extraction_in_progress(
        extraction_status=status,
        updating_this_source=updating_this_source,
    )

    if session_state == "signed_out":
        state: ReadinessState = SIGNED_OUT
    elif session_state == "checking" or verifying:
        state = CHECKING
    elif session_state == "connected" and extraction_correlated:
        state = READY
    elif session_state == "connected" and extracting and not extraction_correlated:
        # Fresh session, private-data pull still in flight for this cycle.
        state = CHECKING
    else:
        # Fresh session without correlated extraction, stale/unknown session,
        # extraction failure, or incomplete evidence → unverified.
        # Do not ask the user to sign in unless session is definitively signed_out.
        state = UNVERIFIED

    return AccountReadiness(
        provider=provider,
        state=state,
        status_label=READINESS_STATUS_LABELS[state],
        status_copy=READINESS_STATUS_COPY[state],
        presentation_key=READINESS_PRESENTATION_KEY[state],
        canonical_status=READINESS_CANONICAL_STATUS[state],
        login_required=login_required,
        session_state=session_state,
        access_cycle_id=access_cycle_id,
        session_evidence_at=session_evidence_at,
        extraction_at=extraction_at,
        extraction_ok=extraction_ok,
        extraction_correlated=extraction_correlated,
        verification_id=verification_id,
        cached_data_label=cached_label if state != READY else None,
    )
