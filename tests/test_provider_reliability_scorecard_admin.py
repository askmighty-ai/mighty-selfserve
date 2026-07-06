"""Route tests for provider reliability scorecard admin page."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.pipeline_inspector import ensure_pipeline_tables, finalize_run, record_stage, start_run
from mighty.pipeline_stages import PipelineStageId, RunInitiator, RunStatus, StageStatus


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_scorecard_admin.db")
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


def _seed_pipeline(db):
    ensure_pipeline_tables(db)
    run_id = start_run(
        db,
        user_id="u1",
        source="amex",
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
    )
    record_stage(
        db,
        run_id,
        PipelineStageId.CAPTURE.value,
        started_at="2026-01-01T00:00:01+00:00",
        finished_at="2026-01-01T00:00:02+00:00",
        status=StageStatus.FAILED.value,
        failure_reason="no_data",
    )
    finalize_run(
        db,
        run_id,
        terminal_stage=PipelineStageId.CAPTURE.value,
        run_status=RunStatus.FAILED.value,
    )


def test_provider_reliability_scorecard_forbidden_for_non_admin(client):
    c, _ = client
    assert c.get("/admin/provider-reliability-scorecard").status_code == 403


def test_provider_reliability_scorecard_loads_for_admin(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    r = c.get("/admin/provider-reliability-scorecard")
    assert r.status_code == 200
    assert b"Provider Reliability Scorecard" in r.data
    assert b"Needs engineering attention" in r.data
    assert b"Top login failure reasons" in r.data
    assert b"Most commonly missing observations" in r.data


def test_provider_reliability_scorecard_shows_failure_reasons(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    import app as mighty

    with mighty.app.app_context():
        _seed_pipeline(mighty.get_db())

    r = c.get("/admin/provider-reliability-scorecard")
    assert r.status_code == 200
    assert b"no_data" in r.data or b"No data captured" in r.data
