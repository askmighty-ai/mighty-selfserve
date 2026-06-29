"""
mighty.decision_engine
──────────────────────
General framework for contextual decision advice.

Pure functions only — no database, AI, or network calls.
Vertical-specific logic (hotels, cards, airlines, etc.) is added later.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


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


from mighty.advisors.email_advisor import evaluate as evaluate_email
from mighty.advisors.hotel import evaluate as evaluate_hotel


def _debug_probe_email_advisor(
    context: DecisionContext,
    user_memory: dict[str, Any] | None,
    *,
    merged_into_response: bool,
) -> int:
    """Temporary debug hook — logs email advisor output without changing responses."""
    email_opportunities = evaluate_email(context, user_memory)
    logger.info(
        "[email_advisor_debug] decision_engine probe: merged_into_response=%s "
        "email_advisor_returned=%d",
        merged_into_response,
        len(email_opportunities),
    )
    return len(email_opportunities)


def detect_situation(context: DecisionContext) -> Situation:
    return Situation(kind="unknown", confidence="low", evidence=[])


def get_recommendations(
    context: DecisionContext,
    user_memory: dict[str, Any] | None = None,
) -> list[Recommendation]:
    logger.info(
        "[email_advisor_debug] get_recommendations called: source=%r "
        "metadata_keys=%s has_user_memory=%s",
        context.source,
        sorted(context.metadata.keys()),
        user_memory is not None,
    )

    if context.source == "dashboard":
        logger.info(
            "[email_advisor_debug] email advisor not merged for dashboard source "
            "(demo recommendations only); running debug probe"
        )
        _debug_probe_email_advisor(
            context, user_memory, merged_into_response=False
        )
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
            ),
            Recommendation(
                title="Transfer Chase Ultimate Rewards to Hyatt",
                summary="World of Hyatt points often deliver strong value at premium properties.",
                rationale="Demo recommendation.",
                recommendation_type="hotel",
                confidence="high",
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
                bullets=[
                    "Companion flies for taxes and fees only",
                    "Valid on both paid and points bookings",
                    "Pass expires — use it before your travel window closes",
                ],
                action_label="Book on Southwest",
                action_url="https://www.southwest.com/",
            ),
        ]

    detect_situation(context)
    opportunities = evaluate_hotel(context, user_memory)
    logger.info(
        "[email_advisor_debug] hotel advisor returned %d recommendation(s); "
        "email advisor not merged into response; running debug probe",
        len(opportunities),
    )
    _debug_probe_email_advisor(context, user_memory, merged_into_response=False)
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
