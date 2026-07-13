"""
mighty.customer_account_access
──────────────────────────────
Customer-facing account access view model shared by Dashboard and Accounts.

Presentation only — does not change readiness, session evidence, extraction,
or verification algorithms. Surfaces must render from this view model and must
not reinterpret legacy sync_status, connection_status, or Gmail discovery as
Connected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from mighty.account_readiness import (
    CHECKING as READINESS_CHECKING,
    READY as READINESS_READY,
    SIGNED_OUT as READINESS_SIGNED_OUT,
    UNVERIFIED as READINESS_UNVERIFIED,
    AccountReadiness,
)
from mighty.provider_account import EXTRACTION_FAILED, EXTRACTION_PENDING
from mighty.session_verification import ACTIVE_VERIFICATION_LIFECYCLES
from mighty import user_copy

# ── Live access (session / readiness posture) ─────────────────────────────────
LIVE_CONNECTED = "Connected"
LIVE_CHECKING = "Checking"
LIVE_SIGNED_OUT = "Signed out"
LIVE_UNKNOWN = "Unknown"

# ── Private data ──────────────────────────────────────────────────────────────
PRIVATE_SEEN = "Seen"
PRIVATE_NOT_YET_SEEN = "Not yet seen"
PRIVATE_SAVED_ONLY = "Saved data only"
PRIVATE_EXTRACTION_FAILED = "Extraction failed"

# ── Background work ───────────────────────────────────────────────────────────
BG_NONE = "None"
BG_VERIFICATION_QUEUED = "Verification queued"
BG_VERIFYING = "Verifying"
BG_EXTRACTING = "Extracting"
BG_AWAITING_FIRST = "Awaiting first check"
BG_FAILED = "Failed"
BG_TIMED_OUT = "Timed out"

# ── Discovery source (never implies Connected) ────────────────────────────────
DISCOVERED_GMAIL = "Gmail"
DISCOVERED_MANUAL = "Manual add"
DISCOVERED_EXTENSION = "Extension visit"

DISCOVERED_FROM_VALUES = frozenset({
    DISCOVERED_GMAIL,
    DISCOVERED_MANUAL,
    DISCOVERED_EXTENSION,
})


@dataclass(frozen=True)
class CustomerAccountAccessView:
    """Canonical customer-facing access-and-data state for one provider."""

    provider: str
    display_name: str
    readiness: str
    session_state: str | None
    private_data_state: str
    last_confirmed_at: str | None
    active_verification_lifecycle: str | None
    discovered_from: str
    user_action_required: bool
    user_action_text: str | None

    live_access: str
    private_data_label: str
    background_work: str
    meaning: str
    status_label: str

    user_action_url: str | None = None
    cached_data_label: str | None = None
    secondary_label: str | None = None
    access_cycle_id: str | None = None
    last_confirmed_access_cycle_id: str | None = None
    evidence_source: str | None = None
    cached_snapshot_at: str | None = None
    background_verification: bool = False
    canonical_status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "display_name": self.display_name,
            "readiness": self.readiness,
            "session_state": self.session_state,
            "private_data_state": self.private_data_state,
            "private_data_label": self.private_data_label,
            "last_confirmed_at": self.last_confirmed_at,
            "active_verification_lifecycle": self.active_verification_lifecycle,
            "discovered_from": self.discovered_from,
            "user_action_required": self.user_action_required,
            "user_action_text": self.user_action_text,
            "user_action_url": self.user_action_url,
            "live_access": self.live_access,
            "background_work": self.background_work,
            "meaning": self.meaning,
            "status_label": self.status_label,
            "cached_data_label": self.cached_data_label,
            "secondary_label": self.secondary_label,
            "access_cycle_id": self.access_cycle_id,
            "last_confirmed_access_cycle_id": self.last_confirmed_access_cycle_id,
            "evidence_source": self.evidence_source,
            "cached_snapshot_at": self.cached_snapshot_at,
            "background_verification": self.background_verification,
            "canonical_status": self.canonical_status,
        }

    def debug_rows(self) -> list[tuple[str, str]]:
        """Admin/alpha-only Why? rows — no secrets or raw payloads."""
        return [
            ("readiness", self.readiness or "—"),
            ("session_state", self.session_state or "—"),
            ("verification_lifecycle", self.active_verification_lifecycle or "—"),
            ("last_ready_cycle_id", self.last_confirmed_access_cycle_id or "—"),
            ("active_cycle_id", self.access_cycle_id or "—"),
            ("cached_snapshot_at", self.cached_snapshot_at or "—"),
            ("evidence_source", self.evidence_source or "—"),
        ]


def resolve_discovered_from(
    *,
    from_email: bool = False,
    data_source: str | None = None,
    in_credentials: bool = False,
) -> str:
    """Map discovery signals to a display label. Never implies Connected."""
    del in_credentials  # Presence in credentials is not a discovery source.
    if from_email:
        return DISCOVERED_GMAIL
    if (data_source or "") == "extension":
        return DISCOVERED_EXTENSION
    return DISCOVERED_MANUAL


def resolve_live_access(
    *,
    readiness: str | None,
    session_state: str | None,
) -> str:
    """Live access label from readiness first, then session_state."""
    if readiness == READINESS_SIGNED_OUT:
        return LIVE_SIGNED_OUT
    if readiness == READINESS_READY:
        # Retain Connected during stale-while-revalidate / background verify.
        return LIVE_CONNECTED
    if readiness == READINESS_CHECKING:
        return LIVE_CHECKING
    if session_state == "signed_out":
        return LIVE_SIGNED_OUT
    if session_state == "checking":
        return LIVE_CHECKING
    if session_state == "connected":
        return LIVE_CONNECTED
    return LIVE_UNKNOWN


def resolve_private_data_state(
    *,
    readiness: AccountReadiness | None,
    extraction_status: str | None = None,
) -> str:
    """Private-data posture. Independent of discovery / legacy sync fields."""
    if readiness is not None and readiness.state == READINESS_READY:
        return "seen"
    if readiness is not None and readiness.extraction_ok and readiness.extraction_correlated:
        return "seen"
    if extraction_status == EXTRACTION_FAILED:
        return "extraction_failed"
    if readiness is not None and readiness.cached_data_label:
        return "saved_data_only"
    if readiness is not None and readiness.extraction_ok and not readiness.extraction_correlated:
        return "saved_data_only"
    return "not_yet_seen"


def _private_data_label(state: str) -> str:
    return {
        "seen": PRIVATE_SEEN,
        "not_yet_seen": PRIVATE_NOT_YET_SEEN,
        "saved_data_only": PRIVATE_SAVED_ONLY,
        "extraction_failed": PRIVATE_EXTRACTION_FAILED,
    }.get(state, PRIVATE_NOT_YET_SEEN)


def resolve_background_work(
    *,
    readiness: str | None,
    verification_lifecycle: str | None,
    extraction_status: str | None = None,
    background_verification: bool = False,
    private_data_state: str = "not_yet_seen",
) -> str:
    """Background work label from verification lifecycle + extraction posture."""
    lifecycle = (verification_lifecycle or "").strip()
    if lifecycle == "failed":
        return BG_FAILED
    if lifecycle == "timed_out":
        return BG_TIMED_OUT
    if lifecycle == "requested":
        return BG_VERIFICATION_QUEUED
    if lifecycle == "extracting" or extraction_status == EXTRACTION_PENDING:
        if readiness == READINESS_READY or background_verification:
            # Ready retained — show Verifying rather than Extracting flip.
            if lifecycle in ACTIVE_VERIFICATION_LIFECYCLES or background_verification:
                return BG_VERIFYING
            return BG_EXTRACTING
        return BG_EXTRACTING
    if (
        lifecycle in ACTIVE_VERIFICATION_LIFECYCLES
        or background_verification
        or readiness == READINESS_CHECKING
    ):
        return BG_VERIFYING
    if (
        readiness in (None, READINESS_UNVERIFIED)
        and private_data_state in ("not_yet_seen", "saved_data_only")
        and lifecycle in ("", "completed", None)
    ):
        # No confirmed access cycle yet — awaiting first check, not Connected.
        return BG_AWAITING_FIRST
    return BG_NONE


def resolve_meaning(
    *,
    live_access: str,
    private_data_state: str,
    readiness: str | None = None,
) -> str:
    """Plain-English meaning for the access + data combination."""
    if live_access == LIVE_SIGNED_OUT or readiness == READINESS_SIGNED_OUT:
        return user_copy.ACCESS_MEANING_SIGNED_OUT
    if live_access == LIVE_CHECKING or readiness == READINESS_CHECKING:
        return user_copy.ACCESS_MEANING_CHECKING
    if live_access == LIVE_CONNECTED and private_data_state == "seen":
        return user_copy.ACCESS_MEANING_CONNECTED_SEEN
    if live_access == LIVE_CONNECTED and private_data_state != "seen":
        return user_copy.ACCESS_MEANING_CONNECTED_NOT_SEEN
    if private_data_state == "extraction_failed":
        return user_copy.ACCESS_MEANING_EXTRACTION_FAILED
    return user_copy.ACCESS_MEANING_UNKNOWN


def resolve_status_label(
    *,
    readiness: str | None,
    live_access: str,
    background_work: str,
) -> str:
    """Primary status chip — readiness wins; discovery never produces Connected."""
    if readiness == READINESS_READY:
        return user_copy.READINESS_STATUS_CONNECTED
    if readiness == READINESS_SIGNED_OUT:
        return user_copy.READINESS_STATUS_SIGNED_OUT
    if readiness == READINESS_CHECKING or live_access == LIVE_CHECKING:
        return user_copy.READINESS_STATUS_CHECKING
    if background_work == BG_AWAITING_FIRST:
        return user_copy.ACCOUNTS_STATUS_AWAITING_FIRST
    if live_access == LIVE_UNKNOWN:
        return user_copy.ACCOUNTS_STATUS_NOT_VERIFIED
    if readiness == READINESS_UNVERIFIED:
        return user_copy.ACCOUNTS_STATUS_NOT_VERIFIED
    return live_access


def build_customer_account_access_view(
    *,
    provider: str,
    display_name: str,
    readiness: AccountReadiness,
    discovered_from: str,
    verification_lifecycle: str | None = None,
    extraction_status: str | None = None,
    user_action_text: str | None = None,
    user_action_url: str | None = None,
    evidence_source: str | None = None,
    cached_snapshot_at: str | None = None,
    canonical_status: str | None = None,
) -> CustomerAccountAccessView:
    """Build the shared customer view from readiness + discovery (presentation only)."""
    session_state = readiness.session_state
    live_access = resolve_live_access(
        readiness=readiness.state,
        session_state=session_state,
    )
    private_state = resolve_private_data_state(
        readiness=readiness,
        extraction_status=extraction_status,
    )
    background = resolve_background_work(
        readiness=readiness.state,
        verification_lifecycle=verification_lifecycle,
        extraction_status=extraction_status,
        background_verification=readiness.background_verification,
        private_data_state=private_state,
    )
    # When ready + background verifying, never show Awaiting first check.
    if readiness.state == READINESS_READY and background == BG_AWAITING_FIRST:
        background = BG_VERIFYING if readiness.background_verification else BG_NONE

    meaning = resolve_meaning(
        live_access=live_access,
        private_data_state=private_state,
        readiness=readiness.state,
    )
    status_label = resolve_status_label(
        readiness=readiness.state,
        live_access=live_access,
        background_work=background,
    )

    action_required = bool(readiness.login_required)
    action_text = user_action_text if action_required else None
    action_url = user_action_url if action_required else None
    if action_required and not action_text:
        action_text = user_copy.CTA_SIGN_IN

    discovered = discovered_from if discovered_from in DISCOVERED_FROM_VALUES else DISCOVERED_MANUAL

    return CustomerAccountAccessView(
        provider=provider,
        display_name=display_name,
        readiness=readiness.state,
        session_state=session_state,
        private_data_state=private_state,
        last_confirmed_at=readiness.last_confirmed_ready_at,
        active_verification_lifecycle=verification_lifecycle,
        discovered_from=discovered,
        user_action_required=action_required,
        user_action_text=action_text,
        user_action_url=action_url,
        live_access=live_access,
        private_data_label=_private_data_label(private_state),
        background_work=background,
        meaning=meaning,
        status_label=status_label,
        cached_data_label=readiness.cached_data_label,
        secondary_label=readiness.secondary_label,
        access_cycle_id=readiness.access_cycle_id,
        last_confirmed_access_cycle_id=readiness.last_confirmed_access_cycle_id,
        evidence_source=evidence_source,
        cached_snapshot_at=cached_snapshot_at or readiness.extraction_at,
        background_verification=readiness.background_verification,
        canonical_status=canonical_status or readiness.canonical_status,
    )


def connected_summary_label(views: Sequence[CustomerAccountAccessView]) -> str | None:
    """Named Connected summary, e.g. 'Connected: American Express'."""
    ready = [v for v in views if v.readiness == READINESS_READY]
    if not ready:
        return None
    if len(ready) == 1:
        return user_copy.access_connected_named(ready[0].display_name)
    names = ", ".join(v.display_name for v in ready[:3])
    if len(ready) > 3:
        names = f"{names} +{len(ready) - 3}"
    return user_copy.access_connected_named(names)


# Accounts section keys — mirrored from accounts_ui to avoid import cycles.
SECTION_NEEDS_LOGIN = "needs_login"
SECTION_NEEDS_ATTENTION = "needs_attention"
SECTION_WAITING = "waiting"
SECTION_UP_TO_DATE = "up_to_date"


def section_for_view(view: CustomerAccountAccessView) -> str:
    """Accounts list section from the shared view — no page-specific reinterpretation."""
    if view.readiness == READINESS_SIGNED_OUT or view.user_action_required:
        return SECTION_NEEDS_LOGIN
    if view.readiness == READINESS_READY:
        return SECTION_UP_TO_DATE
    if view.canonical_status == "error":
        return SECTION_NEEDS_ATTENTION
    return SECTION_WAITING
