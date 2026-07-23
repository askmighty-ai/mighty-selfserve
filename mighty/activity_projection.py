"""Activity V1 — pure server-side projection over actions + receipts.

Composes existing durable facts. Does not own canonical data, create an
event bus, or rank Attention.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from mighty.agent_action_store import (
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
    legacy_status_to_lifecycle,
    list_actions,
)
from mighty.execution_receipt import ExecutionReceipt, list_receipts

CATEGORY_NEEDS_APPROVAL = "needs_approval"
CATEGORY_IN_PROGRESS = "in_progress"
CATEGORY_COMPLETED = "completed"
CATEGORY_COULD_NOT_COMPLETE = "could_not_complete"

STATUS_LABELS = {
    CATEGORY_NEEDS_APPROVAL: "Needs approval",
    CATEGORY_IN_PROGRESS: "In progress",
    CATEGORY_COMPLETED: "Completed",
    CATEGORY_COULD_NOT_COMPLETE: "Could not complete",
}

DEFAULT_PAGE_SIZE = 50
MAX_SCAN = 500
RECEIPT_SCAN = 2000

_FORBIDDEN_DETAIL_KEYS = frozenset(
    {
        "proposal_hash",
        "receipt_hash",
        "prev_receipt_hash",
        "fingerprint",
        "hash",
        "capability",
        "capability_id",
        "store",
        "table",
    }
)


@dataclass(frozen=True)
class ActivityReceiptSummary:
    """Customer-readable receipt attempt (no internal hashes/ids as primary fields)."""

    attempt: int
    happened: str
    why: str | None
    occurred_at: str
    authorization: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "happened": self.happened,
            "why": self.why,
            "occurred_at": self.occurred_at,
            "authorization": self.authorization,
        }


@dataclass(frozen=True)
class ActivityDetail:
    attempted: str
    happened: str
    why: str | None
    requested_at: str
    decided_at: str | None
    provider_display_name: str | None
    fields: tuple[tuple[str, str], ...]
    outcome_detail: str | None
    receipt_history: tuple[ActivityReceiptSummary, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "happened": self.happened,
            "why": self.why,
            "requested_at": self.requested_at,
            "decided_at": self.decided_at,
            "provider_display_name": self.provider_display_name,
            "fields": [{"key": k, "value": v} for k, v in self.fields],
            "outcome_detail": self.outcome_detail,
            "receipt_history": [r.to_dict() for r in self.receipt_history],
        }


@dataclass(frozen=True)
class ActivityItem:
    activity_id: str
    occurred_at: str
    category: str
    status_label: str
    title: str
    explanation: str
    provider: str | None
    provider_display_name: str | None
    action_id: str
    user_action: str | None
    detail: ActivityDetail

    @property
    def is_pending(self) -> bool:
        return self.category == CATEGORY_NEEDS_APPROVAL

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity_id": self.activity_id,
            "occurred_at": self.occurred_at,
            "category": self.category,
            "status_label": self.status_label,
            "title": self.title,
            "explanation": self.explanation,
            "provider": self.provider,
            "provider_display_name": self.provider_display_name,
            "action_id": self.action_id,
            "user_action": self.user_action,
            "detail": self.detail.to_dict(),
        }


@dataclass(frozen=True)
class ActivityProjection:
    generated_at: str
    items: tuple[ActivityItem, ...]
    next_cursor: str | None = None
    has_pending: bool = False
    has_historical: bool = False

    @property
    def nav_visible(self) -> bool:
        return self.has_pending or self.has_historical

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "items": [i.to_dict() for i in self.items],
            "next_cursor": self.next_cursor,
            "has_pending": self.has_pending,
            "has_historical": self.has_historical,
            "nav_visible": self.nav_visible,
        }


def activity_nav_visible(db: Any, user_id: str) -> bool:
    """True when the user has any projectable Activity item."""
    for action in list_actions(db, user_id, limit=MAX_SCAN):
        if category_for_action(action) is not None:
            return True
    return False


def project_activity(
    db: Any,
    user_id: str,
    *,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: str | None = None,
    provider_display_names: Mapping[str, str] | None = None,
) -> ActivityProjection:
    """Compose Activity timeline for one user (server-side only)."""
    names = {
        str(k).lower(): str(v)
        for k, v in (provider_display_names or {}).items()
        if v
    }
    actions = list_actions(db, user_id, limit=MAX_SCAN)
    receipts = list_receipts(db, user_id=user_id, limit=RECEIPT_SCAN)
    by_action: dict[str, list[ExecutionReceipt]] = {}
    for receipt in receipts:
        by_action.setdefault(receipt.action_id, []).append(receipt)

    items: list[ActivityItem] = []
    for action in actions:
        # Isolation: list_actions already scopes user_id; double-check.
        if action.user_id != user_id:
            continue
        item = _project_item(action, by_action.get(action.action_id, ()), names)
        if item is not None:
            items.append(item)

    items.sort(key=lambda i: (_parse_ts(i.occurred_at), i.activity_id), reverse=True)

    has_pending = any(i.is_pending for i in items)
    has_historical = any(not i.is_pending for i in items)

    page_limit = max(1, min(int(limit), 100))
    start = 0
    if cursor:
        start = _index_after_cursor(items, cursor)

    page = items[start : start + page_limit]
    next_cursor = None
    if start + page_limit < len(items) and page:
        last = page[-1]
        next_cursor = encode_cursor(last.occurred_at, last.activity_id)

    return ActivityProjection(
        generated_at=_utc_now_iso(),
        items=tuple(page),
        next_cursor=next_cursor,
        has_pending=has_pending,
        has_historical=has_historical,
    )


def category_for_action(action: AgentAction) -> str | None:
    lifecycle = _lifecycle(action)
    status = str(action.status or "").strip().lower()
    if lifecycle == STATE_PROPOSED:
        return None
    if lifecycle == STATE_AWAITING_AUTHORIZATION or status == "pending":
        return CATEGORY_NEEDS_APPROVAL
    if lifecycle in {STATE_AUTHORIZED, STATE_EXECUTING}:
        return CATEGORY_IN_PROGRESS
    if lifecycle == STATE_COMPLETED or status == "logged":
        return CATEGORY_COMPLETED
    if lifecycle in {STATE_FAILED, STATE_DENIED, STATE_CANCELLED, STATE_EXPIRED}:
        return CATEGORY_COULD_NOT_COMPLETE
    if status in {"timeout", "denied"}:
        return CATEGORY_COULD_NOT_COMPLETE
    if status == "approved":
        return CATEGORY_IN_PROGRESS
    return None


def encode_cursor(occurred_at: str, activity_id: str) -> str:
    return f"{occurred_at}|{activity_id}"


def decode_cursor(cursor: str) -> tuple[str, str] | None:
    if not cursor or "|" not in cursor:
        return None
    occurred_at, activity_id = cursor.split("|", 1)
    if not occurred_at or not activity_id:
        return None
    return occurred_at, activity_id


def _index_after_cursor(items: Sequence[ActivityItem], cursor: str) -> int:
    decoded = decode_cursor(cursor)
    if decoded is None:
        return 0
    occurred_at, activity_id = decoded
    for idx, item in enumerate(items):
        if item.occurred_at == occurred_at and item.activity_id == activity_id:
            return idx + 1
        key_item = (_parse_ts(item.occurred_at), item.activity_id)
        key_cur = (_parse_ts(occurred_at), activity_id)
        if key_item < key_cur:
            return idx
    return len(items)


def _project_item(
    action: AgentAction,
    receipts: Sequence[ExecutionReceipt],
    names: Mapping[str, str],
) -> ActivityItem | None:
    category = category_for_action(action)
    if category is None:
        return None

    ordered = sorted(
        receipts,
        key=lambda r: (r.execution_attempt, _parse_ts(r.created_at), r.receipt_id),
    )
    latest = ordered[-1] if ordered else None
    provider = (action.provider or (latest.provider if latest else None) or None)
    if provider:
        provider = str(provider).lower()
    display = None
    if provider:
        display = names.get(provider) or provider.replace("_", " ").title()

    occurred_at = _occurred_at(action, category, latest)
    explanation = _explanation(action, category, latest)
    detail = _detail(action, category, ordered, display, explanation)

    return ActivityItem(
        activity_id=f"action:{action.action_id}",
        occurred_at=occurred_at,
        category=category,
        status_label=STATUS_LABELS[category],
        title=str(action.label or "Action").strip() or "Action",
        explanation=explanation,
        provider=provider,
        provider_display_name=display,
        action_id=action.action_id,
        user_action="approve_deny" if category == CATEGORY_NEEDS_APPROVAL else None,
        detail=detail,
    )


def _lifecycle(action: AgentAction) -> str:
    if action.lifecycle_state:
        return str(action.lifecycle_state).strip().lower()
    return legacy_status_to_lifecycle(action.status)


def _occurred_at(
    action: AgentAction,
    category: str,
    latest: ExecutionReceipt | None,
) -> str:
    if category == CATEGORY_NEEDS_APPROVAL:
        return action.created_at
    if category == CATEGORY_IN_PROGRESS:
        return action.decided_at or action.created_at
    # Terminal: prefer decided_at; use latest receipt time when later/more meaningful
    candidates = [t for t in (action.decided_at, action.created_at) if t]
    if latest and latest.created_at:
        candidates.append(latest.created_at)
    if not candidates:
        return action.created_at
    return max(candidates, key=_parse_ts)


def _explanation(
    action: AgentAction,
    category: str,
    latest: ExecutionReceipt | None,
) -> str:
    lifecycle = _lifecycle(action)
    if category == CATEGORY_NEEDS_APPROVAL:
        return "Needs your approval to continue."
    if category == CATEGORY_IN_PROGRESS:
        if lifecycle == STATE_EXECUTING:
            return "In progress now."
        return "Approved and ready to continue."
    if category == CATEGORY_COMPLETED:
        phrase = _customer_phrase(action.outcome)
        if phrase and str(action.outcome or "").strip().lower() not in {
            "completed",
            "ok",
            "success",
            "failed",
        }:
            return _ensure_sentence(phrase)
        label = str(action.label or "").strip()
        if label:
            return _ensure_sentence(f"{label} is complete")
        return "This is complete."
    # could_not_complete — precise, no false system-failure implication.
    # Prefer canonical lifecycle over legacy status (cancelled maps to legacy denied).
    if lifecycle == STATE_DENIED:
        return "You declined this request."
    if lifecycle == STATE_CANCELLED:
        return "This request was cancelled."
    if lifecycle == STATE_EXPIRED:
        return "The approval window ended before a decision was made."
    if lifecycle == STATE_FAILED:
        phrase = _customer_phrase(action.outcome)
        if phrase and str(action.outcome or "").strip().lower() not in {"failed"}:
            return _ensure_sentence(f"Couldn’t finish — {phrase}")
        if latest and latest.detail:
            msg = _customer_phrase(_readable_detail_message(latest.detail) or "")
            if msg:
                return _ensure_sentence(f"Couldn’t finish — {msg}")
        return "Couldn’t finish this."
    if action.status == "timeout":
        return "The approval window ended before a decision was made."
    if action.status == "denied":
        return "You declined this request."
    return "This couldn’t be completed."


def _detail(
    action: AgentAction,
    category: str,
    receipts: Sequence[ExecutionReceipt],
    provider_display: str | None,
    explanation: str,
) -> ActivityDetail:
    why = _clean_text(action.decision_explanation)
    if not why and receipts:
        for receipt in reversed(receipts):
            pe = receipt.detail.get("policy_explanation") if isinstance(receipt.detail, dict) else None
            why = _clean_text(pe)
            if why:
                break

    happened = explanation
    outcome_detail = _customer_phrase(action.outcome)
    if outcome_detail and str(action.outcome or "").strip().lower() in {
        "completed",
        "ok",
        "success",
        "failed",
    }:
        outcome_detail = None
    fields = _normalize_fields(action.fields)
    history = tuple(_receipt_summary(r) for r in receipts)

    return ActivityDetail(
        attempted=str(action.label or "Action").strip() or "Action",
        happened=happened,
        why=why,
        requested_at=action.created_at,
        decided_at=action.decided_at,
        provider_display_name=provider_display,
        fields=fields,
        outcome_detail=outcome_detail,
        receipt_history=history,
    )


def _receipt_summary(receipt: ExecutionReceipt) -> ActivityReceiptSummary:
    happened = _human_execution_result(receipt.execution_result)
    why = None
    if isinstance(receipt.detail, dict):
        why = _clean_text(receipt.detail.get("policy_explanation"))
        if not why:
            why = _readable_detail_message(receipt.detail)
    auth = _human_authorization(receipt.authorization_decision)
    return ActivityReceiptSummary(
        attempt=int(receipt.execution_attempt or 1),
        happened=happened,
        why=why,
        occurred_at=receipt.created_at,
        authorization=auth,
    )


def _human_execution_result(result: str | None) -> str:
    r = str(result or "").strip().lower()
    if r in {"completed", "ok", "success"}:
        return "Completed"
    if r in {"failed", "error"}:
        return "Could not finish"
    if r:
        return _soften_outcome(r)
    return "Recorded"


def _human_authorization(decision: str | None) -> str | None:
    d = str(decision or "").strip().lower()
    if not d:
        return None
    if d in {"authorized", "approved"}:
        return "Authorized"
    if d in {"denied"}:
        return "Declined"
    if d in {"expired", "timeout"}:
        return "Expired"
    if d in {"cancelled", "canceled"}:
        return "Cancelled"
    return d.replace("_", " ").title()


def _normalize_fields(fields: Any) -> tuple[tuple[str, str], ...]:
    if not fields:
        return ()
    out: list[tuple[str, str]] = []
    if isinstance(fields, dict):
        for k, v in fields.items():
            key = str(k)
            if key.lower() in _FORBIDDEN_DETAIL_KEYS or key.lower().endswith("_hash"):
                continue
            out.append((key, _stringify_field(v)))
    elif isinstance(fields, list):
        for pair in fields:
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                key = str(pair[0])
                if key.lower() in _FORBIDDEN_DETAIL_KEYS or key.lower().endswith("_hash"):
                    continue
                out.append((key, _stringify_field(pair[1])))
    return tuple(out)


def _stringify_field(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        import json

        return json.dumps(value, default=str)
    except Exception:
        return str(value)


def _readable_detail_message(detail: Mapping[str, Any]) -> str | None:
    for key in ("message", "error", "summary", "reason"):
        if key in detail and key not in _FORBIDDEN_DETAIL_KEYS:
            text = _customer_phrase(detail.get(key))
            if text and not _looks_internal(text):
                return text
    return None


def _looks_internal(text: str) -> bool:
    lower = text.lower()
    if any(tok in lower for tok in ("hash", "fingerprint", "sqlite", "traceback")):
        return True
    if "_" in text and text.lower() == text and any(
        tok in text.lower() for tok in ("provider_", "capability_", "auth_", "exec_")
    ):
        return True
    if len(text) >= 40 and all(c in "0123456789abcdef" for c in text.lower()):
        return True
    return False


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or _looks_internal(text):
        return None
    return text


_CUSTOMER_OUTCOME_PHRASES = {
    "provider_unavailable": "The account wasn’t available",
    "temporary_glitch": "Something went wrong temporarily",
    "not_authorized": "It wasn’t authorized",
    "timeout": "It took too long",
    "timed_out": "It took too long",
    "network_error": "The connection failed",
    "rate_limited": "Too many attempts — try again later",
    "cancelled": "Cancelled",
    "canceled": "Cancelled",
    "denied": "Declined",
    "expired": "Expired",
}


def _customer_phrase(value: Any) -> str | None:
    """Translate implementation tokens into customer language."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    key = text.lower().replace(" ", "_").replace("-", "_")
    if key in _CUSTOMER_OUTCOME_PHRASES:
        return _CUSTOMER_OUTCOME_PHRASES[key]
    if _looks_internal(text):
        return None
    if "_" in text:
        words = " ".join(part for part in text.replace("_", " ").split() if part)
        if not words:
            return None
        return words[0].upper() + words[1:]
    return text


def _soften_outcome(value: Any) -> str:
    phrase = _customer_phrase(value)
    return phrase or ""


def _ensure_sentence(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    if cleaned[-1] in ".!?":
        return cleaned
    return f"{cleaned}."


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def export_activity_rows(
    db: Any,
    user_id: str,
    *,
    provider_display_names: Mapping[str, str] | None = None,
) -> list[dict[str, str]]:
    """Flatten actions + receipt attempts for CSV export (customer-visible fields)."""
    names = {
        str(k).lower(): str(v)
        for k, v in (provider_display_names or {}).items()
        if v
    }
    actions = list_actions(db, user_id, limit=MAX_SCAN)
    receipts = list_receipts(db, user_id=user_id, limit=RECEIPT_SCAN)
    by_action: dict[str, list[ExecutionReceipt]] = {}
    for receipt in receipts:
        by_action.setdefault(receipt.action_id, []).append(receipt)

    rows: list[dict[str, str]] = []
    for action in actions:
        if action.user_id != user_id:
            continue
        item = _project_item(action, by_action.get(action.action_id, ()), names)
        if item is None:
            continue
        history = item.detail.receipt_history
        if not history:
            rows.append(_export_row(item, None))
        else:
            for summary in history:
                rows.append(_export_row(item, summary))
    return rows


def _export_row(
    item: ActivityItem, summary: ActivityReceiptSummary | None
) -> dict[str, str]:
    fields = "; ".join(f"{k}: {v}" for k, v in item.detail.fields)
    return {
        "Date": item.occurred_at,
        "Status": item.status_label,
        "Description": item.title,
        "Explanation": item.explanation,
        "Account": item.provider_display_name or "",
        "Details": fields,
        "Requested At": item.detail.requested_at,
        "Decided At": item.detail.decided_at or "",
        "Why": item.detail.why or "",
        "Outcome": item.detail.outcome_detail or "",
        "Attempt": str(summary.attempt) if summary else "",
        "Attempt Result": summary.happened if summary else "",
        "Attempt Why": (summary.why or "") if summary else "",
        "Attempt At": summary.occurred_at if summary else "",
        "Authorization": (summary.authorization or "") if summary else "",
    }


def delete_activity_data(db: Any, user_id: str, *, commit: bool = True) -> None:
    """Delete Activity-owned customer data including execution receipts."""
    from mighty.execution_receipt import ensure_receipt_tables
    from mighty.agent_action_store import ensure_agent_action_tables

    ensure_receipt_tables(db, commit=False)
    ensure_agent_action_tables(db, commit=False)
    db.execute("DELETE FROM action_execution_receipts WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM actions WHERE user_id=?", (user_id,))
    if commit:
        db.commit()
