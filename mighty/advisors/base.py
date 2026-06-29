"""
mighty.advisors.base
────────────────────
Shared contract for contextual advisors.

Pure interface only — no database, AI, or network calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from mighty.decision_engine import DecisionContext


@dataclass
class Opportunity:
    id: str
    title: str
    summary: str
    score: int = 0
    category: str = ""
    confidence: str = "low"
    rationale: str = ""
    bullets: list[str] = field(default_factory=list)
    action_label: str = ""
    action_url: str = ""


class Advisor(Protocol):
    def evaluate(
        self,
        context: DecisionContext,
        user_memory: dict[str, Any] | None = None,
    ) -> list[Opportunity]: ...
