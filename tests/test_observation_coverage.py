"""Unit tests for observation coverage computation."""

import json
import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.observation_catalog import field_keys_to_observations
from mighty.observation_coverage import (
    collect_observed_from_pipeline,
    compute_provider_coverage,
    coverage_percentage,
    missing_observations,
)
from mighty.pipeline_inspector import ensure_pipeline_tables, finalize_run, record_stage, start_run
from mighty.pipeline_stages import PipelineStageId, RunInitiator, RunStatus, StageStatus


def test_coverage_percentage_full():
    assert coverage_percentage(["a", "b"], ["a", "b"]) == 100


def test_coverage_percentage_partial():
    assert coverage_percentage(["a", "b", "c", "d"], ["a", "c"]) == 50


def test_coverage_percentage_empty_expected():
    assert coverage_percentage([], ["a"]) is None


def test_coverage_percentage_zero_observed():
    assert coverage_percentage(["a", "b"], []) == 0


def test_missing_observations():
    assert missing_observations(["a", "b", "c"], ["b"]) == ["a", "c"]


def test_missing_observations_none_missing():
    assert missing_observations(["a", "b"], ["a", "b"]) == []


def test_field_keys_to_observations_maps_known_keys():
    obs = field_keys_to_observations(["elite_status", "points_balance", "unknown_field"])
    assert "tier" in obs
    assert "points_balance" in obs
    assert "unknown_field" not in obs


def test_compute_provider_coverage_amex_example():
    row = compute_provider_coverage(
        "amex",
        category="credit_card",
        observed_observations={"points_balance", "payment_due_date"},
        display_name="American Express",
    )
    assert row.expected == [
        "points_balance",
        "statement_balance",
        "payment_due_date",
        "credit_limit",
    ]
    assert row.observed == ["payment_due_date", "points_balance"]
    assert row.missing == ["statement_balance", "credit_limit"]
    assert row.coverage_pct == 50


def test_compute_provider_coverage_full():
    row = compute_provider_coverage(
        "amex",
        category="credit_card",
        observed_observations={
            "points_balance",
            "statement_balance",
            "payment_due_date",
            "credit_limit",
        },
    )
    assert row.missing == []
    assert row.coverage_pct == 100


def test_compute_provider_coverage_empty_provider():
    row = compute_provider_coverage(
        "unknown_provider",
        category=None,
        observed_observations=set(),
    )
    assert row.expected == []
    assert row.observed == []
    assert row.missing == []
    assert row.coverage_pct is None


def test_collect_observed_from_pipeline(pipeline_db):
    run_id = start_run(
        pipeline_db,
        user_id="u1",
        source="delta",
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
            "trusted_keys": ["elite_status", "points_balance"],
            "trusted_count": 2,
        },
    )
    finalize_run(
        pipeline_db,
        run_id,
        terminal_stage=PipelineStageId.TRUSTED_OBSERVATIONS.value,
        run_status=RunStatus.COMPLETE.value,
    )

    observed = collect_observed_from_pipeline(pipeline_db)
    assert "delta" in observed
    assert "tier" in observed["delta"]
    assert "points_balance" in observed["delta"]


def test_collect_observed_from_pipeline_ignores_failed_stages(pipeline_db):
    run_id = start_run(
        pipeline_db,
        user_id="u1",
        source="united",
        initiator=RunInitiator.EXTENSION_SYNC.value,
    )
    record_stage(
        pipeline_db,
        run_id,
        PipelineStageId.TRUSTED_OBSERVATIONS.value,
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00",
        status=StageStatus.FAILED.value,
        artifacts={"trusted_keys": ["elite_status"]},
    )
    finalize_run(
        pipeline_db,
        run_id,
        terminal_stage=PipelineStageId.TRUSTED_OBSERVATIONS.value,
        run_status=RunStatus.FAILED.value,
    )

    assert "united" not in collect_observed_from_pipeline(pipeline_db)


@pytest.fixture()
def pipeline_db(tmp_path):
    db_path = tmp_path / "observation_coverage.db"
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_pipeline_tables(conn)
    yield conn
    conn.close()
