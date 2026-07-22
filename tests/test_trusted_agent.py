"""Trusted Agent Authorization lifecycle, receipts, Attention (Milestone 11)."""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.agent_action_store import (
    STATE_AWAITING_AUTHORIZATION,
    STATE_AUTHORIZED,
    STATE_COMPLETED,
    STATE_DENIED,
    ensure_agent_action_tables,
    expire_awaiting_actions,
    get_action,
)
from mighty.attention_compiler import compile_authorize_attention
from mighty.attention_loaders import load_authorize_rows
from mighty.execution_receipt import (
    ensure_receipt_tables,
    list_receipts,
    verify_receipt_integrity,
)
from mighty.trusted_agent import (
    decide_authorization,
    execute_action,
    propose_action,
)

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "a.db"))
    conn.row_factory = sqlite3.Row
    ensure_agent_action_tables(conn)
    ensure_receipt_tables(conn)
    yield conn
    conn.close()


def test_e2e_propose_authorize_execute_receipt(db):
    proposed = propose_action(
        db,
        user_id="u1",
        action_type="redeem",
        label="Redeem dining credit",
        fields={"amount": 40},
        consequence_level="routine",
        agent_id="agent-gpt",
        provider="amex",
        now=NOW,
    )
    assert proposed.action is not None
    assert proposed.action.lifecycle_state == STATE_AWAITING_AUTHORIZATION
    assert proposed.action.status == "pending"
    assert proposed.action.proposal_hash

    # Attention should interrupt
    rows = load_authorize_rows(db, "u1")
    assert any(r.action_id == proposed.action.action_id for r in rows)
    item = compile_authorize_attention(rows[0])
    assert item is not None

    decided = decide_authorization(
        db,
        action_id=proposed.action.action_id,
        user_id="u1",
        decision="approved",
        auth_channel="activity",
        now=NOW,
    )
    assert decided.action.lifecycle_state == STATE_AUTHORIZED

    # Attention clears
    rows_after = load_authorize_rows(db, "u1")
    assert not any(r.action_id == proposed.action.action_id for r in rows_after)

    executed = execute_action(
        db,
        action_id=proposed.action.action_id,
        user_id="u1",
    )
    assert executed.error is None
    assert executed.action.lifecycle_state == STATE_COMPLETED
    assert executed.receipt is not None
    assert verify_receipt_integrity(executed.receipt)
    assert executed.receipt.proposal_hash == proposed.action.proposal_hash
    assert executed.receipt.agent_id == "agent-gpt"
    assert executed.receipt.authorization_decision == "authorized"


def test_idempotent_execution(db):
    proposed = propose_action(
        db,
        user_id="u1",
        action_type="book",
        label="Book hotel",
        consequence_level="informational",
        record_only=True,
        now=NOW,
    )
    assert proposed.action.lifecycle_state == STATE_AUTHORIZED
    first = execute_action(db, action_id=proposed.action.action_id, user_id="u1")
    second = execute_action(db, action_id=proposed.action.action_id, user_id="u1")
    assert first.receipt.receipt_id == second.receipt.receipt_id
    assert second.retried is True
    assert len(list_receipts(db, action_id=proposed.action.action_id)) == 1


def test_duplicate_suppression(db):
    a = propose_action(
        db,
        user_id="u1",
        action_type="pay",
        label="Pay bill",
        fields={"id": 1},
        consequence_level="routine",
        now=NOW,
    )
    assert a.action is not None
    b = propose_action(
        db,
        user_id="u1",
        action_type="pay",
        label="Pay bill",
        fields={"id": 1},
        consequence_level="routine",
        now=NOW,
    )
    assert b.suppressed_duplicate is True
    assert b.action is None


def test_deny_lifecycle(db):
    proposed = propose_action(
        db,
        user_id="u1",
        action_type="transfer",
        label="Transfer points",
        consequence_level="consequential",
        now=NOW,
    )
    decided = decide_authorization(
        db,
        action_id=proposed.action.action_id,
        user_id="u1",
        decision="denied",
        auth_channel="chat",
        now=NOW,
    )
    assert decided.action.lifecycle_state == STATE_DENIED
    executed = execute_action(
        db, action_id=proposed.action.action_id, user_id="u1"
    )
    assert executed.error == "not_authorized"


def test_expire_awaiting(db):
    proposed = propose_action(
        db,
        user_id="u1",
        action_type="modify",
        label="Change reservation",
        consequence_level="routine",
        timeout_sec=1,
        now=NOW - timedelta(seconds=10),
    )
    # Force expires_at in the past via re-read path
    db.execute(
        "UPDATE actions SET expires_at=? WHERE id=?",
        ((NOW - timedelta(seconds=1)).isoformat(), proposed.action.action_id),
    )
    db.commit()
    n = expire_awaiting_actions(db, now=NOW, commit=True)
    assert n >= 1
    action = get_action(db, proposed.action.action_id, "u1")
    assert action.lifecycle_state == "expired"
    assert action.status == "timeout"


def test_receipt_integrity_replay(db):
    proposed = propose_action(
        db,
        user_id="u1",
        action_type="other",
        label="Log note",
        consequence_level="informational",
        record_only=True,
        now=NOW,
    )
    executed = execute_action(
        db, action_id=proposed.action.action_id, user_id="u1"
    )
    receipt = executed.receipt
    assert verify_receipt_integrity(receipt)
    # Mutating detail would break hash — prove check fails on tamper
    from dataclasses import replace

    tampered = replace(receipt, detail={"ok": False, "tampered": True})
    assert verify_receipt_integrity(tampered) is False


def test_failed_execution_writes_receipt(db):
    proposed = propose_action(
        db,
        user_id="u1",
        action_type="book",
        label="Book flight",
        consequence_level="informational",
        record_only=True,
        now=NOW,
    )

    def boom(_action):
        raise RuntimeError("adapter down")

    executed = execute_action(
        db,
        action_id=proposed.action.action_id,
        user_id="u1",
        executor=boom,
    )
    assert executed.action.lifecycle_state == "failed"
    assert executed.receipt.execution_result == "failed"
    assert verify_receipt_integrity(executed.receipt)
