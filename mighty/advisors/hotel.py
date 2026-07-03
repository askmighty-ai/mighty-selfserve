"""
mighty.advisors.hotel
─────────────────────
Hotel-related contextual opportunity advisor.

Deterministic hotel booking advice — no database, AI, or network calls.
"""

from __future__ import annotations

from typing import Any

from mighty.advisors.base import Opportunity
from mighty.decision_engine import DecisionContext

_HOTEL_BENEFIT_MARKERS = (
    "fine hotels",
    "fhr",
    "hotel collection",
    "platinum travel",
    "amex travel",
)


def _available_benefits(
    context: DecisionContext,
    user_memory: dict[str, Any] | None,
) -> list[Any]:
    benefits = context.metadata.get("available_benefits")
    if benefits is None and user_memory:
        benefits = user_memory.get("available_benefits")
    return benefits if isinstance(benefits, list) else []


def _benefit_text(item: Any) -> str:
    if isinstance(item, str):
        return item.lower()
    if isinstance(item, dict):
        for key in ("label", "name", "title", "summary", "source"):
            value = item.get(key)
            if value:
                return str(value).lower()
    return str(item).lower()


def _has_eligible_hotel_benefit(benefits: list[Any]) -> bool:
    return any(
        any(marker in _benefit_text(item) for marker in _HOTEL_BENEFIT_MARKERS)
        for item in benefits
    )


def evaluate(
    context: DecisionContext,
    user_memory: dict[str, Any] | None = None,
) -> list[Opportunity]:
    intent = context.user_intent or str(context.metadata.get("intent", ""))
    if intent != "hotel_booking":
        return []

    if not _has_eligible_hotel_benefit(_available_benefits(context, user_memory)):
        return []

    return [
        Opportunity(
            id="hotel_use_benefits",
            title="Use your hotel benefits",
            summary="Book through Amex Travel to apply eligible hotel benefits.",
            category="travel",
            confidence="high",
            rationale=(
                "A hotel booking was detected and eligible hotel benefits appear available."
            ),
            evidence=[
                "hotel_booking intent detected on current page",
                "Amex Fine Hotels / FHR benefit appears available",
            ],
            why_now="You're on a hotel booking page — benefits must be applied at checkout.",
            alternative_options=[
                "Book direct with the hotel loyalty program",
                "Use points through the hotel's own award portal",
            ],
            bullets=[
                "Book through Amex Travel",
                "Use Fine Hotels & Resorts if eligible",
                "Check for property credits and breakfast",
            ],
            action_label="Open Amex Travel",
            action_url="https://www.americanexpress.com/travel",
        )
    ]
