"""Unit tests for provider reliability scorecard."""

import json
import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.pipeline_inspector import ensure_pipeline_tables, finalize_run, record_stage, start_run
from mighty.pipeline_stages import PipelineStageId, RunInitiator, RunStatus, StageStatus
from mighty.provider_reliability_scorecard import (
    collect_stage_failure_reasons,
    compute_provider_reliability_scorecard,
    failure_reason_label,
    top_failure_reasons,
)


@pytest.fixture()
def pipeline_db(tmp_path):
    import sqlite3

    db_path = tmp_path / "scorecard.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_pipeline_tables(conn)
    yield conn
    conn.close()


def _seed_connection(
    db,
    *,
    source="amex",
    status=StageStatus.SUCCESS.value,
    failure_reason=None,
    created_at=None,
):
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
        failure_reason=failure_reason,
    )
    finalize_run(
        db,
        run_id,
        terminal_stage=PipelineStageId.CONNECTION.value,
        run_status=RunStatus.FAILED.value if status == StageStatus.FAILED.value else RunStatus.COMPLETE.value,
    )
    return run_id


def _seed_capture(
    db,
    *,
    source="amex",
    status=StageStatus.SUCCESS.value,
    failure_reason=None,
    created_at=None,
):
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
        PipelineStageId.CAPTURE.value,
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00",
        status=status,
        failure_reason=failure_reason,
    )
    finalize_run(
        db,
        run_id,
        terminal_stage=PipelineStageId.CAPTURE.value,
        run_status=RunStatus.FAILED.value if status == StageStatus.FAILED.value else RunStatus.COMPLETE.value,
    )
    return run_id


def _seed_trusted(db, *, source="amex", keys=None, created_at=None):
    keys = keys or ["payment_due_date"]
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


class TestFailureReasonHelpers:
    def test_failure_reason_label_known(self):
        assert failure_reason_label("login_required") == "Login required"

    def test_collect_stage_failure_reasons(self, pipeline_db):
        _seed_connection(
            pipeline_db,
            source="amex",
            status=StageStatus.FAILED.value,
            failure_reason="login_required",
        )
        _seed_connection(
            pipeline_db,
            source="delta",
            status=StageStatus.FAILED.value,
            failure_reason="login_required",
        )
        _seed_connection(
            pipeline_db,
            source="delta",
            status=StageStatus.FAILED.value,
            failure_reason="session_expired",
        )

        counts = collect_stage_failure_reasons(
            pipeline_db,
            PipelineStageId.CONNECTION.value,
        )
        top = top_failure_reasons(counts, limit=5)
        assert top[0].reason == "login_required"
        assert top[0].count == 2
        assert top[1].reason == "session_expired"


class TestComputeScorecard:
    def test_compute_provider_reliability_scorecard(self, pipeline_db):
        _seed_connection(pipeline_db, source="amex")
        _seed_connection(
            pipeline_db,
            source="delta",
            status=StageStatus.FAILED.value,
            failure_reason="login_required",
        )
        _seed_capture(
            pipeline_db,
            source="delta",
            status=StageStatus.FAILED.value,
            failure_reason="no_data",
        )
        _seed_trusted(pipeline_db, source="amex", keys=["payment_due_date"])

        scorecard = compute_provider_reliability_scorecard(
            pipeline_db,
            ["amex", "delta"],
            {"amex": "credit_card", "delta": "airline"},
            display_names={"amex": "American Express", "delta": "Delta Air Lines"},
            recent_window_start="2020-01-01T00:00:00+00:00",
        )

        assert len(scorecard.providers) == 2
        amex = next(r for r in scorecard.providers if r.source == "amex")
        delta = next(r for r in scorecard.providers if r.source == "delta")
        assert amex.login_success_pct == 100
        assert delta.login_success_pct == 0
        assert scorecard.top_login_failure_reasons[0].reason == "login_required"
        assert scorecard.top_capture_failure_reasons[0].reason == "no_data"
        assert len(scorecard.needs_attention) <= 5
        assert scorecard.needs_attention[0].source == "delta"

    def test_example_scorecard_json(self, pipeline_db):
        _seed_connection(pipeline_db, source="amex")
        _seed_trusted(
            pipeline_db,
            source="amex",
            keys=["payment_due_date", "statement_balance"],
        )

        scorecard = compute_provider_reliability_scorecard(
            pipeline_db,
            ["amex"],
            {"amex": "credit_card"},
            display_names={"amex": "American Express"},
            recent_window_start="2020-01-01T00:00:00+00:00",
        )
        payload = scorecard.to_dict()
        json.dumps(payload)

        assert payload["window_days"] == 14
        assert payload["providers"][0]["source"] == "amex"
        assert payload["providers"][0]["login_success_pct"] == 100
        assert "top_login_failure_reasons" in payload
        assert "most_missing_observations" in payload
        assert len(payload["needs_attention"]) <= 5
