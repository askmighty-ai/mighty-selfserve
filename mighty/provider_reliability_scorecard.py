"""Provider Reliability Scorecard — aggregates existing diagnostics into one view."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from mighty.observation_catalog import observation_label
from mighty.observation_coverage import collect_observed_from_pipeline, compute_provider_coverage
from mighty.pipeline_stages import PipelineStageId, StageStatus
from mighty.provider_benchmark import (
    TREND_WINDOW_DAYS,
    ProviderBenchmark,
    _recent_window_start_iso,
    attention_priority,
    compute_all_provider_benchmarks,
)

FAILURE_REASON_LABELS: dict[str, str] = {
    "login_required": "Login required",
    "session_expired": "Session expired",
    "needs_first_visit": "Needs first visit",
    "wrong_url": "Wrong URL",
    "timeout": "Navigation timeout",
    "domain_unreachable": "Domain unreachable",
    "no_pages_visited": "No pages visited",
    "no_data": "No data captured",
    "login_wall": "Login wall during capture",
    "quality_gate": "Quality gate failed",
    "payload_too_small": "Payload too small",
    "not_attempted_on_sync_path": "Not attempted on sync path",
    "connector_miss": "Connector miss",
    "json_parse_error": "JSON parse error",
    "invalid_normalized_value": "Invalid normalized value",
    "llm_empty": "LLM returned empty",
    "discovery_error": "Discovery error",
    "discovery_disabled": "Discovery disabled",
    "low_confidence_only": "Low confidence only",
    "stale_date_only": "Stale date only",
    "all_filtered": "All fields filtered",
    "no_trusted_observations": "No trusted observations",
    "storage_split": "Storage split",
    "partial_trust": "Partial trust",
    "write_error": "Write error",
    "exception": "Exception",
}


@dataclass
class FailureReasonCount:
    reason: str
    label: str
    count: int


@dataclass
class MissingObservationCount:
    observation_id: str
    label: str
    provider_count: int


@dataclass
class ProviderScorecardRow:
    source: str
    display_name: str
    login_success_pct: int
    capture_success_pct: int
    observation_success_pct: int
    recommendation_success_pct: int
    reliability_score: int
    attention_rank: int | None = None

    @classmethod
    def from_benchmark(cls, row: ProviderBenchmark) -> ProviderScorecardRow:
        return cls(
            source=row.source,
            display_name=row.display_name,
            login_success_pct=row.login_score,
            capture_success_pct=row.capture_score,
            observation_success_pct=row.observation_score,
            recommendation_success_pct=row.recommendation_score,
            reliability_score=row.readiness_score,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "display_name": self.display_name,
            "login_success_pct": self.login_success_pct,
            "capture_success_pct": self.capture_success_pct,
            "observation_success_pct": self.observation_success_pct,
            "recommendation_success_pct": self.recommendation_success_pct,
            "reliability_score": self.reliability_score,
            "attention_rank": self.attention_rank,
        }


@dataclass
class ProviderReliabilityScorecard:
    providers: list[ProviderScorecardRow]
    top_login_failure_reasons: list[FailureReasonCount]
    top_capture_failure_reasons: list[FailureReasonCount]
    most_missing_observations: list[MissingObservationCount]
    needs_attention: list[ProviderScorecardRow]
    window_days: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_days": self.window_days,
            "providers": [row.to_dict() for row in self.providers],
            "top_login_failure_reasons": [
                {"reason": r.reason, "label": r.label, "count": r.count}
                for r in self.top_login_failure_reasons
            ],
            "top_capture_failure_reasons": [
                {"reason": r.reason, "label": r.label, "count": r.count}
                for r in self.top_capture_failure_reasons
            ],
            "most_missing_observations": [
                {
                    "observation_id": r.observation_id,
                    "label": r.label,
                    "provider_count": r.provider_count,
                }
                for r in self.most_missing_observations
            ],
            "needs_attention": [row.to_dict() for row in self.needs_attention],
        }


def failure_reason_label(reason: str) -> str:
    return FAILURE_REASON_LABELS.get(reason, reason.replace("_", " ").title())


def collect_stage_failure_reasons(
    db: Any,
    stage: str,
    *,
    run_created_before: str | None = None,
    run_created_after: str | None = None,
) -> Counter[str]:
    """Count failure reasons for failed pipeline stages (Pipeline Inspector)."""
    clauses = ["ps.stage = ?", "ps.status = ?"]
    params: list[Any] = [stage, StageStatus.FAILED.value]
    if run_created_before:
        clauses.append("pr.created_at < ?")
        params.append(run_created_before)
    if run_created_after:
        clauses.append("pr.created_at >= ?")
        params.append(run_created_after)

    where = " AND ".join(clauses)
    rows = db.execute(
        f"""
        SELECT ps.failure_reason
        FROM pipeline_stages ps
        JOIN pipeline_runs pr ON pr.run_id = ps.run_id
        WHERE {where}
        """,
        params,
    ).fetchall()

    counts: Counter[str] = Counter()
    for row in rows:
        reason = row["failure_reason"] if isinstance(row, dict) else row[0]
        if reason:
            counts[str(reason)] += 1
    return counts


def top_failure_reasons(
    counts: Counter[str],
    *,
    limit: int = 10,
) -> list[FailureReasonCount]:
    return [
        FailureReasonCount(reason=reason, label=failure_reason_label(reason), count=count)
        for reason, count in counts.most_common(limit)
    ]


def aggregate_missing_observations(
    providers: list[str],
    provider_categories: dict[str, str],
    observed_by_source: dict[str, set[str]],
    *,
    display_names: dict[str, str] | None = None,
    limit: int = 10,
) -> list[MissingObservationCount]:
    """Count how many providers are missing each expected observation type."""
    names = display_names or {}
    missing_counts: Counter[str] = Counter()
    for source in providers:
        cov = compute_provider_coverage(
            source,
            category=provider_categories.get(source),
            observed_observations=observed_by_source.get(source, set()),
            display_name=names.get(source),
        )
        for obs in cov.missing:
            missing_counts[obs] += 1

    return [
        MissingObservationCount(
            observation_id=obs_id,
            label=observation_label(obs_id),
            provider_count=count,
        )
        for obs_id, count in missing_counts.most_common(limit)
    ]


def _rank_attention_rows(
    benchmarks: list[ProviderBenchmark],
) -> list[ProviderScorecardRow]:
    ranked = sorted(benchmarks, key=attention_priority, reverse=True)
    rows: list[ProviderScorecardRow] = []
    for index, benchmark in enumerate(ranked[:5], start=1):
        row = ProviderScorecardRow.from_benchmark(benchmark)
        row.attention_rank = index
        rows.append(row)
    return rows


def compute_provider_reliability_scorecard(
    db: Any,
    providers: list[str],
    provider_categories: dict[str, str],
    *,
    display_names: dict[str, str] | None = None,
    recent_window_start: str | None = None,
    window_days: int = TREND_WINDOW_DAYS,
    failure_reason_limit: int = 10,
    missing_observation_limit: int = 10,
) -> ProviderReliabilityScorecard:
    """Build the full reliability scorecard from existing diagnostic systems."""
    window_start = recent_window_start or _recent_window_start_iso()

    benchmarks = compute_all_provider_benchmarks(
        db,
        providers,
        provider_categories,
        display_names=display_names,
        recent_window_start=window_start,
    )

    login_failures = collect_stage_failure_reasons(
        db,
        PipelineStageId.CONNECTION.value,
        run_created_after=window_start,
    )
    capture_failures = collect_stage_failure_reasons(
        db,
        PipelineStageId.CAPTURE.value,
        run_created_after=window_start,
    )
    observed_by_source = collect_observed_from_pipeline(db, run_created_after=window_start)

    provider_rows = [ProviderScorecardRow.from_benchmark(row) for row in benchmarks]
    needs_attention = _rank_attention_rows(benchmarks)

    return ProviderReliabilityScorecard(
        providers=provider_rows,
        top_login_failure_reasons=top_failure_reasons(
            login_failures,
            limit=failure_reason_limit,
        ),
        top_capture_failure_reasons=top_failure_reasons(
            capture_failures,
            limit=failure_reason_limit,
        ),
        most_missing_observations=aggregate_missing_observations(
            providers,
            provider_categories,
            observed_by_source,
            display_names=display_names,
            limit=missing_observation_limit,
        ),
        needs_attention=needs_attention,
        window_days=window_days,
    )
