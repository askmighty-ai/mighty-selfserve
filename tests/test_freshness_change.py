"""End-to-end freshness/change + Attention interaction (Milestone 9)."""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_snapshot import (
    create_account_snapshot_from_extraction,
    ensure_account_snapshot_tables,
    list_account_snapshots,
)
from mighty.account_state import (
    CONN_CONNECTED,
    DATA_COMPLETE,
    DATA_NONE,
    ensure_account_state_tables,
)
from mighty.attention_compiler import (
    compile_attention_candidates,
    compile_data_gap_attention,
)
from mighty.change_store import list_account_changes
from mighty.freshness_change import (
    compute_account_freshness_counters,
    observe_snapshot_refresh,
    safe_observe_snapshot_refresh,
)
from mighty.freshness_metrics import (
    apply_refresh_observation,
    compute_freshness_metrics,
)
from mighty.freshness_policy import (
    STATE_MATERIALLY_CHANGED,
    STATE_NEWLY_DISCOVERED,
    STATE_REFRESHED_NO_MEANINGFUL,
)


def _fields(points: str):
    return [
        {
            "key": "membership_rewards_points",
            "label": "Membership Rewards Points",
            "value": points,
            "_type": "points_balance",
        }
    ]


@pytest.fixture()
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "f.db"))
    conn.row_factory = sqlite3.Row
    ensure_account_snapshot_tables(conn)
    ensure_account_state_tables(conn)
    yield conn
    conn.close()


def test_e2e_refresh_to_change(db):
    first = create_account_snapshot_from_extraction(
        db,
        user_id="u1",
        provider="amex",
        fields=_fields("1000"),
        verified_at="2026-07-22T10:00:00+00:00",
        access_cycle_id="cycle-a",
    )
    assert first is not None
    events = list_account_changes(db, "u1", "amex")
    assert len(events) == 1
    assert events[0].outcome == STATE_NEWLY_DISCOVERED

    second = create_account_snapshot_from_extraction(
        db,
        user_id="u1",
        provider="amex",
        fields=_fields("1500"),
        verified_at="2026-07-22T11:00:00+00:00",
        access_cycle_id="cycle-b",
    )
    assert second is not None
    events = list_account_changes(db, "u1", "amex", meaningful_only=True)
    assert any(e.outcome == STATE_MATERIALLY_CHANGED for e in events)
    assert list_account_snapshots(db, "u1", "amex")  # history via snapshots


def test_quiet_identical_refresh(db):
    create_account_snapshot_from_extraction(
        db,
        user_id="u1",
        provider="amex",
        fields=_fields("1000"),
        verified_at="2026-07-22T10:00:00+00:00",
        access_cycle_id="cycle-a",
    )
    create_account_snapshot_from_extraction(
        db,
        user_id="u1",
        provider="amex",
        fields=_fields("1000"),
        verified_at="2026-07-22T11:00:00+00:00",
        access_cycle_id="cycle-b",
    )
    events = list_account_changes(db, "u1", "amex", include_suppressed=True)
    quiet = [e for e in events if e.outcome == STATE_REFRESHED_NO_MEANINGFUL]
    assert quiet


def test_failure_isolation(db):
    snap = SimpleNamespace(
        snapshot_id="x",
        user_id="u1",
        provider="amex",
        verified_at="2026-07-22T12:00:00+00:00",
        created_at="2026-07-22T12:00:00+00:00",
        access_cycle_id="c",
        evidence_refs=(),
        normalized_fields=_fields("1"),
        metadata={},
    )

    class Boom:
        def execute(self, *a, **k):
            raise RuntimeError("boom")

        def commit(self):
            raise RuntimeError("boom")

    obs = safe_observe_snapshot_refresh(Boom(), prev=None, new=snap)
    assert obs is not None
    assert obs.error


def test_attention_data_gap_cleared_after_complete_data():
    """Change Intelligence does not rank; complete AccountState clears data_gap."""
    gap_account = SimpleNamespace(
        user_id="u1",
        provider="amex",
        connection_state=CONN_CONNECTED,
        data_status=DATA_NONE,
        last_data_refresh=None,
        updated_at="2026-07-22T12:00:00+00:00",
    )
    assert compile_data_gap_attention(gap_account) is not None

    complete = SimpleNamespace(
        user_id="u1",
        provider="amex",
        connection_state=CONN_CONNECTED,
        data_status=DATA_COMPLETE,
        last_data_refresh="2026-07-22T12:00:00+00:00",
        updated_at="2026-07-22T12:00:00+00:00",
    )
    assert compile_data_gap_attention(complete) is None

    # Gather path must not invent a change Attention class from AccountState alone.
    items = compile_attention_candidates(
        account_states=[complete],
        recovery_attention_allowed=frozenset(),
    )
    assert all(i.attention_class.value != "change" for i in items)
    assert not any(i.attention_class.value == "data_gap" for i in items)


def test_provider_capability_loyalty_ttl():
    now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    accounts = [
        SimpleNamespace(
            provider="amex",
            data_status=DATA_COMPLETE,
            connection_state=CONN_CONNECTED,
            last_data_refresh=(now - timedelta(hours=60)).isoformat(),
        ),
        SimpleNamespace(
            provider="delta",
            data_status=DATA_COMPLETE,
            connection_state=CONN_CONNECTED,
            last_data_refresh=(now - timedelta(hours=60)).isoformat(),
        ),
    ]
    counters = compute_account_freshness_counters(accounts, now=now)
    # Amex financial TTL 48h → stale; Delta default 7d → fresh
    assert counters.stale == 1
    assert counters.fresh == 1


def test_metrics_from_observations(db):
    first = create_account_snapshot_from_extraction(
        db,
        user_id="u1",
        provider="amex",
        fields=_fields("1"),
        verified_at="2026-07-22T10:00:00+00:00",
        access_cycle_id="c1",
        metadata={"refresh_started_at": "2026-07-22T09:59:00+00:00"},
    )
    counters = compute_account_freshness_counters(
        [
            SimpleNamespace(
                provider="amex",
                data_status=DATA_COMPLETE,
                connection_state=CONN_CONNECTED,
                last_data_refresh=first.verified_at,
            )
        ],
        now=datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc),
    )
    # Re-observe for metrics helper (already persisted; use direct observe on copies)
    from mighty.account_snapshot import get_latest_successful_snapshot

    latest = get_latest_successful_snapshot(db, "u1", "amex")
    obs = observe_snapshot_refresh(db, prev=None, new=latest, commit=True)
    # second observe of same first-data path may create another newly_discovered row —
    # for metrics we only care apply_refresh_observation works.
    apply_refresh_observation(counters, obs)
    snap = compute_freshness_metrics(counters)
    assert snap.freshness_rate == 1.0
    assert snap.refreshes >= 1
