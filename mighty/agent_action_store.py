"""Durable Trusted Agent Action store (Milestone 11).

Extends the existing ``actions`` table — does not create a parallel approval
system. Lifecycle is canonical; legacy ``status`` stays synced for Activity /
Attention compatibility.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Canonical lifecycle
STATE_PROPOSED = "proposed"
STATE_AWAITING_AUTHORIZATION = "awaiting_authorization"
STATE_AUTHORIZED = "authorized"
STATE_DENIED = "denied"
STATE_EXECUTING = "executing"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"
STATE_EXPIRED = "expired"

OPEN_AWAITING = frozenset({STATE_PROPOSED, STATE_AWAITING_AUTHORIZATION})
TERMINAL = frozenset(
    {
        STATE_DENIED,
        STATE_COMPLETED,
        STATE_FAILED,
        STATE_CANCELLED,
        STATE_EXPIRED,
    }
)

# Legacy status values Activity / Attention already understand
LEGACY_PENDING = "pending"
LEGACY_APPROVED = "approved"
LEGACY_DENIED = "denied"
LEGACY_TIMEOUT = "timeout"
LEGACY_LOGGED = "logged"

DEFAULT_TIMEOUT_SEC = 300


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_action_id() -> str:
    return secrets.token_hex(16)


def new_approval_token() -> str:
    return secrets.token_urlsafe(24)


def lifecycle_to_legacy_status(lifecycle: str) -> str:
    mapping = {
        STATE_PROPOSED: LEGACY_PENDING,
        STATE_AWAITING_AUTHORIZATION: LEGACY_PENDING,
        STATE_AUTHORIZED: LEGACY_APPROVED,
        STATE_DENIED: LEGACY_DENIED,
        STATE_EXECUTING: LEGACY_APPROVED,
        STATE_COMPLETED: LEGACY_APPROVED,
        STATE_FAILED: LEGACY_APPROVED,
        STATE_CANCELLED: LEGACY_DENIED,
        STATE_EXPIRED: LEGACY_TIMEOUT,
    }
    return mapping.get(lifecycle, LEGACY_PENDING)


def legacy_status_to_lifecycle(status: str) -> str:
    s = str(status or "").strip().lower()
    mapping = {
        LEGACY_PENDING: STATE_AWAITING_AUTHORIZATION,
        LEGACY_APPROVED: STATE_AUTHORIZED,
        LEGACY_DENIED: STATE_DENIED,
        LEGACY_TIMEOUT: STATE_EXPIRED,
        LEGACY_LOGGED: STATE_COMPLETED,
        STATE_AWAITING_AUTHORIZATION: STATE_AWAITING_AUTHORIZATION,
        STATE_AUTHORIZED: STATE_AUTHORIZED,
        STATE_DENIED: STATE_DENIED,
        STATE_EXECUTING: STATE_EXECUTING,
        STATE_COMPLETED: STATE_COMPLETED,
        STATE_FAILED: STATE_FAILED,
        STATE_CANCELLED: STATE_CANCELLED,
        STATE_EXPIRED: STATE_EXPIRED,
        STATE_PROPOSED: STATE_PROPOSED,
    }
    return mapping.get(s, STATE_AWAITING_AUTHORIZATION)


def action_fingerprint(
    *,
    user_id: str,
    agent_id: str | None,
    action_type: str,
    label: str,
    fields: Any,
) -> str:
    fields_blob = json.dumps(fields, sort_keys=True, default=str) if fields else ""
    payload = (
        f"{user_id}|{agent_id or ''}|{action_type}|{label}|{fields_blob}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def proposal_hash(
    *,
    action_id: str,
    user_id: str,
    action_type: str,
    label: str,
    fields: Any,
    consequence_level: str,
    agent_id: str | None,
) -> str:
    payload = {
        "action_id": action_id,
        "user_id": user_id,
        "action_type": action_type,
        "label": label,
        "fields": fields,
        "consequence_level": consequence_level,
        "agent_id": agent_id,
    }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentAction:
    action_id: str
    user_id: str
    action_type: str
    label: str
    fields: Any
    lifecycle_state: str
    status: str
    consequence_level: str
    agent_id: str | None
    provider: str | None
    fingerprint: str
    proposal_hash: str
    approval_token: str | None
    created_at: str
    decided_at: str | None
    expires_at: str | None
    outcome: str | None
    auth_channel: str | None = None
    execution_attempt: int = 0
    decision_explanation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "user_id": self.user_id,
            "action_type": self.action_type,
            "label": self.label,
            "fields": self.fields,
            "lifecycle_state": self.lifecycle_state,
            "status": self.status,
            "consequence_level": self.consequence_level,
            "agent_id": self.agent_id,
            "provider": self.provider,
            "fingerprint": self.fingerprint,
            "proposal_hash": self.proposal_hash,
            "approval_token": self.approval_token,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "expires_at": self.expires_at,
            "outcome": self.outcome,
            "auth_channel": self.auth_channel,
            "execution_attempt": self.execution_attempt,
            "decision_explanation": self.decision_explanation,
        }


def ensure_agent_action_tables(db: Any, *, commit: bool = True) -> None:
    """Ensure ``actions`` exists and M11 columns are present."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS actions (
            id             TEXT PRIMARY KEY,
            user_id        TEXT NOT NULL,
            action_type    TEXT NOT NULL,
            label          TEXT NOT NULL,
            fields         TEXT,
            status         TEXT NOT NULL,
            outcome        TEXT,
            approval_token TEXT UNIQUE,
            created_at     TEXT NOT NULL,
            decided_at     TEXT,
            expires_at     TEXT
        )
        """
    )
    cols: set[str] = set()
    try:
        for row in db.execute("PRAGMA table_info(actions)").fetchall():
            try:
                cols.add(str(row["name"]))
            except (TypeError, KeyError, IndexError):
                cols.add(str(row[1]))
    except Exception:
        cols = set()

    alterations = [
        ("consequence_level", "TEXT DEFAULT 'routine'"),
        ("lifecycle_state", "TEXT"),
        ("agent_id", "TEXT"),
        ("provider", "TEXT"),
        ("fingerprint", "TEXT"),
        ("proposal_hash", "TEXT"),
        ("auth_channel", "TEXT"),
        ("execution_attempt", "INTEGER DEFAULT 0"),
        ("decision_explanation", "TEXT"),
    ]
    for name, decl in alterations:
        if name not in cols:
            try:
                db.execute(f"ALTER TABLE actions ADD COLUMN {name} {decl}")
            except Exception:
                pass

    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_actions_user ON actions(user_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_actions_token ON actions(approval_token)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_actions_fingerprint "
        "ON actions(user_id, fingerprint)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_actions_lifecycle "
        "ON actions(user_id, lifecycle_state)"
    )
    if commit:
        db.commit()


def _parse_fields(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _row_to_action(row: Any) -> AgentAction:
    try:
        mapping = dict(row)
    except Exception:
        mapping = {k: row[k] for k in row.keys()}  # type: ignore[attr-defined]
    status = str(mapping.get("status") or "")
    lifecycle = str(mapping.get("lifecycle_state") or "").strip()
    if not lifecycle:
        lifecycle = legacy_status_to_lifecycle(status)
    return AgentAction(
        action_id=str(mapping.get("id") or ""),
        user_id=str(mapping.get("user_id") or ""),
        action_type=str(mapping.get("action_type") or ""),
        label=str(mapping.get("label") or ""),
        fields=_parse_fields(mapping.get("fields")),
        lifecycle_state=lifecycle,
        status=status,
        consequence_level=str(mapping.get("consequence_level") or "routine"),
        agent_id=(str(mapping["agent_id"]) if mapping.get("agent_id") else None),
        provider=(str(mapping["provider"]).lower() if mapping.get("provider") else None),
        fingerprint=str(mapping.get("fingerprint") or ""),
        proposal_hash=str(mapping.get("proposal_hash") or ""),
        approval_token=mapping.get("approval_token"),
        created_at=str(mapping.get("created_at") or ""),
        decided_at=mapping.get("decided_at"),
        expires_at=mapping.get("expires_at"),
        outcome=mapping.get("outcome"),
        auth_channel=mapping.get("auth_channel"),
        execution_attempt=int(mapping.get("execution_attempt") or 0),
        decision_explanation=mapping.get("decision_explanation"),
    )


def get_action(db: Any, action_id: str, user_id: str | None = None) -> AgentAction | None:
    ensure_agent_action_tables(db, commit=False)
    if user_id:
        row = db.execute(
            "SELECT * FROM actions WHERE id=? AND user_id=?",
            (action_id, user_id),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT * FROM actions WHERE id=?", (action_id,)
        ).fetchone()
    if not row:
        return None
    return _row_to_action(row)


def has_open_fingerprint(db: Any, user_id: str, fingerprint: str) -> bool:
    ensure_agent_action_tables(db, commit=False)
    row = db.execute(
        """
        SELECT id FROM actions
        WHERE user_id=? AND fingerprint=?
          AND (
            lower(COALESCE(lifecycle_state, '')) IN ('proposed','awaiting_authorization','authorized','executing')
            OR (lifecycle_state IS NULL AND lower(status)='pending')
          )
        LIMIT 1
        """,
        (user_id, fingerprint),
    ).fetchone()
    return row is not None


def insert_action(
    db: Any,
    *,
    user_id: str,
    action_type: str,
    label: str,
    fields: Any = None,
    consequence_level: str = "routine",
    agent_id: str | None = None,
    provider: str | None = None,
    lifecycle_state: str,
    approval_token: str | None = None,
    expires_at: str | None = None,
    decided_at: str | None = None,
    outcome: str | None = None,
    auth_channel: str | None = None,
    decision_explanation: str | None = None,
    action_id: str | None = None,
    created_at: str | None = None,
    commit: bool = True,
) -> AgentAction:
    ensure_agent_action_tables(db, commit=False)
    aid = action_id or new_action_id()
    stamp = created_at or utc_now_iso()
    fp = action_fingerprint(
        user_id=user_id,
        agent_id=agent_id,
        action_type=action_type,
        label=label,
        fields=fields,
    )
    ph = proposal_hash(
        action_id=aid,
        user_id=user_id,
        action_type=action_type,
        label=label,
        fields=fields,
        consequence_level=consequence_level,
        agent_id=agent_id,
    )
    legacy = lifecycle_to_legacy_status(lifecycle_state)
    # informational completed → logged for Activity
    if lifecycle_state == STATE_COMPLETED and consequence_level == "informational":
        legacy = LEGACY_LOGGED

    db.execute(
        """
        INSERT INTO actions (
            id, user_id, action_type, label, fields, status, outcome,
            approval_token, created_at, decided_at, expires_at, consequence_level,
            lifecycle_state, agent_id, provider, fingerprint, proposal_hash,
            auth_channel, execution_attempt, decision_explanation
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)
        """,
        (
            aid,
            user_id,
            action_type,
            label,
            json.dumps(fields) if fields is not None else None,
            legacy,
            outcome,
            approval_token,
            stamp,
            decided_at,
            expires_at,
            consequence_level,
            lifecycle_state,
            agent_id,
            (provider or None),
            fp,
            ph,
            auth_channel,
            decision_explanation,
        ),
    )
    if commit:
        db.commit()
    action = get_action(db, aid, user_id)
    assert action is not None
    return action


def update_lifecycle(
    db: Any,
    action_id: str,
    *,
    lifecycle_state: str,
    decided_at: str | None = None,
    outcome: str | None = None,
    auth_channel: str | None = None,
    execution_attempt: int | None = None,
    commit: bool = True,
) -> AgentAction | None:
    ensure_agent_action_tables(db, commit=False)
    existing = get_action(db, action_id)
    if existing is None:
        return None
    legacy = lifecycle_to_legacy_status(lifecycle_state)
    if (
        lifecycle_state == STATE_COMPLETED
        and existing.consequence_level == "informational"
    ):
        legacy = LEGACY_LOGGED
    sets = ["lifecycle_state=?", "status=?"]
    params: list[Any] = [lifecycle_state, legacy]
    if decided_at is not None:
        sets.append("decided_at=?")
        params.append(decided_at)
    if outcome is not None:
        sets.append("outcome=?")
        params.append(outcome)
    if auth_channel is not None:
        sets.append("auth_channel=?")
        params.append(auth_channel)
    if execution_attempt is not None:
        sets.append("execution_attempt=?")
        params.append(execution_attempt)
    params.append(action_id)
    db.execute(
        f"UPDATE actions SET {', '.join(sets)} WHERE id=?",
        params,
    )
    if commit:
        db.commit()
    return get_action(db, action_id)


def expire_awaiting_actions(
    db: Any, *, now: datetime | None = None, commit: bool = True
) -> int:
    ensure_agent_action_tables(db, commit=False)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stamp = now.replace(microsecond=0).isoformat()
    cur = db.execute(
        """
        UPDATE actions
        SET lifecycle_state=?, status=?, decided_at=?
        WHERE (
            lifecycle_state IN ('proposed','awaiting_authorization')
            OR (lifecycle_state IS NULL AND status='pending')
        )
        AND expires_at IS NOT NULL AND expires_at < ?
        """,
        (STATE_EXPIRED, LEGACY_TIMEOUT, stamp, stamp),
    )
    if commit:
        db.commit()
    return int(cur.rowcount or 0)


def list_actions(
    db: Any,
    user_id: str,
    *,
    lifecycle_states: list[str] | None = None,
    limit: int = 100,
) -> list[AgentAction]:
    ensure_agent_action_tables(db, commit=False)
    clauses = ["user_id=?"]
    params: list[Any] = [user_id]
    if lifecycle_states:
        placeholders = ",".join("?" for _ in lifecycle_states)
        clauses.append(f"lifecycle_state IN ({placeholders})")
        params.extend(lifecycle_states)
    where = " AND ".join(clauses)
    rows = db.execute(
        f"""
        SELECT * FROM actions
        WHERE {where}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [_row_to_action(r) for r in rows]
