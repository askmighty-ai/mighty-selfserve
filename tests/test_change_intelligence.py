"""Change intelligence pure + store tests (Milestone 9)."""

from __future__ import annotations

import os
import sqlite3
import sys
from types import SimpleNamespace

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.change_intelligence import (
    KIND_CHANGED,
    diff_snapshots,
    values_equivalent,
)
from mighty.change_store import (
    ensure_change_tables,
    list_account_changes,
    persist_change_event,
)
from mighty.freshness_policy import (
    STATE_MATERIALLY_CHANGED,
    STATE_NEWLY_DISCOVERED,
    STATE_REFRESHED_NO_MEANINGFUL,
)


def _snap(fields, *, provider="amex", snapshot_id="s1"):
    return SimpleNamespace(
        snapshot_id=snapshot_id,
        user_id="u1",
        provider=provider,
        verified_at="2026-07-22T12:00:00+00:00",
        created_at="2026-07-22T12:00:00+00:00",
        access_cycle_id="cycle-1",
        evidence_refs=(),
        normalized_fields=fields,
        metadata={},
    )


def test_values_equivalent_numeric_formatting():
    assert values_equivalent("$1,000", "1000")
    assert not values_equivalent("1000", "1001")


def test_newly_discovered():
    verdict = diff_snapshots(
        None,
        _snap(
            [
                {
                    "key": "points",
                    "label": "Points",
                    "value": "10,000",
                    "_type": "points_balance",
                }
            ]
        ),
    )
    assert verdict.outcome == STATE_NEWLY_DISCOVERED
    assert verdict.meaningful_count == 1
    assert "Amex" in verdict.summary


def test_material_points_change():
    prev = _snap(
        [{"key": "points", "label": "Points", "value": "10000", "_type": "points_balance"}],
        snapshot_id="s0",
    )
    new = _snap(
        [{"key": "points", "label": "Points", "value": "12000", "_type": "points_balance"}],
        snapshot_id="s1",
    )
    verdict = diff_snapshots(prev, new)
    assert verdict.outcome == STATE_MATERIALLY_CHANGED
    assert verdict.deltas[0].kind == KIND_CHANGED
    assert "12000" in verdict.summary


def test_quiet_refresh_non_meaningful_type():
    prev = _snap(
        [{"key": "note", "label": "Note", "value": "a", "_type": "other"}],
        snapshot_id="s0",
    )
    new = _snap(
        [{"key": "note", "label": "Note", "value": "b", "_type": "other"}],
        snapshot_id="s1",
    )
    verdict = diff_snapshots(prev, new)
    assert verdict.outcome == STATE_REFRESHED_NO_MEANINGFUL
    assert verdict.summary == ""


def test_identical_values_are_quiet():
    fields = [
        {"key": "points", "label": "Points", "value": "10000", "_type": "points_balance"}
    ]
    verdict = diff_snapshots(_snap(fields, snapshot_id="s0"), _snap(fields, snapshot_id="s1"))
    assert verdict.outcome == STATE_REFRESHED_NO_MEANINGFUL
    assert verdict.meaningful_count == 0


def test_replay_deterministic():
    prev = _snap(
        [{"key": "points", "label": "Points", "value": "1", "_type": "points_balance"}],
        snapshot_id="s0",
    )
    new = _snap(
        [{"key": "points", "label": "Points", "value": "2", "_type": "points_balance"}],
        snapshot_id="s1",
    )
    a = diff_snapshots(prev, new)
    b = diff_snapshots(prev, new)
    assert a == b
    assert a.change_fingerprint == b.change_fingerprint


@pytest.fixture()
def change_db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "c.db"))
    conn.row_factory = sqlite3.Row
    ensure_change_tables(conn)
    yield conn
    conn.close()


def test_duplicate_suppression(change_db):
    fields = [
        {
            "key": "points",
            "label": "Points",
            "value": "100",
            "_type": "points_balance",
        }
    ]
    prev = _snap(fields, snapshot_id="s0")
    new = _snap(
        [{"key": "points", "label": "Points", "value": "200", "_type": "points_balance"}],
        snapshot_id="s1",
    )
    verdict = diff_snapshots(prev, new)
    first = persist_change_event(
        change_db,
        user_id="u1",
        provider="amex",
        snapshot_id="s1",
        prev_snapshot_id="s0",
        verdict=verdict,
    )
    assert first.outcome == STATE_MATERIALLY_CHANGED
    assert first.suppressed is False

    # Same delta again (e.g. re-extract identical new value) → suppressed
    second = persist_change_event(
        change_db,
        user_id="u1",
        provider="amex",
        snapshot_id="s2",
        prev_snapshot_id="s1",
        verdict=verdict,
    )
    assert second.suppressed is True
    assert second.duplicates_suppressed >= 1
    assert second.outcome == STATE_REFRESHED_NO_MEANINGFUL

    listed = list_account_changes(
        change_db, "u1", "amex", meaningful_only=True, include_suppressed=False
    )
    assert len(listed) == 1
    assert listed[0].change_id == first.change_id
