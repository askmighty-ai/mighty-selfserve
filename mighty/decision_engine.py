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
    evidence: list[str] = field(default_factory=list)
    why_now: str = ""
    alternative_options: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)
    confidence: str = "low"
    action_label: str = ""
    action_url: str = ""
    recommendation_type: str = "general"


def recommendation_contract_violations(rec: Recommendation) -> list[str]:
    """Return missing required recommendation fields (empty list = complete)."""
    missing: list[str] = []
    if not rec.rationale.strip():
        missing.append("rationale")
    if not rec.confidence.strip():
        missing.append("confidence")
    if not rec.why_now.strip():
        missing.append("why_now")
    if not rec.evidence:
        missing.append("evidence")
    if not rec.alternative_options:
        missing.append("alternative_options")
    return missing


from mighty.advisors.email_advisor import evaluate as evaluate_email
from mighty.advisors.hotel import evaluate as evaluate_hotel


def _dashboard_demo_recommendations() -> list[Recommendation]:
    """Deterministic fallback recommendations when the email advisor has no matches."""
    return [
        Recommendation(
            title="Book this hotel through Amex Travel",
            summary="Your Platinum benefits may unlock breakfast, upgrades and late checkout.",
            rationale="Demo recommendation.",
            recommendation_type="hotel",
            confidence="high",
            evidence=["Platinum card with Fine Hotels + Resorts eligibility"],
            why_now="Demo scenario — booking window is open for your upcoming stay.",
            alternative_options=[
                "Book direct with the hotel loyalty program",
                "Use Chase portal with Sapphire Reserve travel credits",
            ],
            bullets=[
                "Fine Hotels + Resorts eligible",
                "Potential room upgrade",
                "Late checkout when available",
            ],
            action_label="Open Amex Travel",
            action_url="https://www.americanexpress.com/travel/",
        ),
        Recommendation(
            title="Transfer Chase Ultimate Rewards to Hyatt",
            summary="World of Hyatt points often deliver strong value at premium properties.",
            rationale="Demo recommendation.",
            recommendation_type="hotel",
            confidence="high",
            evidence=["Ultimate Rewards balance available for 1:1 transfer to Hyatt"],
            why_now="Demo scenario — transfer before booking to lock in Hyatt award rates.",
            alternative_options=[
                "Pay cash and earn points on the stay",
                "Book via Amex Travel with FHR benefits instead",
            ],
            bullets=[
                "1:1 transfer from Chase Ultimate Rewards",
                "Strong redemption value at Category 1–4 hotels",
                "Suite upgrades and elite benefits when available",
            ],
            action_label="Transfer to Hyatt",
            action_url="https://www.hyatt.com/",
        ),
        Recommendation(
            title="Use Southwest Companion Pass before booking",
            summary="Bring a companion for nearly free on Southwest flights this year.",
            rationale="Demo recommendation.",
            recommendation_type="travel",
            confidence="medium",
            evidence=["Companion Pass active on your Southwest account"],
            why_now="Demo scenario — pass expires at end of calendar year.",
            alternative_options=[
                "Book solo at standard Southwest fares",
                "Use points on a partner airline instead",
            ],
            bullets=[
                "Companion flies for taxes and fees only",
                "Valid on both paid and points bookings",
                "Pass expires — use it before your travel window closes",
            ],
            action_label="Book on Southwest",
            action_url="https://www.southwest.com/",
        ),
    ]


def _opportunities_to_recommendations(opportunities: list[Any]) -> list[Recommendation]:
    return [
        Recommendation(
            title=opp.title,
            summary=opp.summary,
            rationale=opp.rationale,
            evidence=list(opp.evidence),
            why_now=opp.why_now,
            alternative_options=list(opp.alternative_options),
            bullets=opp.bullets,
            confidence=opp.confidence,
            action_label=opp.action_label,
            action_url=opp.action_url,
            recommendation_type=opp.category or "general",
        )
        for opp in opportunities
    ]


def _collect_live_advisor_recommendations(
    context: DecisionContext,
    user_memory: dict[str, Any] | None = None,
) -> list[Recommendation]:
    """Collect live recommendations from advisors."""
    return _opportunities_to_recommendations(evaluate_email(context, user_memory))


def detect_situation(context: DecisionContext) -> Situation:
    return Situation(kind="unknown", confidence="low", evidence=[])


def get_recommendations(
    context: DecisionContext,
    user_memory: dict[str, Any] | None = None,
) -> list[Recommendation]:
    if context.source == "dashboard":
        user_memory = user_memory or {}
        recommendations = (
            []
            if user_memory.get("suppress_demo_content")
            else _dashboard_demo_recommendations()
        )
        recommendations.extend(_collect_live_advisor_recommendations(context, user_memory))
        return recommendations

    detect_situation(context)
    opportunities = evaluate_hotel(context, user_memory)
    return _opportunities_to_recommendations(opportunities)
