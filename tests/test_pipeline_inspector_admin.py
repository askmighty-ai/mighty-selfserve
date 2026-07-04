"""Tests for pipeline run admin inspector pages."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.pipeline_inspector import (
    ensure_pipeline_tables,
    finalize_run,
    get_run_stages,
    list_recent_runs,
    record_stage,
    start_run,
)
from mighty.pipeline_stages import PipelineStageId, RunInitiator, RunStatus, StageStatus


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_pipeline_admin.db")
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
        email = f"admin_{os.urandom(4).hex()}@test.local"
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    return c, email


def _seed_run(db, *, user_id="user-1", source="delta", run_status=RunStatus.COMPLETE.value):
    run_id = start_run(
        db,
        user_id=user_id,
        source=source,
        initiator=RunInitiator.EXTENSION_SYNC.value,
        data_source="extension",
    )
    record_stage(
        db,
        run_id,
        PipelineStageId.CONNECTION.value,
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00",
        status=StageStatus.SUCCESS.value,
        artifacts={"inferred": True},
    )
    record_stage(
        db,
        run_id,
        PipelineStageId.NAVIGATION.value,
        started_at="2026-01-01T00:00:01+00:00",
        finished_at="2026-01-01T00:00:02+00:00",
        status=StageStatus.SKIPPED.value,
        failure_reason="skipped_after",
        artifacts={"skipped_after": "connection"},
    )
    record_stage(
        db,
        run_id,
        PipelineStageId.CAPTURE.value,
        started_at="2026-01-01T00:00:02+00:00",
        finished_at="2026-01-01T00:00:03+00:00",
        status=StageStatus.FAILED.value,
        failure_reason="no_data",
        artifacts={"raw_text_chars": 0},
    )
    finalize_run(
        db,
        run_id,
        terminal_stage=PipelineStageId.CAPTURE.value,
        terminal_reason="no_data",
        run_status=run_status,
    )
    return run_id


def test_pipeline_runs_list_forbidden_for_non_admin(client):
    c, _ = client
    assert c.get("/admin/pipeline-runs").status_code == 403


def test_pipeline_runs_list_empty_for_admin(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    r = c.get("/admin/pipeline-runs")
    assert r.status_code == 200
    assert b"Admin Debug" in r.data
    assert b"No pipeline runs yet" in r.data


def test_pipeline_runs_list_shows_runs(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    import app as mighty

    with mighty.app.app_context():
        db = mighty.get_db()
        ensure_pipeline_tables(db)
        _seed_run(db, source="delta")
        _seed_run(db, source="united")

    r = c.get("/admin/pipeline-runs")
    assert r.status_code == 200
    assert b"delta" in r.data
    assert b"united" in r.data
    assert b"extension_sync" in r.data
    assert b"failed" in r.data


def test_pipeline_run_detail_forbidden_for_non_admin(client):
    c, _ = client
    assert c.get("/admin/pipeline-runs/abc-123").status_code == 403


def test_pipeline_run_detail_not_found(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    r = c.get("/admin/pipeline-runs/does-not-exist")
    assert r.status_code == 404


def test_pipeline_run_detail_shows_run_and_stages(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    import app as mighty

    with mighty.app.app_context():
        db = mighty.get_db()
        ensure_pipeline_tables(db)
        run_id = _seed_run(db, source="amex")

    r = c.get(f"/admin/pipeline-runs/{run_id}")
    assert r.status_code == 200
    assert run_id.encode() in r.data
    assert b"amex" in r.data
    assert b"extension_sync" in r.data
    assert b"extension" in r.data
    assert b"capture" in r.data
    assert b"no_data" in r.data
    assert b"stage-skipped" in r.data
    assert b"stage-failed" in r.data
    assert b"inferred" in r.data


def test_list_recent_runs_orders_newest_first(pipeline_db):
    run_old = start_run(
        pipeline_db,
        user_id="u1",
        source="a",
        initiator=RunInitiator.MANUAL.value,
    )
    run_new = start_run(
        pipeline_db,
        user_id="u1",
        source="b",
        initiator=RunInitiator.MANUAL.value,
    )
    runs = list_recent_runs(pipeline_db, limit=10)
    assert len(runs) == 2
    assert runs[0]["run_id"] == run_new
    assert runs[1]["run_id"] == run_old


@pytest.fixture()
def pipeline_db(tmp_path):
    db_path = tmp_path / "pipeline_admin_unit.db"
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_pipeline_tables(conn)
    yield conn
    conn.close()
