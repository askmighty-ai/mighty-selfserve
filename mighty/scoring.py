"""
mighty.scoring
──────────────
Relevance scoring and confidence calibration for discovered benefits.

_relevance_score()   — composite sort key for ordering benefits on the dashboard.
                        Output is internal only; never shown to users.
_confidence_label()  — human-readable label for a 0–1 confidence score.

_system_confidence() stays in app.py for now because it calls get_db().

Usage
-----
    from mighty.scoring import _relevance_score, _confidence_label, _BENEFIT_APPLICABILITY

    score, factors = _relevance_score("companion_cert", "Companion Certificate", "1",
                                      context="flight")
"""

import re
import datetime


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
    Composite relevance score for sorting benefits.
    Output NEVER shown to users — internal sorting only.

    Returns (score, factors) where factors dict has keys:
      value_factor, intent_factor, urgency_factor, confidence_factor
    """
    import re as _re
    import datetime as _dt

    k = field_key.lower()
    l = field_label.lower()  # noqa: E741

    # ── value_factor ─────────────────────────────────────────────────────────
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

    # ── intent_factor ─────────────────────────────────────────────────────────
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
        intent_factor = 0.4  # unknown context

    # ── urgency_factor ────────────────────────────────────────────────────────
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
                combined,
                _re.I,
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
            urgency_factor = 0.0    # already expired
        elif days_left <= 7:
            urgency_factor = 1.0    # expires this week
        elif days_left <= 30:
            urgency_factor = 0.8    # expires this month
        elif days_left <= 90:
            urgency_factor = 0.5    # expires this quarter
        else:
            urgency_factor = 0.2    # long-dated
    else:
        urgency_factor = 0.3    # no expiry found — moderate

    # ── confidence_factor ─────────────────────────────────────────────────────
    if confidence >= 0.85:
        confidence_factor = 1.0
    elif confidence >= 0.60:
        confidence_factor = 0.7
    else:
        confidence_factor = 0.4

    # ── composite score ───────────────────────────────────────────────────────
    score = (
        0.3 * value_factor
        + 0.4 * intent_factor
        + 0.2 * urgency_factor
        + 0.1 * confidence_factor
    )

    factors = {
        "value_factor": round(value_factor, 3),
        "intent_factor": round(intent_factor, 3),
        "urgency_factor": round(urgency_factor, 3),
        "confidence_factor": round(confidence_factor, 3),
    }
    return score, factors



def _confidence_label(score: float) -> str:
    """Convert a 0-1 confidence score to a human-readable label."""
    if score >= 0.85:
        return "High"
    elif score >= 0.60:
        return "Medium"
    else:
        return "Needs review"
