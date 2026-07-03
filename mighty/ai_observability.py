"""Production AI observability: structured logging and SQLite persistence."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Callable

from mighty.ai_metrics import AIMetrics

_db_getter: Callable[[], Any] | None = None
_lock = threading.Lock()


def configure_db(getter: Callable[[], Any]) -> None:
    global _db_getter
    _db_getter = getter


def structured_payload(metrics: AIMetrics, *, failure_reason: str | None = None) -> dict[str, Any]:
    return {
        "event": "ai_request",
        "provider": metrics.provider,
        "model": metrics.model,
        "cache_hit": metrics.cache_hit,
        "latency_ms": round(metrics.latency_ms, 2),
        "prompt_chars": metrics.input_chars,
        "completion_chars": metrics.output_chars,
        "estimated_token_count": metrics.estimated_token_count(),
        "estimated_cost_usd": round(metrics.estimated_cost_usd, 8) if metrics.estimated_cost_usd is not None else None,
        "failure_reason": failure_reason,
        "prompt_id": metrics.prompt_id,
        "prompt_version": metrics.prompt_version,
    }


def observe_request(metrics: AIMetrics, *, failure_reason: str | None = None, source: str | None = None) -> AIMetrics:
    payload = structured_payload(metrics, failure_reason=failure_reason)
    with _lock:
        print(json.dumps(payload, separators=(",", ":")), flush=True)
        if _db_getter is None:
            return metrics
        try:
            db = _db_getter()
            db.execute(
                """INSERT INTO ai_request_log (
                    created_at, provider, model, cache_hit, latency_ms,
                    prompt_chars, completion_chars, estimated_tokens,
                    estimated_cost_usd, failure_reason, prompt_id, prompt_version, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    metrics.provider, metrics.model,
                    1 if metrics.cache_hit else 0, metrics.latency_ms,
                    metrics.input_chars, metrics.output_chars,
                    metrics.estimated_token_count(), metrics.estimated_cost_usd,
                    failure_reason, metrics.prompt_id, metrics.prompt_version, source,
                ),
            )
            db.commit()
        except Exception:
            pass
    return metrics


def get_daily_stats(db: Any) -> dict[str, Any]:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    row = db.execute(
        """SELECT COUNT(*) AS requests_today,
                  SUM(CASE WHEN cache_hit = 1 THEN 1 ELSE 0 END) AS cache_hits,
                  AVG(latency_ms) AS avg_latency_ms,
                  AVG(estimated_cost_usd) AS avg_cost_usd,
                  SUM(CASE WHEN failure_reason IS NOT NULL THEN 1 ELSE 0 END) AS failures
           FROM ai_request_log WHERE created_at >= ?""",
        (today,),
    ).fetchone()
    n = int(row["requests_today"] or 0)
    hits = int(row["cache_hits"] or 0)
    providers = db.execute(
        "SELECT provider, COUNT(*) AS count FROM ai_request_log WHERE created_at >= ? GROUP BY provider ORDER BY count DESC",
        (today,),
    ).fetchall()
    return {
        "requests_today": n,
        "cache_hit_pct": round(hits / n * 100, 1) if n else 0.0,
        "avg_latency_ms": round(float(row["avg_latency_ms"] or 0), 1),
        "avg_cost_usd": round(float(row["avg_cost_usd"] or 0), 8),
        "failures": int(row["failures"] or 0),
        "provider_distribution": [{"provider": r["provider"], "count": int(r["count"])} for r in providers],
        "day_start_utc": today,
    }
