"""Pure freshness policy tests (Milestone 9)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mighty.account_state import CONN_CONNECTED, CONN_NEEDS_LOGIN, DATA_COMPLETE, DATA_NONE
from mighty.freshness_policy import (
    FRESHNESS_FRESH,
    FRESHNESS_STALE,
    FRESHNESS_UNAVAILABLE,
    STATE_MATERIALLY_CHANGED,
    STATE_NEWLY_DISCOVERED,
    STATE_REFRESHED_NO_MEANINGFUL,
    STATE_STALE,
    STATE_UNAVAILABLE,
    STATE_UNCHANGED,
    classify_data_freshness,
    combine_freshness_and_change,
)


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def test_fresh_within_ttl():
    decision = classify_data_freshness(
        last_data_refresh=(NOW - timedelta(hours=1)).isoformat(),
        data_status=DATA_COMPLETE,
        connection_state=CONN_CONNECTED,
        provider="amex",
        now=NOW,
    )
    assert decision.freshness == FRESHNESS_FRESH


def test_stale_past_ttl_financial():
    decision = classify_data_freshness(
        last_data_refresh=(NOW - timedelta(hours=72)).isoformat(),
        data_status=DATA_COMPLETE,
        connection_state=CONN_CONNECTED,
        provider="amex",
        now=NOW,
        ttl_hours=48,
    )
    assert decision.freshness == FRESHNESS_STALE
    assert decision.reason == "past_ttl"


def test_unavailable_no_data():
    decision = classify_data_freshness(
        last_data_refresh=None,
        data_status=DATA_NONE,
        connection_state=CONN_CONNECTED,
        provider="amex",
        now=NOW,
    )
    assert decision.freshness == FRESHNESS_UNAVAILABLE


def test_unavailable_needs_login():
    decision = classify_data_freshness(
        last_data_refresh=(NOW - timedelta(hours=1)).isoformat(),
        data_status=DATA_COMPLETE,
        connection_state=CONN_NEEDS_LOGIN,
        provider="amex",
        now=NOW,
    )
    assert decision.freshness == FRESHNESS_UNAVAILABLE
    assert decision.reason == "not_readable"


def test_combine_states():
    assert (
        combine_freshness_and_change(
            freshness=FRESHNESS_FRESH,
            change_outcome=STATE_MATERIALLY_CHANGED,
        )
        == STATE_MATERIALLY_CHANGED
    )
    assert (
        combine_freshness_and_change(
            freshness=FRESHNESS_FRESH,
            change_outcome=STATE_REFRESHED_NO_MEANINGFUL,
        )
        == STATE_REFRESHED_NO_MEANINGFUL
    )
    assert (
        combine_freshness_and_change(
            freshness=FRESHNESS_FRESH,
            change_outcome=STATE_NEWLY_DISCOVERED,
        )
        == STATE_NEWLY_DISCOVERED
    )
    assert (
        combine_freshness_and_change(freshness=FRESHNESS_STALE, change_outcome=None)
        == STATE_STALE
    )
    assert (
        combine_freshness_and_change(
            freshness=FRESHNESS_UNAVAILABLE, change_outcome=STATE_MATERIALLY_CHANGED
        )
        == STATE_UNAVAILABLE
    )
    assert (
        combine_freshness_and_change(freshness=FRESHNESS_FRESH, change_outcome=None)
        == STATE_UNCHANGED
    )
