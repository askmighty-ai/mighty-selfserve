"""
mighty.advisors.email_advisor
─────────────────────────────
Email-subject contextual opportunity advisor.

Parses recent email subjects for actionable signals (promos, expiring perks,
schedule changes) and emits specific recommendations with rationale tied to
the actual subject line. Generic brand-only mentions are skipped — benefit
advisor handles synced account data for those cases.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from mighty.decision_engine import DecisionContext
from mighty.advisors.base import Opportunity

logger = logging.getLogger(__name__)

_ACTION_SIGNALS: tuple[str, ...] = (
    "expir",
    "expires",
    "ends",
    "ending",
    "offer",
    "bonus",
    "2x",
    "3x",
    "companion pass",
    "free night",
    "schedule change",
    "payment due",
    "sale",
    "limited time",
    "promotion",
    "promo",
    "certificate",
    "upgrade",
    "alert",
    "reminder",
    "confirm",
    "check-in",
    "trip to",
    "points",
    "miles",
    "award",
    "statement",
    "credit",
)

_POINTS_MULTIPLIER = re.compile(r"\b(\d)\s*x\b", re.I)


@dataclass(frozen=True)
class _BrandRule:
    id: str
    keywords: tuple[str, ...]
    display: str
    category: str
    action_label: str
    action_url: str


_BRAND_RULES: tuple[_BrandRule, ...] = (
    _BrandRule("email_hyatt", ("hyatt", "world of hyatt"), "World of Hyatt", "hotel", "Book on Hyatt", "https://www.hyatt.com/"),
    _BrandRule("email_marriott", ("marriott", "bonvoy"), "Marriott Bonvoy", "hotel", "Book with Bonvoy", "https://www.marriott.com/"),
    _BrandRule("email_hilton", ("hilton", "hilton honors"), "Hilton Honors", "hotel", "Book on Hilton", "https://www.hilton.com/"),
    _BrandRule("email_airbnb", ("airbnb",), "Airbnb", "travel", "Open reservation", "https://www.airbnb.com/"),
    _BrandRule("email_southwest", ("southwest", "rapid rewards"), "Southwest", "travel", "Book on Southwest", "https://www.southwest.com/"),
    _BrandRule("email_united", ("united", "mileageplus"), "United", "travel", "Book on United", "https://www.united.com/"),
    _BrandRule("email_delta", ("delta", "skymiles"), "Delta", "travel", "Book on Delta", "https://www.delta.com/"),
    _BrandRule("email_amex", ("amex", "american express"), "Amex", "credit_card", "View Amex Offers", "https://www.americanexpress.com/"),
    _BrandRule("email_chase", ("chase", "ultimate rewards"), "Chase", "credit_card", "Open Chase", "https://www.chase.com/"),
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


def _has_action_signal(subject: str) -> bool:
    subject_lc = subject.lower()
    return any(signal in subject_lc for signal in _ACTION_SIGNALS)


def _match_brand(subject_lc: str) -> _BrandRule | None:
    for rule in _BRAND_RULES:
        if any(keyword in subject_lc for keyword in rule.keywords):
            return rule
    return None


def _truncate_subject(subject: str, max_len: int = 72) -> str:
    text = subject.strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _build_opportunity(rule: _BrandRule, subject: str) -> Opportunity | None:
    if not _has_action_signal(subject):
        return None

    subject_lc = subject.lower()
    quoted = _truncate_subject(subject)
    score = 35

    if _POINTS_MULTIPLIER.search(subject_lc):
        multiplier = _POINTS_MULTIPLIER.search(subject_lc).group(1)
        title = f"Book {rule.display} travel while the {multiplier}x points promo is active"
        summary = f"Limited-time multiplier detected in your inbox: \"{quoted}\"."
        rationale = (
            f"Your email \"{quoted}\" signals a bonus-points promotion — "
            f"booking or staying during the promo window earns {multiplier}x the usual earn rate."
        )
        bullets = (
            f"Promo subject: {quoted}",
            "Book directly through the loyalty program to qualify",
            "Stack with any elite status bonus if applicable",
        )
        score = 55
    elif "companion pass" in subject_lc:
        title = "Add a companion to your next Southwest flight"
        summary = f"Companion Pass activity detected: \"{quoted}\"."
        rationale = (
            f"\"{quoted}\" references Companion Pass — "
            "your companion flies for taxes and fees only when you book together."
        )
        bullets = (
            "Book both tickets on the same reservation",
            "Valid on paid and points bookings",
            "Check pass expiration before booking",
        )
        score = 60
    elif any(k in subject_lc for k in ("free night", "award night", "certificate")):
        title = f"Redeem your {rule.display} free night or certificate"
        summary = f"Certificate or award alert: \"{quoted}\"."
        rationale = (
            f"\"{quoted}\" references an expiring or available certificate — "
            "unused awards typically cannot be recovered after expiration."
        )
        bullets = (
            "Search award availability at your target property",
            "Book before the expiration date in the email",
            "Confirm taxes/fees due at checkout",
        )
        score = 65
    elif any(k in subject_lc for k in ("expir", "expires", "ends", "ending")):
        title = f"Act on your {rule.display} offer before it expires"
        summary = f"Expiration notice: \"{quoted}\"."
        rationale = (
            f"\"{quoted}\" includes an expiration deadline — "
            "waiting risks losing the offer or benefit entirely."
        )
        bullets = (
            f"Source email: {quoted}",
            "Log in and confirm the exact expiration date",
            "Complete the required action before the deadline",
        )
        score = 70
    elif "schedule change" in subject_lc:
        title = f"Review your {rule.display} schedule change"
        summary = f"Flight change notice: \"{quoted}\"."
        rationale = (
            f"\"{quoted}\" indicates a schedule change — "
            "airlines often allow free rebooking or refunds when timing shifts significantly."
        )
        bullets = (
            "Confirm new departure and arrival times",
            "Check connection buffers if you have a layover",
            "Request a refund or credit if the new times don't work",
        )
        score = 75
    elif any(k in subject_lc for k in ("offer", "promo", "promotion", "sale", "bonus")):
        title = f"Claim your {rule.display} offer from email"
        summary = f"Active offer detected: \"{quoted}\"."
        rationale = (
            f"\"{quoted}\" references a current promotion — "
            "activating or booking now locks in the advertised benefit."
        )
        bullets = (
            f"Offer email: {quoted}",
            "Activate the offer in your account if required",
            "Use the linked card or loyalty account to qualify",
        )
        score = 50
    elif "trip to" in subject_lc or "check-in" in subject_lc:
        title = f"Confirm details for your upcoming {rule.display} trip"
        summary = f"Trip update: \"{quoted}\"."
        rationale = (
            f"\"{quoted}\" relates to an upcoming trip — "
            "confirming dates, check-in, or cancellation terms now avoids last-minute issues."
        )
        bullets = (
            "Verify dates and property address",
            "Review cancellation policy before changes",
            "Check whether points or credits apply to this stay",
        )
        score = 45
    elif rule.category == "credit_card" and any(k in subject_lc for k in ("credit", "statement", "points")):
        title = f"Activate expiring {rule.display} credits or offers"
        summary = f"Card benefit update: \"{quoted}\"."
        rationale = (
            f"\"{quoted}\" may reference expiring statement credits, Amex/Chase Offers, "
            "or bonus categories — unused credits reset and cannot roll over."
        )
        bullets = (
            "Check Amex Offers or Chase Offers for activation",
            "Use expiring credits before the billing cycle ends",
            "Confirm merchant category eligibility",
        )
        score = 48
    elif rule.category == "travel" and any(k in subject_lc for k in ("statement", "miles", "balance")):
        title = f"Check {rule.display} for expiring miles or new offers"
        summary = f"Account update: \"{quoted}\"."
        rationale = (
            f"\"{quoted}\" is an account statement or balance notice — "
            "miles and certificates often expire on a fixed schedule, and statements surface limited-time offers."
        )
        bullets = (
            "Review expiring miles or certificates on your account",
            "Look for bonus-mile promotions in the email",
            "Book award travel before prices increase",
        )
        score = 42
    else:
        return None

    return Opportunity(
        id=rule.id,
        title=title,
        summary=summary,
        category=rule.category,
        confidence="high" if score >= 65 else "medium" if score >= 45 else "low",
        rationale=rationale,
        bullets=list(bullets),
        action_label=rule.action_label,
        action_url=rule.action_url,
        score=score,
    )


def _match_subjects(subjects: list[str]) -> list[Opportunity]:
    matched: list[Opportunity] = []
    seen_ids: set[str] = set()

    for subject in subjects:
        subject_lc = subject.lower()
        rule = _match_brand(subject_lc)
        if rule is None or rule.id in seen_ids:
            continue
        opp = _build_opportunity(rule, subject)
        if opp is None:
            continue
        matched.append(opp)
        seen_ids.add(rule.id)

    matched.sort(key=lambda o: (-o.score, o.title))
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
            "%d subject(s) had no actionable matches (sample=%r)",
            len(subjects),
            subjects[:3],
        )
    return matched
