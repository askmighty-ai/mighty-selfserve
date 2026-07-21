"""Attention production metrics (Milestone 5).

Computes autonomous coverage, false silence, false interruption, and delivery
SLA rates. Never raises to Home/Worker/sync callers. Intended for supervisor
heartbeat — not GET hot paths.

See docs/ATTENTION_METRICS.md.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from mighty.attention import AttentionClass, AttentionUrgency
from mighty.attention_delivery import (
    BLOCKER_DELIVERY_SLA_SECONDS,
    get_delivery_receipt,
)
from mighty.attention_engine import read_attention
from mighty.attention_loaders import load_account_states_for_attention
from mighty.auth_truth import ACCESS_MANAGED_RUNTIME, normalize_access_method
from mighty.runtime_access_state import (
    STATUS_HEALTHY,
    compute_presentation_status,
    get_runtime_access_state,
)

logger = logging.getLogger(__name__)

_BLOCKER_CLASSES = frozenset(
    {
        AttentionClass.TRUST,
        AttentionClass.AGENT_AUTHORIZATION,
        AttentionClass.AUTH_BLOCKER,
        AttentionClass.SYSTEM,
    }
)


@dataclass(frozen=True)
class AttentionMetricSnapshot:
    generated_at: str
    users_scanned: int
    autonomous_eligible: int
    autonomous_covered: int
    autonomous_coverage: float
    push_eligible_blockers: int
    false_silence_count: int
    false_silence_rate: float
    unexpected_interrupt_count: int
    visible_blocker_count: int
    false_interruption_rate: float
    delivery_sla_ok: int
    delivery_sla_total: int
    delivery_sla_rate: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ensure_attention_metric_tables(db: Any, *, commit: bool = True) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS attention_metric_snapshot (
            scope        TEXT NOT NULL PRIMARY KEY,
            generated_at TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
        )
        """
    )
    if commit:
        db.commit()


def persist_attention_metric_snapshot(
    db: Any,
    snapshot: AttentionMetricSnapshot,
    *,
    scope: str = "global",
    commit: bool = True,
) -> None:
    ensure_attention_metric_tables(db, commit=False)
    db.execute(
        """
        INSERT INTO attention_metric_snapshot (scope, generated_at, snapshot_json)
        VALUES (?, ?, ?)
        ON CONFLICT(scope) DO UPDATE SET
            generated_at=excluded.generated_at,
            snapshot_json=excluded.snapshot_json
        """,
        (
            str(scope),
            snapshot.generated_at,
            json.dumps(snapshot.to_dict(), separators=(",", ":"), sort_keys=True),
        ),
    )
    if commit:
        db.commit()


def load_attention_metric_snapshot(
    db: Any, *, scope: str = "global"
) -> AttentionMetricSnapshot | None:
    ensure_attention_metric_tables(db, commit=False)
    row = db.execute(
        "SELECT snapshot_json FROM attention_metric_snapshot WHERE scope=?",
        (str(scope),),
    ).fetchone()
    if not row:
        return None
    raw = row[0] if not isinstance(row, dict) else row.get("snapshot_json")
    try:
        payload = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    try:
        return AttentionMetricSnapshot(**payload)
    except TypeError:
        return None


def compute_attention_metrics(
    db: Any,
    *,
    now: datetime,
    user_ids: list[str] | None = None,
) -> AttentionMetricSnapshot:
    """Scan enrolled users and compute product metrics. Never raises."""
    now = _ensure_aware(now)
    generated_at = now.replace(microsecond=0).isoformat()
    try:
        if user_ids is None:
            user_ids = _list_enrolled_user_ids(db)
    except Exception:
        logger.exception("attention_metrics_user_list_failed")
        user_ids = []

    users_scanned = 0
    autonomous_eligible = 0
    push_eligible_blockers = 0
    false_silence_count = 0
    unexpected_interrupt_count = 0
    visible_blocker_count = 0
    delivery_sla_ok = 0
    delivery_sla_total = 0

    for raw in user_ids:
        uid = str(raw or "").strip()
        if not uid:
            continue
        users_scanned += 1
        try:
            accounts = load_account_states_for_attention(db, uid)
            for account in accounts:
                method = normalize_access_method(
                    getattr(account, "access_method", None)
                )
                if method == ACCESS_MANAGED_RUNTIME:
                    autonomous_eligible += 1

            state = read_attention(db, uid, now=now)
            primary = state.primary
            if primary is None:
                continue
            if primary.attention_class in _BLOCKER_CLASSES:
                visible_blocker_count += 1
                if not primary.interruption_expected:
                    unexpected_interrupt_count += 1

            if primary.urgency is AttentionUrgency.BLOCKER:
                push_eligible_blockers += 1
                receipt = get_delivery_receipt(db, uid, primary.attention_id, "push")
                if receipt is None or receipt.get("status") != "delivered":
                    observed = _parse_iso(primary.observed_at) or now
                    age = (now - observed).total_seconds()
                    if age >= BLOCKER_DELIVERY_SLA_SECONDS:
                        false_silence_count += 1
                if receipt is not None:
                    delivery_sla_total += 1
                    first = _parse_iso(
                        receipt.get("first_attempted_at") or receipt.get("attempted_at")
                    )
                    attempted = _parse_iso(receipt.get("attempted_at"))
                    if receipt.get("status") == "delivered":
                        if first is None or attempted is None:
                            delivery_sla_ok += 1
                        elif (attempted - first).total_seconds() <= (
                            BLOCKER_DELIVERY_SLA_SECONDS
                        ):
                            delivery_sla_ok += 1
        except Exception:
            logger.exception("attention_metrics_user_failed user_id=%s", uid)
            continue

    autonomous_covered = _count_autonomous_covered(db, user_ids, now=now)

    return AttentionMetricSnapshot(
        generated_at=generated_at,
        users_scanned=users_scanned,
        autonomous_eligible=autonomous_eligible,
        autonomous_covered=autonomous_covered,
        autonomous_coverage=_rate(autonomous_covered, autonomous_eligible),
        push_eligible_blockers=push_eligible_blockers,
        false_silence_count=false_silence_count,
        false_silence_rate=_rate(false_silence_count, push_eligible_blockers),
        unexpected_interrupt_count=unexpected_interrupt_count,
        visible_blocker_count=visible_blocker_count,
        false_interruption_rate=_rate(
            unexpected_interrupt_count, visible_blocker_count
        ),
        delivery_sla_ok=delivery_sla_ok,
        delivery_sla_total=delivery_sla_total,
        delivery_sla_rate=_rate(delivery_sla_ok, delivery_sla_total),
    )


def run_attention_metrics_sweep(
    db: Any,
    *,
    now: datetime,
    user_ids: list[str] | None = None,
) -> AttentionMetricSnapshot | None:
    """Compute + persist global metrics. Never raises."""
    try:
        snap = compute_attention_metrics(db, now=now, user_ids=user_ids)
        persist_attention_metric_snapshot(db, snap)
        logger.info(
            "attention.metrics coverage=%.3f false_silence=%.3f "
            "false_interruption=%.3f delivery_sla=%.3f users=%s",
            snap.autonomous_coverage,
            snap.false_silence_rate,
            snap.false_interruption_rate,
            snap.delivery_sla_rate,
            snap.users_scanned,
        )
        return snap
    except Exception:
        logger.exception("attention_metrics_sweep_failed")
        return None


def _count_autonomous_covered(
    db: Any, user_ids: list[str], *, now: datetime
) -> int:
    covered = 0
    for raw in user_ids:
        uid = str(raw or "").strip()
        if not uid:
            continue
        try:
            accounts = load_account_states_for_attention(db, uid)
            runtime_accounts = [
                a
                for a in accounts
                if normalize_access_method(getattr(a, "access_method", None))
                == ACCESS_MANAGED_RUNTIME
            ]
            if not runtime_accounts:
                continue
            state = read_attention(db, uid, now=now)
            if state.primary is not None and state.primary.attention_class in {
                AttentionClass.TRUST,
                AttentionClass.AUTH_BLOCKER,
            }:
                continue
            for account in runtime_accounts:
                provider = str(getattr(account, "provider", "") or "").strip().lower()
                row = get_runtime_access_state(db, uid, provider)
                if compute_presentation_status(row, now=now) == STATUS_HEALTHY:
                    covered += 1
        except Exception:
            continue
    return covered


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _list_enrolled_user_ids(db: Any) -> list[str]:
    rows = db.execute(
        "SELECT DISTINCT user_id FROM account_state ORDER BY user_id ASC"
    ).fetchall()
    result: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            uid = row.get("user_id")
        else:
            try:
                uid = row["user_id"]
            except Exception:
                uid = row[0]
        text = str(uid or "").strip()
        if text:
            result.append(text)
    return result


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
