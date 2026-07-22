"""
mighty.intelligence.infer
─────────────────────────
Rule-based inference from aggregated provider account data.

Pure functions only — no database, AI, or network calls.
"""

from __future__ import annotations

import re

from mighty.intelligence.aggregate import (
    AggregatedSnapshot,
    aggregate_input,
    field_text,
    parse_numeric,
    source_domain,
)
from mighty.intelligence.models import (
    HotelPreferences,
    InferredAttribute,
    IntelligenceInput,
    IntelligenceProfile,
    LoyaltyStrategy,
    RiskProfile,
    SpendingStrategy,
    TravelProfile,
)

_TIER_RANK = {
    "member": 0,
    "basic": 0,
    "blue": 1,
    "silver": 2,
    "gold": 3,
    "platinum": 4,
    "titanium": 5,
    "diamond": 6,
    "globalist": 6,
    "ambassador": 7,
    "1k": 7,
    "executive": 7,
    "mosaic": 5,
}

_PAST_DUE_RE = re.compile(r"past\s+due|overdue|delinquent|late\s+fee", re.I)
_AUTOPAY_ON_RE = re.compile(r"autopay\s*(on|enabled|active)|enrolled\s+in\s+autopay", re.I)
_AUTOPAY_OFF_RE = re.compile(r"autopay\s*(off|disabled|not\s+enrolled)|manual\s+payment", re.I)
_PORTAL_RE = re.compile(r"fine\s+hotels|fhr|chase\s+travel|portal|amex\s+travel", re.I)
_EXPIRY_RE = re.compile(r"expir|valid\s+through|use\s+by", re.I)


def _attr(value: str, *, confidence: str = "low", evidence: list[str] | None = None) -> InferredAttribute:
    return InferredAttribute(value=value, confidence=confidence, evidence=list(evidence or []))


def _confidence_from_evidence(count: int) -> str:
    if count >= 3:
        return "high"
    if count >= 2:
        return "medium"
    return "low"


def _intent_domain_weights(snapshot: AggregatedSnapshot) -> dict[str, int]:
    weights = dict(snapshot.intent_summary)
    for acct in snapshot.accounts:
        domain = source_domain(acct.source)
        if domain in {"flight", "hotel", "car", "credit_card"}:
            weights[domain] = weights.get(domain, 0) + 1
    return weights


def _detect_tier(text: str) -> str | None:
    lower = text.lower()
    for tier in sorted(_TIER_RANK, key=len, reverse=True):
        if re.search(rf"\b{re.escape(tier)}\b", lower):
            return tier
    return None


def _tier_rank(text: str) -> int:
    tier = _detect_tier(text)
    return _TIER_RANK.get(tier or "", 0)


def _hotel_brands(snapshot: AggregatedSnapshot) -> list[str]:
    brands: list[str] = []
    for acct in snapshot.accounts:
        if source_domain(acct.source) == "hotel":
            brands.append(acct.source.replace("_", " "))
    return sorted(set(brands))


def _total_points(snapshot: AggregatedSnapshot) -> float:
    total = 0.0
    found = False
    for item in snapshot.fields_for_type("points_balance"):
        amount = parse_numeric(str(item.get("value", "")))
        if amount is not None:
            total += amount
            found = True
    return total if found else 0.0


def _has_expiring_assets(snapshot: AggregatedSnapshot) -> bool:
    for btype in ("certificate", "travel_credit", "cash_credit"):
        for item in snapshot.fields_for_type(btype):
            blob = field_text(item)
            if _EXPIRY_RE.search(blob) or item.get("expiry_date"):
                return True
    return False


def _infer_travel_profile(snapshot: AggregatedSnapshot) -> TravelProfile:
    weights = _intent_domain_weights(snapshot)
    travel_domains = {k: v for k, v in weights.items() if k in {"flight", "hotel", "car"} and v > 0}

    primary = "unknown"
    primary_evidence: list[str] = []
    if travel_domains:
        primary = max(travel_domains, key=travel_domains.get)
        primary_evidence = [
            f"{domain} signal weight {travel_domains[domain]}"
            for domain in sorted(travel_domains, key=travel_domains.get, reverse=True)[:3]
        ]

    travel_accounts = [
        acct for acct in snapshot.accounts
        if source_domain(acct.source) in {"flight", "hotel", "car"}
    ]
    upcoming = snapshot.fields_for_type("upcoming_event") + snapshot.fields_for_type("reservation")
    certs = snapshot.fields_for_type("certificate")

    frequency = "occasional"
    freq_evidence: list[str] = []
    score = len(travel_accounts) + len(upcoming) + len(certs)
    if score >= 5 or len(travel_accounts) >= 3:
        frequency = "frequent"
        freq_evidence.append(f"{len(travel_accounts)} synced travel programs")
    elif score >= 2:
        frequency = "regular"
        freq_evidence.append(f"{len(travel_accounts)} synced travel programs")
    else:
        freq_evidence.append("limited synced travel activity")

    if upcoming:
        freq_evidence.append(f"{len(upcoming)} upcoming trip signal(s)")
    if certs:
        freq_evidence.append(f"{len(certs)} redeemable travel award(s)")

    trip_style = "balanced"
    style_evidence: list[str] = []
    if certs or snapshot.fields_for_type("partner_benefit"):
        trip_style = "award_focused"
        style_evidence.append("holds certificates or partner travel awards")
    elif snapshot.fields_for_type("elite_status"):
        trip_style = "status_driven"
        style_evidence.append("maintains elite status across travel programs")
    elif snapshot.intent_summary.get("hotel", 0) > snapshot.intent_summary.get("flight", 0):
        trip_style = "leisure_hotel"
        style_evidence.append("hotel intent outweighs flight intent")
    elif snapshot.intent_summary.get("flight", 0) > 0:
        trip_style = "mobile"
        style_evidence.append("flight intent present")

    return TravelProfile(
        primary_domain=_attr(
            primary,
            confidence=_confidence_from_evidence(len(primary_evidence)),
            evidence=primary_evidence or ["no strong travel domain signals"],
        ),
        travel_frequency=_attr(
            frequency,
            confidence=_confidence_from_evidence(len(freq_evidence)),
            evidence=freq_evidence,
        ),
        trip_style=_attr(
            trip_style,
            confidence=_confidence_from_evidence(len(style_evidence)),
            evidence=style_evidence or ["insufficient trip-style signals"],
        ),
    )


def _infer_hotel_preferences(snapshot: AggregatedSnapshot) -> HotelPreferences:
    brands = _hotel_brands(snapshot)
    brand_value = ", ".join(brands) if brands else "none_detected"
    brand_evidence = [f"synced hotel program: {brand}" for brand in brands]

    tiers: list[str] = []
    for item in snapshot.fields_for_type("elite_status"):
        source = str(item.get("_source", "hotel"))
        if source_domain(source) != "hotel":
            continue
        tier = _detect_tier(field_text(item)) or str(item.get("value", "")).strip()
        if tier:
            tiers.append(f"{source}:{tier}")

    tier_value = ", ".join(tiers) if tiers else "none_detected"
    tier_evidence = tiers or ["no hotel elite status detected"]

    booking = "direct_loyalty"
    booking_evidence: list[str] = []
    if snapshot.fields_for_type("certificate"):
        booking = "certificate_first"
        booking_evidence.append("holds hotel or travel certificates")
    for item in snapshot.fields_for_domain("credit_card"):
        if _PORTAL_RE.search(field_text(item)):
            if booking == "certificate_first":
                booking = "certificate_and_portal"
            else:
                booking = "premium_portal"
            booking_evidence.append(
                f"premium travel portal benefit on {item.get('_source', 'card')}"
            )
            break
    if brands and not booking_evidence:
        booking_evidence.append("synced hotel loyalty without portal markers")

    return HotelPreferences(
        preferred_brands=_attr(
            brand_value,
            confidence=_confidence_from_evidence(len(brands)),
            evidence=brand_evidence or ["no synced hotel programs"],
        ),
        elite_tiers=_attr(
            tier_value,
            confidence=_confidence_from_evidence(len(tiers)),
            evidence=tier_evidence,
        ),
        booking_approach=_attr(
            booking,
            confidence=_confidence_from_evidence(len(booking_evidence)),
            evidence=booking_evidence,
        ),
    )


def _infer_spending_strategy(snapshot: AggregatedSnapshot) -> SpendingStrategy:
    card_accounts = [acct for acct in snapshot.accounts if acct.category == "credit_card"]
    travel_credits = snapshot.fields_for_type("travel_credit")
    cash_credits = snapshot.fields_for_type("cash_credit")
    points_total = _total_points(snapshot)

    mode = "unknown"
    mode_evidence: list[str] = []
    if points_total >= 100_000 and (travel_credits or cash_credits):
        mode = "hybrid"
        mode_evidence.append(f"{int(points_total):,} total points/miles across programs")
        mode_evidence.append("active card credits detected")
    elif points_total >= 50_000 or len(snapshot.fields_for_type("points_balance")) >= 2:
        mode = "points_first"
        mode_evidence.append("significant loyalty balances across programs")
    elif travel_credits or cash_credits:
        mode = "credit_first"
        mode_evidence.append("uses card-issued travel or statement credits")
    elif card_accounts:
        mode = "card_centric"
        mode_evidence.append("synced credit card account(s) without strong balance signals")

    card_focus = "undetermined"
    focus_evidence: list[str] = []
    if card_accounts:
        card_focus = ", ".join(sorted({acct.source for acct in card_accounts}))
        focus_evidence.append(f"{len(card_accounts)} synced card issuer(s)")
    premium_markers = [
        item for item in snapshot.fields_for_domain("credit_card")
        if "platinum" in field_text(item) or "reserve" in field_text(item)
    ]
    if premium_markers:
        card_focus = "premium_travel_cards"
        focus_evidence.append("premium travel card benefits detected")

    utilization = "monitor_credits"
    util_evidence: list[str] = []
    unused_credits = travel_credits + cash_credits
    if unused_credits:
        util_evidence.append(f"{len(unused_credits)} unused credit benefit(s)")
    annual_fees = snapshot.fields_for_type("renewal")
    if annual_fees:
        utilization = "optimize_benefits"
        util_evidence.append(f"{len(annual_fees)} renewal or annual-fee signal(s)")
    if snapshot.type_affinity.get("cash_credit", 0) >= 2:
        utilization = "active_credit_user"
        util_evidence.append("high cash-credit affinity")

    return SpendingStrategy(
        primary_mode=_attr(
            mode,
            confidence=_confidence_from_evidence(len(mode_evidence)),
            evidence=mode_evidence or ["no spending signals"],
        ),
        card_focus=_attr(
            card_focus,
            confidence=_confidence_from_evidence(len(focus_evidence)),
            evidence=focus_evidence or ["no synced card accounts"],
        ),
        credit_utilization=_attr(
            utilization,
            confidence=_confidence_from_evidence(len(util_evidence)),
            evidence=util_evidence or ["no credit utilization signals"],
        ),
    )


def _infer_loyalty_strategy(snapshot: AggregatedSnapshot) -> LoyaltyStrategy:
    loyalty_accounts = [
        acct for acct in snapshot.accounts if acct.category == "travel_loyalty"
    ]
    progress = snapshot.fields_for_type("progress_toward")
    certs = snapshot.fields_for_type("certificate")

    accumulation = "single_program"
    accum_evidence: list[str] = []
    if len(loyalty_accounts) >= 3:
        accumulation = "multi_program"
        accum_evidence.append(f"{len(loyalty_accounts)} synced loyalty programs")
    elif len(loyalty_accounts) == 2:
        accumulation = "dual_program"
        accum_evidence.append("two synced loyalty programs")
    elif loyalty_accounts:
        accum_evidence.append(f"primary program: {loyalty_accounts[0].source}")

    if progress:
        accumulation = "status_chasing"
        accum_evidence.append(f"{len(progress)} progress-toward-status metric(s)")

    diversity = "focused"
    diversity_evidence: list[str] = []
    domains = {source_domain(acct.source) for acct in loyalty_accounts}
    if len(domains) >= 3:
        diversity = "diversified"
        diversity_evidence.append(f"activity across {len(domains)} travel domains")
    elif len(domains) == 2:
        diversity = "dual_domain"
        diversity_evidence.append("loyalty spread across two travel domains")
    else:
        diversity_evidence.append("loyalty concentrated in one domain")

    redemption = "low_pressure"
    redeem_evidence: list[str] = []
    if certs:
        redemption = "use_awards"
        redeem_evidence.append(f"{len(certs)} certificate(s) on account")
    if _has_expiring_assets(snapshot):
        redemption = "time_sensitive"
        redeem_evidence.append("expiring certificates or credits detected")
    if snapshot.type_affinity.get("certificate", 0) >= 2:
        redeem_evidence.append("elevated certificate affinity")

    return LoyaltyStrategy(
        accumulation_style=_attr(
            accumulation,
            confidence=_confidence_from_evidence(len(accum_evidence)),
            evidence=accum_evidence or ["no loyalty programs synced"],
        ),
        program_diversity=_attr(
            diversity,
            confidence=_confidence_from_evidence(len(diversity_evidence)),
            evidence=diversity_evidence,
        ),
        redemption_pressure=_attr(
            redemption,
            confidence=_confidence_from_evidence(len(redeem_evidence)),
            evidence=redeem_evidence or ["no near-term redemption pressure"],
        ),
    )


def _infer_risk_profile(snapshot: AggregatedSnapshot) -> RiskProfile:
    payments = snapshot.fields_for_type("payment_due")
    renewals = snapshot.fields_for_type("renewal")

    past_due = [
        item for item in payments
        if _PAST_DUE_RE.search(field_text(item))
    ]
    autopay_on = any(_AUTOPAY_ON_RE.search(field_text(item)) for item in payments)
    autopay_off = any(_AUTOPAY_OFF_RE.search(field_text(item)) for item in payments)

    financial_risk = "low"
    risk_evidence: list[str] = []
    if past_due:
        financial_risk = "high"
        risk_evidence.append(f"{len(past_due)} past-due payment signal(s)")
    elif payments and autopay_off:
        financial_risk = "medium"
        risk_evidence.append("manual payment pattern on synced accounts")
    elif payments:
        risk_evidence.append(f"{len(payments)} payment-due field(s) without delinquency markers")

    payment_health = "stable"
    health_evidence: list[str] = []
    if autopay_on:
        payment_health = "autopay_enabled"
        health_evidence.append("autopay enabled on synced account(s)")
    elif autopay_off:
        payment_health = "manual_payments"
        health_evidence.append("manual payment preference detected")
    elif not payments:
        payment_health = "unknown"
        health_evidence.append("no payment fields synced")

    attention_parts: list[str] = []
    if past_due:
        attention_parts.append("past_due_payments")
    if renewals:
        attention_parts.append("upcoming_renewals")
    if _has_expiring_assets(snapshot):
        attention_parts.append("expiring_benefits")
    if not snapshot.synced_sources and snapshot.connected_sources:
        attention_parts.append("accounts_need_sync")

    attention_value = ", ".join(attention_parts) if attention_parts else "none"
    attention_evidence = attention_parts or ["no immediate attention areas detected"]

    return RiskProfile(
        financial_risk=_attr(
            financial_risk,
            confidence="high" if past_due else _confidence_from_evidence(len(risk_evidence)),
            evidence=risk_evidence or ["no payment risk signals"],
        ),
        payment_health=_attr(
            payment_health,
            confidence=_confidence_from_evidence(len(health_evidence)),
            evidence=health_evidence,
        ),
        attention_areas=_attr(
            attention_value,
            confidence=_confidence_from_evidence(len(attention_parts)),
            evidence=attention_evidence,
        ),
    )


def infer_from_snapshot(snapshot: AggregatedSnapshot) -> IntelligenceProfile:
    """Run all inference passes on an aggregated snapshot."""
    return IntelligenceProfile(
        travel_profile=_infer_travel_profile(snapshot),
        hotel_preferences=_infer_hotel_preferences(snapshot),
        spending_strategy=_infer_spending_strategy(snapshot),
        loyalty_strategy=_infer_loyalty_strategy(snapshot),
        risk_profile=_infer_risk_profile(snapshot),
    )


def infer_intelligence(input_data: IntelligenceInput) -> IntelligenceProfile:
    """Infer user intelligence from provider account data."""
    snapshot = aggregate_input(input_data)
    return infer_from_snapshot(snapshot)
