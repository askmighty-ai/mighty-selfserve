"""
mighty.account_presentation
───────────────────────────
Shared user-facing account state vocabulary for Account Center and the extension.

Presentation states (exact labels):
  Needs login · Checking account · Connected · Needs attention · No data yet
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from mighty.account_state import (
    CONFIDENCE_LOW,
    CONN_CONNECTED,
    CONN_CONNECTING,
    CONN_NEEDS_LOGIN,
    CONN_NOT_CONNECTED,
    DATA_COMPLETE,
    DATA_NONE,
    DATA_PARTIAL,
    FINANCIAL_PROVIDERS,
    SESSION_EXPIRED,
    SESSION_EXPIRING,
    AccountState,
)
from mighty.user_copy import (
    ACCOUNT_STATE_LABELS,
    ACCOUNT_STATE_CHECKING,
    ACCOUNT_STATE_CONNECTED,
    ACCOUNT_STATE_NEEDS_ATTENTION,
    ACCOUNT_STATE_NEEDS_LOGIN,
    ACCOUNT_STATE_NO_DATA,
    CTA_CHECKING,
    CTA_REFRESH,
    CTA_RETRY,
    CTA_SIGN_IN,
    CTA_VIEW,
    EXT_ACCOUNT_CHECKING_HINT,
    EXT_ACCOUNT_NEEDS_LOGIN_HINT,
)

SESSION_TTL_HOURS: dict[str, tuple[int, int]] = {
    "financial": (24, 24 * 7),
    "default": (24 * 7, 24 * 14),
}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _session_ttl_hours(provider: str) -> tuple[int, int]:
    if provider in FINANCIAL_PROVIDERS:
        return SESSION_TTL_HOURS["financial"]
    return SESSION_TTL_HOURS["default"]


def is_recent_session_verification(
    last_verified_at: str | None,
    *,
    provider: str = "",
    max_hours: int | None = None,
) -> bool:
    """True when the extension verified a browser session within the healthy TTL."""
    verified = _parse_iso(last_verified_at)
    if not verified:
        return False
    if verified.tzinfo is None:
        verified = verified.replace(tzinfo=timezone.utc)
    if max_hours is None:
        max_hours, _ = _session_ttl_hours(provider)
    age = datetime.now(timezone.utc) - verified
    return age <= timedelta(hours=max_hours)


@dataclass(frozen=True)
class AccountPresentation:
    key: str
    label: str
    cta_label: str
    cta_disabled: bool
    extension_hint: str | None = None


def _connected_data_is_fresh(state: AccountState) -> bool:
    if state.data_status != DATA_COMPLETE:
        return False
    if state.session_health not in {SESSION_EXPIRED, SESSION_EXPIRING}:
        return True
    return False


def resolve_account_presentation(
    state: AccountState,
    *,
    sync_running: bool = False,
    updating_source: str | None = None,
) -> AccountPresentation:
    """Map AccountState to the shared user-facing vocabulary."""
    provider = state.provider
    recently_verified = is_recent_session_verification(
        state.last_verified_at, provider=provider,
    )

    if state.connection_state == CONN_NOT_CONNECTED:
        return AccountPresentation(
            key=ACCOUNT_STATE_NEEDS_LOGIN,
            label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_LOGIN],
            cta_label=CTA_SIGN_IN,
            cta_disabled=False,
            extension_hint=EXT_ACCOUNT_NEEDS_LOGIN_HINT,
        )

    signed_out = state.connection_state == CONN_NEEDS_LOGIN
    if signed_out and not recently_verified:
        return AccountPresentation(
            key=ACCOUNT_STATE_NEEDS_LOGIN,
            label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_LOGIN],
            cta_label=CTA_SIGN_IN,
            cta_disabled=False,
            extension_hint=EXT_ACCOUNT_NEEDS_LOGIN_HINT,
        )

    if sync_running and updating_source == provider:
        return AccountPresentation(
            key=ACCOUNT_STATE_CHECKING,
            label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_CHECKING],
            cta_label=CTA_CHECKING,
            cta_disabled=True,
            extension_hint=EXT_ACCOUNT_CHECKING_HINT,
        )

    if state.connection_state == CONN_CONNECTING:
        return AccountPresentation(
            key=ACCOUNT_STATE_CHECKING,
            label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_CHECKING],
            cta_label=CTA_CHECKING,
            cta_disabled=True,
            extension_hint=EXT_ACCOUNT_CHECKING_HINT,
        )

    if state.connection_state == CONN_CONNECTED:
        if state.data_status == DATA_NONE:
            if recently_verified or state.session_health not in {SESSION_EXPIRED}:
                return AccountPresentation(
                    key=ACCOUNT_STATE_NO_DATA,
                    label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NO_DATA],
                    cta_label=CTA_RETRY,
                    cta_disabled=False,
                )
            return AccountPresentation(
                key=ACCOUNT_STATE_CHECKING,
                label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_CHECKING],
                cta_label=CTA_CHECKING,
                cta_disabled=True,
                extension_hint=EXT_ACCOUNT_CHECKING_HINT,
            )

        if state.session_health in (SESSION_EXPIRING, SESSION_EXPIRED):
            return AccountPresentation(
                key=ACCOUNT_STATE_NEEDS_ATTENTION,
                label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_ATTENTION],
                cta_label=CTA_SIGN_IN,
                cta_disabled=False,
                extension_hint=EXT_ACCOUNT_NEEDS_LOGIN_HINT,
            )

        if (
            state.confidence.level == CONFIDENCE_LOW
            and state.data_status == DATA_PARTIAL
        ):
            return AccountPresentation(
                key=ACCOUNT_STATE_NEEDS_ATTENTION,
                label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_ATTENTION],
                cta_label=CTA_REFRESH,
                cta_disabled=False,
            )

        if _connected_data_is_fresh(state):
            return AccountPresentation(
                key=ACCOUNT_STATE_CONNECTED,
                label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_CONNECTED],
                cta_label=CTA_VIEW,
                cta_disabled=False,
            )

        return AccountPresentation(
            key=ACCOUNT_STATE_CONNECTED,
            label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_CONNECTED],
            cta_label=CTA_REFRESH,
            cta_disabled=False,
        )

    return AccountPresentation(
        key=ACCOUNT_STATE_NEEDS_ATTENTION,
        label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_ATTENTION],
        cta_label=CTA_SIGN_IN,
        cta_disabled=False,
        extension_hint=EXT_ACCOUNT_NEEDS_LOGIN_HINT,
    )


def resolve_presentation_from_status_signals(
    *,
    provider: str,
    connection_status: str | None,
    sync_status: str,
    lifecycle_state: str,
    has_meaningful_data: bool,
    last_verified_at: str | None,
    is_updating: bool,
    sync_status_error: str | None = None,
) -> AccountPresentation:
    """Map extension/account-status signals to the shared vocabulary."""
    from mighty.account_lifecycle import (
        CONNECTED as LC_CONNECTED,
        NEEDS_LOGIN as LC_NEEDS_LOGIN,
        SYNCED as LC_SYNCED,
        WAITING_FOR_EXTENSION as LC_WAITING,
    )

    conn = connection_status or ""
    recently_verified = is_recent_session_verification(
        last_verified_at, provider=provider,
    )

    if lifecycle_state == LC_NEEDS_LOGIN and not recently_verified and conn != CONN_CONNECTED:
        return AccountPresentation(
            key=ACCOUNT_STATE_NEEDS_LOGIN,
            label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_LOGIN],
            cta_label=CTA_SIGN_IN,
            cta_disabled=False,
            extension_hint=EXT_ACCOUNT_NEEDS_LOGIN_HINT,
        )

    if sync_status == "login_required" and conn != CONN_CONNECTED and not recently_verified:
        return AccountPresentation(
            key=ACCOUNT_STATE_NEEDS_LOGIN,
            label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_LOGIN],
            cta_label=CTA_SIGN_IN,
            cta_disabled=False,
            extension_hint=EXT_ACCOUNT_NEEDS_LOGIN_HINT,
        )

    if is_updating or lifecycle_state == LC_WAITING:
        return AccountPresentation(
            key=ACCOUNT_STATE_CHECKING,
            label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_CHECKING],
            cta_label=CTA_CHECKING,
            cta_disabled=True,
            extension_hint=EXT_ACCOUNT_CHECKING_HINT,
        )

    if lifecycle_state in (LC_CONNECTED, LC_SYNCED) or conn == CONN_CONNECTED:
        if not has_meaningful_data and lifecycle_state != LC_SYNCED:
            return AccountPresentation(
                key=ACCOUNT_STATE_NO_DATA,
                label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NO_DATA],
                cta_label=CTA_RETRY,
                cta_disabled=False,
            )
        if lifecycle_state == LC_SYNCED or has_meaningful_data:
            return AccountPresentation(
                key=ACCOUNT_STATE_CONNECTED,
                label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_CONNECTED],
                cta_label=CTA_VIEW if lifecycle_state == LC_SYNCED else CTA_REFRESH,
                cta_disabled=False,
            )

    if sync_status_error == "no_data" or sync_status == "no_data":
        return AccountPresentation(
            key=ACCOUNT_STATE_NO_DATA,
            label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NO_DATA],
            cta_label=CTA_RETRY,
            cta_disabled=False,
        )

    return AccountPresentation(
        key=ACCOUNT_STATE_NEEDS_ATTENTION,
        label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_ATTENTION],
        cta_label=CTA_RETRY,
        cta_disabled=False,
    )
