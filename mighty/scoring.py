"""
mighty.scoring
──────────────
Relevance scoring and confidence calibration for discovered benefits.

score_opportunity()   — unified 0-100 score for action center + surface ranking.
                        Four components: expiration, value, intent, rarity.
urgency_from_score()  — maps score → 'urgent' | 'soon' | 'info'.
_relevance_score()    — legacy composite sort key (used by /api/opportunities).
_confidence_label()   — human-readable label for a 0–1 confidence score.

score_opportunity design
------------------------
    score = expiration_weight(days_left)     0–40
          + value_weight(literal_amount)     0–30
          + intent_weight(domain_match)      0–20
          + rarity_weight(btype)             0–10

urgency tiers from score:
    ≥ 65  → urgent   (strong expiry pressure OR very high value + intent)
    ≥ 38  → soon
    <  38 → info

Action center entry threshold: score ≥ 30
  A $500+ credit with no expiry scores ≈ 37-57 (value 30 + rarity 7 + intent 0-20)
  and enters at "info" or "soon" — which is correct.
  A $50 credit expiring in 45 days scores ≈ 37 (exp 12 + value 8 + rarity 7 + intent 0-10).

Usage
-----
    from mighty.scoring import score_opportunity, urgency_from_score

    item = {"label": "Free Night Award", "value": "Expires Aug 2026",
            "btype": "certificate", "days_left": 73}
    ctx  = {"flight": 2, "hotel": 5}   # from users.intent_summary
    sc   = score_opportunity(item, user_intent=ctx, source="hyatt")
    urg  = urgency_from_score(sc)
"""

import re as _re
import datetime as _dt


# ── Domain mapping ────────────────────────────────────────────────────────────
# Maps a source key or display name fragment → intent domain category.
# Intent domain categories align with intent_history.intent_type values.

_SOURCE_DOMAIN: dict[str, str] = {
    # Airlines
    "delta": "flight", "american": "flight", "united": "flight",
    "southwest": "flight", "alaska": "flight", "jetblue": "flight",
    "spirit": "flight", "frontier": "flight", "hawaiian": "flight",
    # Hotels
    "hyatt": "hotel", "marriott": "hotel", "hilton": "hotel",
    "ihg": "hotel", "wyndham": "hotel", "choice": "hotel",
    "radisson": "hotel", "best western": "hotel", "accor": "hotel",
    # Rental cars
    "hertz": "car", "avis": "car", "enterprise": "car",
    "national": "car", "budget": "car", "alamo": "car", "thrifty": "car",
    # Retail / dining
    "amazon": "shopping", "walmart": "shopping", "target": "shopping",
    "doordash": "dining", "ubereats": "dining", "grubhub": "dining",
    # Credit cards (cross-domain)
    "amex": "credit_card", "chase": "credit_card", "citi": "credit_card",
    "capital one": "credit_card", "discover": "credit_card",
    "bank of america": "credit_card", "barclays": "credit_card",
}

# Adjacent domains that still get a partial intent boost
_ADJACENT_DOMAINS: dict[str, set[str]] = {
    "flight":      {"hotel", "car"},
    "hotel":       {"flight", "car"},
    "car":         {"flight", "hotel"},
    "credit_card": {"flight", "hotel", "car", "shopping", "dining"},
    "shopping":    {"dining"},
    "dining":      {"shopping"},
}

# Rarity scores by canonical benefit type (0–10)
_RARITY: dict[str, int] = {
    "certificate":     10,
    "partner_benefit":  9,
    "travel_credit":    7,
    "cash_credit":      6,
    "elite_status":     5,
    "membership":       4,
    "upcoming_event":   4,
    "points_balance":   3,
    "progress_toward":  2,
    "payment_due":      2,
    "renewal":          2,
    "reservation":      1,
    "expiry_date":      0,
    "other":            0,
}

# Dollar thresholds for value_weight (0–30)
_VALUE_BRACKETS = [
    (500,  30),
    (200,  22),
    (100,  16),
    (50,   10),
    (1,     5),
]

# Intrinsic value estimates for non-dollar items (treated as synthetic dollar values)
_INTRINSIC_DOLLARS: dict[str, int] = {
    "certificate":     300,   # free night / companion cert ≈ $250-500
    "travel_credit":   100,   # conservative floor; real value extracted if literal
    "partner_benefit": 150,
    "membership":       80,
    "elite_status":    200,   # status has significant redeemable value
}


def _source_domain(source: str) -> str | None:
    """Return the intent domain for a source key, or None."""
    s = source.lower().replace("_", " ")
    for fragment, domain in _SOURCE_DOMAIN.items():
        if fragment in s:
            return domain
    return None


def _extract_dollar(value: str) -> float | None:
    """Return the first literal dollar amount from a value string, or None."""
    m = _re.search(r'\$\s*([\d,]+(?:\.\d{1,2})?)', value)
    if m:
        try:
            return float(m.group(1).replace(',', ''))
        except ValueError:
            pass
    return None


def _expiration_weight(days_left: int | None) -> int:
    """Translate days_left → 0-40 expiration urgency points."""
    if days_left is None:
        return 0
    if days_left < 0:
        return 0    # already expired — don't surface
    if days_left == 0:
        return 40
    if days_left <= 7:
        return 35
    if days_left <= 14:
        return 28
    if days_left <= 30:
        return 20
    if days_left <= 60:
        return 12
    if days_left <= 120:
        return 6
    if days_left <= 365:
        return 2
    return 0


def _value_weight(item: dict) -> int:
    """Derive 0-30 value points from literal dollar amount or intrinsic type value."""
    btype = item.get("btype", "other")
    label = item.get("label", "")
    value = str(item.get("value", ""))

    # Prefer literal dollar over intrinsic estimate
    dollars = _extract_dollar(value) or _extract_dollar(label)
    if dollars is None:
        # For certificates/credits, look for count × intrinsic value
        count_m = _re.match(r'^(\d+)\s+', value.strip())
        count = int(count_m.group(1)) if count_m else 1
        intrinsic = _INTRINSIC_DOLLARS.get(btype, 0)
        dollars = count * intrinsic

    if dollars <= 0:
        return 0

    for threshold, points in _VALUE_BRACKETS:
        if dollars >= threshold:
            return points
    return 0


def _intent_weight(item: dict, user_intent: dict, source: str = "") -> int:
    """Return 0-20 intent match points based on user's recent browsing domain."""
    if not user_intent:
        return 0

    domain = _source_domain(source or item.get("source", ""))
    if domain is None:
        return 0

    direct = user_intent.get(domain, 0)
    if direct > 0:
        return 20

    adjacent = _ADJACENT_DOMAINS.get(domain, set())
    if any(user_intent.get(adj, 0) > 0 for adj in adjacent):
        return 10

    return 0


def _rarity_weight(btype: str) -> int:
    """Return 0-10 rarity points by benefit type."""
    return _RARITY.get(btype, 0)


def _affinity_weight(btype: str, user_type_affinity: dict) -> int:
    """Return 0-15 bonus points from the user's learned interaction pattern.

    Positive affinity (completed / marked useful) → up to 15 pts.
    Negative or zero → 0 (no boost, no penalty — low-affinity types just rank
    lower because they don't receive the bonus).

    Scale: raw ≥ 5 → 15pts, raw = 3 → 9pts, raw = 1 → 3pts.
    """
    if not user_type_affinity:
        return 0
    raw = user_type_affinity.get(btype, 0.0)
    if not isinstance(raw, (int, float)) or raw <= 0:
        return 0
    return min(int(raw * 3), 15)


def score_opportunity(
    item: dict,
    user_intent: dict | None = None,
    source: str = "",
    user_type_affinity: dict | None = None,
) -> int:
    """Return a 0-100 opportunity score for an item.

    item keys used: label, value, btype, days_left
    user_intent: dict from users.intent_summary, e.g. {"hotel": 3, "flight": 1}
    source: source key string (e.g. "delta", "hyatt_world_of_hyatt")
    user_type_affinity: dict from users.type_affinity (learned per-btype signal)

    Score components (max 100):
        expiration  0-40   time pressure
        value       0-30   literal dollar or intrinsic type value
        intent      0-20   domain match with recent browsing
        rarity      0-10   scarcity of benefit type
        affinity    0-15   learned from user's past interactions
    """
    days  = item.get("days_left")
    btype = item.get("btype", "other")

    exp   = _expiration_weight(days)
    val   = _value_weight(item)
    inten = _intent_weight(item, user_intent or {}, source)
    rar   = _rarity_weight(btype)
    aff   = _affinity_weight(btype, user_type_affinity or {})

    return min(exp + val + inten + rar + aff, 100)


def score_components(
    item: dict,
    user_intent: dict | None = None,
    source: str = "",
    user_type_affinity: dict | None = None,
) -> dict:
    """Return a breakdown dict — useful for debugging and the benefit drawer."""
    days  = item.get("days_left")
    btype = item.get("btype", "other")
    exp   = _expiration_weight(days)
    val   = _value_weight(item)
    inten = _intent_weight(item, user_intent or {}, source)
    rar   = _rarity_weight(btype)
    aff   = _affinity_weight(btype, user_type_affinity or {})
    total = min(exp + val + inten + rar + aff, 100)
    return {
        "total": total,
        "expiration": exp,
        "value": val,
        "intent": inten,
        "rarity": rar,
        "affinity": aff,
    }


def urgency_from_score(score: int) -> str:
    """Map a 0-100 opportunity score to an urgency tier string."""
    if score >= 65:
        return "urgent"
    if score >= 35:
        return "soon"
    return "info"


def urgency_for_attention(days_left: int | None) -> str:
    """Time-based urgency for payment_due / renewal types.

    These are fundamentally time-pressure items where expiry IS the urgency —
    use a simple tiered rule rather than the composite score.
    """
    if days_left is None:
        return "info"
    if days_left <= 3:
        return "urgent"
    if days_left <= 14:
        return "soon"
    return "info"


# ── Legacy API (kept for /api/opportunities compatibility) ────────────────────

_BENEFIT_APPLICABILITY: dict[str, list[str]] = {
    "flight":   ["companion", "ecredit", "miles", "flight_credit", "travel_credit", "upgrade", "certificate"],
    "hotel":    ["free_night", "award_night", "points", "hotel_credit", "travel_credit", "certificate"],
    "car":      ["rental", "insurance", "coverage"],
    "shopping": ["purchase_protection", "extended_warranty", "cash_back", "price_protection", "credit"],
    "dining":   ["dining_credit", "dining", "cash_back", "credit"],
}


def _relevance_score(
    field_key: str,
    field_label: str,
    field_value: str,
    confidence: float = 0.85,
    context: str | None = None,
    expiry_date_str: str | None = None,
) -> tuple[float, dict]:
    """
    Legacy composite relevance score for sorting benefits in /api/opportunities.
    Returns (score, factors) — internal only, never shown to users.
    """
    k = field_key.lower()
    l = field_label.lower()  # noqa: E741

    # value_factor
    raw = 0.0
    if "point" in k or "mile" in k:
        nums = _re.findall(r'[\d,]+', field_value)
        pts = max(
            (int(n.replace(',', '')) for n in nums if int(n.replace(',', '')) < 2_000_000),
            default=0,
        )
        raw = pts * 0.01
    elif "free_night" in k or "award_night" in k:
        raw = 200.0
    elif "certificate" in k or "cert" in k:
        raw = 300.0
    elif "credit" in k:
        nums = _re.findall(r'\d+(?:\.\d+)?', field_value)
        raw = float(nums[0]) if nums else 50.0
    else:
        raw = 10.0
    value_factor = min(raw / 300.0, 1.0)

    # intent_factor
    if context is None:
        intent_factor = 0.5
    elif context in _BENEFIT_APPLICABILITY:
        relevant_keys = _BENEFIT_APPLICABILITY[context]
        k_lower = field_key.lower()
        l_lower = field_label.lower()
        if any(rk in k_lower or rk in l_lower for rk in relevant_keys):
            intent_factor = 1.0
        else:
            in_any = any(
                any(rk in k_lower or rk in l_lower for rk in keys)
                for keys in _BENEFIT_APPLICABILITY.values()
            )
            intent_factor = 0.1 if in_any else 0.3
    else:
        intent_factor = 0.4

    # urgency_factor
    parsed_expiry = None
    if expiry_date_str:
        for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%m/%Y'):
            try:
                parsed_expiry = _dt.datetime.strptime(expiry_date_str, fmt).date()
                break
            except ValueError:
                pass

    if parsed_expiry is None:
        combined = f"{field_label} {field_value}"
        m = _re.search(r'\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b', combined)
        if m:
            mo, da, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if yr < 100:
                yr += 2000
            try:
                parsed_expiry = _dt.date(yr, mo, da)
            except ValueError:
                pass
        if not parsed_expiry:
            m2 = _re.search(
                r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+(\d{1,2}),?\s+(\d{4})',
                combined, _re.I,
            )
            if m2:
                mon_map = {
                    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
                    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
                }
                mo = mon_map[m2.group(1)[:3].lower()]
                da, yr = int(m2.group(2)), int(m2.group(3))
                try:
                    parsed_expiry = _dt.date(yr, mo, da)
                except ValueError:
                    pass

    if parsed_expiry:
        today = _dt.date.today()
        days_left = (parsed_expiry - today).days
        if days_left < 0:
            urgency_factor = 0.0
        elif days_left <= 7:
            urgency_factor = 1.0
        elif days_left <= 30:
            urgency_factor = 0.8
        elif days_left <= 90:
            urgency_factor = 0.5
        else:
            urgency_factor = 0.2
    else:
        urgency_factor = 0.3

    # confidence_factor
    confidence_factor = 1.0 if confidence >= 0.85 else (0.7 if confidence >= 0.60 else 0.4)

    score = (
        0.3 * value_factor
        + 0.4 * intent_factor
        + 0.2 * urgency_factor
        + 0.1 * confidence_factor
    )
    factors = {
        "value_factor":      round(value_factor, 3),
        "intent_factor":     round(intent_factor, 3),
        "urgency_factor":    round(urgency_factor, 3),
        "confidence_factor": round(confidence_factor, 3),
    }
    return score, factors


def _confidence_label(score: float) -> str:
    if score >= 0.85:
        return "High"
    elif score >= 0.60:
        return "Medium"
    return "Needs review"
