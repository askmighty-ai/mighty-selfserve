"""Route tests for provider benchmark admin page."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.pipeline_inspector import ensure_pipeline_tables, finalize_run, record_stage, start_run
from mighty.pipeline_stages import PipelineStageId, RunInitiator, RunStatus, StageStatus


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_benchmark_admin.db")
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
        PipelineStageId.TRUSTED_OBSERVATIONS.value,
        started_at="2026-01-01T00:00:01+00:00",
        finished_at="2026-01-01T00:00:02+00:00",
        status=StageStatus.SUCCESS.value,
        artifacts={"trusted_keys": ["payment_due_date", "statement_balance"]},
    )
    finalize_run(
        db,
        run_id,
        terminal_stage=PipelineStageId.TRUSTED_OBSERVATIONS.value,
        run_status=RunStatus.COMPLETE.value,
    )


def test_provider_benchmark_forbidden_for_non_admin(client):
    c, _ = client
    assert c.get("/admin/provider-benchmark").status_code == 403


def test_provider_benchmark_loads_for_admin(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    r = c.get("/admin/provider-benchmark")
    assert r.status_code == 200
    assert b"Provider Benchmark" in r.data
    assert b"Scoring formula" in r.data
    assert b"Needs attention first" in r.data


def test_provider_benchmark_shows_provider_rows(client, monkeypatch):
    c, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    import app as mighty

    with mighty.app.app_context():
        _seed_pipeline(mighty.get_db())

    r = c.get("/admin/provider-benchmark")
    assert r.status_code == 200
    assert b"American Express" in r.data or b"amex" in r.data
