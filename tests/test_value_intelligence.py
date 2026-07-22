"""Opportunity store lifecycle + e2e snapshot→opportunity (Milestone 10)."""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import date
from types import SimpleNamespace

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_snapshot import (
    create_account_snapshot_from_extraction,
    ensure_account_snapshot_tables,
)
from mighty.attention_compiler import compile_attention_candidates
from mighty.opportunity_store import (
    STATE_ACTIVE,
    STATE_DISMISSED,
    STATE_EXPIRED,
    dismiss_opportunity,
    ensure_opportunity_tables,
    list_opportunities,
    reconcile_opportunities,
)
from mighty.value_capability_registry import KIND_EXPIRING_CREDIT
from mighty.value_intelligence import (
    ValueSweepCounters,
    apply_observation,
    reconcile_opportunities_from_snapshot,
    safe_reconcile_opportunities_from_snapshot,
)
from mighty.value_metrics import compute_value_metrics
from mighty.value_policy import compute_opportunity_candidates

TODAY = date(2026, 7, 22)


@pytest.fixture()
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "v.db"))
    conn.row_factory = sqlite3.Row
    ensure_account_snapshot_tables(conn)
    ensure_opportunity_tables(conn)
    yield conn
    conn.close()


def test_lifecycle_expire_when_missing(db):
    policy = compute_opportunity_candidates(
        [
            {
                "key": "dining",
                "label": "Dining Credit",
                "value": "$40 Expires Aug 1, 2026",
                "_type": "cash_credit",
            }
        ],
        provider="amex",
        today=TODAY,
    )
    first = reconcile_opportunities(
        db,
        user_id="u1",
        provider="amex",
        candidates=policy.candidates,
        snapshot_id="s1",
        today=TODAY,
    )
    assert first.generated >= 1
    assert first.active >= 1

    empty = compute_opportunity_candidates([], provider="amex", today=TODAY)
    second = reconcile_opportunities(
        db,
        user_id="u1",
        provider="amex",
        candidates=empty.candidates,
        snapshot_id="s2",
        today=TODAY,
    )
    assert second.expired >= 1
    open_rows = list_opportunities(
        db, "u1", "amex", states=["discovered", "active"]
    )
    assert open_rows == []


def test_dismiss_preserved(db):
    policy = compute_opportunity_candidates(
        [
            {
                "key": "dining",
                "label": "Dining Credit",
                "value": "$40 Expires Aug 1, 2026",
                "_type": "cash_credit",
            }
        ],
        provider="amex",
        today=TODAY,
    )
    reconcile_opportunities(
        db,
        user_id="u1",
        provider="amex",
        candidates=policy.candidates,
        snapshot_id="s1",
        today=TODAY,
    )
    rows = list_opportunities(db, "u1", "amex", states=["discovered", "active"])
    assert rows
    assert dismiss_opportunity(db, "u1", rows[0].opportunity_id)

    again = reconcile_opportunities(
        db,
        user_id="u1",
        provider="amex",
        candidates=policy.candidates,
        snapshot_id="s2",
        today=TODAY,
    )
    assert again.preserved_dismissed >= 1
    dismissed = list_opportunities(db, "u1", "amex", states=[STATE_DISMISSED])
    assert len(dismissed) == 1


def test_duplicate_fingerprint_suppression(db):
    policy = compute_opportunity_candidates(
        [
            {
                "key": "dining",
                "label": "Dining Credit",
                "value": "$40 Expires Aug 1, 2026",
                "_type": "cash_credit",
            }
        ],
        provider="amex",
        today=TODAY,
    )
    a = reconcile_opportunities(
        db,
        user_id="u1",
        provider="amex",
        candidates=policy.candidates,
        snapshot_id="s1",
        today=TODAY,
    )
    b = reconcile_opportunities(
        db,
        user_id="u1",
        provider="amex",
        candidates=policy.candidates,
        snapshot_id="s2",
        today=TODAY,
    )
    assert a.generated >= 1
    assert b.generated == 0
    assert b.updated >= 1
    rows = list_opportunities(db, "u1", "amex")
    fps = [r.fingerprint for r in rows if r.lifecycle_state in {"discovered", "active"}]
    assert len(fps) == len(set(fps))


def test_e2e_snapshot_to_opportunity(db):
    snap = create_account_snapshot_from_extraction(
        db,
        user_id="u1",
        provider="amex",
        fields=[
            {
                "key": "dining_credit",
                "label": "Dining Credit",
                "value": "$40 Expires Aug 15, 2026",
                "_type": "cash_credit",
            }
        ],
        verified_at="2026-07-22T12:00:00+00:00",
        access_cycle_id="cycle-v1",
    )
    assert snap is not None
    rows = list_opportunities(db, "u1", "amex", states=["discovered", "active"])
    assert any(r.kind == KIND_EXPIRING_CREDIT for r in rows)


def test_failure_isolation():
    snap = SimpleNamespace(
        snapshot_id="x",
        user_id="u1",
        provider="amex",
        normalized_fields=[
            {
                "key": "dining",
                "label": "Dining Credit",
                "value": "$40 Expires Aug 1, 2026",
                "_type": "cash_credit",
            }
        ],
    )

    class Boom:
        def execute(self, *a, **k):
            raise RuntimeError("boom")

        def commit(self):
            raise RuntimeError("boom")

    obs = safe_reconcile_opportunities_from_snapshot(Boom(), snapshot=snap)
    assert obs is not None
    assert obs.error


def test_attention_independence():
    """Value Intelligence must not invent Attention ranking classes."""
    items = compile_attention_candidates(recovery_attention_allowed=frozenset())
    assert items == ()


def test_metrics(db):
    snap = SimpleNamespace(
        snapshot_id="s1",
        user_id="u1",
        provider="amex",
        normalized_fields=[
            {
                "key": "dining",
                "label": "Dining Credit",
                "value": "$200 Expires Aug 1, 2026",
                "_type": "cash_credit",
            }
        ],
    )
    obs = reconcile_opportunities_from_snapshot(
        db, snapshot=snap, today=TODAY, commit=True
    )
    counters = ValueSweepCounters()
    apply_observation(counters, obs)
    metrics = compute_value_metrics(counters)
    assert metrics.generated >= 1
    assert metrics.active >= 1
    assert metrics.value_at_risk_total >= 0
