"""Product session access — single bridge from provider_session_state to UI.

All customer-facing login decisions must go through compute_current_account_access_rows
(resolve_current_account_access). Legacy sync_status / connection_status must not
decide banners, counts, badges, or /api/account-status login messaging.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from mighty.login_truth import (
    CurrentAccess,
    CurrentAccountAccess,
    compute_current_account_access_rows,
)
from mighty.provider_access_probe import PROBE_PROVIDERS
from mighty.user_copy import (
    ACCOUNT_STATE_CHECKING,
    ACCOUNT_STATE_NEEDS_ATTENTION,
    ACCOUNT_STATE_NEEDS_SIGN_IN,
    ACCOUNT_STATE_READY,
    CTA_SIGN_IN,
    CTA_UPDATING,
    CTA_VIEW,
)

# Product-facing session vocabulary (PR contract).
ProductSessionState = Literal["connected", "checking", "signed_out", "unknown"]

CHECKING = "checking"
NEEDS_LOGIN = "needs_login"
UP_TO_DATE = "up_to_date"
UPDATING = "updating"
WAITING_FOR_EXTENSION = "waiting_for_extension"
ERROR = "error"

PRODUCT_SESSION_FROM_CURRENT_ACCESS: dict[CurrentAccess, ProductSessionState] = {
    "connected_now": "connected",
    "checking": "checking",
    "signed_out": "signed_out",
    "error": "signed_out",  # verification failure → Needs login
    "unknown": "unknown",
}

SESSION_STATUS_LABELS: dict[ProductSessionState, str] = {
    "connected": "Connected",
    "checking": "Checking...",
    "signed_out": "Needs login",
    "unknown": "Unknown",
}


@dataclass(frozen=True)
class SessionAccessPresentation:
    """Login/session slice derived solely from Current Access."""

    session_state: ProductSessionState
    current_access: CurrentAccess
    presentation_key: str
    presentation_label: str
    status: str
    status_color: str
    cta_label: str | None
    extension_hint: str | None
    verification_message: str | None = None


def to_product_session_state(current_access: CurrentAccess) -> ProductSessionState:
    return PRODUCT_SESSION_FROM_CURRENT_ACCESS[current_access]


def verification_message_for(display_name: str, session_state: ProductSessionState) -> str | None:
    if session_state != "checking":
        return None
    return f"Verifying your {display_name} session..."


def resolve_session_access_presentation(
    access: CurrentAccountAccess,
    *,
    display_name: str,
) -> SessionAccessPresentation:
    """Map canonical Current Access to product status + Access Loop presentation."""
    session_state = to_product_session_state(access.current_access)
    verify_msg = verification_message_for(display_name, session_state)

    if session_state == "signed_out":
        return SessionAccessPresentation(
            session_state=session_state,
            current_access=access.current_access,
            presentation_key=ACCOUNT_STATE_NEEDS_SIGN_IN,
            presentation_label="Needs sign in",
            status=NEEDS_LOGIN,
            status_color="#dc2626",
            cta_label=CTA_SIGN_IN,
            extension_hint="Sign in to refresh this account",
            verification_message=None,
        )
    if session_state == "checking":
        return SessionAccessPresentation(
            session_state=session_state,
            current_access=access.current_access,
            presentation_key=ACCOUNT_STATE_CHECKING,
            presentation_label="Checking...",
            status=CHECKING,
            status_color="#6366f1",
            cta_label=CTA_UPDATING,
            extension_hint=verify_msg,
            verification_message=verify_msg,
        )
    if session_state == "connected":
        return SessionAccessPresentation(
            session_state=session_state,
            current_access=access.current_access,
            presentation_key=ACCOUNT_STATE_READY,
            presentation_label="Ready",
            status=UP_TO_DATE,
            status_color="#16a34a",
            cta_label=CTA_VIEW,
            extension_hint=None,
            verification_message=None,
        )
    # unknown — never "needs login"; no fresh signed-out evidence
    return SessionAccessPresentation(
        session_state=session_state,
        current_access=access.current_access,
        presentation_key=ACCOUNT_STATE_READY if access.cached_data_state != "none" else ACCOUNT_STATE_NEEDS_ATTENTION,
        presentation_label="Ready" if access.cached_data_state != "none" else "Needs attention",
        status=UP_TO_DATE if access.cached_data_state != "none" else WAITING_FOR_EXTENSION,
        status_color="#16a34a" if access.cached_data_state != "none" else "#6366f1",
        cta_label=CTA_VIEW if access.cached_data_state != "none" else None,
        extension_hint=None,
        verification_message=None,
    )


def load_session_access_by_provider(
    db: Any,
    user_id: str,
    *,
    decrypt_fn: Callable[[str, str], dict[str, Any]],
    providers: tuple[str, ...] | list[str] | None = None,
) -> dict[str, CurrentAccountAccess]:
    """Load Current Access rows keyed by provider (source key for probe providers)."""
    rows = compute_current_account_access_rows(
        db,
        user_id,
        decrypt_account_fn=decrypt_fn,
        providers=providers or sorted(PROBE_PROVIDERS),
    )
    return {row.provider: row for row in rows}


def is_probe_session_provider(source: str) -> bool:
    return source in PROBE_PROVIDERS
