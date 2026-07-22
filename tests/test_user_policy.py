"""User Policy evaluation, conflicts, explainability (Milestone 12)."""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.authorization_policy import (
    AUTH_AUTO_AUTHORIZE,
    AUTH_DENY,
    AUTH_REQUIRE_HUMAN,
    evaluate_authorization_policy,
)
from mighty.execution_receipt import ensure_receipt_tables, verify_receipt_integrity
from mighty.policy_evaluation import (
    evaluate_authorization_with_policy,
    explain_coverage,
)
from mighty.policy_metrics import PolicyEvalCounters, apply_explained, compute_policy_metrics
from mighty.policy_store import (
    ensure_policy_tables,
    load_user_policy,
    save_user_policy,
    sync_policy_from_users,
)
from mighty.trusted_agent import decide_authorization, execute_action, propose_action
from mighty.user_policy import (
    UserPolicy,
    default_user_policy,
    opportunity_kind_suppressed,
    provider_monitoring_enabled,
)

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "p.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            minimal_logging INTEGER DEFAULT 0,
            delete_raw_after_extract INTEGER DEFAULT 0,
            notify_email INTEGER DEFAULT 1,
            notify_push INTEGER DEFAULT 1,
            notify_ntfy INTEGER DEFAULT 1,
            alert_expiry_emails INTEGER DEFAULT 1,
            notification_pref TEXT DEFAULT 'quiet'
        );
        """
    )
    conn.execute("INSERT INTO users (id) VALUES ('u1')")
    ensure_policy_tables(conn)
    ensure_receipt_tables(conn)
    from mighty.agent_action_store import ensure_agent_action_tables

    ensure_agent_action_tables(conn)
    conn.commit()
    yield conn
    conn.close()


def test_default_routine_requires_human():
    explained = evaluate_authorization_with_policy(
        action_type="book",
        consequence_level="routine",
        policy=default_user_policy("u1"),
        now=NOW,
    )
    assert explained.decision.outcome == AUTH_REQUIRE_HUMAN
    assert explain_coverage(explained)


def test_auto_execute_routine_below_threshold():
    policy = UserPolicy(
        user_id="u1",
        require_human_at_or_above="consequential",
        auto_execute_routine=True,
        source="store",
    )
    explained = evaluate_authorization_with_policy(
        action_type="book",
        consequence_level="routine",
        policy=policy,
        now=NOW,
    )
    assert explained.decision.outcome == AUTH_AUTO_AUTHORIZE


def test_conflict_require_human_wins_over_auto_routine():
    policy = UserPolicy(
        user_id="u1",
        require_human_at_or_above="routine",
        auto_execute_routine=True,
        source="store",
    )
    explained = evaluate_authorization_with_policy(
        action_type="pay",
        consequence_level="routine",
        policy=policy,
        now=NOW,
    )
    assert explained.decision.outcome == AUTH_REQUIRE_HUMAN
    assert explained.conflict_resolution == "require_human_over_auto"


def test_provider_override_deny():
    policy = UserPolicy(
        user_id="u1",
        provider_overrides={"amex": {"deny_execution": True}},
        source="store",
    )
    explained = evaluate_authorization_with_policy(
        action_type="redeem",
        consequence_level="informational",
        provider="amex",
        record_only=True,
        policy=policy,
        now=NOW,
    )
    assert explained.decision.outcome == AUTH_DENY
    assert explained.overridden is True
    assert explained.suppressed_execution is True


def test_replay_deterministic():
    policy = default_user_policy("u1")
    a = evaluate_authorization_with_policy(
        action_type="book", consequence_level="critical", policy=policy, now=NOW
    )
    b = evaluate_authorization_with_policy(
        action_type="book", consequence_level="critical", policy=policy, now=NOW
    )
    assert a == b


def test_opportunity_suppress_and_monitor():
    policy = UserPolicy(
        user_id="u1",
        suppress_opportunity_kinds=("expiring_credit",),
        monitor_providers=True,
        provider_overrides={"delta": {"monitor": False}},
        source="store",
    )
    assert opportunity_kind_suppressed(policy, "expiring_credit")
    assert provider_monitoring_enabled(policy, "amex")
    assert not provider_monitoring_enabled(policy, "delta")


def test_store_sync_from_users(db):
    db.execute("UPDATE users SET minimal_logging=1 WHERE id='u1'")
    db.commit()
    sync_policy_from_users(db, "u1")
    policy = load_user_policy(db, "u1")
    assert policy.minimal_logging is True


def test_save_policy_syncs_users(db):
    policy = UserPolicy(
        user_id="u1",
        minimal_logging=True,
        notify_email=False,
        require_human_at_or_above="consequential",
        auto_execute_routine=True,
        source="store",
    )
    save_user_policy(db, policy, sync_users=True)
    row = db.execute("SELECT minimal_logging, notify_email FROM users WHERE id='u1'").fetchone()
    assert row["minimal_logging"] == 1
    assert row["notify_email"] == 0
    loaded = load_user_policy(db, "u1")
    assert loaded.require_human_at_or_above == "consequential"
    assert loaded.auto_execute_routine is True


def test_authorization_by_policy_api(db):
    save_user_policy(
        db,
        UserPolicy(
            user_id="u1",
            require_human_at_or_above="consequential",
            auto_execute_routine=True,
            source="store",
        ),
    )
    d = evaluate_authorization_policy(
        action_type="book",
        consequence_level="routine",
        user_policy=load_user_policy(db, "u1"),
        now=NOW,
    )
    assert d.outcome == AUTH_AUTO_AUTHORIZE
    assert d.explanation


def test_e2e_policy_authorize_execute_receipt(db):
    save_user_policy(
        db,
        UserPolicy(
            user_id="u1",
            require_human_at_or_above="consequential",
            auto_execute_routine=True,
            source="store",
        ),
    )
    proposed = propose_action(
        db,
        user_id="u1",
        action_type="redeem",
        label="Use credit",
        consequence_level="routine",
        agent_id="agent-1",
        provider="amex",
        now=NOW,
    )
    assert proposed.action is not None
    assert proposed.action.lifecycle_state == "authorized"
    assert proposed.action.decision_explanation
    assert "auto" in proposed.action.decision_explanation.lower() or "Auto" in (
        proposed.action.decision_explanation or ""
    )

    executed = execute_action(
        db, action_id=proposed.action.action_id, user_id="u1"
    )
    assert executed.receipt is not None
    assert verify_receipt_integrity(executed.receipt)
    assert "policy_explanation" in executed.receipt.detail


def test_e2e_policy_requires_human(db):
    proposed = propose_action(
        db,
        user_id="u1",
        action_type="transfer",
        label="Transfer miles",
        consequence_level="consequential",
        now=NOW,
    )
    assert proposed.action.lifecycle_state == "awaiting_authorization"
    assert proposed.action.decision_explanation
    decided = decide_authorization(
        db,
        action_id=proposed.action.action_id,
        user_id="u1",
        decision="approved",
        auth_channel="activity",
        now=NOW,
    )
    assert decided.action.lifecycle_state == "authorized"


def test_metrics_explainability(db):
    counters = PolicyEvalCounters()
    explained = evaluate_authorization_with_policy(
        action_type="book",
        consequence_level="routine",
        policy=default_user_policy("u1"),
        now=NOW,
    )
    apply_explained(counters, explained)
    snap = compute_policy_metrics(counters)
    assert snap.explainability_coverage == 1.0
    assert snap.require_human == 1
