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

DEFAULT_MAX_RECOMMENDATIONS = 5


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

_DEMO_RELATED_PROGRAMS: dict[str, frozenset[str]] = {
    "demo_amex_hotel": frozenset({"amex"}),
    "demo_chase_hyatt": frozenset({"chase", "hyatt"}),
    "demo_southwest_companion": frozenset({"southwest"}),
}


def _dashboard_demo_recommendations() -> list[Recommendation]:
    """Deterministic fallback recommendations when the email advisor has no matches."""
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


def _resolve_max_recommendations(user_memory: dict[str, Any] | None) -> int:
    raw = (user_memory or {}).get("max_recommendations", DEFAULT_MAX_RECOMMENDATIONS)
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_RECOMMENDATIONS
    return max(1, limit)


def _program_key_from_recommendation(rec: Recommendation) -> str | None:
    rec_id = (rec.id or "").lower()
    for email_id, program in _EMAIL_PROGRAM_KEYS.items():
        if email_id in rec_id or program in rec_id:
            return program
    title_lc = rec.title.lower()
    for program in ("marriott", "bonvoy", "hyatt", "hilton", "united", "delta", "southwest"):
        if program in title_lc:
            return "marriott" if program == "bonvoy" else program
    return None


def _opportunity_slot(rec: Recommendation) -> tuple[str, str] | None:
    """Stable (program, kind) key — same slot = same underlying opportunity."""
    rec_id = (rec.id or "").lower()
    if rec_id.startswith("benefit_points_"):
        return (rec_id.removeprefix("benefit_points_"), "points")
    if rec_id.startswith("benefit_status_"):
        return (rec_id.removeprefix("benefit_status_"), "status")
    if rec_id.startswith("benefit_cert_"):
        program = _program_key_from_recommendation(rec) or rec_id.removeprefix("benefit_cert_")
        return (program, "certificate")
    if rec_id in _EMAIL_PROGRAM_KEYS:
        return (_EMAIL_PROGRAM_KEYS[rec_id], "email")
    return None


def _dedupe_and_rank(
    recommendations: list[Recommendation],
    user_memory: dict[str, Any] | None = None,
) -> list[Recommendation]:
    """Remove duplicate opportunities, rank by score, and cap output."""
    ranked = sorted(recommendations, key=lambda rec: (-rec.score, rec.title))

    benefit_programs: set[str] = set()
    for rec in ranked:
        if rec.id and rec.id.startswith("benefit_"):
            program = _program_key_from_recommendation(rec)
            if program:
                benefit_programs.add(program)

    seen_ids: set[str] = set()
    seen_slots: set[tuple[str, str]] = set()
    seen_titles: set[str] = set()
    deduped: list[Recommendation] = []

    for rec in ranked:
        if rec.id and rec.id in seen_ids:
            continue

        program = _program_key_from_recommendation(rec)
        if rec.id and rec.id.startswith("email_") and program in benefit_programs:
            continue

        if rec.id and rec.id.startswith("demo_"):
            related = _DEMO_RELATED_PROGRAMS.get(rec.id, frozenset())
            if related & benefit_programs:
                continue

        slot = _opportunity_slot(rec)
        if slot is not None:
            if slot in seen_slots:
                continue
            seen_slots.add(slot)

        title_key = rec.title.strip().lower()
        if title_key in seen_titles:
            continue

        if rec.id:
            seen_ids.add(rec.id)
        seen_titles.add(title_key)
        deduped.append(rec)

    return deduped[: _resolve_max_recommendations(user_memory)]


def _collect_live_advisor_recommendations(
    context: DecisionContext,
    user_memory: dict[str, Any] | None = None,
) -> list[Recommendation]:
    """Collect live recommendations from benefit and email advisors."""
    from mighty.advisors.benefit_advisor import evaluate as evaluate_benefits
    from mighty.advisors.email_advisor import evaluate as evaluate_email

    benefit_recs = _opportunities_to_recommendations(
        evaluate_benefits(context, user_memory)
    )
    email_recs = _opportunities_to_recommendations(
        evaluate_email(context, user_memory)
    )
    return _dedupe_and_rank(benefit_recs + email_recs, user_memory)


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
        return _dedupe_and_rank(recommendations, user_memory)

    detect_situation(context)
    from mighty.advisors.hotel import evaluate as evaluate_hotel

    opportunities = evaluate_hotel(context, user_memory)
    return _dedupe_and_rank(
        _opportunities_to_recommendations(opportunities),
        user_memory,
    )
