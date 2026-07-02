"""
mighty.advisors.benefit_advisor
───────────────────────────────
Benefit-driven contextual recommendations from synced account data.

Turns raw points balances, elite status, progress metrics, and certificates
into specific, actionable recommendations with rationale, urgency, and
confidence scores. Pure functions only — no database, AI, or network calls.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from mighty.advisors.base import Opportunity
from mighty.decision_engine import DecisionContext
from mighty.scoring import score_opportunity

logger = logging.getLogger(__name__)

_FREE_NIGHT_POINTS: dict[str, int] = {
    "marriott": 35_000,
    "bonvoy": 35_000,
    "hyatt": 25_000,
    "hilton": 30_000,
    "ihg": 30_000,
    "wyndham": 15_000,
}

_ROUND_TRIP_MILES: dict[str, int] = {
    "united": 25_000,
    "delta": 25_000,
    "american": 25_000,
    "southwest": 15_000,
    "alaska": 25_000,
    "jetblue": 20_000,
}

_PROGRAM_META: dict[str, dict[str, str]] = {
    "marriott": {
        "display": "Marriott Bonvoy",
        "category": "hotel",
        "action_label": "Book with Bonvoy points",
        "action_url": "https://www.marriott.com/",
    },
    "bonvoy": {
        "display": "Marriott Bonvoy",
        "category": "hotel",
        "action_label": "Book with Bonvoy points",
        "action_url": "https://www.marriott.com/",
    },
    "hyatt": {
        "display": "World of Hyatt",
        "category": "hotel",
        "action_label": "Book with Hyatt points",
        "action_url": "https://www.hyatt.com/",
    },
    "hilton": {
        "display": "Hilton Honors",
        "category": "hotel",
        "action_label": "Book with Hilton points",
        "action_url": "https://www.hilton.com/",
    },
    "united": {
        "display": "United MileagePlus",
        "category": "travel",
        "action_label": "Book on United",
        "action_url": "https://www.united.com/",
    },
    "delta": {
        "display": "Delta SkyMiles",
        "category": "travel",
        "action_label": "Book on Delta",
        "action_url": "https://www.delta.com/",
    },
    "southwest": {
        "display": "Southwest Rapid Rewards",
        "category": "travel",
        "action_label": "Book on Southwest",
        "action_url": "https://www.southwest.com/",
    },
}

_DEFAULT_CITIES: dict[str, str] = {
    "hotel": "Boston",
    "flight": "Chicago",
    "car": "Denver",
}

_CITY_PATTERN = re.compile(
    r"\b(?:to|in|for|visit(?:ing)?)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b"
)
_POINTS_PATTERN = re.compile(r"([\d,]+)\s*(?:points|miles|bonvoy|skymiles)?", re.I)
_PROGRESS_PATTERN = re.compile(r"([\d,]+)\s*(?:of|/)\s*([\d,]+)")
_TIER_PATTERN = re.compile(
    r"\b(platinum|gold|silver|diamond|titanium|ambassador|globalist|"
    r"explorist|discoverist|1k|premier|executive platinum|"
    r"platinum pro|mvp|a-list|companion pass)\b",
    re.I,
)
_SEGMENT_KEYWORDS = ("segment", "flight", "trip", "leg", "pqp", "pqm", "eqm", "eqs")


def _normalize_benefits(user_memory: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = (user_memory or {}).get("available_benefits")
    if not isinstance(raw, list):
        return []
    benefits: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        value = str(item.get("value") or "").strip()
        source = str(item.get("source") or "").strip()
        if not label or not value or value in {"0", "—", "-", "N/A", "None", "TBD"}:
            continue
        benefits.append(
            {
                "label": label,
                "value": value,
                "source": source,
                "btype": str(item.get("btype") or "").strip().lower(),
                "days_left": item.get("days_left"),
            }
        )
    return benefits


def _program_key(source: str, label: str = "") -> str | None:
    combined = f"{source} {label}".lower().replace("_", " ")
    for key in sorted(_PROGRAM_META, key=len, reverse=True):
        if key in combined:
            return key
    return None


def _parse_points(value: str) -> int | None:
    match = _POINTS_PATTERN.search(value.replace(",", ""))
    if not match:
        digits = re.sub(r"[^\d]", "", value)
        if digits and len(digits) >= 3:
            return int(digits)
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_progress(value: str) -> tuple[int, int] | None:
    match = _PROGRESS_PATTERN.search(value.replace(",", ""))
    if not match:
        return None
    try:
        current = int(match.group(1).replace(",", ""))
        target = int(match.group(2).replace(",", ""))
    except ValueError:
        return None
    if target <= 0 or current < 0 or current > target:
        return None
    return current, target


def _extract_tier(text: str) -> str:
    match = _TIER_PATTERN.search(text)
    if not match:
        return ""
    tier = match.group(1).strip()
    if tier.lower() == "1k":
        return "Global Services"
    return tier.title()


def _infer_destination(user_memory: dict[str, Any] | None) -> str | None:
    if not user_memory:
        return None

    page_url = str(user_memory.get("recent_page_url") or "")
    city_match = _CITY_PATTERN.search(page_url.replace("-", " ").replace("/", " "))
    if city_match:
        return city_match.group(1).strip()

    subjects = user_memory.get("email_subjects") or []
    if isinstance(subjects, list):
        for subject in subjects:
            city_match = _CITY_PATTERN.search(str(subject))
            if city_match:
                return city_match.group(1).strip()

    reservations = user_memory.get("available_benefits") or []
    if isinstance(reservations, list):
        for item in reservations:
            if not isinstance(item, dict):
                continue
            if str(item.get("btype") or "").lower() != "reservation":
                continue
            combined = f"{item.get('label', '')} {item.get('value', '')}"
            city_match = _CITY_PATTERN.search(combined)
            if city_match:
                return city_match.group(1).strip()

    intent = user_memory.get("intent") or {}
    if isinstance(intent, dict):
        if intent.get("hotel", 0) > 0:
            return _DEFAULT_CITIES["hotel"]
        if intent.get("flight", 0) > 0:
            return _DEFAULT_CITIES["flight"]

    return None


def _confidence_from_score(score: int, data_quality: str = "synced") -> str:
    if data_quality == "inferred":
        return "low"
    if score >= 65:
        return "high"
    if score >= 35:
        return "medium"
    return "low"


def _score_opportunity(
    *,
    label: str,
    value: str,
    btype: str,
    source: str,
    days_left: int | None,
    user_memory: dict[str, Any] | None,
) -> int:
    intent = (user_memory or {}).get("intent") or {}
    affinity = (user_memory or {}).get("type_affinity") or {}
    return score_opportunity(
        {"label": label, "value": value, "btype": btype, "days_left": days_left},
        user_intent=intent if isinstance(intent, dict) else {},
        source=source,
        user_type_affinity=affinity if isinstance(affinity, dict) else {},
    )


def _recommend_points(
    benefit: dict[str, Any],
    destination: str | None,
    user_memory: dict[str, Any] | None,
) -> Opportunity | None:
    source = benefit["source"]
    label = benefit["label"]
    value = benefit["value"]
    program = _program_key(source, label)
    if not program:
        return None

    points = _parse_points(value)
    if points is None or points <= 0:
        return None

    meta = _PROGRAM_META[program]
    free_night_threshold = _FREE_NIGHT_POINTS.get(program)
    round_trip_threshold = _ROUND_TRIP_MILES.get(program)

    if free_night_threshold and points >= free_night_threshold:
        city = destination or _DEFAULT_CITIES["hotel"]
        title = f"You have enough {meta['display']} points for a free night in {city}."
        summary = f"{points:,} points covers a standard award night at many {meta['display']} properties."
        rationale = (
            f"Your synced balance meets the typical {free_night_threshold:,}-point threshold "
            f"for a free night — booking now locks in value before points devalue or expire."
        )
        bullets = [
            f"Balance: {points:,} points",
            f"Typical free-night cost: {free_night_threshold:,} points",
            f"Strong fit for a stay in {city}" if destination else "Search award availability at your destination",
        ]
        category = meta["category"]
    elif round_trip_threshold and points >= round_trip_threshold:
        title = f"You have enough {meta['display']} miles for a round-trip flight."
        summary = f"{points:,} miles can cover a domestic round trip on {meta['display']}."
        rationale = (
            "Your mile balance is high enough to book travel outright — "
            "redeeming now avoids paying cash and protects against award price increases."
        )
        bullets = [
            f"Balance: {points:,} miles",
            f"Typical domestic round trip: {round_trip_threshold:,} miles",
            "Check saver award space before prices rise",
        ]
        category = meta["category"]
    else:
        remaining = (free_night_threshold or round_trip_threshold or 0) - points
        if remaining <= 0 or remaining > (free_night_threshold or round_trip_threshold or 1) * 0.5:
            return None
        unit = "points" if free_night_threshold else "miles"
        title = f"You're {remaining:,} {unit} away from a free redemption on {meta['display']}."
        summary = f"Current balance: {points:,} {unit}."
        rationale = (
            "You're close to a meaningful redemption threshold — "
            "one more stay or transfer could unlock a free night or flight."
        )
        bullets = [
            f"Current balance: {points:,} {unit}",
            f"Target: {(free_night_threshold or round_trip_threshold):,} {unit}",
        ]
        category = meta["category"]

    score = _score_opportunity(
        label=label,
        value=value,
        btype="points_balance",
        source=source,
        days_left=benefit.get("days_left"),
        user_memory=user_memory,
    )
    return Opportunity(
        id=f"benefit_points_{program}",
        title=title,
        summary=summary,
        category=category,
        confidence=_confidence_from_score(score),
        rationale=rationale,
        bullets=bullets,
        action_label=meta["action_label"],
        action_url=meta["action_url"],
        score=score,
    )


def _status_tier_for_program(
    benefits: list[dict[str, Any]],
    program: str,
) -> str:
    for benefit in benefits:
        if benefit["btype"] != "elite_status":
            continue
        if _program_key(benefit["source"], benefit["label"]) != program:
            continue
        tier = _extract_tier(benefit["value"]) or benefit["value"].strip()
        if tier and len(tier.split()) <= 4:
            return tier
    return ""


def _is_segment_progress(label: str) -> bool:
    label_lc = label.lower()
    return any(keyword in label_lc for keyword in _SEGMENT_KEYWORDS)


def _recommend_status_progress(
    benefit: dict[str, Any],
    all_benefits: list[dict[str, Any]],
    user_memory: dict[str, Any] | None,
) -> Opportunity | None:
    if benefit["btype"] != "progress_toward":
        return None

    progress = _parse_progress(benefit["value"])
    if not progress:
        return None

    current, target = progress
    remaining = target - current
    near_goal = remaining <= max(1, int(target * 0.2))
    if not near_goal:
        return None

    program = _program_key(benefit["source"], benefit["label"])
    if not program:
        return None

    meta = _PROGRAM_META.get(program)
    if not meta:
        return None

    tier = _status_tier_for_program(all_benefits, program)
    program_display = meta["display"]
    short_program = program_display.split()[0]

    if _is_segment_progress(benefit["label"]) or remaining == 1:
        action_phrase = "One more round trip" if remaining == 1 else f"{remaining} more round trips"
        if tier:
            title = f"{action_phrase} keeps your {short_program} {tier} status."
        else:
            title = f"{action_phrase} keeps your {program_display} status."
    else:
        if tier:
            title = (
                f"{remaining:,} more qualifying units keep your "
                f"{short_program} {tier} status."
            )
        else:
            title = (
                f"{remaining:,} more qualifying units keep your "
                f"{program_display} status."
            )

    tier_phrase = f" {tier}" if tier else ""
    summary = f"Progress: {current:,} of {target:,} ({benefit['label']})."
    rationale = (
        f"You're {remaining:,} away from retaining{tier_phrase} status — "
        "missing the threshold means losing lounge access, upgrades, and partner perks for the next year."
    )
    bullets = [
        f"{current:,} of {target:,} completed",
        f"{remaining:,} remaining before the qualification period ends",
        "Book qualifying travel soon to secure status",
    ]

    days_left = benefit.get("days_left")
    score = _score_opportunity(
        label=benefit["label"],
        value=benefit["value"],
        btype="progress_toward",
        source=benefit["source"],
        days_left=days_left,
        user_memory=user_memory,
    )
    if days_left is not None and days_left <= 60:
        score = min(score + 15, 100)

    return Opportunity(
        id=f"benefit_status_{program}",
        title=title,
        summary=summary,
        category=meta["category"],
        confidence=_confidence_from_score(score),
        rationale=rationale,
        bullets=bullets,
        action_label=meta["action_label"],
        action_url=meta["action_url"],
        score=score,
    )


def _recommend_certificate(
    benefit: dict[str, Any],
    destination: str | None,
    user_memory: dict[str, Any] | None,
) -> Opportunity | None:
    if benefit["btype"] != "certificate":
        return None

    program = _program_key(benefit["source"], benefit["label"])
    meta = _PROGRAM_META.get(program or "", {})
    display = meta.get("display") or benefit["source"].replace("_", " ").title()
    category = meta.get("category", "hotel")

    label_lc = benefit["label"].lower()
    if "free night" in label_lc or "award night" in label_lc:
        cert_kind = "free night"
    elif "companion" in label_lc:
        cert_kind = "companion certificate"
    elif "upgrade" in label_lc:
        cert_kind = "upgrade certificate"
    else:
        cert_kind = "certificate"

    days_left = benefit.get("days_left")
    if isinstance(days_left, int) and days_left >= 0:
        if days_left == 0:
            expiry_phrase = "expires today"
        elif days_left == 1:
            expiry_phrase = "expires tomorrow"
        else:
            expiry_phrase = f"expires in {days_left} days"
        title = f"Use your {display} {cert_kind} before it {expiry_phrase}."
    elif destination:
        title = f"Use your {display} {cert_kind} on your trip to {destination}."
    else:
        title = f"Use your {display} {cert_kind} before it expires."

    summary = benefit["value"]
    rationale = (
        f"Unused {cert_kind}s typically expire and cannot be recovered — "
        "redeeming now preserves hundreds of dollars in value."
    )
    if destination:
        rationale += f" Your recent activity suggests {destination} is a timely destination."

    bullets = [
        f"Certificate: {benefit['label']}",
        f"Value: {benefit['value']}",
    ]
    if days_left is not None and days_left >= 0:
        bullets.append(f"Time left: {days_left} days")

    score = _score_opportunity(
        label=benefit["label"],
        value=benefit["value"],
        btype="certificate",
        source=benefit["source"],
        days_left=days_left if isinstance(days_left, int) else None,
        user_memory=user_memory,
    )

    return Opportunity(
        id=f"benefit_cert_{program or benefit['source']}",
        title=title,
        summary=summary,
        category=category,
        confidence=_confidence_from_score(score),
        rationale=rationale,
        bullets=bullets,
        action_label=meta.get("action_label", "View certificate"),
        action_url=meta.get("action_url", ""),
        score=score,
    )


def evaluate(
    context: DecisionContext,
    user_memory: dict[str, Any] | None = None,
) -> list[Opportunity]:
    """Generate benefit-driven recommendations from synced account data."""
    if context.source not in ("dashboard", "browser", "email"):
        return []

    benefits = _normalize_benefits(user_memory)
    if not benefits:
        logger.info("[benefit_advisor] no available_benefits in user_memory")
        return []

    destination = _infer_destination(user_memory)
    opportunities: list[Opportunity] = []
    seen_ids: set[str] = set()

    for benefit in benefits:
        candidates: list[Opportunity | None] = []

        if benefit["btype"] == "points_balance":
            candidates.append(_recommend_points(benefit, destination, user_memory))
        elif benefit["btype"] == "progress_toward":
            candidates.append(_recommend_status_progress(benefit, benefits, user_memory))
        elif benefit["btype"] == "certificate":
            candidates.append(_recommend_certificate(benefit, destination, user_memory))

        for opp in candidates:
            if opp is None or opp.id in seen_ids:
                continue
            seen_ids.add(opp.id)
            opportunities.append(opp)

    opportunities.sort(key=lambda o: (-o.score, o.title))
    if opportunities:
        logger.info(
            "[benefit_advisor] returning %d recommendation(s): ids=%s",
            len(opportunities),
            [o.id for o in opportunities],
        )
    return opportunities
