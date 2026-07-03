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
    why_now: str
    alternative_options: tuple[str, ...]
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
        why_now="The email arrived recently — offers and promotions often expire within days.",
        alternative_options=(
            "Ignore the email if you have no upcoming Hyatt stays",
            "Check your Hyatt account directly instead of acting on the email",
        ),
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
        why_now="The email arrived recently — free-night and points offers often have short windows.",
        alternative_options=(
            "Check Marriott Bonvoy app for the same offers",
            "Wait for a better targeted promotion before booking",
        ),
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
        why_now="The email arrived recently — status and bonus offers may expire soon.",
        alternative_options=(
            "Book through Amex FHR if the property is eligible",
            "Check Hilton Honors app instead of the email link",
        ),
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
        why_now="Trip-related emails often contain time-sensitive check-in or change details.",
        alternative_options=(
            "Open the Airbnb app to view the same trip details",
            "Contact the host directly through the platform",
        ),
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
        why_now="Flight and Companion Pass notices often require action before departure.",
        alternative_options=(
            "Check southwest.com My Trips for the same updates",
            "Call Southwest if the email mentions a schedule change",
        ),
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
        why_now="Upgrade and schedule-change notices often have response deadlines.",
        alternative_options=(
            "Check united.com My Trips for the same flight status",
            "Use the United app for real-time gate and seat updates",
        ),
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
        why_now="Certificate and upgrade offers often expire before your next trip.",
        alternative_options=(
            "Check delta.com My Trips for the same updates",
            "Use the Fly Delta app for live flight status",
        ),
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
        why_now="Amex Offers and expiring credits typically have enrollment or use-by dates.",
        alternative_options=(
            "Check Amex app Offers tab for the same deals",
            "Log in to americanexpress.com to review all active credits",
        ),
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
        why_now="Bonus category and transfer promotions often have limited enrollment windows.",
        alternative_options=(
            "Check Chase app for the same offers and credits",
            "Review Ultimate Rewards portal before transferring points",
        ),
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
    matched: list[Opportunity] = []
    seen_ids: set[str] = set()

    for rule in _KEYWORD_RULES:
        if rule.id in seen_ids:
            continue
        for subject in subjects:
            subject_lower = subject.lower()
            if any(keyword in subject_lower for keyword in rule.keywords):
                matched.append(
                    Opportunity(
                        id=rule.id,
                        title=rule.title,
                        summary=rule.summary,
                        category=rule.category,
                        confidence=rule.confidence,
                        rationale=rule.rationale,
                        evidence=[f"Email subject: {subject.strip()}"],
                        why_now=rule.why_now,
                        alternative_options=list(rule.alternative_options),
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
