"""Immutable execution receipts for Trusted Agent Actions (Milestone 11).

Append-only. Never updated after insert. Each receipt binds Action,
agent, authorization decision, execution result, and integrity hashes.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_receipt_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class ExecutionReceipt:
    receipt_id: str
    action_id: str
    user_id: str
    agent_id: str | None
    authorization_decision: str
    authorization_at: str | None
    auth_channel: str | None
    execution_result: str
    execution_attempt: int
    proposal_hash: str
    receipt_hash: str
    prev_receipt_hash: str | None
    detail: dict[str, Any]
    created_at: str
    provider: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "action_id": self.action_id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "authorization_decision": self.authorization_decision,
            "authorization_at": self.authorization_at,
            "auth_channel": self.auth_channel,
            "execution_result": self.execution_result,
            "execution_attempt": self.execution_attempt,
            "proposal_hash": self.proposal_hash,
            "receipt_hash": self.receipt_hash,
            "prev_receipt_hash": self.prev_receipt_hash,
            "detail": dict(self.detail),
            "created_at": self.created_at,
            "provider": self.provider,
        }


def compute_receipt_hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def ensure_receipt_tables(db: Any, *, commit: bool = True) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS action_execution_receipts (
            receipt_id              TEXT PRIMARY KEY,
            action_id               TEXT NOT NULL,
            user_id                 TEXT NOT NULL,
            agent_id                TEXT,
            authorization_decision  TEXT NOT NULL,
            authorization_at        TEXT,
            auth_channel            TEXT,
            execution_result        TEXT NOT NULL,
            execution_attempt       INTEGER NOT NULL DEFAULT 1,
            proposal_hash           TEXT NOT NULL,
            receipt_hash            TEXT NOT NULL,
            prev_receipt_hash       TEXT,
            detail_json             TEXT NOT NULL DEFAULT '{}',
            provider                TEXT,
            created_at              TEXT NOT NULL,
            UNIQUE(action_id, execution_attempt)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_action_receipts_action "
        "ON action_execution_receipts(action_id, created_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_action_receipts_user "
        "ON action_execution_receipts(user_id, created_at DESC)"
    )
    if commit:
        db.commit()


def get_receipt_for_attempt(
    db: Any, action_id: str, execution_attempt: int
) -> ExecutionReceipt | None:
    ensure_receipt_tables(db, commit=False)
    row = db.execute(
        """
        SELECT * FROM action_execution_receipts
        WHERE action_id=? AND execution_attempt=?
        """,
        (action_id, execution_attempt),
    ).fetchone()
    if not row:
        return None
    return _row_to_receipt(row)


def latest_receipt_hash(db: Any, action_id: str) -> str | None:
    ensure_receipt_tables(db, commit=False)
    row = db.execute(
        """
        SELECT receipt_hash FROM action_execution_receipts
        WHERE action_id=?
        ORDER BY execution_attempt DESC, created_at DESC
        LIMIT 1
        """,
        (action_id,),
    ).fetchone()
    if not row:
        return None
    try:
        return row["receipt_hash"]
    except (TypeError, KeyError):
        return row[0]


def persist_receipt(
    db: Any,
    *,
    action_id: str,
    user_id: str,
    agent_id: str | None,
    authorization_decision: str,
    authorization_at: str | None,
    auth_channel: str | None,
    execution_result: str,
    execution_attempt: int,
    proposal_hash: str,
    detail: dict[str, Any] | None = None,
    provider: str | None = None,
    created_at: str | None = None,
    commit: bool = True,
) -> ExecutionReceipt:
    """Insert an immutable receipt. Idempotent on (action_id, attempt)."""
    ensure_receipt_tables(db, commit=False)
    existing = get_receipt_for_attempt(db, action_id, execution_attempt)
    if existing is not None:
        return existing

    stamp = created_at or utc_now_iso()
    detail_payload = dict(detail or {})
    prev = latest_receipt_hash(db, action_id)
    receipt_id = new_receipt_id()
    hash_payload = {
        "receipt_id": receipt_id,
        "action_id": action_id,
        "user_id": user_id,
        "agent_id": agent_id,
        "authorization_decision": authorization_decision,
        "authorization_at": authorization_at,
        "auth_channel": auth_channel,
        "execution_result": execution_result,
        "execution_attempt": execution_attempt,
        "proposal_hash": proposal_hash,
        "prev_receipt_hash": prev,
        "detail": detail_payload,
        "provider": provider,
        "created_at": stamp,
    }
    rhash = compute_receipt_hash(hash_payload)

    db.execute(
        """
        INSERT INTO action_execution_receipts (
            receipt_id, action_id, user_id, agent_id, authorization_decision,
            authorization_at, auth_channel, execution_result, execution_attempt,
            proposal_hash, receipt_hash, prev_receipt_hash, detail_json,
            provider, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            receipt_id,
            action_id,
            user_id,
            agent_id,
            authorization_decision,
            authorization_at,
            auth_channel,
            execution_result,
            execution_attempt,
            proposal_hash,
            rhash,
            prev,
            json.dumps(detail_payload, default=str),
            provider,
            stamp,
        ),
    )
    if commit:
        db.commit()
    receipt = get_receipt_for_attempt(db, action_id, execution_attempt)
    assert receipt is not None
    return receipt


def list_receipts(
    db: Any, action_id: str | None = None, user_id: str | None = None, *, limit: int = 50
) -> list[ExecutionReceipt]:
    ensure_receipt_tables(db, commit=False)
    clauses: list[str] = []
    params: list[Any] = []
    if action_id:
        clauses.append("action_id=?")
        params.append(action_id)
    if user_id:
        clauses.append("user_id=?")
        params.append(user_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = db.execute(
        f"""
        SELECT * FROM action_execution_receipts
        {where}
        ORDER BY created_at DESC, receipt_id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [_row_to_receipt(r) for r in rows]


def verify_receipt_integrity(receipt: ExecutionReceipt) -> bool:
    """Recompute receipt hash and compare (detail + core fields)."""
    payload = {
        "receipt_id": receipt.receipt_id,
        "action_id": receipt.action_id,
        "user_id": receipt.user_id,
        "agent_id": receipt.agent_id,
        "authorization_decision": receipt.authorization_decision,
        "authorization_at": receipt.authorization_at,
        "auth_channel": receipt.auth_channel,
        "execution_result": receipt.execution_result,
        "execution_attempt": receipt.execution_attempt,
        "proposal_hash": receipt.proposal_hash,
        "prev_receipt_hash": receipt.prev_receipt_hash,
        "detail": dict(receipt.detail),
        "provider": receipt.provider,
        "created_at": receipt.created_at,
    }
    return compute_receipt_hash(payload) == receipt.receipt_hash


def _row_to_receipt(row: Any) -> ExecutionReceipt:
    try:
        mapping = dict(row)
    except Exception:
        mapping = {k: row[k] for k in row.keys()}  # type: ignore[attr-defined]
    detail = {}
    try:
        detail = json.loads(mapping.get("detail_json") or "{}")
    except Exception:
        detail = {}
    return ExecutionReceipt(
        receipt_id=str(mapping.get("receipt_id") or ""),
        action_id=str(mapping.get("action_id") or ""),
        user_id=str(mapping.get("user_id") or ""),
        agent_id=mapping.get("agent_id"),
        authorization_decision=str(mapping.get("authorization_decision") or ""),
        authorization_at=mapping.get("authorization_at"),
        auth_channel=mapping.get("auth_channel"),
        execution_result=str(mapping.get("execution_result") or ""),
        execution_attempt=int(mapping.get("execution_attempt") or 1),
        proposal_hash=str(mapping.get("proposal_hash") or ""),
        receipt_hash=str(mapping.get("receipt_hash") or ""),
        prev_receipt_hash=mapping.get("prev_receipt_hash"),
        detail=detail if isinstance(detail, dict) else {},
        created_at=str(mapping.get("created_at") or ""),
        provider=mapping.get("provider"),
    )
