"""
mighty.account_presentation
───────────────────────────
Shared Account Access Loop for Account Center and the extension.

Four user-facing states (exact labels):
  Needs sign in · Updating · Ready · Needs attention
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
from mighty.provider_account import EXTRACTION_PENDING
from mighty.user_copy import (
    ACCOUNT_STATE_CHECKING,
    ACCOUNT_STATE_CTAS,
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

DATA_REFRESH_TTL_HOURS: dict[str, int] = {
    "financial": 48,
    "default": 24 * 7,
}

UPDATING_TTL_MINUTES = 15


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


def _data_refresh_ttl_hours(provider: str) -> int:
    if provider in FINANCIAL_PROVIDERS:
        return DATA_REFRESH_TTL_HOURS["financial"]
    return DATA_REFRESH_TTL_HOURS["default"]


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


def is_recent_data_refresh(
    last_data_refresh: str | None,
    *,
    provider: str = "",
    max_hours: int | None = None,
) -> bool:
    """True when account data was refreshed within the freshness TTL."""
    refreshed = _parse_iso(last_data_refresh)
    if not refreshed:
        return False
    if refreshed.tzinfo is None:
        refreshed = refreshed.replace(tzinfo=timezone.utc)
    if max_hours is None:
        max_hours = _data_refresh_ttl_hours(provider)
    age = datetime.now(timezone.utc) - refreshed
    return age <= timedelta(hours=max_hours)


def is_recent_activity(
    timestamp: str | None,
    *,
    max_minutes: int = UPDATING_TTL_MINUTES,
) -> bool:
    """True when a connect/refresh attempt started within the updating TTL."""
    when = _parse_iso(timestamp)
    if not when:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - when
    return age <= timedelta(minutes=max_minutes)


@dataclass(frozen=True)
class AccountPresentation:
    key: str
    label: str
    cta_label: str
    cta_disabled: bool
    extension_hint: str | None = None


@dataclass
class PresentationDebug:
    """Admin-only fields explaining presentation resolution."""

    why_state: str
    winning_signal: str
    ignored_stale_signals: list[str] = field(default_factory=list)


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


def _presentation_for_key(key: str) -> AccountPresentation:
    if key == ACCOUNT_STATE_NEEDS_SIGN_IN:
        return AccountPresentation(
            key=key,
            label=ACCOUNT_STATE_LABELS[key],
            cta_label=CTA_SIGN_IN,
            cta_disabled=False,
            extension_hint=EXT_ACCOUNT_NEEDS_SIGN_IN_HINT,
        )
    if key == ACCOUNT_STATE_CHECKING:
        return AccountPresentation(
            key=key,
            label=ACCOUNT_STATE_LABELS[key],
            cta_label=ACCOUNT_STATE_CTAS[ACCOUNT_STATE_CHECKING],
            cta_disabled=True,
            extension_hint="Mighty is verifying this account now",
        )
    if key == ACCOUNT_STATE_UPDATING:
        return AccountPresentation(
            key=key,
            label=ACCOUNT_STATE_LABELS[key],
            cta_label=CTA_UPDATING,
            cta_disabled=True,
            extension_hint=EXT_ACCOUNT_UPDATING_HINT,
        )
    if key == ACCOUNT_STATE_READY:
        return AccountPresentation(
            key=key,
            label=ACCOUNT_STATE_LABELS[key],
            cta_label=CTA_VIEW,
            cta_disabled=False,
        )
    return AccountPresentation(
        key=ACCOUNT_STATE_NEEDS_ATTENTION,
        label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_ATTENTION],
        cta_label=CTA_FIX,
        cta_disabled=False,
    )


def has_usable_account_data(state: AccountState) -> bool:
    """Trusted observations or meaningful normalized account details."""
    if state.observations_available:
        return True
    if state.field_count > 0:
        return True
    return state.data_status in {DATA_COMPLETE, DATA_PARTIAL}


def _stale_login_required(state: AccountState, *, recently_verified: bool) -> bool:
    """True when login_required is stale and should not drive presentation."""
    if state.sync_status != "login_required":
        return False
    if recently_verified:
        return True
    if is_recent_data_refresh(state.last_data_refresh, provider=state.provider) and has_usable_account_data(state):
        return True
    if state.connection_state == CONN_CONNECTED:
        return True
    return False


def _collect_ignored_stale_signals(
    state: AccountState,
    *,
    recently_verified: bool,
    recent_data: bool,
) -> list[str]:
    ignored: list[str] = []
    if state.sync_status == "login_required" and _stale_login_required(state, recently_verified=recently_verified):
        ignored.append("sync_status:login_required")
    if state.connection_state == CONN_NEEDS_LOGIN and recently_verified:
        ignored.append("connection_state:needs_login")
    if state.connection_state == CONN_NEEDS_LOGIN and recent_data and has_usable_account_data(state):
        ignored.append("connection_state:needs_login")
    if state.extraction_status == EXTRACTION_PENDING and not is_recent_activity(
        state.last_data_refresh or state.last_verified_at or state.updated_at,
    ):
        ignored.append("extraction_status:pending")
    if state.session_health == SESSION_EXPIRED and recently_verified:
        ignored.append("session_health:expired")
    return ignored


def _has_current_blocking_error(
    state: AccountState,
    *,
    recently_verified: bool,
    recent_data: bool,
) -> bool:
    action = state.next_recommended_action
    if action and action.urgency == "blocker":
        if action.reason == "session_expired" and (recently_verified or _stale_login_required(state, recently_verified=recently_verified)):
            return False
        return True
    if state.sync_status == "no_data" and not recent_data:
        return True
    if state.session_health == SESSION_EXPIRED and not recently_verified and not recent_data:
        return True
    return False


def _is_ready(
    state: AccountState,
    *,
    recently_verified: bool,
    recent_data: bool,
) -> bool:
    if state.connection_state in {CONN_NOT_CONNECTED, CONN_CONNECTING}:
        return False
    if not has_usable_account_data(state):
        return False
    if not recent_data:
        return False
    if _has_current_blocking_error(state, recently_verified=recently_verified, recent_data=recent_data):
        return False
    if state.session_health == SESSION_EXPIRED and not recently_verified:
        return False
    if state.confidence.level == CONFIDENCE_LOW and state.data_status == DATA_PARTIAL:
        return False
    # Fresh success beats stale failure — Ready even if connection_state still says needs_login.
    return True


def _needs_sign_in(
    state: AccountState,
    *,
    recently_verified: bool,
    recent_data: bool,
) -> bool:
    if recently_verified:
        return False
    if recent_data and has_usable_account_data(state):
        return False
    if _stale_login_required(state, recently_verified=recently_verified):
        return False
    if state.connection_state == CONN_NOT_CONNECTED:
        return True
    if state.connection_state == CONN_NEEDS_LOGIN:
        return True
    if state.sync_status == "login_required":
        return True
    return False


def _is_updating(
    state: AccountState,
    *,
    recently_verified: bool,
    recent_data: bool,
    sync_running: bool,
    updating_source: str | None,
) -> bool:
    provider = state.provider
    if sync_running and updating_source == provider:
        return True
    if state.connection_state == CONN_CONNECTING and is_recent_activity(
        state.last_verified_at or state.updated_at,
    ):
        return True
    if state.extraction_status == EXTRACTION_PENDING:
        anchor = state.last_verified_at or state.last_data_refresh or state.updated_at
        if is_recent_activity(anchor) and not (recent_data and has_usable_account_data(state)):
            return True
    if state.connection_state == CONN_CONNECTED and state.data_status == DATA_NONE:
        if recently_verified and is_recent_activity(state.last_verified_at):
            return True
        if state.session_health not in {SESSION_EXPIRED} and is_recent_activity(state.updated_at):
            return True
    return False


def _is_needs_attention(
    state: AccountState,
    *,
    recently_verified: bool,
    recent_data: bool,
) -> bool:
    if recently_verified and recent_data:
        return False
    if _has_current_blocking_error(state, recently_verified=recently_verified, recent_data=recent_data):
        return True
    if state.session_health in (SESSION_EXPIRING, SESSION_EXPIRED) and state.connection_state == CONN_CONNECTED:
        if not recently_verified:
            return True
    if state.connection_state == CONN_CONNECTED and state.data_status == DATA_NONE and not recently_verified:
        return True
    return True


def resolve_account_presentation_with_debug(
    state: AccountState,
    *,
    sync_running: bool = False,
    updating_source: str | None = None,
) -> tuple[AccountPresentation, PresentationDebug]:
    """Map AccountState to Access Loop vocabulary with admin debug metadata."""
    provider = state.provider
    recently_verified = is_recent_session_verification(
        state.last_verified_at, provider=provider,
    )
    recent_data = is_recent_data_refresh(state.last_data_refresh, provider=provider)
    ignored = _collect_ignored_stale_signals(
        state, recently_verified=recently_verified, recent_data=recent_data,
    )

    if _is_ready(state, recently_verified=recently_verified, recent_data=recent_data):
        signal = "fresh_data_with_usable_observations"
        if recently_verified:
            signal = "fresh_data_and_recent_session"
        return _presentation_for_key(ACCOUNT_STATE_READY), PresentationDebug(
            why_state="Ready: fresh data with usable observations and no current blocking error",
            winning_signal=signal,
            ignored_stale_signals=ignored,
        )

    if _is_updating(
        state,
        recently_verified=recently_verified,
        recent_data=recent_data,
        sync_running=sync_running,
        updating_source=updating_source,
    ):
        if sync_running and updating_source == provider:
            signal = "sync_running"
        elif state.extraction_status == EXTRACTION_PENDING:
            signal = "extraction_pending"
        elif state.connection_state == CONN_CONNECTING:
            signal = "connecting"
        else:
            signal = "awaiting_first_data"
        return _presentation_for_key(ACCOUNT_STATE_UPDATING), PresentationDebug(
            why_state="Updating: active refresh or connect attempt within TTL",
            winning_signal=signal,
            ignored_stale_signals=ignored,
        )

    if _needs_sign_in(state, recently_verified=recently_verified, recent_data=recent_data):
        signal = state.connection_state or state.sync_status or "login_required"
        return _presentation_for_key(ACCOUNT_STATE_NEEDS_SIGN_IN), PresentationDebug(
            why_state="Needs sign in: no recent verified session and login is required",
            winning_signal=str(signal),
            ignored_stale_signals=ignored,
        )

    if _is_needs_attention(
        state, recently_verified=recently_verified, recent_data=recent_data,
    ):
        return _presentation_for_key(ACCOUNT_STATE_NEEDS_ATTENTION), PresentationDebug(
            why_state="Needs attention: blocking error or unresolved gap after stale-signal cleanup",
            winning_signal=state.next_recommended_action.reason if state.next_recommended_action else "unresolved",
            ignored_stale_signals=ignored,
        )

    if recently_verified or recent_data:
        return _presentation_for_key(ACCOUNT_STATE_UPDATING), PresentationDebug(
            why_state="Updating: recent session or refresh without usable data yet",
            winning_signal="awaiting_first_observations",
            ignored_stale_signals=ignored,
        )

    return _presentation_for_key(ACCOUNT_STATE_NEEDS_ATTENTION), PresentationDebug(
        why_state="Needs attention: fallback",
        winning_signal="fallback",
        ignored_stale_signals=ignored,
    )


def resolve_account_presentation(
    state: AccountState,
    *,
    sync_running: bool = False,
    updating_source: str | None = None,
) -> AccountPresentation:
    """Map AccountState to the shared Account Access Loop vocabulary."""
    presentation, _debug = resolve_account_presentation_with_debug(
        state,
        sync_running=sync_running,
        updating_source=updating_source,
    )
    return presentation


def attach_presentation_debug(state: AccountState, debug: PresentationDebug) -> AccountState:
    """Attach admin-only debug fields to an AccountState (not persisted)."""
    state.why_state = debug.why_state
    state.winning_signal = debug.winning_signal
    state.ignored_stale_signals = list(debug.ignored_stale_signals)
    return state


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
    extraction_status: str | None = None,
    last_data_refresh: str | None = None,
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
        last_data_refresh=last_data_refresh,
        observations_available=[],
        field_count=1 if has_meaningful_data else 0,
        next_recommended_action=None,
        confidence=Confidence(level="high", score=90, factors=ConfidenceFactors()),
        status_line="",
        is_actionable=False,
        updated_at=datetime.now(timezone.utc).isoformat(),
        sync_status=sync_status,
        extraction_status=extraction_status,
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
        ACCOUNT_STATE_CHECKING: 0,
        ACCOUNT_STATE_READY: 0,
        ACCOUNT_STATE_NEEDS_ATTENTION: 0,
    }
    for presentation in presentations:
        counts[presentation.key] = counts.get(presentation.key, 0) + 1

    # Checking is session verification in progress — never "needs sign in".
    updating_like = counts[ACCOUNT_STATE_UPDATING] + counts[ACCOUNT_STATE_CHECKING]

    detail_lines: list[str] = []
    if counts[ACCOUNT_STATE_NEEDS_SIGN_IN]:
        detail_lines.append(access_loop_count_needs_sign_in(counts[ACCOUNT_STATE_NEEDS_SIGN_IN]))
    if updating_like:
        detail_lines.append(access_loop_count_updating(updating_like))
    if counts[ACCOUNT_STATE_READY]:
        detail_lines.append(access_loop_count_ready(counts[ACCOUNT_STATE_READY]))
    if counts[ACCOUNT_STATE_NEEDS_ATTENTION]:
        detail_lines.append(
            access_loop_count_needs_attention(counts[ACCOUNT_STATE_NEEDS_ATTENTION])
        )

    if updating_like:
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
        updating=updating_like,
        ready=counts[ACCOUNT_STATE_READY],
        needs_attention=counts[ACCOUNT_STATE_NEEDS_ATTENTION],
        headline=headline,
        detail_lines=detail_lines,
        open_account_center_label=WORKER_OPEN_ACCOUNT_CENTER,
        is_updating=updating_like > 0,
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
