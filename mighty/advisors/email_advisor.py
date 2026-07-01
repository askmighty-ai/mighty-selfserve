"""
mighty.advisors.email_advisor
─────────────────────────────
Email-subject contextual opportunity advisor.

Deterministic keyword matching on recent email subjects — no database, AI,
or network calls. The matching layer is isolated so it can later be swapped
for an LLM or rules engine without changing callers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from mighty.decision_engine import DecisionContext
from mighty.advisors.base import Opportunity

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _KeywordRule:
    id: str
    keywords: tuple[str, ...]
    title: str
    summary: str
    category: str
    confidence: str
    rationale: str
    bullets: tuple[str, ...]
    action_label: str
    action_url: str


_KEYWORD_RULES: tuple[_KeywordRule, ...] = (
    _KeywordRule(
        id="email_hyatt",
        keywords=("hyatt", "world of hyatt"),
        title="Review your Hyatt emails",
        summary="Recent Hyatt messages may include points offers or stay updates.",
        category="hotel",
        confidence="medium",
        rationale="A recent email subject mentioned Hyatt.",
        bullets=(
            "Check for bonus points or status promotions",
            "Confirm upcoming reservation details",
            "Compare Chase transfer value before booking",
        ),
        action_label="Open World of Hyatt",
        action_url="https://www.hyatt.com/",
    ),
    _KeywordRule(
        id="email_marriott",
        keywords=("marriott", "bonvoy"),
        title="Review your Marriott emails",
        summary="Recent Marriott messages may include Bonvoy offers or stay alerts.",
        category="hotel",
        confidence="medium",
        rationale="A recent email subject mentioned Marriott.",
        bullets=(
            "Look for free-night or points promotions",
            "Verify upcoming reservation details",
            "Check elite benefit eligibility at your property",
        ),
        action_label="Open Marriott Bonvoy",
        action_url="https://www.marriott.com/",
    ),
    _KeywordRule(
        id="email_hilton",
        keywords=("hilton", "hilton honors"),
        title="Review your Hilton emails",
        summary="Recent Hilton messages may include Honors offers or stay updates.",
        category="hotel",
        confidence="medium",
        rationale="A recent email subject mentioned Hilton.",
        bullets=(
            "Check for bonus points or status offers",
            "Confirm upcoming reservation details",
            "Review Amex FHR eligibility if booking premium stays",
        ),
        action_label="Open Hilton Honors",
        action_url="https://www.hilton.com/",
    ),
    _KeywordRule(
        id="email_airbnb",
        keywords=("airbnb",),
        title="Review your Airbnb emails",
        summary="Recent Airbnb messages may include trip details or host updates.",
        category="travel",
        confidence="medium",
        rationale="A recent email subject mentioned Airbnb.",
        bullets=(
            "Confirm check-in instructions and dates",
            "Review cancellation policy before changes",
            "Check whether a travel card covers the stay",
        ),
        action_label="Open Airbnb",
        action_url="https://www.airbnb.com/",
    ),
    _KeywordRule(
        id="email_southwest",
        keywords=("southwest", "rapid rewards"),
        title="Review your Southwest emails",
        summary="Recent Southwest messages may include Companion Pass or flight updates.",
        category="travel",
        confidence="medium",
        rationale="A recent email subject mentioned Southwest.",
        bullets=(
            "Check Companion Pass status before booking",
            "Review change or cancellation notices",
            "Look for fare sale or points promotions",
        ),
        action_label="Open Southwest",
        action_url="https://www.southwest.com/",
    ),
    _KeywordRule(
        id="email_united",
        keywords=("united", "mileageplus"),
        title="Review your United emails",
        summary="Recent United messages may include MileagePlus or flight updates.",
        category="travel",
        confidence="medium",
        rationale="A recent email subject mentioned United.",
        bullets=(
            "Check upgrade or schedule-change notices",
            "Review expiring miles or certificate offers",
            "Confirm seat assignments for upcoming trips",
        ),
        action_label="Open United",
        action_url="https://www.united.com/",
    ),
    _KeywordRule(
        id="email_delta",
        keywords=("delta", "skymiles"),
        title="Review your Delta emails",
        summary="Recent Delta messages may include SkyMiles or flight updates.",
        category="travel",
        confidence="medium",
        rationale="A recent email subject mentioned Delta.",
        bullets=(
            "Check upgrade or schedule-change notices",
            "Review Medallion or certificate offers",
            "Confirm seat assignments for upcoming trips",
        ),
        action_label="Open Delta",
        action_url="https://www.delta.com/",
    ),
    _KeywordRule(
        id="email_amex",
        keywords=("amex", "american express"),
        title="Review your Amex emails",
        summary="Recent Amex messages may include card benefits or offer updates.",
        category="credit_card",
        confidence="medium",
        rationale="A recent email subject mentioned Amex.",
        bullets=(
            "Check for new Amex Offers",
            "Review expiring credits or benefits",
            "Confirm travel booking channels for card perks",
        ),
        action_label="Open Amex",
        action_url="https://www.americanexpress.com/",
    ),
    _KeywordRule(
        id="email_chase",
        keywords=("chase", "ultimate rewards"),
        title="Review your Chase emails",
        summary="Recent Chase messages may include card benefits or Ultimate Rewards updates.",
        category="credit_card",
        confidence="medium",
        rationale="A recent email subject mentioned Chase.",
        bullets=(
            "Check for new Chase Offers",
            "Review expiring credits or bonus categories",
            "Compare Ultimate Rewards transfer partners",
        ),
        action_label="Open Chase",
        action_url="https://www.chase.com/",
    ),
)


def _recent_subjects(
    context: DecisionContext,
    user_memory: dict[str, Any] | None,
) -> list[str]:
    subjects = context.metadata.get("email_subjects")
    if subjects is None and user_memory:
        subjects = user_memory.get("email_subjects")
    if not isinstance(subjects, list):
        return []
    return [str(s).strip() for s in subjects if str(s).strip()]


def _match_subjects(subjects: list[str]) -> list[Opportunity]:
    """Keyword matcher — replace this function to swap in an LLM or rules engine."""
    normalized = [s.lower() for s in subjects]
    matched: list[Opportunity] = []
    seen_ids: set[str] = set()

    for rule in _KEYWORD_RULES:
        if rule.id in seen_ids:
            continue
        for subject in normalized:
            if any(keyword in subject for keyword in rule.keywords):
                matched.append(
                    Opportunity(
                        id=rule.id,
                        title=rule.title,
                        summary=rule.summary,
                        category=rule.category,
                        confidence=rule.confidence,
                        rationale=rule.rationale,
                        bullets=list(rule.bullets),
                        action_label=rule.action_label,
                        action_url=rule.action_url,
                    )
                )
                seen_ids.add(rule.id)
                break

    return matched


def evaluate(
    context: DecisionContext,
    user_memory: dict[str, Any] | None = None,
) -> list[Opportunity]:
    metadata_has_subjects = "email_subjects" in context.metadata
    memory_has_subjects = bool(
        user_memory and isinstance(user_memory.get("email_subjects"), list)
    )
    logger.info(
        "[email_advisor_debug] evaluate called: source=%r "
        "metadata_has_subjects=%s user_memory_has_subjects=%s",
        context.source,
        metadata_has_subjects,
        memory_has_subjects,
    )

    has_subjects = metadata_has_subjects or memory_has_subjects
    if context.source not in ("email", "dashboard") and not has_subjects:
        logger.info(
            "[email_advisor_debug] returning 0 recommendations: "
            "source=%r has no email_subjects in metadata or user_memory",
            context.source,
        )
        return []

    subjects = _recent_subjects(context, user_memory)
    if not subjects:
        logger.info(
            "[email_advisor_debug] returning 0 recommendations: "
            "no email subjects in metadata or user_memory "
            "(metadata_has_subjects=%s user_memory_has_subjects=%s)",
            metadata_has_subjects,
            memory_has_subjects,
        )
        return []

    matched = _match_subjects(subjects)
    if matched:
        logger.info(
            "[email_advisor_debug] returning %d recommendation(s): ids=%s "
            "from %d subject(s)",
            len(matched),
            [opp.id for opp in matched],
            len(subjects),
        )
    else:
        logger.info(
            "[email_advisor_debug] returning 0 recommendations: "
            "%d subject(s) had no keyword matches (sample=%r)",
            len(subjects),
            subjects[:3],
        )
    return matched
