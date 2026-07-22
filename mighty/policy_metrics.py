"""User Policy / governance metrics (Milestone 12)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from mighty.authorization_policy import AUTH_AUTO_AUTHORIZE, AUTH_DENY, AUTH_REQUIRE_HUMAN
from mighty.policy_evaluation import ExplainedAuthorizationDecision

logger = logging.getLogger(__name__)


@dataclass
class PolicyEvalCounters:
    evaluations: int = 0
    overrides: int = 0
    require_human: int = 0
    auto_authorize: int = 0
    deny: int = 0
    suppressed_executions: int = 0
    conflicts: int = 0
    explainability_hits: int = 0
    errors: int = 0


@dataclass(frozen=True)
class PolicyMetricSnapshot:
    evaluations: int
    overrides: int
    require_human: int
    auto_authorize: int
    deny: int
    suppressed_executions: int
    conflicts: int
    explainability_coverage: float
    computed_at: str


def apply_explained(
    counters: PolicyEvalCounters, explained: ExplainedAuthorizationDecision
) -> None:
    counters.evaluations += 1
    outcome = explained.decision.outcome
    if outcome == AUTH_REQUIRE_HUMAN:
        counters.require_human += 1
    elif outcome == AUTH_AUTO_AUTHORIZE:
        counters.auto_authorize += 1
    elif outcome == AUTH_DENY:
        counters.deny += 1
    if explained.overridden:
        counters.overrides += 1
    if explained.suppressed_execution:
        counters.suppressed_executions += 1
    if explained.conflict_resolution:
        counters.conflicts += 1
    if explained.explanation.strip() and explained.policy_refs:
        counters.explainability_hits += 1


def compute_policy_metrics(
    counters: PolicyEvalCounters, *, now: datetime | None = None
) -> PolicyMetricSnapshot:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    coverage = (
        counters.explainability_hits / counters.evaluations
        if counters.evaluations
        else 1.0
    )
    snap = PolicyMetricSnapshot(
        evaluations=counters.evaluations,
        overrides=counters.overrides,
        require_human=counters.require_human,
        auto_authorize=counters.auto_authorize,
        deny=counters.deny,
        suppressed_executions=counters.suppressed_executions,
        conflicts=counters.conflicts,
        explainability_coverage=coverage,
        computed_at=now.replace(microsecond=0).isoformat(),
    )
    logger.info(
        "policy.metrics evaluations=%s require_human=%s auto=%s deny=%s "
        "overrides=%s explain=%.3f",
        snap.evaluations,
        snap.require_human,
        snap.auto_authorize,
        snap.deny,
        snap.overrides,
        snap.explainability_coverage,
    )
    return snap


def persist_policy_metric_snapshot(
    db: Any, snapshot: PolicyMetricSnapshot, *, commit: bool = True
) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS policy_metric_snapshot (
            scope TEXT PRIMARY KEY,
            evaluations INTEGER NOT NULL,
            overrides INTEGER NOT NULL,
            require_human INTEGER NOT NULL,
            auto_authorize INTEGER NOT NULL,
            deny INTEGER NOT NULL,
            suppressed_executions INTEGER NOT NULL,
            conflicts INTEGER NOT NULL,
            explainability_coverage REAL NOT NULL,
            computed_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        INSERT INTO policy_metric_snapshot (
            scope, evaluations, overrides, require_human, auto_authorize, deny,
            suppressed_executions, conflicts, explainability_coverage, computed_at
        ) VALUES ('global',?,?,?,?,?,?,?,?,?)
        ON CONFLICT(scope) DO UPDATE SET
            evaluations=excluded.evaluations,
            overrides=excluded.overrides,
            require_human=excluded.require_human,
            auto_authorize=excluded.auto_authorize,
            deny=excluded.deny,
            suppressed_executions=excluded.suppressed_executions,
            conflicts=excluded.conflicts,
            explainability_coverage=excluded.explainability_coverage,
            computed_at=excluded.computed_at
        """,
        (
            snapshot.evaluations,
            snapshot.overrides,
            snapshot.require_human,
            snapshot.auto_authorize,
            snapshot.deny,
            snapshot.suppressed_executions,
            snapshot.conflicts,
            snapshot.explainability_coverage,
            snapshot.computed_at,
        ),
    )
    if commit:
        db.commit()
