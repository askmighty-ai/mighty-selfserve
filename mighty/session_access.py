"""Product session access — single bridge from provider_session_state to UI.

Architecture:
  provider_session_state
    → login_truth.compute_current_account_access_rows
    → session_access.resolve_product_account_state
    → Dashboard / Accounts / Account Center / Popup / /api/account-status / Admin

All customer-facing login decisions must go through this bridge. Legacy
sync_status / connection_status must not decide banners, counts, badges, or
/api/account-status login messaging.

Surfaces may choose wording and detail level, but must never reinterpret
session_state, login_required, user_attention_required, or next_action.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from mighty.login_truth import (
    CurrentAccess,
    CurrentAccountAccess,
    NextActionType,
    compute_current_account_access_rows,
)
from mighty.provider_access_probe import PROBE_PROVIDERS
from mighty.user_copy import (
    ACCOUNT_STATE_CHECKING,
    ACCOUNT_STATE_NEEDS_SIGN_IN,
    ACCOUNT_STATE_READY,
    ACCOUNT_STATE_UNKNOWN,
    CTA_SIGN_IN,
    CTA_UPDATING,
    CTA_VIEW,
)

# Product-facing session vocabulary (shared across admin + customer surfaces).
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
    "checking": "Checking",
    "signed_out": "Sign in required",
    "unknown": "Unable to verify",
}

# Recommended user action for the product session vocabulary.
# Admin Current Access may show more diagnostic detail, but the recommended
# user action for a given product session_state must match these values.
PRODUCT_NEXT_ACTION: dict[ProductSessionState, tuple[NextActionType, str]] = {
    "connected": (
        "none",
        "Nothing. Mighty can monitor this account automatically.",
    ),
    "checking": (
        "verifying",
        "Mighty is verifying this account now.",
    ),
    "signed_out": (
        "reauthenticate",
        "Sign into this account again.",
    ),
    # unknown is not login-required — no fresh signed-out evidence.
    "unknown": (
        "none",
        "Mighty could not verify this account automatically.",
    ),
}


@dataclass(frozen=True)
class ProductAccountState:
    """Canonical product projection of Current Access.

    Every status consumer should derive session/login/next-action from this
    object (or an equivalent resolve_product_account_state call) rather than
    re-mapping Current Access locally.
    """

    provider: str
    session_state: ProductSessionState
    current_access: CurrentAccess
    login_required: bool
    user_attention_required: bool
    next_action_type: NextActionType
    next_action_text: str
    status_label: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "session_state": self.session_state,
            "current_access": self.current_access,
            "login_required": self.login_required,
            "user_attention_required": self.user_attention_required,
            "next_action_type": self.next_action_type,
            "next_action_text": self.next_action_text,
            "status_label": self.status_label,
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
    login_required: bool = False
    user_attention_required: bool = False
    next_action_type: NextActionType | None = None
    next_action_text: str | None = None


def to_product_session_state(current_access: CurrentAccess) -> ProductSessionState:
    return PRODUCT_SESSION_FROM_CURRENT_ACCESS[current_access]


def resolve_product_account_state(access: CurrentAccountAccess) -> ProductAccountState:
    """Project Current Access into the shared product account state contract."""
    session_state = to_product_session_state(access.current_access)
    login_required = session_state == "signed_out"
    next_action_type, next_action_text = PRODUCT_NEXT_ACTION[session_state]
    return ProductAccountState(
        provider=access.provider,
        session_state=session_state,
        current_access=access.current_access,
        login_required=login_required,
        user_attention_required=login_required,
        next_action_type=next_action_type,
        next_action_text=next_action_text,
        status_label=SESSION_STATUS_LABELS[session_state],
    )


def product_state_for_session(
    session_state: ProductSessionState,
    *,
    provider: str = "",
    current_access: CurrentAccess | None = None,
) -> ProductAccountState:
    """Build ProductAccountState from an already-resolved product session_state."""
    login_required = session_state == "signed_out"
    next_action_type, next_action_text = PRODUCT_NEXT_ACTION[session_state]
    if current_access is None:
        # Inverse of PRODUCT_SESSION_FROM_CURRENT_ACCESS for the common cases.
        inverse: dict[ProductSessionState, CurrentAccess] = {
            "connected": "connected_now",
            "checking": "checking",
            "signed_out": "signed_out",
            "unknown": "unknown",
        }
        current_access = inverse[session_state]
    return ProductAccountState(
        provider=provider,
        session_state=session_state,
        current_access=current_access,
        login_required=login_required,
        user_attention_required=login_required,
        next_action_type=next_action_type,
        next_action_text=next_action_text,
        status_label=SESSION_STATUS_LABELS[session_state],
    )


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
    product = resolve_product_account_state(access)
    session_state = product.session_state
    verify_msg = verification_message_for(display_name, session_state)

    if session_state == "signed_out":
        return SessionAccessPresentation(
            session_state=session_state,
            current_access=access.current_access,
            presentation_key=ACCOUNT_STATE_NEEDS_SIGN_IN,
            presentation_label=SESSION_STATUS_LABELS["signed_out"],
            status=NEEDS_LOGIN,
            status_color="#dc2626",
            cta_label=CTA_SIGN_IN,
            extension_hint="Sign in to refresh this account",
            verification_message=None,
            login_required=product.login_required,
            user_attention_required=product.user_attention_required,
            next_action_type=product.next_action_type,
            next_action_text=product.next_action_text,
        )
    if session_state == "checking":
        return SessionAccessPresentation(
            session_state=session_state,
            current_access=access.current_access,
            presentation_key=ACCOUNT_STATE_CHECKING,
            presentation_label=SESSION_STATUS_LABELS["checking"],
            status=CHECKING,
            status_color="#6366f1",
            cta_label=CTA_UPDATING,
            extension_hint=verify_msg,
            verification_message=verify_msg,
            login_required=product.login_required,
            user_attention_required=product.user_attention_required,
            next_action_type=product.next_action_type,
            next_action_text=product.next_action_text,
        )
    if session_state == "connected":
        return SessionAccessPresentation(
            session_state=session_state,
            current_access=access.current_access,
            presentation_key=ACCOUNT_STATE_READY,
            presentation_label=SESSION_STATUS_LABELS["connected"],
            status=UP_TO_DATE,
            status_color="#16a34a",
            cta_label=CTA_VIEW,
            extension_hint=None,
            verification_message=None,
            login_required=product.login_required,
            user_attention_required=product.user_attention_required,
            next_action_type=product.next_action_type,
            next_action_text=product.next_action_text,
        )
    # unknown — never "needs login"; no fresh signed-out evidence; no login CTA
    return SessionAccessPresentation(
        session_state=session_state,
        current_access=access.current_access,
        presentation_key=ACCOUNT_STATE_UNKNOWN,
        presentation_label=SESSION_STATUS_LABELS["unknown"],
        status="unknown",
        status_color="#6b7280",
        cta_label=None,
        extension_hint="Mighty could not verify this account automatically.",
        verification_message=None,
        login_required=product.login_required,
        user_attention_required=product.user_attention_required,
        next_action_type=product.next_action_type,
        next_action_text=product.next_action_text,
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


def client_login_badge_kind(
    *,
    session_state: str | None = None,
    login_required: bool = False,
    sync_status: str | None = None,
) -> Literal["needs_login", "checking"] | None:
    """Dashboard client login-badge decision mirrored from updateSyncTimes.

    Legacy sync_status=login_required must never invent Needs login when a
    canonical session_state is present (connected / unknown / checking).
    """
    if login_required or session_state == "signed_out":
        return "needs_login"
    if session_state == "checking":
        return "checking"
    if session_state in ("connected", "unknown"):
        return None
    # No session_state: checking sync_status is a non-login setup signal only.
    if not session_state and sync_status == "checking":
        return "checking"
    return None
