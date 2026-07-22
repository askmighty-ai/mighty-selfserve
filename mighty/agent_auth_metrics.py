"""Trusted Agent Authorization metrics (Milestone 11)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from mighty.trusted_agent import (
    AgentAuthCounters,
    DecideResult,
    ExecuteResult,
    ProposeResult,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentAuthMetricSnapshot:
    proposed: int
    approvals_requested: int
    approvals_granted: int
    approvals_denied: int
    executions: int
    failures: int
    retries: int
    duplicates_suppressed: int
    expired: int
    errors: int
    computed_at: str


def apply_propose(counters: AgentAuthCounters, result: ProposeResult) -> None:
    if result.error:
        counters.errors += 1
        return
    if result.suppressed_duplicate:
        counters.duplicates_suppressed += 1
        return
    if result.action is None:
        return
    counters.proposed += 1
    state = result.action.lifecycle_state
    if state == "awaiting_authorization":
        counters.approvals_requested += 1
    elif state == "authorized":
        counters.approvals_granted += 1
    elif state == "denied":
        counters.approvals_denied += 1


def apply_decide(counters: AgentAuthCounters, result: DecideResult) -> None:
    if result.error:
        counters.errors += 1
        return
    if result.action is None:
        return
    if result.action.lifecycle_state == "authorized":
        counters.approvals_granted += 1
    elif result.action.lifecycle_state in {"denied", "cancelled"}:
        counters.approvals_denied += 1


def apply_execute(counters: AgentAuthCounters, result: ExecuteResult) -> None:
    if result.error:
        counters.errors += 1
        return
    if result.retried:
        counters.retries += 1
        return
    counters.executions += 1
    if result.action and result.action.lifecycle_state == "failed":
        counters.failures += 1


def compute_agent_auth_metrics(
    counters: AgentAuthCounters, *, now: datetime | None = None
) -> AgentAuthMetricSnapshot:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    snap = AgentAuthMetricSnapshot(
        proposed=counters.proposed,
        approvals_requested=counters.approvals_requested,
        approvals_granted=counters.approvals_granted,
        approvals_denied=counters.approvals_denied,
        executions=counters.executions,
        failures=counters.failures,
        retries=counters.retries,
        duplicates_suppressed=counters.duplicates_suppressed,
        expired=counters.expired,
        errors=counters.errors,
        computed_at=now.replace(microsecond=0).isoformat(),
    )
    logger.info(
        "agent_auth.metrics proposed=%s requested=%s granted=%s denied=%s "
        "executions=%s failures=%s dupes=%s",
        snap.proposed,
        snap.approvals_requested,
        snap.approvals_granted,
        snap.approvals_denied,
        snap.executions,
        snap.failures,
        snap.duplicates_suppressed,
    )
    return snap


def persist_agent_auth_metric_snapshot(
    db: Any, snapshot: AgentAuthMetricSnapshot, *, commit: bool = True
) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_auth_metric_snapshot (
            scope TEXT PRIMARY KEY,
            proposed INTEGER NOT NULL,
            approvals_requested INTEGER NOT NULL,
            approvals_granted INTEGER NOT NULL,
            approvals_denied INTEGER NOT NULL,
            executions INTEGER NOT NULL,
            failures INTEGER NOT NULL,
            retries INTEGER NOT NULL,
            duplicates_suppressed INTEGER NOT NULL,
            expired INTEGER NOT NULL,
            errors INTEGER NOT NULL,
            computed_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        INSERT INTO agent_auth_metric_snapshot (
            scope, proposed, approvals_requested, approvals_granted,
            approvals_denied, executions, failures, retries,
            duplicates_suppressed, expired, errors, computed_at
        ) VALUES ('global',?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(scope) DO UPDATE SET
            proposed=excluded.proposed,
            approvals_requested=excluded.approvals_requested,
            approvals_granted=excluded.approvals_granted,
            approvals_denied=excluded.approvals_denied,
            executions=excluded.executions,
            failures=excluded.failures,
            retries=excluded.retries,
            duplicates_suppressed=excluded.duplicates_suppressed,
            expired=excluded.expired,
            errors=excluded.errors,
            computed_at=excluded.computed_at
        """,
        (
            snapshot.proposed,
            snapshot.approvals_requested,
            snapshot.approvals_granted,
            snapshot.approvals_denied,
            snapshot.executions,
            snapshot.failures,
            snapshot.retries,
            snapshot.duplicates_suppressed,
            snapshot.expired,
            snapshot.errors,
            snapshot.computed_at,
        ),
    )
    if commit:
        db.commit()
