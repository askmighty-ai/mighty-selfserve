"""Tests for provider pipeline core (PR 1)."""

import json
import os
import secrets
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.pipeline_inspector import (
    abort_pipeline_run,
    ensure_pipeline_tables,
    finalize_run,
    finalize_sync_without_discovery,
    get_run,
    get_run_stages,
    ingest_client_stages,
    new_run_id,
    pipeline_run_guard,
    record_inferred_client_stages,
    record_structured_stage,
    record_trusted_observations_stage,
    start_run,
)
from mighty.pipeline_stages import (
    FAIL_CONNECTOR_MISS,
    FAIL_EXCEPTION,
    FAIL_NOT_ATTEMPTED_ON_SYNC_PATH,
    PipelineStageId,
    RunInitiator,
    RunStatus,
    StageStatus,
)


@pytest.fixture()
def pipeline_db(tmp_path):
    db_path = tmp_path / "pipeline.db"
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_pipeline_tables(conn)
    yield conn
    conn.close()


class TestPipelineTables:
    def test_start_run_creates_row(self, pipeline_db):
        run_id = start_run(
            pipeline_db,
            user_id="user-1",
            source="delta",
            initiator=RunInitiator.EXTENSION_SYNC.value,
            data_source="extension",
        )
        row = get_run(pipeline_db, run_id)
        assert row is not None
        assert row["run_status"] == RunStatus.RUNNING.value
        assert row["source"] == "delta"


class TestInferredClientStages:
    def test_capture_failure_on_empty_payload(self, pipeline_db):
        run_id = new_run_id()
        start_run(
            pipeline_db,
            user_id="user-1",
            source="united",
            initiator=RunInitiator.RAILWAY_SYNC.value,
            data_source="railway",
            run_id=run_id,
        )
        may_continue, terminal = record_inferred_client_stages(
            pipeline_db,
            run_id,
            sync_status="no_data",
            sync_failure_reason="no_data",
            connection_status=None,
            raw_text="",
            items=[],
        )
        assert may_continue is False
        assert terminal == PipelineStageId.NAVIGATION.value
        row = get_run(pipeline_db, run_id)
        assert row["run_status"] == RunStatus.FAILED.value
        assert row["terminal_stage"] == PipelineStageId.NAVIGATION.value

    def test_success_through_capture(self, pipeline_db):
        run_id = new_run_id()
        start_run(
            pipeline_db,
            user_id="user-1",
            source="delta",
            initiator=RunInitiator.EXTENSION_SYNC.value,
            data_source="extension",
            run_id=run_id,
        )
        raw = "=== https://www.delta.com/us/en/my-account/account-summary ===\nDiamond Medallion\n75,000 miles"
        may_continue, terminal = record_inferred_client_stages(
            pipeline_db,
            run_id,
            sync_status="ok",
            sync_failure_reason=None,
            connection_status="connected",
            raw_text=raw,
            items=[],
        )
        assert may_continue is True
        assert terminal is None
        stages = get_run_stages(pipeline_db, run_id)
        assert len(stages) == 3
        assert stages[-1]["stage"] == PipelineStageId.CAPTURE.value
        assert stages[-1]["status"] == StageStatus.SUCCESS.value


class TestTrustedObservations:
    def test_storage_split_detected(self, pipeline_db):
        run_id = new_run_id()
        status, reason = record_trusted_observations_stage(
            pipeline_db,
            run_id,
            trusted_items=[],
            discovered_field_count=2,
            enabled_field_count=2,
            extraction_status="pending",
            items_written=0,
        )
        assert status == StageStatus.FAILED.value
        assert reason == "storage_split"


class TestFinalizeWithoutDiscovery:
    def test_full_pipeline_from_sync_items(self, pipeline_db):
        run_id = new_run_id()
        start_run(
            pipeline_db,
            user_id="user-1",
            source="chase",
            initiator=RunInitiator.EXTENSION_SYNC.value,
            data_source="extension",
            run_id=run_id,
        )
        record_inferred_client_stages(
            pipeline_db,
            run_id,
            sync_status="ok",
            sync_failure_reason=None,
            connection_status="connected",
            raw_text="Ultimate Rewards 45,000",
            items=[{"key": "points_balance", "label": "Ultimate Rewards", "value": "45,000"}],
        )
        finalize_sync_without_discovery(
            pipeline_db,
            run_id,
            items=[{"key": "points_balance", "label": "Ultimate Rewards", "value": "45,000"}],
            extraction_status="complete",
            has_structured_extractor=True,
        )
        row = get_run(pipeline_db, run_id)
        assert row["run_status"] == RunStatus.COMPLETE.value
        assert row["terminal_stage"] == PipelineStageId.TRUSTED_OBSERVATIONS.value
        stages = {s["stage"]: s for s in get_run_stages(pipeline_db, run_id)}
        structured = stages[PipelineStageId.STRUCTURED.value]
        assert structured["status"] == StageStatus.SKIPPED.value
        assert structured["failure_reason"] == FAIL_NOT_ATTEMPTED_ON_SYNC_PATH
        assert stages[PipelineStageId.TRUSTED_OBSERVATIONS.value]["status"] == StageStatus.SUCCESS.value


class TestStructuredStageSemantics:
    def test_sync_without_discovery_skips_structured_when_not_attempted(self, pipeline_db):
        run_id = new_run_id()
        start_run(
            pipeline_db,
            user_id="user-1",
            source="delta",
            initiator=RunInitiator.EXTENSION_SYNC.value,
            data_source="extension",
            run_id=run_id,
        )
        record_inferred_client_stages(
            pipeline_db,
            run_id,
            sync_status="ok",
            sync_failure_reason=None,
            connection_status="connected",
            raw_text="=== https://delta.com/account ===\nPoints 12,000",
            items=[{"key": "points_balance", "label": "Points", "value": "12,000"}],
        )
        finalize_sync_without_discovery(
            pipeline_db,
            run_id,
            items=[{"key": "points_balance", "label": "Points", "value": "12,000"}],
            extraction_status="complete",
            has_structured_extractor=True,
        )
        structured = next(
            s for s in get_run_stages(pipeline_db, run_id)
            if s["stage"] == PipelineStageId.STRUCTURED.value
        )
        assert structured["status"] == StageStatus.SKIPPED.value
        assert structured["failure_reason"] == FAIL_NOT_ATTEMPTED_ON_SYNC_PATH
        assert structured["status"] != StageStatus.FAILED.value

    def test_sync_without_discovery_still_completes_at_trusted_observations(self, pipeline_db):
        run_id = new_run_id()
        start_run(
            pipeline_db,
            user_id="user-1",
            source="amex",
            initiator=RunInitiator.EXTENSION_SYNC.value,
            data_source="extension",
            run_id=run_id,
        )
        record_inferred_client_stages(
            pipeline_db,
            run_id,
            sync_status="ok",
            sync_failure_reason=None,
            connection_status="connected",
            raw_text="Membership Rewards 85,000",
            items=[{"key": "points_balance", "label": "MR Points", "value": "85,000"}],
        )
        finalize_sync_without_discovery(
            pipeline_db,
            run_id,
            items=[{"key": "points_balance", "label": "MR Points", "value": "85,000"}],
            extraction_status="complete",
            has_structured_extractor=True,
        )
        row = get_run(pipeline_db, run_id)
        assert row["run_status"] == RunStatus.COMPLETE.value
        assert row["terminal_stage"] == PipelineStageId.TRUSTED_OBSERVATIONS.value

    def test_connector_miss_only_when_structured_extractor_attempted(self, pipeline_db):
        run_id = new_run_id()
        start_run(
            pipeline_db,
            user_id="user-1",
            source="delta",
            initiator=RunInitiator.INTERCEPT.value,
            data_source="extension",
            run_id=run_id,
        )
        record_structured_stage(
            pipeline_db,
            run_id,
            fields=[],
            has_extractor=True,
            attempted=True,
        )
        structured = next(
            s for s in get_run_stages(pipeline_db, run_id)
            if s["stage"] == PipelineStageId.STRUCTURED.value
        )
        assert structured["status"] == StageStatus.FAILED.value
        assert structured["failure_reason"] == FAIL_CONNECTOR_MISS


class TestClientStageIngest:
    def test_ingest_client_stages(self, pipeline_db):
        run_id = new_run_id()
        ingest_client_stages(
            pipeline_db,
            user_id="user-1",
            source="delta",
            run_id=run_id,
            initiator=RunInitiator.EXTENSION_SYNC.value,
            data_source="extension",
            stages=[
                {
                    "stage": "connection",
                    "started_at": "2026-07-04T10:00:00+00:00",
                    "finished_at": "2026-07-04T10:00:01+00:00",
                    "status": "success",
                    "artifacts": {"session_verified": True},
                }
            ],
        )
        row = get_run(pipeline_db, run_id)
        assert row is not None
        stages = get_run_stages(pipeline_db, run_id)
        assert len(stages) == 1
        assert stages[0]["artifacts"]["session_verified"] is True


class TestPipelineCrashMidRun:
    def test_pipeline_crash_mid_run(self, pipeline_db):
        run_id = new_run_id()
        start_run(
            pipeline_db,
            user_id="user-1",
            source="delta",
            initiator=RunInitiator.INTERCEPT.value,
            data_source="extension",
            run_id=run_id,
        )
        record_inferred_client_stages(
            pipeline_db,
            run_id,
            sync_status="ok",
            sync_failure_reason=None,
            connection_status="connected",
            raw_text="=== https://delta.com/account ===\nDiamond Medallion",
            items=[],
            json_payload_chars=1200,
        )
        record_structured_stage(
            pipeline_db,
            run_id,
            fields=[{"key": "elite_status", "label": "Medallion Status", "value": "Diamond"}],
            has_extractor=True,
        )

        with pytest.raises(RuntimeError):
            with pipeline_run_guard(pipeline_db, run_id):
                raise RuntimeError("simulated worker crash")

        row = get_run(pipeline_db, run_id)
        assert row["run_status"] in (RunStatus.ABORTED.value, RunStatus.FAILED.value)
        assert row["terminal_stage"] == PipelineStageId.STRUCTURED.value
        assert row["terminal_reason"] == FAIL_EXCEPTION
        assert row["finished_at"] is not None

        stages = {s["stage"]: s for s in get_run_stages(pipeline_db, run_id)}
        assert stages[PipelineStageId.INTELLIGENT.value]["status"] == StageStatus.SKIPPED.value
        assert stages[PipelineStageId.TRUSTED_OBSERVATIONS.value]["status"] == StageStatus.SKIPPED.value

    def test_finalize_run_is_idempotent(self, pipeline_db):
        run_id = new_run_id()
        start_run(
            pipeline_db,
            user_id="user-1",
            source="chase",
            initiator=RunInitiator.EXTENSION_SYNC.value,
            data_source="extension",
            run_id=run_id,
        )
        record_inferred_client_stages(
            pipeline_db,
            run_id,
            sync_status="ok",
            sync_failure_reason=None,
            connection_status="connected",
            raw_text="points 1000",
            items=[{"key": "points_balance", "label": "Points", "value": "1,000"}],
        )
        finalize_sync_without_discovery(
            pipeline_db,
            run_id,
            items=[{"key": "points_balance", "label": "Points", "value": "1,000"}],
            extraction_status="complete",
            has_structured_extractor=True,
        )
        row = get_run(pipeline_db, run_id)
        assert row["run_status"] == RunStatus.COMPLETE.value

        changed = finalize_run(
            pipeline_db,
            run_id,
            terminal_stage=PipelineStageId.CAPTURE.value,
            terminal_reason=FAIL_EXCEPTION,
            run_status=RunStatus.FAILED.value,
        )
        assert changed is False
        row_after = get_run(pipeline_db, run_id)
        assert row_after["run_status"] == RunStatus.COMPLETE.value
        assert row_after["terminal_stage"] == PipelineStageId.TRUSTED_OBSERVATIONS.value

        changed_abort = abort_pipeline_run(pipeline_db, run_id)
        assert changed_abort is False
        assert get_run(pipeline_db, run_id)["run_status"] == RunStatus.COMPLETE.value


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_pipeline.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)

    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()

    mighty.app.config["TESTING"] = True
    c = mighty.app.test_client()
    c.get("/signup")
    with c.session_transaction() as sess:
        csrf = sess["_csrf"]
    c.post(
        "/signup",
        data={
            "email": f"pipe_{secrets.token_hex(4)}@test.local",
            "password": "pass12345",
            "_csrf": csrf,
        },
    )
    return c


def test_sync_creates_pipeline_run(client, monkeypatch):
    import app as mighty

    monkeypatch.setattr(mighty, "_claude", None)
    monkeypatch.setattr(mighty, "is_field_discovery_enabled", lambda: False)

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        db = mighty.get_db()
        now = mighty.iso()
        stub = mighty.encrypt_account_data(
            uid,
            {"items": [{"key": "points_balance", "label": "Points", "value": "12,000"}], "sync_status": "ok"},
        )
        db.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, "delta", "", "", "", now, now),
        )
        db.execute(
            "INSERT INTO account_data (user_id, source, display_name, icon, color, data_enc, synced_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, "delta", "Delta", "✈", "#003366", stub, now),
        )
        db.commit()
        api_key = db.execute("SELECT api_key FROM users WHERE id=?", (uid,)).fetchone()["api_key"]

    resp = client.post(
        "/api/data/sync",
        headers={"X-Mighty-Key": api_key, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "source": "delta",
                "sync_source": "extension",
                "data": {
                    "name": "Delta",
                    "items": [{"key": "points_balance", "label": "Points", "value": "12,000"}],
                    "raw_text": "=== https://delta.com/account ===\nPoints 12,000",
                    "sync_status": "ok",
                },
            }
        ),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("pipeline_run_id")

    with mighty.app.app_context():
        run = get_run(mighty.get_db(), body["pipeline_run_id"])
        assert run["run_status"] == RunStatus.COMPLETE.value
        stages = get_run_stages(mighty.get_db(), body["pipeline_run_id"])
        assert len(stages) == 7
