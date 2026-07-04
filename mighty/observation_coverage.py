"""Observation coverage computation from pipeline trusted_observations data."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from mighty.observation_catalog import (
    expected_observations_for_provider,
    field_keys_to_observations,
)
from mighty.pipeline_stages import PipelineStageId, StageStatus


@dataclass
class ProviderCoverage:
    source: str
    display_name: str
    expected: list[str]
    observed: list[str]
    missing: list[str]
    coverage_pct: int | None


def coverage_percentage(expected: set[str] | list[str], observed: set[str] | list[str]) -> int | None:
    """Return coverage % (0–100) or None when there are no expected observations."""
    expected_set = set(expected)
    if not expected_set:
        return None
    observed_set = set(observed)
    matched = len(expected_set & observed_set)
    return int(matched / len(expected_set) * 100)


def missing_observations(expected: list[str], observed: list[str]) -> list[str]:
    """Return expected observation types not present in observed."""
    observed_set = set(observed)
    return [obs for obs in expected if obs not in observed_set]


def compute_provider_coverage(
    source: str,
    *,
    category: str | None,
    observed_observations: set[str],
    display_name: str | None = None,
) -> ProviderCoverage:
    expected = expected_observations_for_provider(source, category)
    expected_set = set(expected)
    observed = sorted(observed_observations & expected_set)
    missing = missing_observations(expected, observed)
    pct = coverage_percentage(expected, observed)
    return ProviderCoverage(
        source=source,
        display_name=display_name or source.replace("_", " ").title(),
        expected=expected,
        observed=observed,
        missing=missing,
        coverage_pct=pct,
    )


def collect_observed_from_pipeline(db: Any) -> dict[str, set[str]]:
    """Aggregate observation types observed in successful trusted_observations stages."""
    rows = db.execute(
        """
        SELECT pr.source, ps.artifacts_json
        FROM pipeline_stages ps
        JOIN pipeline_runs pr ON pr.run_id = ps.run_id
        WHERE ps.stage = ? AND ps.status = ?
        """,
        (PipelineStageId.TRUSTED_OBSERVATIONS.value, StageStatus.SUCCESS.value),
    ).fetchall()

    by_source: dict[str, set[str]] = {}
    for row in rows:
        source = row["source"] if isinstance(row, dict) else row[0]
        artifacts_json = row["artifacts_json"] if isinstance(row, dict) else row[1]
        if not artifacts_json:
            continue
        try:
            artifacts = json.loads(artifacts_json)
        except (json.JSONDecodeError, TypeError):
            continue
        trusted_keys = artifacts.get("trusted_keys") or []
        if not isinstance(trusted_keys, list):
            continue
        obs = field_keys_to_observations([str(k) for k in trusted_keys])
        if not obs:
            continue
        by_source.setdefault(source, set()).update(obs)
    return by_source


def collect_field_keys_from_pipeline(db: Any) -> dict[str, set[str]]:
    """Return raw trusted field keys per provider (for detail/debug views)."""
    rows = db.execute(
        """
        SELECT pr.source, ps.artifacts_json
        FROM pipeline_stages ps
        JOIN pipeline_runs pr ON pr.run_id = ps.run_id
        WHERE ps.stage = ? AND ps.status = ?
        """,
        (PipelineStageId.TRUSTED_OBSERVATIONS.value, StageStatus.SUCCESS.value),
    ).fetchall()

    by_source: dict[str, set[str]] = {}
    for row in rows:
        source = row["source"] if isinstance(row, dict) else row[0]
        artifacts_json = row["artifacts_json"] if isinstance(row, dict) else row[1]
        if not artifacts_json:
            continue
        try:
            artifacts = json.loads(artifacts_json)
        except (json.JSONDecodeError, TypeError):
            continue
        trusted_keys = artifacts.get("trusted_keys") or []
        if not isinstance(trusted_keys, list):
            continue
        by_source.setdefault(source, set()).update(str(k) for k in trusted_keys if k)
    return by_source


def compute_all_provider_coverage(
    db: Any,
    providers: list[str],
    provider_categories: dict[str, str],
    *,
    display_names: dict[str, str] | None = None,
) -> list[ProviderCoverage]:
    """Compute coverage for all providers, sorted lowest coverage first."""
    observed_by_source = collect_observed_from_pipeline(db)
    names = display_names or {}
    results: list[ProviderCoverage] = []
    for source in providers:
        category = provider_categories.get(source)
        row = compute_provider_coverage(
            source,
            category=category,
            observed_observations=observed_by_source.get(source, set()),
            display_name=names.get(source),
        )
        results.append(row)

    def sort_key(row: ProviderCoverage) -> tuple[int | float, str]:
        pct = row.coverage_pct
        if pct is None:
            return (999, row.display_name.lower())
        return (pct, row.display_name.lower())

    results.sort(key=sort_key)
    return results
