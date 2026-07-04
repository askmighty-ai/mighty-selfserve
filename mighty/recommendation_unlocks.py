"""Recommendation unlock computation from pipeline observation coverage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mighty.observation_coverage import collect_observed_from_pipeline
from mighty.recommendation_unlock_catalog import (
    RECOMMENDATION_TYPES,
    RecommendationType,
    all_recommendation_types,
)


@dataclass
class BlockedRecommendation:
    recommendation_id: str
    title: str
    missing_observations: list[str]


@dataclass
class ProviderUnlockSummary:
    source: str
    display_name: str
    observed: list[str]
    unlocked: list[str]
    blocked: list[BlockedRecommendation]


def _group_satisfied(group: tuple[str, ...], observed: set[str]) -> bool:
    return any(obs in observed for obs in group)


def missing_for_recommendation(
    rec: RecommendationType,
    observed: set[str],
) -> list[str]:
    """Return observation types still needed to unlock this recommendation."""
    missing: list[str] = []
    for group in rec.required_groups:
        if not _group_satisfied(group, observed):
            for obs in group:
                if obs not in missing:
                    missing.append(obs)
    return missing


def is_recommendation_unlocked(rec: RecommendationType, observed: set[str]) -> bool:
    return all(_group_satisfied(group, observed) for group in rec.required_groups)


def compute_provider_unlocks(
    source: str,
    observed_observations: set[str],
    *,
    display_name: str | None = None,
    catalog: dict[str, RecommendationType] | None = None,
) -> ProviderUnlockSummary:
    """Compute unlocked and blocked recommendations for one provider."""
    types = catalog or RECOMMENDATION_TYPES
    observed = sorted(observed_observations)
    observed_set = set(observed_observations)

    unlocked: list[str] = []
    blocked: list[BlockedRecommendation] = []

    for rec in types.values():
        if is_recommendation_unlocked(rec, observed_set):
            unlocked.append(rec.id)
        else:
            blocked.append(
                BlockedRecommendation(
                    recommendation_id=rec.id,
                    title=rec.title,
                    missing_observations=missing_for_recommendation(rec, observed_set),
                )
            )

    unlocked.sort()
    blocked.sort(key=lambda b: b.recommendation_id)

    return ProviderUnlockSummary(
        source=source,
        display_name=display_name or source.replace("_", " ").title(),
        observed=observed,
        unlocked=unlocked,
        blocked=blocked,
    )


def compute_all_provider_unlocks(
    db: Any,
    providers: list[str],
    *,
    display_names: dict[str, str] | None = None,
    catalog: dict[str, RecommendationType] | None = None,
) -> list[ProviderUnlockSummary]:
    """Compute unlock summaries for all providers, sorted by unlock count ascending."""
    observed_by_source = collect_observed_from_pipeline(db)
    names = display_names or {}
    results: list[ProviderUnlockSummary] = []

    for source in providers:
        row = compute_provider_unlocks(
            source,
            observed_by_source.get(source, set()),
            display_name=names.get(source),
            catalog=catalog,
        )
        results.append(row)

    def sort_key(row: ProviderUnlockSummary) -> tuple[int, str]:
        return (len(row.unlocked), row.display_name.lower())

    results.sort(key=sort_key)
    return results
