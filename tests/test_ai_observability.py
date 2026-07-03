"""Tests for production AI observability."""

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.ai_metrics import build_metrics, estimate_cost_usd
from mighty.ai_observability import configure_db, get_daily_stats, observe_request, structured_payload


@pytest.fixture
def observability_db(tmp_path):
    db_path = tmp_path / "observability.db"
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
    configure_db(lambda: conn)
    yield conn
    conn.close()


class TestStructuredPayload:
    def test_includes_required_fields(self):
        metrics = build_metrics(
            provider="openai",
            model="gpt-5.4-mini",
            latency_ms=42.5,
            cache_hit=False,
            prompt_id="field_discovery",
            prompt_version="1.0.0",
            input_text="prompt text",
            output_text='{"fields":[]}',
        )
        payload = structured_payload(metrics, failure_reason="timeout")
        assert payload["event"] == "ai_request"
        assert payload["provider"] == "openai"
        assert payload["model"] == "gpt-5.4-mini"
        assert payload["cache_hit"] is False
        assert payload["latency_ms"] == 42.5
        assert payload["prompt_chars"] == len("prompt text")
        assert payload["completion_chars"] == len('{"fields":[]}')
        assert payload["estimated_token_count"] == metrics.estimated_token_count()
        assert payload["estimated_cost_usd"] is not None
        assert payload["failure_reason"] == "timeout"

    def test_unknown_model_cost_is_none(self):
        assert (
            estimate_cost_usd(
                "unknown",
                "unknown-model-xyz",
                input_chars=1000,
                output_chars=1000,
            )
            is None
        )


class TestObserveRequest:
    def test_persists_to_db(self, observability_db, capsys):
        metrics = build_metrics(
            provider="gemini",
            model="gemini-2.5-flash",
            latency_ms=100.0,
            cache_hit=True,
            prompt_id="field_discovery",
            prompt_version="1.0.0",
            input_text="",
            output_text="",
        )
        observe_request(metrics, source="amex")

        row = observability_db.execute(
            "SELECT provider, model, cache_hit, source FROM ai_request_log"
        ).fetchone()
        assert row["provider"] == "gemini"
        assert row["model"] == "gemini-2.5-flash"
        assert row["cache_hit"] == 1
        assert row["source"] == "amex"

        captured = capsys.readouterr()
        logged = json.loads(captured.out.strip())
        assert logged["event"] == "ai_request"
        assert logged["provider"] == "gemini"


class TestDailyStats:
    def test_aggregates_today(self, observability_db):
        today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        rows = [
            ("openai", "gpt-5.4-mini", 0, 120.0, 100, 50, 38, 0.00001, None),
            ("openai", "gpt-5.4-mini", 1, 0.0, 0, 0, 0, None, None),
            ("gemini", "gemini-2.5-flash", 0, 80.0, 200, 100, 75, 0.00002, "timeout"),
        ]
        for provider, model, cache_hit, latency, pc, cc, tokens, cost, failure in rows:
            observability_db.execute(
                """
                INSERT INTO ai_request_log (
                    created_at, provider, model, cache_hit, latency_ms,
                    prompt_chars, completion_chars, estimated_tokens,
                    estimated_cost_usd, failure_reason, prompt_id, prompt_version, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    today,
                    provider,
                    model,
                    cache_hit,
                    latency,
                    pc,
                    cc,
                    tokens,
                    cost,
                    failure,
                    "field_discovery",
                    "1.0.0",
                    "amex",
                ),
            )
        observability_db.commit()

        stats = get_daily_stats(observability_db)
        assert stats["requests_today"] == 3
        assert stats["cache_hit_pct"] == pytest.approx(33.3, abs=0.1)
        assert stats["avg_latency_ms"] == pytest.approx(66.7, abs=0.1)
        assert stats["failures"] == 1
        assert len(stats["provider_distribution"]) == 2
        assert stats["provider_distribution"][0]["provider"] == "openai"
        assert stats["provider_distribution"][0]["count"] == 2
