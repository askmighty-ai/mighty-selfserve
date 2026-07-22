"""Pure data-currency freshness policy (Milestone 9).

Classifies AccountState-facing data freshness. Does not schedule sessions
(Natural Session / PAM) and does not detect field changes (Change Intelligence).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from mighty.account_presentation import DATA_REFRESH_TTL_HOURS
from mighty.account_state import (
    CONN_CONNECTED,
    DATA_COMPLETE,
    DATA_NONE,
    DATA_PARTIAL,
    FINANCIAL_PROVIDERS,
)

FRESHNESS_FRESH = "fresh"
FRESHNESS_STALE = "stale"
FRESHNESS_UNAVAILABLE = "unavailable"

# Combined product-facing states (freshness ∪ change).
STATE_UNCHANGED = "unchanged"
STATE_REFRESHED_NO_MEANINGFUL = "refreshed_no_meaningful_change"
STATE_MATERIALLY_CHANGED = "materially_changed"
STATE_NEWLY_DISCOVERED = "newly_discovered"
STATE_STALE = "stale"
STATE_UNAVAILABLE = "unavailable"


def data_refresh_ttl_hours(provider: str) -> int:
    if str(provider or "").strip().lower() in FINANCIAL_PROVIDERS:
        return DATA_REFRESH_TTL_HOURS["financial"]
    return DATA_REFRESH_TTL_HOURS["default"]


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass(frozen=True)
class FreshnessDecision:
    freshness: str
    reason: str
    ttl_hours: int


def classify_data_freshness(
    *,
    last_data_refresh: str | None,
    data_status: str,
    connection_state: str = CONN_CONNECTED,
    provider: str = "",
    now: datetime | None = None,
    ttl_hours: int | None = None,
) -> FreshnessDecision:
    """Classify product data currency for an enrolled account."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    ttl = ttl_hours if ttl_hours is not None else data_refresh_ttl_hours(provider)
    status = str(data_status or "").strip().lower()
    conn = str(connection_state or "").strip().lower()

    if status not in {DATA_PARTIAL, DATA_COMPLETE}:
        return FreshnessDecision(
            FRESHNESS_UNAVAILABLE, "no_usable_data", ttl
        )
    if conn and conn != CONN_CONNECTED:
        # Connected is required for "fresh/stale" currency; otherwise unavailable.
        return FreshnessDecision(
            FRESHNESS_UNAVAILABLE, "not_readable", ttl
        )

    refreshed = _parse_iso(last_data_refresh)
    if refreshed is None:
        return FreshnessDecision(
            FRESHNESS_UNAVAILABLE, "missing_refresh_timestamp", ttl
        )

    age = now - refreshed
    if age <= timedelta(hours=ttl):
        return FreshnessDecision(FRESHNESS_FRESH, "within_ttl", ttl)
    return FreshnessDecision(FRESHNESS_STALE, "past_ttl", ttl)


def combine_freshness_and_change(
    *,
    freshness: str,
    change_outcome: str | None,
) -> str:
    """Map freshness + change outcome → one of the six product states."""
    if freshness == FRESHNESS_UNAVAILABLE:
        return STATE_UNAVAILABLE
    if freshness == FRESHNESS_STALE and not change_outcome:
        return STATE_STALE

    outcome = str(change_outcome or "").strip().lower()
    if outcome == STATE_NEWLY_DISCOVERED:
        return STATE_NEWLY_DISCOVERED
    if outcome == STATE_MATERIALLY_CHANGED:
        return STATE_MATERIALLY_CHANGED
    if outcome == STATE_REFRESHED_NO_MEANINGFUL:
        return STATE_REFRESHED_NO_MEANINGFUL
    if freshness == FRESHNESS_STALE:
        return STATE_STALE
    return STATE_UNCHANGED


def freshness_from_account_state(account: Any, *, now: datetime | None = None) -> FreshnessDecision:
    """Convenience wrapper over AccountState-like objects."""
    return classify_data_freshness(
        last_data_refresh=getattr(account, "last_data_refresh", None),
        data_status=str(getattr(account, "data_status", "") or ""),
        connection_state=str(getattr(account, "connection_state", "") or ""),
        provider=str(getattr(account, "provider", "") or ""),
        now=now,
    )
