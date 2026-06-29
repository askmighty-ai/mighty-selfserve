"""
mighty.decision_engine
──────────────────────
General framework for contextual decision advice.

Pure functions only — no database, AI, or network calls.
Vertical-specific logic (hotels, cards, airlines, etc.) is added later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DecisionContext:
    url: str
    page_title: str = ""
    page_text: str = ""
    source: str = "browser"
    user_intent: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Situation:
    kind: str
    merchant: str = ""
    category: str = ""
    confidence: str = "low"
    evidence: list[str] = field(default_factory=list)


@dataclass
class Recommendation:
    title: str
    summary: str
    rationale: str = ""
    bullets: list[str] = field(default_factory=list)
    confidence: str = "low"
    action_label: str = ""
    action_url: str = ""


def detect_situation(context: DecisionContext) -> Situation:
    return Situation(kind="unknown", confidence="low", evidence=[])


def get_recommendations(
    context: DecisionContext,
    user_memory: dict[str, Any] | None = None,
) -> list[Recommendation]:
    detect_situation(context)
    return []
