"""Structured extraction outcome — extractor is the sole account-data authority.

Callers decide lifecycle. The extractor decides extraction outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExtractionStatus(str, Enum):
    EXTRACTION_SUCCESS = "EXTRACTION_SUCCESS"
    NO_ACCOUNT_DATA = "NO_ACCOUNT_DATA"
    NOT_READY = "NOT_READY"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"


class ExtractionReason(str, Enum):
    MEMBERSHIP_REWARDS_FOUND = "membership_rewards_found"
    STATEMENT_BALANCE_FOUND = "statement_balance_found"
    CARD_ENDING_FOUND = "card_ending_found"
    PUBLISHABLE_FIELDS_FOUND = "publishable_fields_found"
    NO_PUBLISHABLE_WIDGETS = "no_publishable_widgets"
    SPA_NOT_HYDRATED = "spa_not_hydrated"
    LOGIN_PAGE = "login_page"
    MARKETING_PAGE = "marketing_page"
    DOM_CHANGED = "dom_changed"
    TAB_CLOSED = "tab_closed"
    NAVIGATION_DURING_RETRY = "navigation_during_retry"
    NAVIGATED_AWAY = "navigated_away"


@dataclass(frozen=True)
class ExtractionResult:
    status: ExtractionStatus
    reason: str
    publishable_fields: tuple[str, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_diagnostic_kwargs(self) -> dict[str, Any]:
        """Sanitized labels only — never balances, cookies, tokens, or DOM excerpts."""
        labels = []
        diag = self.diagnostics if isinstance(self.diagnostics, dict) else {}
        raw_labels = diag.get("labels")
        if isinstance(raw_labels, (list, tuple)):
            labels = [str(x) for x in raw_labels if x]
        if not labels:
            labels = [self.reason]
        return {
            "status": self.status.value if isinstance(self.status, ExtractionStatus) else str(self.status),
            "reason": str(self.reason),
            "publishable_fields": ",".join(self.publishable_fields),
            "diagnostic_labels": ",".join(labels),
        }


def parse_extraction_result_payload(body: dict[str, Any] | None) -> ExtractionResult | None:
    """Parse extension-posted ExtractionResult fields (status/reason/labels only)."""
    if not isinstance(body, dict):
        return None
    status_raw = str(body.get("extraction_status") or "").strip()
    reason = str(body.get("extraction_reason") or body.get("reason") or "").strip()
    if not status_raw:
        return None
    try:
        status = ExtractionStatus(status_raw)
    except ValueError:
        return None
    fields_raw = body.get("publishable_fields") or []
    if isinstance(fields_raw, str):
        publishable = tuple(x for x in fields_raw.split(",") if x)
    elif isinstance(fields_raw, (list, tuple)):
        publishable = tuple(str(x) for x in fields_raw if x)
    else:
        publishable = ()
    labels = body.get("diagnostic_labels") or body.get("diagnostics")
    diagnostics: dict[str, Any] = {}
    if isinstance(labels, dict):
        diagnostics = {"labels": labels.get("labels") or [reason]}
    elif isinstance(labels, (list, tuple)):
        diagnostics = {"labels": list(labels)}
    elif reason:
        diagnostics = {"labels": [reason]}
    return ExtractionResult(
        status=status,
        reason=reason or status.value.lower(),
        publishable_fields=publishable,
        diagnostics=diagnostics,
    )
