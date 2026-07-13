"""
mighty.control_tower
────────────────────
Dashboard Control Tower presentation — product language over canonical access state.

Presentation only. Does not change readiness, session evidence, extraction,
snapshots, provider adapters, recommendations, or lifecycle logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from mighty.customer_account_access import (
    BG_AWAITING_FIRST,
    BG_EXTRACTING,
    BG_FAILED,
    BG_NONE,
    BG_TIMED_OUT,
    BG_VERIFICATION_QUEUED,
    BG_VERIFYING,
    CustomerAccountAccessView,
)
from mighty import user_copy

# Current activity (product language)
ACTIVITY_WATCHING = "Watching"
ACTIVITY_REFRESHING = "Refreshing account"
ACTIVITY_CHECKING_SIGN_IN = "Checking sign-in"
ACTIVITY_WAITING_FIRST = "Waiting for first visit"
ACTIVITY_WAITING_FOR_YOU = "Waiting for you"
ACTIVITY_IDLE = "Idle"

# Card primary status (product language)
STATUS_WATCHING = "✓ Watching"
STATUS_CONNECTED = "✓ Connected"
STATUS_SIGN_IN = "⚠ Sign in required"
STATUS_WAITING_FIRST = "Waiting for first verification"
STATUS_CHECKING = "✓ Connected"
STATUS_NEEDS_ATTENTION = "Needs attention"

_REFRESHING_BG = frozenset({
    BG_VERIFYING,
    BG_EXTRACTING,
    BG_VERIFICATION_QUEUED,
})


def current_activity(view: CustomerAccountAccessView) -> str:
    """Map canonical background/readiness to Current activity labels."""
    if view.user_action_required or view.readiness == "signed_out":
        return ACTIVITY_WAITING_FOR_YOU
    bg = view.background_work
    if bg in _REFRESHING_BG:
        return ACTIVITY_REFRESHING
    if bg == BG_AWAITING_FIRST:
        return ACTIVITY_WAITING_FIRST
    if view.readiness == "checking":
        if view.live_access == "Checking":
            return ACTIVITY_CHECKING_SIGN_IN
        return ACTIVITY_REFRESHING
    if bg in (BG_FAILED, BG_TIMED_OUT):
        return ACTIVITY_IDLE
    if view.readiness == "ready":
        return ACTIVITY_WATCHING
    if bg == BG_NONE:
        return ACTIVITY_IDLE
    return ACTIVITY_IDLE


def card_status_label(view: CustomerAccountAccessView) -> str:
    """Primary status line on a Control Tower account card."""
    if view.user_action_required or view.readiness == "signed_out":
        return STATUS_SIGN_IN
    if view.readiness == "ready":
        if view.background_work in _REFRESHING_BG or view.background_verification:
            return STATUS_CONNECTED
        return STATUS_WATCHING
    if view.readiness == "checking":
        return STATUS_CHECKING
    if view.background_work == BG_AWAITING_FIRST or view.readiness == "unverified":
        return STATUS_WAITING_FIRST
    if view.canonical_status == "error":
        return STATUS_NEEDS_ATTENTION
    return view.status_label


def card_meaning(view: CustomerAccountAccessView) -> str:
    """Product meaning for the visible card (not developer jargon)."""
    if view.user_action_required or view.readiness == "signed_out":
        return user_copy.TOWER_MEANING_SIGN_IN
    if view.readiness == "ready":
        if view.background_work in _REFRESHING_BG or view.background_verification:
            return user_copy.TOWER_MEANING_REFRESHING
        return user_copy.TOWER_MEANING_WATCHING
    if view.readiness == "checking":
        return user_copy.TOWER_MEANING_CHECKING
    if view.background_work == BG_AWAITING_FIRST or view.readiness == "unverified":
        return user_copy.TOWER_MEANING_WAITING_FIRST
    if view.canonical_status == "error":
        return user_copy.TOWER_MEANING_ATTENTION
    return view.meaning


def action_required_label(view: CustomerAccountAccessView) -> str:
    if view.user_action_required or view.readiness == "signed_out":
        return user_copy.TOWER_ACTION_SIGN_IN
    if view.canonical_status == "error":
        return user_copy.TOWER_ACTION_NEEDED
    return user_copy.TOWER_ACTION_NONE


def last_verified_label(view: CustomerAccountAccessView) -> str:
    if view.readiness == "ready" or view.last_confirmed_at:
        return user_copy.TOWER_LAST_VERIFIED
    if view.user_action_required or view.readiness == "signed_out":
        return user_copy.TOWER_LAST_SUCCESSFUL
    return user_copy.TOWER_LAST_VERIFIED


def is_watching(view: CustomerAccountAccessView) -> bool:
    return view.readiness == "ready" and not view.user_action_required


def is_refreshing(view: CustomerAccountAccessView) -> bool:
    if view.user_action_required or view.readiness == "signed_out":
        return False
    if view.background_work in _REFRESHING_BG or view.background_verification:
        return True
    return view.readiness == "checking"


def is_waiting(view: CustomerAccountAccessView) -> bool:
    if view.user_action_required or view.readiness == "signed_out":
        return False
    if is_watching(view) or is_refreshing(view):
        return False
    return (
        view.background_work == BG_AWAITING_FIRST
        or view.readiness == "unverified"
        or view.live_access == "Unknown"
    )


def is_needs_you(view: CustomerAccountAccessView) -> bool:
    return bool(
        view.user_action_required
        or view.readiness == "signed_out"
        or view.canonical_status == "error"
    )


@dataclass
class ControlTowerSummary:
    """Canonical Control Tower buckets — shared by hero, summary, and system health."""

    watching_names: list[str] = field(default_factory=list)
    refreshing_names: list[str] = field(default_factory=list)
    waiting_names: list[str] = field(default_factory=list)
    needs_you_names: list[str] = field(default_factory=list)
    active_refresh_label: str | None = None

    @property
    def watching_count(self) -> int:
        return len(self.watching_names)

    @property
    def refreshing_count(self) -> int:
        return len(self.refreshing_names)

    @property
    def waiting_count(self) -> int:
        return len(self.waiting_names)

    @property
    def needs_you_count(self) -> int:
        return len(self.needs_you_names)

    @property
    def needs_attention(self) -> bool:
        return self.needs_you_count > 0

    @property
    def has_background_work(self) -> bool:
        return self.refreshing_count > 0 or self.waiting_count > 0

    def hero_headline(self) -> str:
        if self.needs_attention:
            return user_copy.TOWER_HERO_NEEDS_YOU
        if self.watching_count > 0:
            return user_copy.TOWER_HERO_WATCHING
        if self.refreshing_count > 0:
            return user_copy.TOWER_HERO_WORKING
        if self.waiting_count > 0:
            return user_copy.TOWER_HERO_WAITING
        return user_copy.TOWER_HERO_WATCHING

    def attention_line(self) -> str:
        if self.needs_attention:
            return user_copy.TOWER_ATTENTION_NEEDED
        return user_copy.TOWER_ATTENTION_NONE

    def hero_lines(self) -> list[str]:
        """Structured status lines for the system-status hero body."""
        lines: list[str] = []
        if self.watching_count == 1:
            lines.append(f"✓ {self.watching_names[0]}")
        elif self.watching_count > 1:
            lines.append(
                f"✓ {self.watching_count} accounts connected"
            )
        if self.refreshing_count == 1:
            lines.append(f"⟳ Refreshing {self.refreshing_names[0]}")
        elif self.refreshing_count > 1:
            lines.append(
                f"⟳ {self.refreshing_count} accounts being verified"
            )
        if self.waiting_count == 1:
            lines.append(f"· Waiting on {self.waiting_names[0]}")
        elif self.waiting_count > 1:
            lines.append(f"· {self.waiting_count} accounts waiting")
        if self.needs_you_count == 1:
            lines.append(f"⚠ {self.needs_you_names[0]} needs you")
        elif self.needs_you_count > 1:
            lines.append(f"⚠ {self.needs_you_count} accounts need you")
        if self.active_refresh_label and self.refreshing_count == 0:
            lines.append(f"Current activity: {self.active_refresh_label}")
        return lines


def build_control_tower_summary(
    views: Sequence[CustomerAccountAccessView],
    *,
    updating_display_name: str | None = None,
) -> ControlTowerSummary:
    watching: list[str] = []
    refreshing: list[str] = []
    waiting: list[str] = []
    needs_you: list[str] = []

    for view in views:
        name = view.display_name
        if is_needs_you(view):
            needs_you.append(name)
            continue
        if is_watching(view):
            watching.append(name)
            # Ready accounts can also be refreshing in the background.
            if is_refreshing(view):
                refreshing.append(name)
            continue
        if is_refreshing(view):
            refreshing.append(name)
            continue
        if is_waiting(view):
            waiting.append(name)
            continue
        waiting.append(name)

    active = None
    if updating_display_name:
        active = ACTIVITY_REFRESHING
    elif refreshing:
        active = ACTIVITY_REFRESHING

    return ControlTowerSummary(
        watching_names=watching,
        refreshing_names=refreshing,
        waiting_names=waiting,
        needs_you_names=needs_you,
        active_refresh_label=active,
    )


def build_control_tower_from_statuses(
    accounts: Sequence[Any],
    *,
    updating_display_name: str | None = None,
) -> ControlTowerSummary:
    """Prefer access views; fall back to canonical AccountStatus buckets."""
    views = [
        a.customer_access
        for a in accounts
        if getattr(a, "customer_access", None) is not None
    ]
    if views:
        return build_control_tower_summary(
            views,
            updating_display_name=updating_display_name,
        )

    watching: list[str] = []
    refreshing: list[str] = []
    waiting: list[str] = []
    needs_you: list[str] = []
    for acct in accounts:
        status = getattr(acct, "status", "") or ""
        name = getattr(acct, "display_name", "") or "Account"
        if status == "needs_login" or status == "error":
            needs_you.append(name)
        elif status == "up_to_date":
            watching.append(name)
        elif status in ("updating", "checking"):
            refreshing.append(name)
        else:
            waiting.append(name)

    active = ACTIVITY_REFRESHING if (updating_display_name or refreshing) else None
    return ControlTowerSummary(
        watching_names=watching,
        refreshing_names=refreshing,
        waiting_names=waiting,
        needs_you_names=needs_you,
        active_refresh_label=active,
    )


def why_rows(
    view: CustomerAccountAccessView,
    *,
    include_debug: bool = False,
) -> list[tuple[str, str]]:
    """Implementation details for the expandable Why? section."""
    rows: list[tuple[str, str]] = [
        ("Discovered from", view.discovered_from),
        (user_copy.ACCESS_PRIVATE_DATA_PREFIX, view.private_data_label),
        ("Background", view.background_work),
        ("Evidence source", view.evidence_source or "—"),
        ("Live access", view.live_access),
        ("Readiness", view.readiness or "—"),
    ]
    if include_debug:
        rows.extend(view.debug_rows())
    return rows
