"""Unit tests for provider readiness benchmark."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.capture_capability import compute_provider_capability
from mighty.observation_coverage import compute_provider_coverage
from mighty.pipeline_inspector import ensure_pipeline_tables, finalize_run, record_stage, start_run
from mighty.pipeline_stages import PipelineStageId, RunInitiator, RunStatus, StageStatus
from mighty.provider_benchmark import (
    ConnectionStats,
    attention_priority,
    capture_score_from_capability,
    collect_connection_stats_from_pipeline,
    compute_all_provider_benchmarks,
    compute_provider_benchmark,
    login_score_from_stats,
    observation_score_from_coverage,
    readiness_score,
    recommendation_score_from_unlocks,
)
from mighty.recommendation_unlocks import compute_provider_unlocks


@pytest.fixture()
def pipeline_db(tmp_path):
    import sqlite3

    db_path = tmp_path / "benchmark.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_pipeline_tables(conn)
    yield conn
    conn.close()


def _seed_connection(db, *, source="amex", status=StageStatus.SUCCESS.value, created_at=None):
    run_id = start_run(
        db,
        user_id="u1",
        source=source,
        initiator=RunInitiator.EXTENSION_SYNC.value,
        data_source="extension",
    )
    if created_at:
        db.execute(
            "UPDATE pipeline_runs SET created_at = ? WHERE run_id = ?",
            (created_at, run_id),
        )
        db.commit()
    record_stage(
        db,
        run_id,
        PipelineStageId.CONNECTION.value,
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00",
        status=status,
    )
    finalize_run(
        db,
        run_id,
        terminal_stage=PipelineStageId.CONNECTION.value,
        run_status=RunStatus.COMPLETE.value,
    )
    return run_id


def _seed_trusted(db, *, source="amex", keys=None, created_at=None):
    keys = keys or ["payment_due_date", "statement_balance"]
    run_id = start_run(
        db,
        user_id="u1",
        source=source,
        initiator=RunInitiator.EXTENSION_SYNC.value,
        data_source="extension",
    )
    if created_at:
        db.execute(
            "UPDATE pipeline_runs SET created_at = ? WHERE run_id = ?",
            (created_at, run_id),
        )
        db.commit()
    record_stage(
        db,
        run_id,
        PipelineStageId.TRUSTED_OBSERVATIONS.value,
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00",
        status=StageStatus.SUCCESS.value,
        artifacts={"trusted_keys": keys},
    )
    finalize_run(
        db,
        run_id,
        terminal_stage=PipelineStageId.TRUSTED_OBSERVATIONS.value,
        run_status=RunStatus.COMPLETE.value,
    )
    return run_id


class TestScoreFunctions:
    def test_login_score_all_success(self):
        assert login_score_from_stats(ConnectionStats(total=4, success=4)) == 100

    def test_login_score_partial(self):
        assert login_score_from_stats(ConnectionStats(total=4, success=3)) == 75

    def test_login_score_no_runs(self):
        assert login_score_from_stats(ConnectionStats()) == 0

    def test_capture_score(self):
        cap = compute_provider_capability("amex", signals=None, display_name="Amex")
        score = capture_score_from_capability(cap)
        assert score == 0

    def test_observation_score(self):
        cov = compute_provider_coverage(
            "amex",
            category="credit_card",
            observed_observations={"points_balance", "payment_due_date"},
        )
        assert observation_score_from_coverage(cov) == 50

    def test_recommendation_score(self):
        unlocks = compute_provider_unlocks("amex", {"payment_due_date", "statement_balance"})
        score = recommendation_score_from_unlocks(unlocks)
        assert 0 <= score <= 100

    def test_readiness_weighted_average(self):
        assert readiness_score(login=100, capture=80, observation=60, recommendation=40) == 70


class TestConnectionStats:
    def test_collect_connection_stats(self, pipeline_db):
        _seed_connection(pipeline_db, source="amex", status=StageStatus.SUCCESS.value)
        _seed_connection(pipeline_db, source="amex", status=StageStatus.FAILED.value)
        _seed_connection(pipeline_db, source="delta", status=StageStatus.SUCCESS.value)

        stats = collect_connection_stats_from_pipeline(pipeline_db)
        assert stats["amex"].total == 2
        assert stats["amex"].success == 1
        assert stats["delta"].success == 1


class TestComputeBenchmark:
    def test_compute_provider_benchmark(self, pipeline_db):
        _seed_connection(pipeline_db, source="amex")
        _seed_trusted(pipeline_db, source="amex", keys=["payment_due_date", "statement_balance"])

        rows = compute_all_provider_benchmarks(
            pipeline_db,
            ["amex"],
            {"amex": "credit_card"},
            display_names={"amex": "American Express"},
            trend_cutoff="2099-01-01T00:00:00+00:00",
        )
        assert len(rows) == 1
        row = rows[0]
        assert row.source == "amex"
        assert row.display_name == "American Express"
        assert row.login_score == 100
        assert row.observation_score == 50
        assert 0 <= row.readiness_score <= 100

    def test_trend_delta_improved(self, pipeline_db):
        _seed_trusted(
            pipeline_db,
            source="amex",
            keys=["payment_due_date"],
            created_at="2020-01-01T00:00:00+00:00",
        )
        _seed_trusted(
            pipeline_db,
            source="amex",
            keys=["payment_due_date", "statement_balance", "points_balance", "credit_limit"],
            created_at="2026-06-01T00:00:00+00:00",
        )

        rows = compute_all_provider_benchmarks(
            pipeline_db,
            ["amex"],
            {"amex": "credit_card"},
            trend_cutoff="2026-01-01T00:00:00+00:00",
        )
        assert rows[0].trend_delta is not None
        assert rows[0].trend_delta > 0

    def test_attention_priority(self):
        healthy = compute_provider_benchmark(
            "amex",
            connection_stats=ConnectionStats(total=10, success=10),
            capability=compute_provider_capability("amex", signals=None),
            coverage=compute_provider_coverage(
                "amex",
                category="credit_card",
                observed_observations={"points_balance", "payment_due_date", "statement_balance", "credit_limit"},
            ),
            unlocks=compute_provider_unlocks(
                "amex",
                {"points_balance", "payment_due_date", "statement_balance", "credit_limit"},
            ),
            prior_readiness=90,
        )
        weak = compute_provider_benchmark(
            "delta",
            connection_stats=ConnectionStats(total=10, success=2),
            capability=compute_provider_capability("delta", signals=None),
            coverage=compute_provider_coverage("delta", category="airline", observed_observations=set()),
            unlocks=compute_provider_unlocks("delta", set()),
            prior_readiness=30,
        )
        assert attention_priority(weak) > attention_priority(healthy)
