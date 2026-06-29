"""
mighty.partnerships
───────────────────
Cross-program partnership graph and card-recommendation knowledge base.

Two functions are exported:

  get_derived_benefits(items, connected_sources, context)
    → list[dict]   perks unlocked by accounts the user already has
                   (e.g. Delta Gold → Hertz Gold Plus)

  get_card_recommendations(connected_sources, context=None)
    → list[dict]   factual card/account descriptions when the user has a
                   related account but not the card.  No dollar estimates.

Rule metadata
─────────────
Each rule carries:
  source_url     – official program page confirming the partnership
  last_verified  – "YYYY-MM" — update this when re-checking the benefit
  confidence     – "high" (long-standing, well-documented)
                   "medium" (documented but terms change frequently)
                   "low" (reported but not confirmed on official page)

Source key matching
───────────────────
PARTNERSHIPS is keyed by *pattern strings*, substring-matched against the
user's account source key (e.g. "delta" matches "delta_skymiles").
Use the narrowest pattern that still catches all reasonable source key
variants — avoid generic words like "american" that match unrelated sources.
For AA use "american_air" (matches american_airlines, american_air)
and alias "aadvantage" (matches aadvantage, aa_aadvantage).
"""
from __future__ import annotations

# ── Partnership graph ────────────────────────────────────────────────────────

PARTNERSHIPS: dict[str, dict[str, list[dict]]] = {

    # ── Delta SkyMiles ───────────────────────────────────────────────────────
    "delta": {
        "car": [
            {
                "tiers": ["silver", "gold", "platinum", "diamond"],
                "program": "Hertz",
                "benefit": "Gold Plus Rewards status",
                "detail": "Skip the counter and go straight to your car.",
                "enroll_hint": "Enroll at delta.com/hertz",
                "source_url": "https://www.delta.com/us/en/skymiles/partner/hertz",
                "last_verified": "2025-01",
                "confidence": "high",
            },
            {
                "tiers": ["platinum", "diamond"],
                "program": "Hertz",
                "benefit": "Five Star status",
                "detail": "Guaranteed upgrades and access to a wider selection of premium vehicles.",
                "enroll_hint": "Enroll at delta.com/hertz",
                "source_url": "https://www.delta.com/us/en/skymiles/partner/hertz",
                "last_verified": "2025-01",
                "confidence": "high",
            },
        ],
    },

    # ── United MileagePlus ───────────────────────────────────────────────────
    "united": {
        "car": [
            {
                "tiers": ["silver", "gold", "platinum", "1k"],
                "program": "Hertz",
                "benefit": "Gold Plus Rewards status",
                "detail": "No counter wait — go directly to your car.",
                "enroll_hint": "Link at united.com/hertz",
                "source_url": "https://www.united.com/ual/en/us/fly/mileageplus/partners/car-rentals.html",
                "last_verified": "2025-01",
                "confidence": "high",
            },
            {
                "tiers": ["1k"],
                "program": "Hertz",
                "benefit": "Five Star status",
                "detail": "Guaranteed upgrade and priority vehicle selection.",
                "enroll_hint": "Link at united.com/hertz",
                "source_url": "https://www.united.com/ual/en/us/fly/mileageplus/partners/car-rentals.html",
                "last_verified": "2025-01",
                "confidence": "high",
            },
        ],
    },

    # ── American Airlines / AAdvantage ───────────────────────────────────────
    # Use "american_air" to match american_airlines, american_air — avoids
    # collisions with amex or other "american_*" source keys.
    # "aadvantage" is aliased below to the same rules.
    "american_air": {
        "car": [
            {
                "tiers": ["gold", "platinum"],
                "program": "Hertz",
                "benefit": "Five Star status",
                "detail": "Guaranteed upgrades and access to premium vehicles.",
                "enroll_hint": "Link at aa.com/hertz",
                "source_url": "https://www.aa.com/i18n/aadvantage-program/partners/car-partners/hertz.jsp",
                "last_verified": "2025-01",
                "confidence": "high",
            },
            {
                "tiers": ["platinum pro", "executive platinum"],
                "program": "Hertz",
                "benefit": "President's Circle status",
                "detail": "Top-tier status — guaranteed upgrades, choice of any car in the lot.",
                "enroll_hint": "Link at aa.com/hertz",
                "source_url": "https://www.aa.com/i18n/aadvantage-program/partners/car-partners/hertz.jsp",
                "last_verified": "2025-01",
                "confidence": "high",
            },
        ],
        "hotel": [
            {
                "tiers": ["executive platinum"],
                "program": "Marriott Bonvoy",
                "benefit": "Gold Elite status",
                "detail": "Complimentary room upgrades and bonus points when available.",
                "enroll_hint": "Link your AAdvantage account in Marriott Bonvoy settings",
                "source_url": "https://www.marriott.com/loyalty/earn/airlinePartners.mi",
                "last_verified": "2025-01",
                "confidence": "medium",
            },
        ],
    },

    # ── Alaska Airlines Mileage Plan ─────────────────────────────────────────
    "alaska": {
        "car": [
            {
                "tiers": ["mvp", "gold", "75k"],
                "program": "Hertz",
                "benefit": "Gold Plus Rewards status",
                "detail": "Skip the counter, go straight to your car.",
                "enroll_hint": "Link at alaskaair.com/hertz",
                "source_url": "https://www.alaskaair.com/content/mileage-plan/earn-miles/travel/car-rental",
                "last_verified": "2025-01",
                "confidence": "high",
            },
        ],
    },

    # ── Southwest Rapid Rewards ──────────────────────────────────────────────
    "southwest": {
        "car": [
            {
                "tiers": ["a-list", "a-list preferred", "companion pass"],
                "program": "Hertz",
                "benefit": "Gold Plus Rewards status",
                "detail": "No counter wait on Southwest partner rentals.",
                "enroll_hint": "Link at southwest.com/hertz",
                "source_url": "https://www.southwest.com/rapidrewards/partners",
                "last_verified": "2025-01",
                "confidence": "medium",
            },
        ],
    },

    # ── World of Hyatt ───────────────────────────────────────────────────────
    "hyatt": {
        "car": [
            {
                "tiers": ["globalist"],
                "program": "Hertz",
                "benefit": "President's Circle status",
                "detail": "Top-tier status — guaranteed upgrades, choice of any car in the lot.",
                "enroll_hint": "Link in your World of Hyatt account settings",
                "source_url": "https://world.hyatt.com/content/gp/en/rewards/partners/car-rental.html",
                "last_verified": "2025-01",
                "confidence": "high",
            },
        ],
        "flight": [
            {
                "tiers": ["globalist"],
                "program": "American Airlines",
                "benefit": "AAdvantage miles on Hyatt stays",
                "detail": "Earn AAdvantage miles instead of Hyatt points on qualifying stays.",
                "enroll_hint": "Link in your World of Hyatt account settings",
                "source_url": "https://world.hyatt.com/content/gp/en/rewards/partners/airline-partners.html",
                "last_verified": "2025-01",
                "confidence": "medium",
            },
        ],
    },

    # ── Marriott Bonvoy ──────────────────────────────────────────────────────
    "marriott": {
        "car": [
            {
                "tiers": ["platinum", "titanium", "ambassador"],
                "program": "Hertz",
                "benefit": "Gold Plus Rewards status",
                "detail": "Skip the counter and go directly to your car.",
                "enroll_hint": "Link in your Marriott Bonvoy account",
                "source_url": "https://www.marriott.com/loyalty/earn/travel-partners/car-rental.mi",
                "last_verified": "2025-01",
                "confidence": "high",
            },
        ],
    },

    # ── Hilton Honors ────────────────────────────────────────────────────────
    "hilton": {
        "car": [
            {
                "tiers": ["gold", "diamond"],
                "program": "Hertz",
                "benefit": "Gold Plus Rewards status",
                "detail": "Counter-bypass on Hertz rentals.",
                "enroll_hint": "Link in your Hilton Honors account",
                "source_url": "https://www.hilton.com/en/hilton-honors/partners/",
                "last_verified": "2025-01",
                "confidence": "medium",
            },
        ],
    },

    # ── Credit cards ─────────────────────────────────────────────────────────
    # tiers: [] means the benefit applies to any cardholder (no status check).

    "amex_platinum": {
        "car": [
            {
                "tiers": [],
                "program": "Hertz",
                "benefit": "President's Circle status",
                "detail": "Top-tier status — guaranteed upgrades, any car in the lot.",
                "enroll_hint": "Activate via your Amex Platinum card benefits portal",
                "source_url": "https://www.americanexpress.com/us/credit-cards/features-benefits/hertz/",
                "last_verified": "2025-01",
                "confidence": "high",
            },
            {
                "tiers": [],
                "program": "Avis",
                "benefit": "President's Club status",
                "detail": "Complimentary upgrades and priority service at the counter.",
                "enroll_hint": "Activate via your Amex Platinum card benefits portal",
                "source_url": "https://www.americanexpress.com/us/credit-cards/features-benefits/",
                "last_verified": "2025-01",
                "confidence": "high",
            },
            {
                "tiers": [],
                "program": "National",
                "benefit": "Emerald Club Executive status",
                "detail": "Choose any car in the Executive Aisle — no counter, no waiting.",
                "enroll_hint": "Activate via your Amex Platinum card benefits portal",
                "source_url": "https://www.americanexpress.com/us/credit-cards/features-benefits/",
                "last_verified": "2025-01",
                "confidence": "high",
            },
        ],
        "hotel": [
            {
                "tiers": [],
                "program": "Hilton Honors",
                "benefit": "Gold status",
                "detail": "Complimentary breakfast at most properties, room upgrades, and 80% bonus points.",
                "enroll_hint": "Activate via your Amex Platinum card benefits portal",
                "source_url": "https://www.americanexpress.com/us/credit-cards/features-benefits/hilton/",
                "last_verified": "2025-01",
                "confidence": "high",
            },
            {
                "tiers": [],
                "program": "Marriott Bonvoy",
                "benefit": "Gold Elite status",
                "detail": "Complimentary room upgrades and enhanced point earning.",
                "enroll_hint": "Activate via your Amex Platinum card benefits portal",
                "source_url": "https://www.americanexpress.com/us/credit-cards/features-benefits/marriott/",
                "last_verified": "2025-01",
                "confidence": "high",
            },
        ],
    },

    "amex_gold": {
        "hotel": [
            {
                "tiers": [],
                "program": "Marriott Bonvoy",
                "benefit": "Gold Elite status",
                "detail": "Complimentary room upgrades when available.",
                "enroll_hint": "Activate via your Amex Gold card benefits portal",
                "source_url": "https://www.americanexpress.com/us/credit-cards/features-benefits/",
                "last_verified": "2025-01",
                "confidence": "medium",
            },
        ],
    },

    "chase_sapphire_reserve": {
        "car": [
            {
                "tiers": [],
                "program": "National",
                "benefit": "Emerald Club Executive Elite status",
                "detail": "Choose any car in the Executive Selection — no counter required.",
                "enroll_hint": "Activate via Chase Ultimate Rewards benefits",
                "source_url": "https://creditcards.chase.com/sapphire/reserve",
                "last_verified": "2025-01",
                "confidence": "high",
            },
        ],
        "flight": [
            {
                "tiers": [],
                "program": "Priority Pass",
                "benefit": "Select membership",
                "detail": "Access 1,300+ airport lounges worldwide at no per-visit charge.",
                "enroll_hint": "Activate in your Chase Sapphire Reserve card benefits",
                "source_url": "https://creditcards.chase.com/sapphire/reserve",
                "last_verified": "2025-01",
                "confidence": "high",
            },
        ],
    },

    "chase_sapphire_preferred": {
        "car": [
            {
                "tiers": [],
                "program": "National",
                "benefit": "Emerald Club Executive status",
                "detail": "Choose any car in the Executive Aisle — no counter wait.",
                "enroll_hint": "Activate via Chase Ultimate Rewards benefits",
                "source_url": "https://creditcards.chase.com/rewards-credit-cards/sapphire/preferred",
                "last_verified": "2025-01",
                "confidence": "high",
            },
        ],
    },

    "citi_prestige": {
        "car": [
            {
                "tiers": [],
                "program": "Hertz",
                "benefit": "President's Circle status",
                "detail": "Top-tier status with guaranteed upgrades.",
                "enroll_hint": "Link via your Citi Prestige card benefits",
                "source_url": "https://www.citi.com/credit-cards/citi-prestige-credit-card",
                "last_verified": "2025-01",
                "confidence": "medium",
            },
            {
                "tiers": [],
                "program": "National",
                "benefit": "Emerald Club Executive status",
                "detail": "Choose any car in the Executive Aisle.",
                "enroll_hint": "Link via your Citi Prestige card benefits",
                "source_url": "https://www.citi.com/credit-cards/citi-prestige-credit-card",
                "last_verified": "2025-01",
                "confidence": "medium",
            },
        ],
    },

    "citi_aadvantage_executive": {
        "flight": [
            {
                "tiers": [],
                "program": "Admirals Club",
                "benefit": "Full membership",
                "detail": "Access to all Admirals Club lounges when flying American, plus two guests.",
                "enroll_hint": "Show your card at any Admirals Club entrance",
                "source_url": "https://www.citi.com/credit-cards/citi-aadvantage-executive-world-elite-mastercard",
                "last_verified": "2025-01",
                "confidence": "high",
            },
        ],
    },

    "delta_reserve": {
        "flight": [
            {
                "tiers": [],
                "program": "Delta Sky Club",
                "benefit": "Access on days you fly Delta",
                "detail": "Access to Delta Sky Club lounges on days you're flying Delta-operated flights.",
                "enroll_hint": "Show your card at any Delta Sky Club entrance",
                "source_url": "https://www.americanexpress.com/us/credit-cards/card/delta-skymiles-reserve/",
                "last_verified": "2025-01",
                "confidence": "high",
            },
        ],
    },

    "united_club": {
        "flight": [
            {
                "tiers": [],
                "program": "United Club",
                "benefit": "Full membership",
                "detail": "Access to all United Club and Star Alliance lounges for you and eligible guests.",
                "enroll_hint": "Show your card at any United Club entrance",
                "source_url": "https://creditcards.chase.com/travel-credit-cards/united/club-card",
                "last_verified": "2025-01",
                "confidence": "high",
            },
        ],
    },
}

# Alias: "aadvantage" source keys also match the American Airlines rules
import copy as _copy_part
PARTNERSHIPS["aadvantage"] = _copy_part.deepcopy(PARTNERSHIPS["american_air"])


# ── Card recommendations ─────────────────────────────────────────────────────
# Surfaced when the user has a trigger_source account but not the card.
# Benefits are factual, published descriptions — no dollar-value estimates.
# "contexts" gates where this card is shown (extension pill + dashboard filter).

CARD_RECOMMENDATIONS: list[dict] = [
    {
        "id": "jetblue_plus",
        "card_name": "JetBlue Plus Card",
        "issuer": "Barclays",
        "trigger_sources": ["jetblue"],
        "skip_if_sources": ["jetblue_plus", "jetblue_card", "jetblue_mastercard", "jetblue_biz"],
        "contexts": ["flight"],
        "source_url": "https://www.barclaysus.com/cards/jetblue-plus-card",
        "last_verified": "2025-01",
        "confidence": "high",
        "benefits": [
            "Free first checked bag for you and up to 3 companions on every JetBlue-operated flight",
            "Annual companion certificate after your account anniversary",
            "6x points on JetBlue purchases, 2x on dining and grocery store purchases",
            "50% savings on eligible inflight food and drink purchases",
            "Mosaic 1 status — priority boarding and security access where available",
        ],
    },
    {
        "id": "southwest_priority",
        "card_name": "Southwest Rapid Rewards Priority Card",
        "issuer": "Chase",
        "trigger_sources": ["southwest"],
        "skip_if_sources": ["southwest_priority", "southwest_card", "southwest_premier",
                            "southwest_plus", "southwest_performance"],
        "contexts": ["flight"],
        "source_url": "https://creditcards.chase.com/travel-credit-cards/southwest/priority",
        "last_verified": "2025-01",
        "confidence": "high",
        "benefits": [
            "7,500 anniversary bonus points each year",
            "4 upgraded boardings per year when available",
            "20% back on inflight purchases as a statement credit",
            "3x points on Southwest purchases",
            "Each purchase counts toward Companion Pass earning",
        ],
    },
    {
        "id": "delta_gold_amex",
        "card_name": "Delta SkyMiles Gold American Express Card",
        "issuer": "American Express",
        "trigger_sources": ["delta"],
        "skip_if_sources": ["delta_gold", "delta_platinum_amex", "delta_reserve",
                            "delta_card", "amex_delta", "delta_blue"],
        "contexts": ["flight"],
        "source_url": "https://www.americanexpress.com/us/credit-cards/card/delta-skymiles-gold/",
        "last_verified": "2025-01",
        "confidence": "high",
        "benefits": [
            "First checked bag free on Delta flights for you and up to 8 companions on the same reservation",
            "Priority boarding on Delta flights",
            "2x miles on Delta purchases and at restaurants",
            "20% back on eligible inflight Delta purchases as a statement credit",
        ],
    },
    {
        "id": "united_explorer",
        "card_name": "United Explorer Card",
        "issuer": "Chase",
        "trigger_sources": ["united"],
        "skip_if_sources": ["united_explorer", "united_card", "united_club",
                            "united_quest", "united_infinite"],
        "contexts": ["flight"],
        "source_url": "https://creditcards.chase.com/travel-credit-cards/united/explorer",
        "last_verified": "2025-01",
        "confidence": "high",
        "benefits": [
            "Free first checked bag on United-operated flights for you and one companion",
            "Priority boarding",
            "2x miles on United purchases, at restaurants, and on hotel stays",
            "2 one-time United Club passes per year",
            "25% back on United inflight and Club Premium purchases",
        ],
    },
    {
        "id": "alaska_visa",
        "card_name": "Alaska Airlines Visa Signature Card",
        "issuer": "Bank of America",
        "trigger_sources": ["alaska"],
        "skip_if_sources": ["alaska_card", "alaska_visa", "alaska_airlines_card",
                            "alaska_business"],
        "contexts": ["flight"],
        "source_url": "https://www.bankofamerica.com/credit-cards/products/alaska-airlines-visa-credit-card/",
        "last_verified": "2025-01",
        "confidence": "high",
        "benefits": [
            "Companion fare each account anniversary — a second ticket for a companion on any Alaska flight",
            "Free first checked bag for you and up to 6 guests on Alaska flights",
            "3x miles on Alaska Airlines purchases",
            "Priority boarding and 20% back on Alaska inflight purchases",
        ],
    },
    {
        "id": "aa_platinum_select",
        "card_name": "Citi AAdvantage Platinum Select Card",
        "issuer": "Citi",
        "trigger_sources": ["american_air", "aadvantage"],
        "skip_if_sources": ["aa_card", "aadvantage_card", "citi_aadvantage",
                            "aviator", "aa_platinum", "aa_executive", "aa_gold_card"],
        "contexts": ["flight"],
        "source_url": "https://www.citi.com/credit-cards/citi-aadvantage-platinum-select-world-elite",
        "last_verified": "2025-01",
        "confidence": "high",
        "benefits": [
            "First checked bag free for you and up to 4 companions on domestic American Airlines itineraries",
            "Preferred boarding on American Airlines flights",
            "2x miles on American Airlines purchases, at restaurants, and at gas stations",
            "25% savings on inflight food and beverage purchases",
        ],
    },
    {
        "id": "marriott_boundless",
        "card_name": "Marriott Bonvoy Boundless Card",
        "issuer": "Chase",
        "trigger_sources": ["marriott"],
        "skip_if_sources": ["marriott_card", "marriott_boundless", "marriott_brilliant",
                            "marriott_bevy", "marriott_bold", "marriott_biz"],
        "contexts": ["hotel"],
        "source_url": "https://creditcards.chase.com/travel-credit-cards/marriott/boundless",
        "last_verified": "2025-01",
        "confidence": "high",
        "benefits": [
            "One free night award each account anniversary",
            "Automatic Silver Elite status, with a path to Gold Elite after 25 nights",
            "6x points at Marriott Bonvoy hotels, 3x on up to the first $6,000 in other purchases",
            "15 Elite Night Credits per year toward status",
        ],
    },
    {
        "id": "world_of_hyatt_card",
        "card_name": "World of Hyatt Credit Card",
        "issuer": "Chase",
        "trigger_sources": ["hyatt"],
        "skip_if_sources": ["hyatt_card", "world_of_hyatt_card"],
        "contexts": ["hotel"],
        "source_url": "https://creditcards.chase.com/travel-credit-cards/hyatt",
        "last_verified": "2025-01",
        "confidence": "high",
        "benefits": [
            "One free night at any Category 1–4 Hyatt hotel each account anniversary",
            "Automatic Discoverist status (additional Discoverist status after qualifying stays)",
            "4x Hyatt points per dollar at Hyatt hotels",
            "2x points on dining, airline tickets, fitness clubs, and local transit",
            "5 qualifying night credits per year toward status",
        ],
    },
    {
        "id": "hilton_aspire",
        "card_name": "Hilton Honors American Express Aspire Card",
        "issuer": "American Express",
        "trigger_sources": ["hilton"],
        "skip_if_sources": ["hilton_aspire", "hilton_card", "hilton_surpass",
                            "hilton_honors_card", "hilton_biz"],
        "contexts": ["hotel"],
        "source_url": "https://www.americanexpress.com/us/credit-cards/card/hilton-honors-aspire/",
        "last_verified": "2025-01",
        "confidence": "high",
        "benefits": [
            "Automatic Diamond status — the highest tier in Hilton Honors",
            "Annual free night reward valid at nearly any Hilton property",
            "14x points on Hilton stays, 7x on select travel and dining purchases",
            "Priority Pass Select membership for airport lounge access",
        ],
    },
    {
        "id": "ihg_premier",
        "card_name": "IHG One Rewards Premier Card",
        "issuer": "Chase",
        "trigger_sources": ["ihg"],
        "skip_if_sources": ["ihg_card", "ihg_premier", "ihg_traveler"],
        "contexts": ["hotel"],
        "source_url": "https://creditcards.chase.com/travel-credit-cards/ihg/premier",
        "last_verified": "2025-01",
        "confidence": "high",
        "benefits": [
            "Annual free night at IHG Hotels after each account anniversary",
            "Automatic Platinum Elite status",
            "10x points at IHG Hotels and Resorts",
            "4th reward night free when redeeming points for 3 consecutive nights",
        ],
    },
    {
        "id": "amazon_prime_visa",
        "card_name": "Amazon Prime Visa",
        "issuer": "Chase",
        "trigger_sources": ["amazon"],
        "skip_if_sources": ["amazon_prime_visa", "amazon_card", "amazon_visa",
                            "amazon_store_card"],
        "contexts": ["shopping"],
        "source_url": "https://www.amazon.com/Amazon-Prime-Visa",
        "last_verified": "2025-01",
        "confidence": "high",
        "benefits": [
            "5% back on Amazon.com and Whole Foods Market purchases (Prime membership required)",
            "5% back on Chase Travel purchases",
            "2% back at restaurants, gas stations, and on local transit",
            "No foreign transaction fees",
        ],
    },
    {
        "id": "costco_anywhere_visa",
        "card_name": "Costco Anywhere Visa Card",
        "issuer": "Citi",
        "trigger_sources": ["costco"],
        "skip_if_sources": ["costco_card", "costco_visa", "costco_anywhere"],
        "contexts": ["shopping"],
        "source_url": "https://www.citi.com/credit-cards/citi-costco-anywhere-visa-credit-card",
        "last_verified": "2025-01",
        "confidence": "high",
        "benefits": [
            "4% cash back on eligible gas and EV charging (up to $7,000/year, then 1%)",
            "3% cash back on restaurant and eligible travel purchases",
            "2% cash back on all Costco and Costco.com purchases",
            "No foreign transaction fees",
        ],
    },
]


# ── Helper functions ─────────────────────────────────────────────────────────

def _src_matches(user_source: str, pattern: str) -> bool:
    """True if pattern appears as a substring in the user's source key."""
    u = user_source.lower().replace("-", "_")
    p = pattern.lower().replace("-", "_")
    return p in u


def _tier_matches(status_value: str, tiers: list[str]) -> bool:
    """True if the stored status value contains any of the required tier keywords."""
    if not tiers:
        return True  # card benefit — no tier check
    val_lc = status_value.lower()
    return any(t.lower() in val_lc for t in tiers)


def get_derived_benefits(
    items: list[dict],
    connected_sources: list[str],
    context: str,
) -> list[dict]:
    """
    Scan user's stored items for elite status / membership indicators.
    Return derived benefits implied by cross-program partnerships.

    Returned dicts include:
      account, source, label, value, derived=True,
      program, benefit, detail, enroll_hint,
      source_url, last_verified, confidence,
      _score, _why
    """
    results: list[dict] = []
    seen: set[tuple] = set()

    # Build index: source_key → list of (label, value, display_name) for status items
    status_by_source: dict[str, list[tuple[str, str, str]]] = {}
    for it in items:
        src = (it.get("source") or "").lower().replace("-", "_")
        btype = it.get("_type") or ""
        lbl   = (it.get("label") or "").lower()
        key   = (it.get("key")   or "").lower()

        is_status = btype in ("elite_status", "membership") or any(
            kw in key + lbl
            for kw in ["status", "tier", "level", "medallion", "elite",
                        "member", "globalist", "circle", "chairman", "premier"]
        )
        if not is_status:
            continue
        fv = str(it.get("value") or "").strip()
        if not fv or fv.lower() in ("none", "n/a", "unknown", ""):
            continue
        disp = it.get("display_name") or src.replace("_", " ").title()
        status_by_source.setdefault(src, []).append((it.get("label", ""), fv, disp))

    all_sources = set(status_by_source.keys()) | {
        s.lower().replace("-", "_") for s in connected_sources
    }

    for prog_key, ctx_map in PARTNERSHIPS.items():
        if context not in ctx_map:
            continue

        for user_src in all_sources:
            if not _src_matches(user_src, prog_key):
                continue

            for rule in ctx_map[context]:
                tier_ok      = False
                status_label = ""
                status_val   = ""
                display_name = user_src.replace("_", " ").title()

                if not rule["tiers"]:
                    tier_ok    = True
                    status_val = rule.get("benefit", "Included")
                else:
                    for lbl, val, disp in status_by_source.get(user_src, []):
                        if _tier_matches(val, rule["tiers"]):
                            tier_ok      = True
                            status_label = lbl
                            status_val   = val
                            display_name = disp
                            break

                if not tier_ok:
                    continue

                dedup = (user_src, rule["program"], rule["benefit"])
                if dedup in seen:
                    continue
                seen.add(dedup)

                results.append({
                    "account":       display_name,
                    "source":        user_src,
                    "label":         f"{rule['program']} — {rule['benefit']}",
                    "value":         status_val or "Included",
                    "derived":       True,
                    "via_status":    status_label or None,
                    "program":       rule["program"],
                    "benefit":       rule["benefit"],
                    "detail":        rule.get("detail", ""),
                    "enroll_hint":   rule.get("enroll_hint", ""),
                    "source_url":    rule.get("source_url", ""),
                    "last_verified": rule.get("last_verified", ""),
                    "confidence":    rule.get("confidence", "medium"),
                    "_score":        0.85,
                    "_why": {
                        "intent_factor":     0.9,
                        "value_factor":      0.7,
                        "urgency_factor":    0.0,
                        "confidence_factor": 0.9,
                    },
                })

    return results


def get_card_recommendations(
    connected_sources: list[str],
    context: str | None = None,
) -> list[dict]:
    """
    Return factual card/account descriptions when the user has a related
    account but not the card.

    If context is provided, only returns cards whose contexts list includes it.
    No dollar-value estimates.
    """
    sources_lc = [s.lower().replace("-", "_") for s in connected_sources]
    results: list[dict] = []

    for rec in CARD_RECOMMENDATIONS:
        # Context gate — skip cards not relevant to the current activity
        if context and context not in rec["contexts"]:
            continue
        # Skip if user appears to already have the card
        if any(
            _src_matches(s, skip)
            for s in sources_lc
            for skip in rec["skip_if_sources"]
        ):
            continue
        # Only suggest if user has the trigger account
        if not any(
            _src_matches(s, trigger)
            for s in sources_lc
            for trigger in rec["trigger_sources"]
        ):
            continue
        results.append({
            "id":             rec["id"],
            "card_name":      rec["card_name"],
            "issuer":         rec["issuer"],
            "benefits":       rec["benefits"],
            "contexts":       rec["contexts"],
            "trigger_source": rec["trigger_sources"][0],
            "source_url":     rec.get("source_url", ""),
            "last_verified":  rec.get("last_verified", ""),
            "confidence":     rec.get("confidence", "medium"),
        })

    return results
