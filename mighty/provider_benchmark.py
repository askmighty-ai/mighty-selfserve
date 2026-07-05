"""Provider readiness benchmark — combines existing diagnostics into one score."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from mighty.capture_capability import (
    ProviderCapability,
    collect_signals_from_pipeline,
    compute_provider_capability,
)
from mighty.observation_coverage import (
    ProviderCoverage,
    collect_observed_from_pipeline,
    compute_provider_coverage,
)
from mighty.pipeline_stages import PipelineStageId, StageStatus
from mighty.recommendation_unlock_catalog import RECOMMENDATION_TYPES
from mighty.recommendation_unlocks import (
    ProviderUnlockSummary,
    compute_provider_unlocks,
)

# Deterministic weights for Provider Readiness Score (must sum to 1.0).
SCORE_WEIGHTS: dict[str, float] = {
    "login": 0.25,
    "capture": 0.25,
    "observation": 0.25,
    "recommendation": 0.25,
}

TREND_WINDOW_DAYS = 14


@dataclass
class ConnectionStats:
    total: int = 0
    success: int = 0


@dataclass
class ProviderBenchmark:
    source: str
    display_name: str
    login_score: int
    capture_score: int
    observation_score: int
    recommendation_score: int
    readiness_score: int
    trend_delta: int | None
    connection_total: int
    connection_success: int
    capture_present: int
    capture_needed: int
    observation_observed: int
    observation_expected: int
    recommendations_unlocked: int
    recommendations_total: int


def login_score_from_stats(stats: ConnectionStats) -> int:
    """Connection stage success rate from Pipeline Inspector (0–100)."""
    if stats.total == 0:
        return 0
    return int(stats.success / stats.total * 100)


def capture_score_from_capability(cap: ProviderCapability) -> int:
    """Present / needed capture capabilities (0–100)."""
    if cap.needed_count == 0:
        return 100
    return int(cap.present_count / cap.needed_count * 100)


def observation_score_from_coverage(cov: ProviderCoverage) -> int:
    """Observation coverage percentage (0–100)."""
    return cov.coverage_pct if cov.coverage_pct is not None else 0


def recommendation_score_from_unlocks(unlocks: ProviderUnlockSummary) -> int:
    """Unlocked recommendations / catalog total (0–100)."""
    total = len(RECOMMENDATION_TYPES)
    if total == 0:
        return 100
    return int(len(unlocks.unlocked) / total * 100)


def readiness_score(
    *,
    login: int,
    capture: int,
    observation: int,
    recommendation: int,
) -> int:
    """Weighted Provider Readiness Score (0–100)."""
    weighted = (
        login * SCORE_WEIGHTS["login"]
        + capture * SCORE_WEIGHTS["capture"]
        + observation * SCORE_WEIGHTS["observation"]
        + recommendation * SCORE_WEIGHTS["recommendation"]
    )
    return int(round(weighted))


def collect_connection_stats_from_pipeline(
    db: Any,
    *,
    run_created_before: str | None = None,
    run_created_after: str | None = None,
) -> dict[str, ConnectionStats]:
    """Aggregate connection stage outcomes per provider from Pipeline Inspector."""
    clauses = ["ps.stage = ?"]
    params: list[Any] = [PipelineStageId.CONNECTION.value]
    if run_created_before:
        clauses.append("pr.created_at < ?")
        params.append(run_created_before)
    if run_created_after:
        clauses.append("pr.created_at >= ?")
        params.append(run_created_after)

    where = " AND ".join(clauses)
    rows = db.execute(
        f"""
        SELECT pr.source, ps.status
        FROM pipeline_stages ps
        JOIN pipeline_runs pr ON pr.run_id = ps.run_id
        WHERE {where}
        """,
        params,
    ).fetchall()

    by_source: dict[str, ConnectionStats] = {}
    for row in rows:
        source = row["source"] if isinstance(row, dict) else row[0]
        status = row["status"] if isinstance(row, dict) else row[1]
        stats = by_source.setdefault(source, ConnectionStats())
        stats.total += 1
        if status == StageStatus.SUCCESS.value:
            stats.success += 1
    return by_source


def _recent_window_start_iso(*, now: datetime | None = None, days: int = TREND_WINDOW_DAYS) -> str:
    """ISO timestamp marking the start of the recent window (last N days)."""
    ref = now or datetime.now(timezone.utc)
    return (ref - timedelta(days=days)).isoformat()


def compute_provider_benchmark(
    source: str,
    *,
    connection_stats: ConnectionStats,
    capability: ProviderCapability,
    coverage: ProviderCoverage,
    unlocks: ProviderUnlockSummary,
    prior_readiness: int | None = None,
    display_name: str | None = None,
) -> ProviderBenchmark:
    login = login_score_from_stats(connection_stats)
    capture = capture_score_from_capability(capability)
    observation = observation_score_from_coverage(coverage)
    recommendation = recommendation_score_from_unlocks(unlocks)
    overall = readiness_score(
        login=login,
        capture=capture,
        observation=observation,
        recommendation=recommendation,
    )
    trend = (overall - prior_readiness) if prior_readiness is not None else None

    return ProviderBenchmark(
        source=source,
        display_name=display_name or capability.display_name,
        login_score=login,
        capture_score=capture,
        observation_score=observation,
        recommendation_score=recommendation,
        readiness_score=overall,
        trend_delta=trend,
        connection_total=connection_stats.total,
        connection_success=connection_stats.success,
        capture_present=capability.present_count,
        capture_needed=capability.needed_count,
        observation_observed=len(coverage.observed),
        observation_expected=len(coverage.expected),
        recommendations_unlocked=len(unlocks.unlocked),
        recommendations_total=len(RECOMMENDATION_TYPES),
    )


def _has_pipeline_data(
    *,
    connection: ConnectionStats,
    signals: Any,
    observed: set[str],
) -> bool:
    return connection.total > 0 or signals is not None or bool(observed)


def compute_all_provider_benchmarks(
    db: Any,
    providers: list[str],
    provider_categories: dict[str, str],
    *,
    display_names: dict[str, str] | None = None,
    recent_window_start: str | None = None,
) -> list[ProviderBenchmark]:
    """Compute readiness benchmarks for all providers.

    Scores reflect the recent window (last TREND_WINDOW_DAYS by default).
    Trend compares recent readiness vs prior readiness (all runs before recent window).
    """
    names = display_names or {}
    window_start = recent_window_start or _recent_window_start_iso()

    recent_connection = collect_connection_stats_from_pipeline(db, run_created_after=window_start)
    prior_connection = collect_connection_stats_from_pipeline(db, run_created_before=window_start)
    recent_signals = collect_signals_from_pipeline(db, run_created_after=window_start)
    prior_signals = collect_signals_from_pipeline(db, run_created_before=window_start)
    recent_observed = collect_observed_from_pipeline(db, run_created_after=window_start)
    prior_observed = collect_observed_from_pipeline(db, run_created_before=window_start)

    results: list[ProviderBenchmark] = []
    for source in providers:
        category = provider_categories.get(source)
        name = names.get(source)

        cap = compute_provider_capability(
            source,
            signals=recent_signals.get(source),
            display_name=name,
        )
        cov = compute_provider_coverage(
            source,
            category=category,
            observed_observations=recent_observed.get(source, set()),
            display_name=name,
        )
        unlocks = compute_provider_unlocks(
            source,
            recent_observed.get(source, set()),
            display_name=name,
        )

        prior_cap = compute_provider_capability(
            source,
            signals=prior_signals.get(source),
            display_name=name,
        )
        prior_cov = compute_provider_coverage(
            source,
            category=category,
            observed_observations=prior_observed.get(source, set()),
            display_name=name,
        )
        prior_unlocks = compute_provider_unlocks(
            source,
            prior_observed.get(source, set()),
            display_name=name,
        )
        prior_overall = readiness_score(
            login=login_score_from_stats(prior_connection.get(source, ConnectionStats())),
            capture=capture_score_from_capability(prior_cap),
            observation=observation_score_from_coverage(prior_cov),
            recommendation=recommendation_score_from_unlocks(prior_unlocks),
        )

        has_prior = _has_pipeline_data(
            connection=prior_connection.get(source, ConnectionStats()),
            signals=prior_signals.get(source),
            observed=prior_observed.get(source, set()),
        )

        results.append(
            compute_provider_benchmark(
                source,
                connection_stats=recent_connection.get(source, ConnectionStats()),
                capability=cap,
                coverage=cov,
                unlocks=unlocks,
                prior_readiness=prior_overall if has_prior else None,
                display_name=name,
            )
        )

    results.sort(key=lambda r: (-r.readiness_score, r.display_name.lower()))
    return results


def attention_priority(row: ProviderBenchmark) -> int:
    """Lower = needs engineering attention sooner (inverse readiness + negative trend)."""
    penalty = 0
    if row.trend_delta is not None and row.trend_delta < 0:
        penalty += abs(row.trend_delta)
    return 100 - row.readiness_score + penalty
