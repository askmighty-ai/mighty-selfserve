"""
mighty.advisors.cross_account
─────────────────────────────
Combine signals across synced accounts, card programs, and email context
into unified recommendations. Pure functions only.
"""

from __future__ import annotations

import re
from typing import Any

from mighty.advisors.base import Opportunity
from mighty.decision_engine import DecisionContext, Recommendation
from mighty.scoring import score_opportunity, urgency_from_score

_POINTS_PATTERN = re.compile(r"([\d,]+)\s*(?:points|miles|bonvoy|skymiles)?", re.I)

_TRANSFER_PARTNERS: dict[str, tuple[str, ...]] = {
    "chase": ("hyatt", "marriott", "united", "southwest"),
    "amex": ("delta", "hilton", "marriott"),
}

_PROGRAM_ALIASES: dict[str, str] = {
    "bonvoy": "marriott",
    "world of hyatt": "hyatt",
    "ultimate rewards": "chase",
    "rapid rewards": "southwest",
    "skymiles": "delta",
    "mileageplus": "united",
    "hilton honors": "hilton",
}


def _normalize_program(text: str) -> str | None:
    combined = text.lower().replace("_", " ")
    for alias, key in sorted(_PROGRAM_ALIASES.items(), key=len, reverse=True):
        if alias in combined:
            return key
    for key in ("marriott", "hyatt", "hilton", "united", "delta", "southwest", "chase", "amex"):
        if key in combined:
            return key
    return None


def _benefits(user_memory: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = (user_memory or {}).get("available_benefits")
    if not isinstance(raw, list):
        return []
    return [b for b in raw if isinstance(b, dict)]


def _programs_from_benefits(benefits: list[dict[str, Any]]) -> set[str]:
    programs: set[str] = set()
    for benefit in benefits:
        label = str(benefit.get("label") or "")
        source = str(benefit.get("source") or "")
        program = _normalize_program(f"{source} {label}")
        if program:
            programs.add(program)
    return programs


def _parse_points(value: str) -> int | None:
    match = _POINTS_PATTERN.search(str(value or "").replace(",", ""))
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _has_hotel_intent(user_memory: dict[str, Any] | None) -> bool:
    intent = (user_memory or {}).get("intent") or {}
    if isinstance(intent, dict) and intent.get("hotel", 0) > 0:
        return True
    subjects = (user_memory or {}).get("email_subjects") or []
    if isinstance(subjects, list):
        for subject in subjects:
            subject_lc = str(subject).lower()
            if any(k in subject_lc for k in ("hotel", "stay", "check-in", "bonvoy", "hyatt", "marriott", "hilton")):
                return True
    return False


def _has_flight_intent(user_memory: dict[str, Any] | None) -> bool:
    intent = (user_memory or {}).get("intent") or {}
    if isinstance(intent, dict) and intent.get("flight", 0) > 0:
        return True
    subjects = (user_memory or {}).get("email_subjects") or []
    if isinstance(subjects, list):
        for subject in subjects:
            subject_lc = str(subject).lower()
            if any(k in subject_lc for k in ("flight", "trip to", "boarding", "mileageplus", "skymiles")):
                return True
    return False


def _email_promo_for_program(user_memory: dict[str, Any] | None, program: str) -> str | None:
    subjects = (user_memory or {}).get("email_subjects") or []
    if not isinstance(subjects, list):
        return None
    for subject in subjects:
        subject_lc = str(subject).lower()
        if _normalize_program(subject_lc) != program:
            continue
        if any(sig in subject_lc for sig in ("2x", "3x", "bonus", "offer", "promo", "promotion", "sale", "limited")):
            return str(subject).strip()
    return None


def _rec_key(rec: Recommendation) -> str:
    return (rec.id or rec.title).lower()


def _already_covers(recommendations: list[Recommendation], *needles: str) -> bool:
    for rec in recommendations:
        blob = f"{rec.id} {rec.title} {rec.summary}".lower()
        if all(needle.lower() in blob for needle in needles):
            return True
    return False


def _score_cross(
    *,
    label: str,
    value: str,
    btype: str,
    source: str,
    user_memory: dict[str, Any] | None,
) -> int:
    intent = (user_memory or {}).get("intent") or {}
    affinity = (user_memory or {}).get("type_affinity") or {}
    return score_opportunity(
        {"label": label, "value": value, "btype": btype, "days_left": None},
        user_intent=intent if isinstance(intent, dict) else {},
        source=source,
        user_type_affinity=affinity if isinstance(affinity, dict) else {},
    )


def synthesize_cross_account(
    recommendations: list[Recommendation],
    user_memory: dict[str, Any] | None,
) -> list[Recommendation]:
    """Merge account signals and enrich existing recommendations with cross-program context."""
    benefits = _benefits(user_memory)
    programs = _programs_from_benefits(benefits)
    if not programs and not recommendations:
        return recommendations

    enriched: list[Recommendation] = []
    existing_keys = {_rec_key(r) for r in recommendations}

    for rec in recommendations:
        program = _normalize_program(rec.id or rec.title)
        promo_subject = _email_promo_for_program(user_memory, program) if program else None
        if promo_subject and promo_subject not in rec.rationale:
            rationale = (
                f"{rec.rationale} A recent email (\"{promo_subject[:80]}\") "
                "highlights a limited-time offer — acting now stacks extra value."
            )
            enriched.append(
                Recommendation(
                    id=rec.id,
                    title=rec.title,
                    summary=rec.summary,
                    rationale=rationale,
                    bullets=list(rec.bullets),
                    confidence=rec.confidence,
                    action_label=rec.action_label,
                    action_url=rec.action_url,
                    recommendation_type=rec.recommendation_type,
                    score=min(rec.score + 10, 100),
                    urgency=urgency_from_score(min(rec.score + 10, 100)),
                )
            )
        else:
            enriched.append(rec)

    # Chase UR + Hyatt: classic 1:1 transfer when both accounts are present
    if (
        "chase" in programs
        and "hyatt" in programs
        and _has_hotel_intent(user_memory)
        and not _already_covers(enriched, "transfer", "hyatt")
    ):
        chase_balance = next(
            (
                _parse_points(str(b.get("value") or ""))
                for b in benefits
                if _normalize_program(f"{b.get('source')} {b.get('label')}") == "chase"
                and str(b.get("btype") or "") == "points_balance"
            ),
            None,
        )
        balance_phrase = f"{chase_balance:,} Ultimate Rewards points" if chase_balance else "Chase Ultimate Rewards"
        score = _score_cross(
            label="Ultimate Rewards Transfer",
            value=str(chase_balance or 0),
            btype="points_balance",
            source="Chase",
            user_memory=user_memory,
        ) + 15
        score = min(score, 100)
        enriched.append(
            Recommendation(
                id="cross_chase_hyatt_transfer",
                title="Transfer Chase points to Hyatt for your hotel booking",
                summary=f"You have {balance_phrase} and a synced Hyatt account — 1:1 transfers often beat cash rates.",
                rationale=(
                    "Chase Ultimate Rewards transfers to World of Hyatt at 1:1, and your synced accounts "
                    "show balances in both programs. Hyatt awards frequently deliver strong cents-per-point "
                    "value at premium properties compared with paying cash or transferring elsewhere."
                ),
                bullets=[
                    "1:1 transfer from Chase Ultimate Rewards",
                    "Hyatt redemptions often beat Marriott and Hilton on premium stays",
                    "Stack with any active Hyatt promo from your recent email",
                ],
                confidence="high" if score >= 65 else "medium",
                action_label="Transfer to Hyatt",
                action_url="https://www.chase.com/",
                recommendation_type="hotel",
                score=score,
                urgency=urgency_from_score(score),
            )
        )
        existing_keys.add("cross_chase_hyatt_transfer")

    # Amex FHR + hotel intent when Amex card benefits are synced
    amex_hotel_benefits = [
        b for b in benefits
        if _normalize_program(str(b.get("source") or "")) == "amex"
        or any(
            marker in f"{b.get('label', '')} {b.get('value', '')}".lower()
            for marker in ("fine hotels", "fhr", "hotel collection", "platinum travel")
        )
    ]
    if (
        amex_hotel_benefits
        and _has_hotel_intent(user_memory)
        and not _already_covers(enriched, "amex travel")
    ):
        score = _score_cross(
            label="Fine Hotels + Resorts",
            value="eligible",
            btype="credit",
            source="Amex",
            user_memory=user_memory,
        ) + 10
        score = min(score, 100)
        enriched.append(
            Recommendation(
                id="cross_amex_fhr_hotel",
                title="Book your hotel through Amex Travel for FHR perks",
                summary="Your synced Amex benefits include hotel program perks — FHR unlocks breakfast, credits, and upgrades.",
                rationale=(
                    "Your connected Amex account shows eligible hotel benefits, and your recent browsing "
                    "or email activity suggests an upcoming hotel stay. Fine Hotels & Resorts bookings "
                    "include property credits, daily breakfast for two, room upgrades when available, "
                    "and guaranteed 4pm late checkout."
                ),
                bullets=[
                    "Book via Amex Travel, not the hotel site directly",
                    "Fine Hotels & Resorts or The Hotel Collection depending on card",
                    "Perks apply at checkout — no post-stay claims needed",
                ],
                confidence="high" if score >= 65 else "medium",
                action_label="Open Amex Travel",
                action_url="https://www.americanexpress.com/travel/",
                recommendation_type="hotel",
                score=score,
                urgency=urgency_from_score(score),
            )
        )

    # Chase + airline when flight intent and transfer partner overlap
    if "chase" in programs and _has_flight_intent(user_memory):
        for airline in ("united", "southwest"):
            if airline not in programs:
                continue
            if airline not in _TRANSFER_PARTNERS.get("chase", ()):
                continue
            cross_id = f"cross_chase_{airline}_transfer"
            if cross_id in existing_keys or _already_covers(enriched, "transfer", airline):
                continue
            display = {"united": "United", "southwest": "Southwest"}[airline]
            score = _score_cross(
                label=f"Transfer to {display}",
                value="1:1",
                btype="points_balance",
                source="Chase",
                user_memory=user_memory,
            ) + 10
            score = min(score, 100)
            enriched.append(
                Recommendation(
                    id=cross_id,
                    title=f"Transfer Chase points to {display} for your upcoming flight",
                    summary=f"Chase transfers 1:1 to {display} — your synced accounts make this a direct path to award space.",
                    rationale=(
                        f"You have synced Chase and {display} accounts, and recent activity points to "
                        f"flight booking. Transferring Ultimate Rewards to {display} lets you search "
                        "award availability immediately instead of paying cash."
                    ),
                    bullets=[
                        f"1:1 transfer from Chase to {display}",
                        "Search saver award space before transferring",
                        "Transfers are usually instant and irreversible",
                    ],
                    confidence="medium",
                    action_label=f"Transfer to {display}",
                    action_url="https://www.chase.com/",
                    recommendation_type="travel",
                    score=score,
                    urgency=urgency_from_score(score),
                )
            )

    return enriched


def evaluate(
    context: DecisionContext,
    user_memory: dict[str, Any] | None = None,
) -> list[Opportunity]:
    """Advisor hook — cross-account synthesis runs in decision_engine post-processing."""
    return []
