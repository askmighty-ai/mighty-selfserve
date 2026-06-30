"""
mighty.action
─────────────
Unified Action model for everything Mighty surfaces that needs user attention.

Recommendations, alerts, expiring benefits, discoveries, login issues, savings
opportunities, and approval requests all normalize to a single Action shape so
prioritization and AI reasoning can operate on one list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any


class ActionCategory(str, Enum):
    RECOMMENDATION = "recommendation"
    ALERT = "alert"
    EXPIRING_BENEFIT = "expiring_benefit"
    DISCOVERY = "discovery"
    LOGIN_ISSUE = "login_issue"
    SAVINGS_OPPORTUNITY = "savings_opportunity"
    APPROVAL_REQUEST = "approval_request"


class ActionPriority(str, Enum):
    URGENT = "urgent"
    SOON = "soon"
    INFO = "info"


class CompletionState(str, Enum):
    OPEN = "open"
    COMPLETED = "completed"
    DISMISSED = "dismissed"
    SNOOZED = "snoozed"
    PENDING_APPROVAL = "pending_approval"


@dataclass
class Action:
    """A single item Mighty wants the user to know about or act on."""

    title: str
    summary: str = ""
    priority: ActionPriority = ActionPriority.INFO
    category: ActionCategory = ActionCategory.RECOMMENDATION
    estimated_value: str = ""
    due_date: date | None = None
    confidence: str = "low"
    reasoning: str = ""
    source_accounts: list[str] = field(default_factory=list)
    recommended_next_step: str = ""
    completion_state: CompletionState = CompletionState.OPEN

    # Rendering and traceability (not part of the core contract, but needed today)
    id: str | int | None = None
    subcategory: str = ""
    bullets: list[str] = field(default_factory=list)
    action_url: str = ""
    days_until_due: int | None = None
    benefit_type: str = ""
    display_name: str = ""
    score: int | None = None

    @property
    def is_demo(self) -> bool:
        return self.reasoning.strip().lower() == "demo recommendation."

    @property
    def is_open(self) -> bool:
        return self.completion_state == CompletionState.OPEN

    # Compatibility with recommendation card renderer (getattr-based)
    @property
    def recommendation_type(self) -> str:
        return self.subcategory or "general"

    @property
    def rationale(self) -> str:
        return self.reasoning

    @property
    def action_label(self) -> str:
        return self.recommended_next_step

    def primary_source(self) -> str:
        return self.source_accounts[0] if self.source_accounts else ""

    def source_display(self) -> str:
        return self.primary_source().replace("_", " ").title()

    def detail_line(self) -> str:
        """Brief-style detail: ``Source · summary`` when source isn't already in summary."""
        source = self.source_display()
        summary = (self.summary or self.estimated_value or "").strip()
        if source and source.lower() not in summary.lower():
            return f"{source} · {summary}" if summary else source
        return summary

    def expiry_phrase(self) -> str:
        if self.days_until_due is None or self.days_until_due < 0:
            return ""
        if self.days_until_due == 0:
            return "expires today"
        if self.days_until_due == 1:
            return "expires tomorrow"
        return f"expires in {self.days_until_due} days"

    def is_expiring_soon(self, within_days: int = 45) -> bool:
        if self.days_until_due is None:
            return False
        return 0 <= self.days_until_due <= within_days


def parse_due_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def priority_from_urgency(urgency: str) -> ActionPriority:
    key = (urgency or "").strip().lower()
    if key == "urgent":
        return ActionPriority.URGENT
    if key == "soon":
        return ActionPriority.SOON
    return ActionPriority.INFO
