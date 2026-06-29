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
    recommendation_type: str = "general"


from mighty.advisors.hotel import evaluate as evaluate_hotel


def detect_situation(context: DecisionContext) -> Situation:
    return Situation(kind="unknown", confidence="low", evidence=[])


def get_recommendations(
    context: DecisionContext,
    user_memory: dict[str, Any] | None = None,
) -> list[Recommendation]:
    if context.source == "dashboard":
        return [
            Recommendation(
                title="Book this hotel through Amex Travel",
                summary="Your Platinum benefits may unlock breakfast, upgrades and late checkout.",
                rationale="Demo recommendation.",
                recommendation_type="hotel",
                confidence="high",
                bullets=[
                    "Fine Hotels + Resorts eligible",
                    "Potential room upgrade",
                    "Late checkout when available",
                ],
                action_label="Open Amex Travel",
                action_url="https://www.americanexpress.com/travel/",
            )
        ]

    detect_situation(context)
    opportunities = evaluate_hotel(context, user_memory)
    return [
        Recommendation(
            title=opp.title,
            summary=opp.summary,
            rationale=opp.rationale,
            bullets=opp.bullets,
            confidence=opp.confidence,
            action_label=opp.action_label,
            action_url=opp.action_url,
        )
        for opp in opportunities
    ]
