"""Unit tests for recommendation unlock computation."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.recommendation_unlock_catalog import RECOMMENDATION_TYPES
from mighty.recommendation_unlocks import (
    compute_all_provider_unlocks,
    compute_provider_unlocks,
    is_recommendation_unlocked,
    missing_for_recommendation,
)
from mighty.pipeline_inspector import ensure_pipeline_tables, finalize_run, record_stage, start_run
from mighty.pipeline_stages import PipelineStageId, RunInitiator, RunStatus, StageStatus


def test_payment_due_unlocked_with_statement_balance():
    row = compute_provider_unlocks(
        "amex",
        {"payment_due_date", "statement_balance"},
        display_name="American Express",
    )
    assert "payment_due" in row.unlocked
    payment_blocked = next(
        (b for b in row.blocked if b.recommendation_id == "payment_due"),
        None,
    )
    assert payment_blocked is None


def test_payment_due_unlocked_with_amount_due_or():
    row = compute_provider_unlocks(
        "amex",
        {"payment_due_date", "amount_due"},
    )
    assert "payment_due" in row.unlocked


def test_payment_due_blocked_when_missing_balance_or():
    row = compute_provider_unlocks(
        "amex",
        {"payment_due_date"},
    )
    assert "payment_due" not in row.unlocked
    blocked = next(b for b in row.blocked if b.recommendation_id == "payment_due")
    assert "statement_balance" in blocked.missing_observations
    assert "amount_due" in blocked.missing_observations


def test_expiring_value_or_group_partial():
    rec = RECOMMENDATION_TYPES["expiring_value"]
    assert is_recommendation_unlocked(rec, {"expiration_date", "points_balance"})
    assert not is_recommendation_unlocked(rec, {"expiration_date"})
    missing = missing_for_recommendation(rec, {"expiration_date"})
    assert "rewards_balance" in missing
    assert "points_balance" in missing
    assert "miles_balance" in missing


def test_credit_limit_warning_requires_both():
    rec = RECOMMENDATION_TYPES["credit_limit_warning"]
    assert not is_recommendation_unlocked(rec, {"credit_limit"})
    assert is_recommendation_unlocked(rec, {"credit_limit", "available_credit"})


def test_amex_example_from_spec():
    row = compute_provider_unlocks(
        "amex",
        {"payment_due_date", "statement_balance"},
        display_name="American Express",
    )
    assert "payment_due" in row.unlocked
    assert "payment_due_date" in row.observed
    assert "statement_balance" in row.observed

    expiring = next(b for b in row.blocked if b.recommendation_id == "expiring_value")
    assert "expiration_date" in expiring.missing_observations
    assert "rewards_balance" in expiring.missing_observations

    credit = next(b for b in row.blocked if b.recommendation_id == "credit_limit_warning")
    assert "credit_limit" in credit.missing_observations
    assert "available_credit" in credit.missing_observations


def test_empty_provider_all_blocked():
    row = compute_provider_unlocks("unknown_provider", set())
    assert row.unlocked == []
    assert len(row.blocked) == len(RECOMMENDATION_TYPES)
    assert all(b.missing_observations for b in row.blocked)


def test_provider_level_summary_fields():
    row = compute_provider_unlocks(
        "delta",
        {"tier", "miles_balance", "next_trip"},
        display_name="Delta",
    )
    assert "status_progress" in row.unlocked
    assert "upcoming_trip" in row.unlocked
    assert row.display_name == "Delta"


def test_compute_all_provider_unlocks(pipeline_db):
    run_id = start_run(
        pipeline_db,
        user_id="u1",
        source="amex",
        initiator=RunInitiator.EXTENSION_SYNC.value,
        data_source="extension",
    )
    record_stage(
        pipeline_db,
        run_id,
        PipelineStageId.TRUSTED_OBSERVATIONS.value,
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00",
        status=StageStatus.SUCCESS.value,
        artifacts={
            "trusted_keys": ["payment_due_date", "statement_balance"],
            "trusted_count": 2,
        },
    )
    finalize_run(
        pipeline_db,
        run_id,
        terminal_stage=PipelineStageId.TRUSTED_OBSERVATIONS.value,
        run_status=RunStatus.COMPLETE.value,
    )

    rows = compute_all_provider_unlocks(
        pipeline_db,
        ["amex", "delta"],
        display_names={"amex": "American Express", "delta": "Delta"},
    )
    amex = next(r for r in rows if r.source == "amex")
    delta = next(r for r in rows if r.source == "delta")
    assert "payment_due" in amex.unlocked
    assert delta.unlocked == []


@pytest.fixture()
def pipeline_db(tmp_path):
    db_path = tmp_path / "recommendation_unlocks.db"
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_pipeline_tables(conn)
    yield conn
    conn.close()
