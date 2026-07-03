"""Tests for prompt evaluation tooling."""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.prompt_eval import (
    FixtureEvalResult,
    aggregate_fixture_metrics,
    collect_prompt_version_metrics,
    compare_prompt_versions,
    get_production_metrics,
    is_validation_failure,
    load_fixture_cases,
    score_extraction_completeness,
)


@pytest.fixture
def prompt_eval_db(tmp_path):
    db_path = tmp_path / "prompt_eval.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE ai_request_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT NOT NULL,
            provider            TEXT NOT NULL,
            model               TEXT NOT NULL,
            cache_hit           INTEGER NOT NULL DEFAULT 0,
            latency_ms          REAL NOT NULL DEFAULT 0,
            prompt_chars        INTEGER NOT NULL DEFAULT 0,
            completion_chars    INTEGER NOT NULL DEFAULT 0,
            estimated_tokens    INTEGER NOT NULL DEFAULT 0,
            estimated_cost_usd  REAL,
            failure_reason      TEXT,
            prompt_id           TEXT,
            prompt_version      TEXT,
            source              TEXT
        )
        """
    )
    conn.commit()
    yield conn
    conn.close()


class TestValidationFailureDetection:
    @pytest.mark.parametrize(
        "reason,expected",
        [
            ("Field at index 0 is not an object", True),
            ("Gemini returned invalid JSON", True),
            ("timeout", False),
            (None, False),
        ],
    )
    def test_is_validation_failure(self, reason, expected):
        assert is_validation_failure(reason) is expected


class TestExtractionCompleteness:
    def test_perfect_score(self):
        spec = {
            "min_fields": 2,
            "required_keys": ["points"],
            "must_contain_values": ["1,000"],
        }
        fields = [
            {"key": "points_balance", "label": "Points", "value": "1,000"},
            {"key": "status", "label": "Status", "value": "Gold"},
        ]
        score, issues = score_extraction_completeness(fields, spec)
        assert score == 100.0
        assert issues == []

    def test_partial_score(self):
        spec = {"required_keys": ["missing_key"], "must_contain_values": ["abc"]}
        score, issues = score_extraction_completeness([], spec)
        assert score == 0.0
        assert len(issues) == 2


class TestProductionMetrics:
    def test_aggregates_by_prompt_version(self, prompt_eval_db):
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            ("field_discovery", "1.0.0", 100.0, 0.00001, None),
            ("field_discovery", "1.0.0", 200.0, 0.00002, "timeout"),
            ("field_discovery_v2", "2.0.0", 150.0, 0.00003, "Field at index 0 is not an object"),
        ]
        for prompt_id, version, latency, cost, failure in rows:
            prompt_eval_db.execute(
                """
                INSERT INTO ai_request_log (
                    created_at, provider, model, cache_hit, latency_ms,
                    prompt_chars, completion_chars, estimated_tokens,
                    estimated_cost_usd, failure_reason, prompt_id, prompt_version, source
                ) VALUES (?, 'openai', 'gpt-5.4-mini', 0, ?, 100, 50, 38, ?, ?, ?, ?, 'delta')
                """,
                (now, latency, cost, failure, prompt_id, version),
            )
        prompt_eval_db.commit()

        metrics = get_production_metrics(prompt_eval_db, days=None)
        v1 = metrics[("field_discovery", "1.0.0")]
        assert v1["request_count"] == 2
        assert v1["avg_latency_ms"] == 150.0
        assert v1["success_rate"] == 50.0
        assert v1["validation_failures"] == 0
        assert v1["total_failures"] == 1

        v2 = metrics[("field_discovery_v2", "2.0.0")]
        assert v2["validation_failures"] == 1


class TestComparePromptVersions:
    def test_includes_registered_prompts_without_logs(self, prompt_eval_db):
        rows = compare_prompt_versions(prompt_eval_db, days=30)
        labels = {r.version_label for r in rows}
        assert "field_discovery@1.0.0" in labels
        assert "field_discovery_v2@2.0.0" in labels

    def test_merges_fixture_results(self, prompt_eval_db):
        fixture_results = {
            "field_discovery": [
                FixtureEvalResult(
                    fixture="delta_companion_cert.txt",
                    source="delta",
                    prompt_id="field_discovery",
                    prompt_version="1.0.0",
                    completeness_pct=92.5,
                    validation_failed=False,
                    field_count=5,
                ),
                FixtureEvalResult(
                    fixture="marriott_free_night.txt",
                    source="marriott",
                    prompt_id="field_discovery",
                    prompt_version="1.0.0",
                    completeness_pct=87.5,
                    validation_failed=False,
                    field_count=4,
                ),
            ],
        }
        rows = collect_prompt_version_metrics(
            prompt_eval_db,
            days=30,
            fixture_results=fixture_results,
        )
        row = next(r for r in rows if r.prompt_id == "field_discovery")
        assert row.extraction_completeness == 90.0
        assert row.fixture_count == 2


class TestFixtureCases:
    def test_loads_expected_fixtures(self):
        cases = load_fixture_cases()
        assert cases
        names = {name for name, _text, _spec in cases}
        assert "delta_companion_cert.txt" in names


class TestAggregateFixtureMetrics:
    def test_average_completeness(self):
        results = [
            FixtureEvalResult(
                fixture="a.txt",
                source="delta",
                prompt_id="field_discovery",
                prompt_version="1.0.0",
                completeness_pct=80.0,
                validation_failed=False,
            ),
            FixtureEvalResult(
                fixture="b.txt",
                source="marriott",
                prompt_id="field_discovery",
                prompt_version="1.0.0",
                completeness_pct=100.0,
                validation_failed=True,
            ),
        ]
        avg, count, validation_failures = aggregate_fixture_metrics(results)
        assert avg == 90.0
        assert count == 2
        assert validation_failures == 1


class TestAdminPromptEvalRoutes:
    @pytest.fixture()
    def admin_client(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "mighty_prompt_eval.db")
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
        monkeypatch.setenv("ADMIN_EMAIL", email)
        return c

    def test_page_requires_admin(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "mighty_prompt_eval2.db")
        monkeypatch.setenv("DATABASE_PATH", db_path)
        import app as mighty

        mighty.DATABASE = db_path
        with mighty.app.app_context():
            mighty.init_db()
        mighty.app.config["TESTING"] = True
        c = mighty.app.test_client()
        c.get("/signup")
        with c.session_transaction() as sess:
            csrf = sess["_csrf"]
        c.post("/signup", data={"email": "user@test.local", "password": "pass12345", "_csrf": csrf})
        assert c.get("/admin/prompt-eval").status_code == 403

    def test_page_loads_for_admin(self, admin_client):
        r = admin_client.get("/admin/prompt-eval")
        assert r.status_code == 200
        assert b"Prompt Evaluation" in r.data
        assert b"Prompt version comparison" in r.data

    def test_api_returns_versions(self, admin_client):
        r = admin_client.get("/api/admin/prompt-eval")
        assert r.status_code == 200
        data = json.loads(r.data)
        assert "versions" in data
        assert any(v["prompt_id"] == "field_discovery" for v in data["versions"])
