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

from mighty.scoring import urgency_from_score


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
    score: int = 0
    urgency: str = "info"
    id: str = ""


_EMAIL_PROGRAM_KEYS: dict[str, str] = {
    "email_marriott": "marriott",
    "email_hyatt": "hyatt",
    "email_hilton": "hilton",
    "email_united": "united",
    "email_delta": "delta",
    "email_southwest": "southwest",
    "email_chase": "chase",
    "email_amex": "amex",
    "email_airbnb": "airbnb",
}

_GENERIC_TITLE_PREFIXES = ("review your ",)
_GENERIC_RATIONALES = {"demo recommendation.", "a recent email subject mentioned"}


def _dashboard_demo_recommendations() -> list[Recommendation]:
    """Deterministic fallback recommendations when no live account data exists."""
    return [
        Recommendation(
            id="demo_amex_hotel",
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
            score=70,
            urgency="soon",
        ),
        Recommendation(
            id="demo_chase_hyatt",
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
            score=65,
            urgency="soon",
        ),
        Recommendation(
            id="demo_southwest_companion",
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
            score=55,
            urgency="soon",
        ),
    ]


def _opportunity_to_recommendation(opp: Any) -> Recommendation:
    score = int(getattr(opp, "score", 0) or 0)
    return Recommendation(
        id=str(getattr(opp, "id", "") or ""),
        title=opp.title,
        summary=opp.summary,
        rationale=opp.rationale,
        bullets=list(opp.bullets),
        confidence=opp.confidence,
        action_label=opp.action_label,
        action_url=opp.action_url,
        recommendation_type=opp.category or "general",
        score=score,
        urgency=urgency_from_score(score),
    )


def _opportunities_to_recommendations(opportunities: list[Any]) -> list[Recommendation]:
    return [_opportunity_to_recommendation(opp) for opp in opportunities]


def _is_demo(rec: Recommendation) -> bool:
    return rec.rationale.strip().lower() == "demo recommendation."


def _is_generic(rec: Recommendation) -> bool:
    if rec.id.startswith("demo_"):
        return False
    title_lc = rec.title.strip().lower()
    rationale_lc = rec.rationale.strip().lower()
    if any(title_lc.startswith(prefix) for prefix in _GENERIC_TITLE_PREFIXES):
        return True
    if rationale_lc in _GENERIC_RATIONALES:
        return True
    if not rec.rationale.strip():
        return True
    if not rec.action_label.strip() and not rec.bullets:
        return True
    return False


def _ensure_rationale(rec: Recommendation) -> Recommendation:
    if rec.rationale.strip():
        return rec
    fallback = (
        f"This recommendation is based on your synced accounts and recent activity "
        f"({rec.summary or rec.title})."
    )
    return Recommendation(
        id=rec.id,
        title=rec.title,
        summary=rec.summary,
        rationale=fallback,
        bullets=list(rec.bullets),
        confidence=rec.confidence,
        action_label=rec.action_label,
        action_url=rec.action_url,
        recommendation_type=rec.recommendation_type,
        score=rec.score,
        urgency=rec.urgency,
    )


def _program_key_from_recommendation(rec: Recommendation) -> str | None:
    rec_id = (rec.id or "").lower()
    for email_id, program in _EMAIL_PROGRAM_KEYS.items():
        if email_id in rec_id or program in rec_id:
            return program
    title_lc = rec.title.lower()
    for program in ("marriott", "bonvoy", "hyatt", "hilton", "united", "delta", "southwest", "chase", "amex"):
        if program in title_lc:
            return "marriott" if program == "bonvoy" else program
    return None


def _dedupe_and_rank(recommendations: list[Recommendation]) -> list[Recommendation]:
    """Remove duplicates, drop generic advice, and rank by score."""
    cleaned = [_ensure_rationale(rec) for rec in recommendations if not _is_generic(rec)]
    ranked = sorted(cleaned, key=lambda rec: (-rec.score, rec.title))
    seen_ids: set[str] = set()
    benefit_programs: set[str] = set()
    deduped: list[Recommendation] = []

    for rec in ranked:
        if rec.id and rec.id.startswith("benefit_"):
            program = _program_key_from_recommendation(rec)
            if program:
                benefit_programs.add(program)

    seen_titles: set[str] = set()
    for rec in ranked:
        if rec.id and rec.id in seen_ids:
            continue
        program = _program_key_from_recommendation(rec)
        if rec.id and rec.id.startswith("email_") and program in benefit_programs:
            continue
        title_key = rec.title.strip().lower()
        if title_key in seen_titles:
            continue
        if rec.id:
            seen_ids.add(rec.id)
        seen_titles.add(title_key)
        deduped.append(rec)

    return deduped


def _collect_live_advisor_recommendations(
    context: DecisionContext,
    user_memory: dict[str, Any] | None = None,
) -> list[Recommendation]:
    """Collect live recommendations from benefit and email advisors."""
    from mighty.advisors.benefit_advisor import evaluate as evaluate_benefits
    from mighty.advisors.email_advisor import evaluate as evaluate_email
    from mighty.advisors.cross_account import synthesize_cross_account

    benefit_recs = _opportunities_to_recommendations(
        evaluate_benefits(context, user_memory)
    )
    email_recs = _opportunities_to_recommendations(
        evaluate_email(context, user_memory)
    )
    combined = _dedupe_and_rank(benefit_recs + email_recs)
    return synthesize_cross_account(combined, user_memory)


def detect_situation(context: DecisionContext) -> Situation:
    return Situation(kind="unknown", confidence="low", evidence=[])


def get_recommendations(
    context: DecisionContext,
    user_memory: dict[str, Any] | None = None,
) -> list[Recommendation]:
    if context.source == "dashboard":
        user_memory = user_memory or {}
        live = _collect_live_advisor_recommendations(context, user_memory)
        if live or user_memory.get("suppress_demo_content"):
            return _dedupe_and_rank(live)
        return _dedupe_and_rank(_dashboard_demo_recommendations())

    detect_situation(context)
    from mighty.advisors.hotel import evaluate as evaluate_hotel

    opportunities = evaluate_hotel(context, user_memory)
    return _dedupe_and_rank(_opportunities_to_recommendations(opportunities))
