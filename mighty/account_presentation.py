"""
mighty.account_presentation
───────────────────────────
Shared Account Access Loop for Account Center and the extension.

Four user-facing states (exact labels):
  Needs sign in · Updating · Ready · Needs attention
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

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
    ACCOUNT_STATE_NEEDS_ATTENTION,
    ACCOUNT_STATE_NEEDS_SIGN_IN,
    ACCOUNT_STATE_READY,
    ACCOUNT_STATE_UPDATING,
    CTA_FIX,
    CTA_SIGN_IN,
    CTA_UPDATING,
    CTA_VIEW,
    EXT_ACCOUNT_NEEDS_SIGN_IN_HINT,
    EXT_ACCOUNT_UPDATING_HINT,
    WORKER_ACCESS_LOOP_UPDATING,
    WORKER_OPEN_ACCOUNT_CENTER,
    access_loop_count_needs_attention,
    access_loop_count_needs_sign_in,
    access_loop_count_ready,
    access_loop_count_updating,
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


@dataclass
class AccessLoopSummary:
    """Aggregate Account Access Loop state for Account Center and extension."""

    total: int
    needs_sign_in: int
    updating: int
    ready: int
    needs_attention: int
    headline: str
    detail_lines: list[str]
    open_account_center_label: str
    is_updating: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "needs_sign_in": self.needs_sign_in,
            "updating": self.updating,
            "ready": self.ready,
            "needs_attention": self.needs_attention,
            "headline": self.headline,
            "detail_lines": self.detail_lines,
            "open_account_center_label": self.open_account_center_label,
            "is_updating": self.is_updating,
            # Legacy fields consumed by older extension builds.
            "is_syncing": self.is_updating,
            "needs_login_count": self.needs_sign_in,
            "updating_count": self.updating,
            "subline": " · ".join(self.detail_lines) if self.detail_lines else "",
        }


def _needs_sign_in(state: AccountState, *, recently_verified: bool) -> bool:
    if state.connection_state == CONN_NOT_CONNECTED:
        return True
    if state.connection_state == CONN_NEEDS_LOGIN and not recently_verified:
        return True
    return False


def _is_updating(
    state: AccountState,
    *,
    recently_verified: bool,
    sync_running: bool,
    updating_source: str | None,
) -> bool:
    provider = state.provider
    if sync_running and updating_source == provider:
        return True
    if state.connection_state == CONN_CONNECTING:
        return True
    if state.connection_state == CONN_CONNECTED and state.data_status == DATA_NONE:
        if recently_verified or state.session_health not in {SESSION_EXPIRED}:
            return True
    return False


def _is_ready(state: AccountState) -> bool:
    if state.connection_state != CONN_CONNECTED:
        return False
    if state.data_status not in {DATA_COMPLETE, DATA_PARTIAL}:
        return False
    if state.session_health in (SESSION_EXPIRING, SESSION_EXPIRED):
        return False
    if state.confidence.level == CONFIDENCE_LOW and state.data_status == DATA_PARTIAL:
        return False
    return True


def resolve_account_presentation(
    state: AccountState,
    *,
    sync_running: bool = False,
    updating_source: str | None = None,
) -> AccountPresentation:
    """Map AccountState to the shared Account Access Loop vocabulary."""
    provider = state.provider
    recently_verified = is_recent_session_verification(
        state.last_verified_at, provider=provider,
    )

    if _needs_sign_in(state, recently_verified=recently_verified):
        return AccountPresentation(
            key=ACCOUNT_STATE_NEEDS_SIGN_IN,
            label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_SIGN_IN],
            cta_label=CTA_SIGN_IN,
            cta_disabled=False,
            extension_hint=EXT_ACCOUNT_NEEDS_SIGN_IN_HINT,
        )

    if _is_updating(
        state,
        recently_verified=recently_verified,
        sync_running=sync_running,
        updating_source=updating_source,
    ):
        return AccountPresentation(
            key=ACCOUNT_STATE_UPDATING,
            label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_UPDATING],
            cta_label=CTA_UPDATING,
            cta_disabled=True,
            extension_hint=EXT_ACCOUNT_UPDATING_HINT,
        )

    if _is_ready(state):
        return AccountPresentation(
            key=ACCOUNT_STATE_READY,
            label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_READY],
            cta_label=CTA_VIEW,
            cta_disabled=False,
        )

    return AccountPresentation(
        key=ACCOUNT_STATE_NEEDS_ATTENTION,
        label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_ATTENTION],
        cta_label=CTA_FIX,
        cta_disabled=False,
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
    """Map legacy status signals through AccountState projection when possible."""
    from mighty.account_lifecycle import (
        CONNECTED as LC_CONNECTED,
        NEEDS_LOGIN as LC_NEEDS_LOGIN,
        SYNCED as LC_SYNCED,
        WAITING_FOR_EXTENSION as LC_WAITING,
    )
    from mighty.account_state import (
        ACCESS_BROWSER_SESSION,
        Confidence,
        ConfidenceFactors,
    )

    conn = connection_status or ""
    recently_verified = is_recent_session_verification(
        last_verified_at, provider=provider,
    )

    if conn == CONN_CONNECTED and recently_verified:
        connection_state = CONN_CONNECTED
    elif lifecycle_state == LC_NEEDS_LOGIN or sync_status == "login_required":
        connection_state = CONN_NEEDS_LOGIN if not recently_verified else CONN_CONNECTED
    elif lifecycle_state == LC_WAITING:
        connection_state = CONN_CONNECTING
    elif lifecycle_state in (LC_CONNECTED, LC_SYNCED) or conn == CONN_CONNECTED:
        connection_state = CONN_CONNECTED
    else:
        connection_state = CONN_NOT_CONNECTED

    if has_meaningful_data or lifecycle_state == LC_SYNCED:
        data_status = DATA_COMPLETE
    elif sync_status in ("no_data",) or sync_status_error == "no_data":
        data_status = DATA_NONE
    else:
        data_status = DATA_NONE if not has_meaningful_data else DATA_PARTIAL

    shadow = AccountState(
        user_id="",
        provider=provider,
        display_name=provider,
        category=None,
        access_method=ACCESS_BROWSER_SESSION,
        connection_state=connection_state,
        session_health="healthy" if recently_verified else "unknown",
        last_verified_at=last_verified_at,
        data_status=data_status,
        last_data_refresh=None,
        observations_available=[],
        field_count=0,
        next_recommended_action=None,
        confidence=Confidence(level="high", score=90, factors=ConfidenceFactors()),
        status_line="",
        is_actionable=False,
        updated_at="",
    )
    return resolve_account_presentation(
        shadow,
        sync_running=is_updating,
        updating_source=provider if is_updating else None,
    )


def build_access_loop_summary(
    presentations: list[AccountPresentation],
) -> AccessLoopSummary:
    """Build aggregate copy from per-account Access Loop presentations."""
    counts = {
        ACCOUNT_STATE_NEEDS_SIGN_IN: 0,
        ACCOUNT_STATE_UPDATING: 0,
        ACCOUNT_STATE_READY: 0,
        ACCOUNT_STATE_NEEDS_ATTENTION: 0,
    }
    for presentation in presentations:
        counts[presentation.key] = counts.get(presentation.key, 0) + 1

    detail_lines: list[str] = []
    if counts[ACCOUNT_STATE_NEEDS_SIGN_IN]:
        detail_lines.append(access_loop_count_needs_sign_in(counts[ACCOUNT_STATE_NEEDS_SIGN_IN]))
    if counts[ACCOUNT_STATE_UPDATING]:
        detail_lines.append(access_loop_count_updating(counts[ACCOUNT_STATE_UPDATING]))
    if counts[ACCOUNT_STATE_READY]:
        detail_lines.append(access_loop_count_ready(counts[ACCOUNT_STATE_READY]))
    if counts[ACCOUNT_STATE_NEEDS_ATTENTION]:
        detail_lines.append(
            access_loop_count_needs_attention(counts[ACCOUNT_STATE_NEEDS_ATTENTION])
        )

    if counts[ACCOUNT_STATE_UPDATING]:
        headline = WORKER_ACCESS_LOOP_UPDATING
    elif counts[ACCOUNT_STATE_NEEDS_SIGN_IN]:
        headline = access_loop_count_needs_sign_in(counts[ACCOUNT_STATE_NEEDS_SIGN_IN])
    elif counts[ACCOUNT_STATE_NEEDS_ATTENTION]:
        headline = access_loop_count_needs_attention(counts[ACCOUNT_STATE_NEEDS_ATTENTION])
    elif counts[ACCOUNT_STATE_READY]:
        headline = access_loop_count_ready(counts[ACCOUNT_STATE_READY])
    else:
        headline = "No accounts yet"

    return AccessLoopSummary(
        total=len(presentations),
        needs_sign_in=counts[ACCOUNT_STATE_NEEDS_SIGN_IN],
        updating=counts[ACCOUNT_STATE_UPDATING],
        ready=counts[ACCOUNT_STATE_READY],
        needs_attention=counts[ACCOUNT_STATE_NEEDS_ATTENTION],
        headline=headline,
        detail_lines=detail_lines,
        open_account_center_label=WORKER_OPEN_ACCOUNT_CENTER,
        is_updating=counts[ACCOUNT_STATE_UPDATING] > 0,
    )


def presentations_for_states(
    states: list[AccountState],
    *,
    sync_running: bool = False,
    updating_source: str | None = None,
) -> list[AccountPresentation]:
    effective_source = updating_source if sync_running else None
    return [
        resolve_account_presentation(
            state,
            sync_running=sync_running,
            updating_source=effective_source,
        )
        for state in states
    ]
