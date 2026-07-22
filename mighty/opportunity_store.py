"""Durable opportunity facts (Milestone 10).

Lifecycle-aware store. Not an Attention ranker and not a marketing feed.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Sequence

from mighty.value_policy import OpportunityCandidate

STATE_DISCOVERED = "discovered"
STATE_ACTIVE = "active"
STATE_CONSUMED = "consumed"
STATE_EXPIRED = "expired"
STATE_DISMISSED = "dismissed"

OPEN_STATES = frozenset({STATE_DISCOVERED, STATE_ACTIVE})
TERMINAL_PRESERVE = frozenset({STATE_CONSUMED, STATE_DISMISSED})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_opportunity_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class OpportunityRecord:
    opportunity_id: str
    user_id: str
    provider: str
    kind: str
    field_key: str
    label: str
    value: str
    field_type: str
    score: int
    urgency: str
    days_left: int | None
    exp_date: str | None
    value_estimate: float | None
    fingerprint: str
    lifecycle_state: str
    snapshot_id: str | None
    summary: str
    created_at: str
    updated_at: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "user_id": self.user_id,
            "provider": self.provider,
            "kind": self.kind,
            "field_key": self.field_key,
            "label": self.label,
            "value": self.value,
            "field_type": self.field_type,
            "score": self.score,
            "urgency": self.urgency,
            "days_left": self.days_left,
            "exp_date": self.exp_date,
            "value_estimate": self.value_estimate,
            "fingerprint": self.fingerprint,
            "lifecycle_state": self.lifecycle_state,
            "snapshot_id": self.snapshot_id,
            "summary": self.summary,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }


@dataclass
class ReconcileResult:
    generated: int = 0
    updated: int = 0
    expired: int = 0
    duplicates_suppressed: int = 0
    preserved_dismissed: int = 0
    preserved_consumed: int = 0
    active: int = 0
    value_at_risk_total: float = 0.0
    records: list[OpportunityRecord] | None = None


def ensure_opportunity_tables(db: Any, *, commit: bool = True) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS account_opportunities (
            opportunity_id   TEXT PRIMARY KEY,
            user_id          TEXT NOT NULL,
            provider         TEXT NOT NULL,
            kind             TEXT NOT NULL,
            field_key        TEXT NOT NULL,
            label            TEXT NOT NULL,
            value            TEXT NOT NULL DEFAULT '',
            field_type       TEXT NOT NULL DEFAULT 'other',
            score            INTEGER NOT NULL DEFAULT 0,
            urgency          TEXT NOT NULL DEFAULT 'info',
            days_left        INTEGER,
            exp_date         TEXT,
            value_estimate   REAL,
            fingerprint      TEXT NOT NULL,
            lifecycle_state  TEXT NOT NULL,
            snapshot_id      TEXT,
            summary          TEXT NOT NULL DEFAULT '',
            metadata_json    TEXT NOT NULL DEFAULT '{}',
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL,
            UNIQUE(user_id, provider, fingerprint)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_account_opportunities_user_provider "
        "ON account_opportunities(user_id, provider, lifecycle_state)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_account_opportunities_fingerprint "
        "ON account_opportunities(user_id, provider, fingerprint)"
    )
    if commit:
        db.commit()


def _row_to_record(row: Any) -> OpportunityRecord:
    meta = {}
    try:
        meta = json.loads(row["metadata_json"] or "{}")
    except Exception:
        meta = {}
    return OpportunityRecord(
        opportunity_id=row["opportunity_id"],
        user_id=row["user_id"],
        provider=row["provider"],
        kind=row["kind"],
        field_key=row["field_key"],
        label=row["label"],
        value=row["value"] or "",
        field_type=row["field_type"] or "other",
        score=int(row["score"] or 0),
        urgency=row["urgency"] or "info",
        days_left=row["days_left"],
        exp_date=row["exp_date"],
        value_estimate=row["value_estimate"],
        fingerprint=row["fingerprint"],
        lifecycle_state=row["lifecycle_state"],
        snapshot_id=row["snapshot_id"],
        summary=row["summary"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        metadata=meta if isinstance(meta, dict) else {},
    )


def list_opportunities(
    db: Any,
    user_id: str,
    provider: str | None = None,
    *,
    states: Sequence[str] | None = None,
    limit: int = 100,
) -> list[OpportunityRecord]:
    ensure_opportunity_tables(db, commit=False)
    clauses = ["user_id=?"]
    params: list[Any] = [user_id]
    if provider:
        clauses.append("provider=?")
        params.append(str(provider).strip().lower())
    if states:
        placeholders = ",".join("?" for _ in states)
        clauses.append(f"lifecycle_state IN ({placeholders})")
        params.extend(states)
    where = " AND ".join(clauses)
    rows = db.execute(
        f"""
        SELECT * FROM account_opportunities
        WHERE {where}
        ORDER BY score DESC, updated_at DESC, opportunity_id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [_row_to_record(r) for r in rows]


def dismiss_opportunity(
    db: Any, user_id: str, opportunity_id: str, *, commit: bool = True
) -> bool:
    ensure_opportunity_tables(db, commit=False)
    stamp = utc_now_iso()
    cur = db.execute(
        """
        UPDATE account_opportunities
        SET lifecycle_state=?, updated_at=?
        WHERE user_id=? AND opportunity_id=? AND lifecycle_state IN ('discovered','active')
        """,
        (STATE_DISMISSED, stamp, user_id, opportunity_id),
    )
    if commit:
        db.commit()
    return (cur.rowcount or 0) > 0


def mark_opportunity_consumed(
    db: Any, user_id: str, opportunity_id: str, *, commit: bool = True
) -> bool:
    ensure_opportunity_tables(db, commit=False)
    stamp = utc_now_iso()
    cur = db.execute(
        """
        UPDATE account_opportunities
        SET lifecycle_state=?, updated_at=?
        WHERE user_id=? AND opportunity_id=? AND lifecycle_state IN ('discovered','active')
        """,
        (STATE_CONSUMED, stamp, user_id, opportunity_id),
    )
    if commit:
        db.commit()
    return (cur.rowcount or 0) > 0


def _is_expired_candidate(cand: OpportunityCandidate, *, today: date) -> bool:
    if cand.days_left is not None and cand.days_left < 0:
        return True
    if cand.exp_date:
        try:
            return date.fromisoformat(str(cand.exp_date)[:10]) < today
        except ValueError:
            return False
    return False


def reconcile_opportunities(
    db: Any,
    *,
    user_id: str,
    provider: str,
    candidates: Sequence[OpportunityCandidate],
    snapshot_id: str | None = None,
    today: date | None = None,
    commit: bool = True,
) -> ReconcileResult:
    """Upsert active candidates; expire missing open rows; preserve dismiss/consumed."""
    ensure_opportunity_tables(db, commit=False)
    uid = str(user_id).strip()
    prov = str(provider).strip().lower()
    today = today or date.today()
    stamp = utc_now_iso()
    result = ReconcileResult(records=[])

    existing_rows = db.execute(
        """
        SELECT * FROM account_opportunities
        WHERE user_id=? AND provider=?
        """,
        (uid, prov),
    ).fetchall()
    by_fp = {str(r["fingerprint"]): _row_to_record(r) for r in existing_rows}

    live_fps: set[str] = set()
    active_candidates = [c for c in candidates if not c.suppressed]

    for cand in active_candidates:
        fp = cand.fingerprint
        live_fps.add(fp)
        prior = by_fp.get(fp)
        expired = _is_expired_candidate(cand, today=today)

        if prior and prior.lifecycle_state in TERMINAL_PRESERVE:
            if prior.lifecycle_state == STATE_DISMISSED:
                result.preserved_dismissed += 1
            else:
                result.preserved_consumed += 1
            result.duplicates_suppressed += 1
            continue

        state = STATE_EXPIRED if expired else (
            STATE_DISCOVERED if prior is None else STATE_ACTIVE
        )
        if prior is None:
            oid = new_opportunity_id()
            created = stamp
            result.generated += 1
        else:
            oid = prior.opportunity_id
            created = prior.created_at
            if prior.lifecycle_state == STATE_EXPIRED and not expired:
                state = STATE_ACTIVE
                result.updated += 1
            elif prior.lifecycle_state in OPEN_STATES:
                state = STATE_EXPIRED if expired else STATE_ACTIVE
                result.updated += 1
            if expired and prior.lifecycle_state in OPEN_STATES:
                result.expired += 1

        meta = dict(cand.metadata)
        db.execute(
            """
            INSERT INTO account_opportunities (
                opportunity_id, user_id, provider, kind, field_key, label, value,
                field_type, score, urgency, days_left, exp_date, value_estimate,
                fingerprint, lifecycle_state, snapshot_id, summary, metadata_json,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id, provider, fingerprint) DO UPDATE SET
                kind=excluded.kind,
                field_key=excluded.field_key,
                label=excluded.label,
                value=excluded.value,
                field_type=excluded.field_type,
                score=excluded.score,
                urgency=excluded.urgency,
                days_left=excluded.days_left,
                exp_date=excluded.exp_date,
                value_estimate=excluded.value_estimate,
                lifecycle_state=CASE
                    WHEN account_opportunities.lifecycle_state IN ('dismissed','consumed')
                    THEN account_opportunities.lifecycle_state
                    ELSE excluded.lifecycle_state
                END,
                snapshot_id=excluded.snapshot_id,
                summary=excluded.summary,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                oid,
                uid,
                prov,
                cand.kind,
                cand.field_key,
                cand.label,
                cand.value,
                cand.field_type,
                int(cand.score),
                cand.urgency,
                cand.days_left,
                cand.exp_date,
                cand.value_estimate,
                fp,
                state,
                snapshot_id,
                cand.summary,
                json.dumps(meta, default=str),
                created,
                stamp,
            ),
        )

    # Expire open opportunities no longer evidenced.
    for fp, prior in by_fp.items():
        if fp in live_fps:
            continue
        if prior.lifecycle_state not in OPEN_STATES:
            continue
        db.execute(
            """
            UPDATE account_opportunities
            SET lifecycle_state=?, updated_at=?, snapshot_id=COALESCE(?, snapshot_id)
            WHERE opportunity_id=?
            """,
            (STATE_EXPIRED, stamp, snapshot_id, prior.opportunity_id),
        )
        result.expired += 1

    if commit:
        db.commit()

    active = list_opportunities(
        db, uid, prov, states=list(OPEN_STATES), limit=500
    )
    result.active = len(active)
    var_kinds = {
        "expiring_credit",
        "expiring_certificate",
        "expiring_points",
        "payment_due",
        "renewal",
        "elite_qualification_risk",
    }
    total = 0.0
    for rec in active:
        if rec.kind in var_kinds and rec.value_estimate is not None:
            total += float(rec.value_estimate)
    result.value_at_risk_total = total
    result.records = active
    return result
