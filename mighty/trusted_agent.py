"""Trusted Agent Authorization coordinator (Milestone 11).

Propose → authorize → execute → immutable receipt.

Does not rank Attention. Does not own Recovery. Provider-specific work runs
only through injectable executors / capability registry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from mighty.agent_action_store import (
    DEFAULT_TIMEOUT_SEC,
    STATE_AUTHORIZED,
    STATE_AWAITING_AUTHORIZATION,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_DENIED,
    STATE_EXECUTING,
    STATE_EXPIRED,
    STATE_FAILED,
    STATE_PROPOSED,
    AgentAction,
    ensure_agent_action_tables,
    expire_awaiting_actions,
    get_action,
    has_open_fingerprint,
    insert_action,
    new_approval_token,
    update_lifecycle,
    utc_now_iso,
    action_fingerprint,
)
from mighty.authorization_policy import (
    AUTH_AUTO_AUTHORIZE,
    AUTH_DENY,
    AUTH_REQUIRE_HUMAN,
    AuthorizationDecision,
    evaluate_authorization_policy,
    normalize_consequence_level,
)
from mighty.execution_receipt import (
    ExecutionReceipt,
    ensure_receipt_tables,
    get_receipt_for_attempt,
    persist_receipt,
)

logger = logging.getLogger(__name__)

ExecutorFn = Callable[[AgentAction], dict[str, Any]]


@dataclass
class ProposeResult:
    action: AgentAction | None = None
    decision: AuthorizationDecision | None = None
    suppressed_duplicate: bool = False
    error: str | None = None


@dataclass
class DecideResult:
    action: AgentAction | None = None
    error: str | None = None


@dataclass
class ExecuteResult:
    action: AgentAction | None = None
    receipt: ExecutionReceipt | None = None
    retried: bool = False
    error: str | None = None


@dataclass
class AgentAuthCounters:
    proposed: int = 0
    approvals_requested: int = 0
    approvals_granted: int = 0
    approvals_denied: int = 0
    executions: int = 0
    failures: int = 0
    retries: int = 0
    duplicates_suppressed: int = 0
    expired: int = 0
    errors: int = 0


def _default_executor(action: AgentAction) -> dict[str, Any]:
    """Record-only executor — real provider work belongs in adapters."""
    return {
        "ok": True,
        "mode": "record_only",
        "action_type": action.action_type,
        "label": action.label,
    }


def propose_action(
    db: Any,
    *,
    user_id: str,
    action_type: str,
    label: str,
    fields: Any = None,
    consequence_level: str | None = None,
    agent_id: str | None = None,
    provider: str | None = None,
    record_only: bool = False,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    now: datetime | None = None,
    commit: bool = True,
) -> ProposeResult:
    """Create a durable Action and apply authorization policy."""
    result = ProposeResult()
    try:
        ensure_agent_action_tables(db, commit=False)
        ensure_receipt_tables(db, commit=False)
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        level = normalize_consequence_level(consequence_level)
        fp = action_fingerprint(
            user_id=user_id,
            agent_id=agent_id,
            action_type=action_type,
            label=label,
            fields=fields,
        )
        duplicate = has_open_fingerprint(db, user_id, fp)
        expires_at = (now + timedelta(seconds=timeout_sec)).replace(
            microsecond=0
        ).isoformat()
        decision = evaluate_authorization_policy(
            action_type=action_type,
            consequence_level=level,
            provider=provider,
            expires_at=expires_at,
            now=now,
            duplicate_open=duplicate,
            record_only=record_only,
        )
        result.decision = decision

        if decision.outcome == AUTH_DENY and decision.reason == "duplicate_open_action":
            result.suppressed_duplicate = True
            logger.info(
                "trusted_agent.propose_suppressed_duplicate user=%s type=%s",
                user_id,
                action_type,
            )
            return result

        if decision.outcome == AUTH_DENY:
            action = insert_action(
                db,
                user_id=user_id,
                action_type=action_type,
                label=label,
                fields=fields,
                consequence_level=level,
                agent_id=agent_id,
                provider=provider,
                lifecycle_state=STATE_DENIED,
                decided_at=utc_now_iso(),
                expires_at=expires_at,
                outcome=decision.reason,
                auth_channel="policy",
                commit=commit,
            )
            result.action = action
            return result

        if decision.outcome == AUTH_REQUIRE_HUMAN:
            action = insert_action(
                db,
                user_id=user_id,
                action_type=action_type,
                label=label,
                fields=fields,
                consequence_level=level,
                agent_id=agent_id,
                provider=provider,
                lifecycle_state=STATE_AWAITING_AUTHORIZATION,
                approval_token=new_approval_token(),
                expires_at=expires_at,
                commit=commit,
            )
            result.action = action
            return result

        # Auto-authorize (informational / record_only)
        stamp = utc_now_iso()
        action = insert_action(
            db,
            user_id=user_id,
            action_type=action_type,
            label=label,
            fields=fields,
            consequence_level=level,
            agent_id=agent_id,
            provider=provider,
            lifecycle_state=STATE_AUTHORIZED,
            decided_at=stamp,
            expires_at=expires_at,
            auth_channel="policy_auto",
            commit=commit,
        )
        result.action = action
        return result
    except Exception as exc:
        logger.exception("trusted_agent.propose_failed err=%s", exc)
        result.error = str(exc)
        return result


def decide_authorization(
    db: Any,
    *,
    action_id: str,
    user_id: str,
    decision: str,
    auth_channel: str = "api",
    now: datetime | None = None,
    commit: bool = True,
) -> DecideResult:
    """Record human authorization decision (approved/denied)."""
    out = DecideResult()
    try:
        expire_awaiting_actions(db, now=now, commit=False)
        action = get_action(db, action_id, user_id)
        if action is None:
            out.error = "not_found"
            return out
        if action.lifecycle_state == STATE_EXPIRED or action.status == "timeout":
            out.error = "expired"
            out.action = action
            return out
        if action.lifecycle_state not in {
            STATE_AWAITING_AUTHORIZATION,
            STATE_PROPOSED,
        } and action.status != "pending":
            out.error = "not_awaiting"
            out.action = action
            return out

        decision_norm = str(decision or "").strip().lower()
        if decision_norm in {"approved", "authorize", "authorized"}:
            lifecycle = STATE_AUTHORIZED
        elif decision_norm in {"denied", "deny"}:
            lifecycle = STATE_DENIED
        elif decision_norm in {"cancelled", "cancel"}:
            lifecycle = STATE_CANCELLED
        else:
            out.error = "invalid_decision"
            return out

        updated = update_lifecycle(
            db,
            action_id,
            lifecycle_state=lifecycle,
            decided_at=utc_now_iso(),
            auth_channel=auth_channel,
            commit=commit,
        )
        out.action = updated
        return out
    except Exception as exc:
        logger.exception("trusted_agent.decide_failed err=%s", exc)
        out.error = str(exc)
        return out


def execute_action(
    db: Any,
    *,
    action_id: str,
    user_id: str,
    executor: ExecutorFn | None = None,
    commit: bool = True,
) -> ExecuteResult:
    """Execute an authorized Action idempotently and write an immutable receipt."""
    out = ExecuteResult()
    try:
        action = get_action(db, action_id, user_id)
        if action is None:
            out.error = "not_found"
            return out
        if action.lifecycle_state in {STATE_DENIED, STATE_CANCELLED, STATE_EXPIRED}:
            out.error = "not_authorized"
            out.action = action
            return out
        if action.lifecycle_state not in {
            STATE_AUTHORIZED,
            STATE_EXECUTING,
            STATE_COMPLETED,
            STATE_FAILED,
        } and action.status not in {"approved", "logged"}:
            out.error = "not_authorized"
            out.action = action
            return out

        attempt = max(int(action.execution_attempt or 0), 0) + 1
        if action.lifecycle_state == STATE_COMPLETED:
            existing = get_receipt_for_attempt(db, action_id, max(action.execution_attempt, 1))
            if existing is not None:
                out.action = action
                out.receipt = existing
                out.retried = True
                return out

        update_lifecycle(
            db,
            action_id,
            lifecycle_state=STATE_EXECUTING,
            execution_attempt=attempt,
            commit=False,
        )
        action = get_action(db, action_id, user_id)
        assert action is not None

        fn = executor or _default_executor
        try:
            detail = fn(action) or {}
            ok = bool(detail.get("ok", True))
        except Exception as exc:
            detail = {"ok": False, "error": str(exc)}
            ok = False

        lifecycle = STATE_COMPLETED if ok else STATE_FAILED
        result_label = "completed" if ok else "failed"
        updated = update_lifecycle(
            db,
            action_id,
            lifecycle_state=lifecycle,
            outcome=result_label,
            execution_attempt=attempt,
            commit=False,
        )
        receipt = persist_receipt(
            db,
            action_id=action_id,
            user_id=user_id,
            agent_id=action.agent_id,
            authorization_decision="authorized",
            authorization_at=action.decided_at,
            auth_channel=action.auth_channel,
            execution_result=result_label,
            execution_attempt=attempt,
            proposal_hash=action.proposal_hash,
            detail=detail if isinstance(detail, dict) else {"raw": detail},
            provider=action.provider,
            commit=commit,
        )
        if commit:
            db.commit()
        out.action = updated
        out.receipt = receipt
        return out
    except Exception as exc:
        logger.exception("trusted_agent.execute_failed err=%s", exc)
        out.error = str(exc)
        return out


def propose_and_log(
    db: Any,
    *,
    user_id: str,
    action_type: str,
    label: str,
    fields: Any = None,
    consequence_level: str = "informational",
    agent_id: str | None = None,
    decision: str | None = None,
    commit: bool = True,
) -> ProposeResult | DecideResult | ExecuteResult:
    """Compatibility helper for chat log-decision / record paths."""
    if decision in {"approved", "denied"}:
        # Inline decide without awaiting: propose as informational authorize/deny
        level = normalize_consequence_level(consequence_level)
        if decision == "denied":
            action = insert_action(
                db,
                user_id=user_id,
                action_type=action_type,
                label=label,
                fields=fields,
                consequence_level=level,
                agent_id=agent_id,
                lifecycle_state=STATE_DENIED,
                decided_at=utc_now_iso(),
                auth_channel="chat_inline",
                commit=commit,
            )
            return DecideResult(action=action)
        proposed = propose_action(
            db,
            user_id=user_id,
            action_type=action_type,
            label=label,
            fields=fields,
            consequence_level="informational",
            agent_id=agent_id,
            record_only=True,
            commit=False,
        )
        if proposed.action and proposed.action.lifecycle_state == STATE_AUTHORIZED:
            return execute_action(
                db,
                action_id=proposed.action.action_id,
                user_id=user_id,
                commit=commit,
            )
        if commit:
            db.commit()
        return proposed

    proposed = propose_action(
        db,
        user_id=user_id,
        action_type=action_type,
        label=label,
        fields=fields,
        consequence_level=consequence_level or "informational",
        agent_id=agent_id,
        record_only=True,
        commit=False,
    )
    if proposed.action and proposed.action.lifecycle_state == STATE_AUTHORIZED:
        return execute_action(
            db,
            action_id=proposed.action.action_id,
            user_id=user_id,
            commit=commit,
        )
    if commit:
        db.commit()
    return proposed


def safe_propose_action(db: Any, **kwargs: Any) -> ProposeResult:
    try:
        return propose_action(db, **kwargs)
    except Exception as exc:
        logger.exception("trusted_agent.safe_propose_failed err=%s", exc)
        return ProposeResult(error=str(exc))
