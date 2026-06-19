"""
mighty.partnerships
───────────────────
Cross-program partnership graph and card-recommendation knowledge base.

Two functions are exported:

  get_derived_benefits(items, connected_sources, context)
    → list[dict]   perks unlocked by accounts the user already has
                   (e.g. Delta Gold → Hertz Gold Plus)

  get_card_recommendations(connected_sources)
    → list[dict]   factual card/product options the user may not have,
                   triggered by accounts they do have
                   (e.g. has JetBlue → JetBlue Plus Card facts)

No dollar-value estimates are included — only factual, published benefit
descriptions directly from each program's terms.
"""
from __future__ import annotations

# ── Partnership graph ────────────────────────────────────────────────────────
# Keyed by source pattern (substring-matched against account source keys).
# Each source maps to contexts, which map to a list of partnership rules.
#
# Rule fields:
#   tiers        – status keywords that must appear in the stored value.
#                  Empty list = no status check (card benefit, not tier-based).
#   program      – third-party program granting the perk.
#   benefit      – short name of the perk.
#   detail       – one-sentence factual description.
#   enroll_hint  – where to activate (no URL shorteners, no paid links).

PARTNERSHIPS: dict[str, dict[str, list[dict]]] = {

    # ── Airlines → car rental ────────────────────────────────────────────────

    "delta": {
        "car": [
            {
                "tiers": ["silver", "gold", "platinum", "diamond"],
                "program": "Hertz",
                "benefit": "Gold Plus Rewards status",
                "detail": "Skip the counter and go straight to your car.",
                "enroll_hint": "Enroll at delta.com/hertz",
            },
            {
                "tiers": ["platinum", "diamond"],
                "program": "Hertz",
                "benefit": "Five Star status",
                "detail": "Guaranteed upgrades and access to a wider selection of premium vehicles.",
                "enroll_hint": "Enroll at delta.com/hertz",
            },
        ],
    },

    "united": {
        "car": [
            {
                "tiers": ["silver", "gold", "platinum", "1k"],
                "program": "Hertz",
                "benefit": "Gold Plus Rewards status",
                "detail": "No counter wait — go directly to your car.",
                "enroll_hint": "Link at united.com/hertz",
            },
            {
                "tiers": ["1k"],
                "program": "Hertz",
                "benefit": "Five Star status",
                "detail": "Guaranteed upgrade and priority vehicle selection.",
                "enroll_hint": "Link at united.com/hertz",
            },
        ],
    },

    "american": {
        "car": [
            {
                "tiers": ["gold", "platinum"],
                "program": "Hertz",
                "benefit": "Five Star status",
                "detail": "Guaranteed upgrades and access to premium vehicles.",
                "enroll_hint": "Link at aa.com/hertz",
            },
            {
                "tiers": ["platinum pro", "executive platinum"],
                "program": "Hertz",
                "benefit": "President's Circle status",
                "detail": "Top-tier status — guaranteed upgrades, choice of any car in the lot.",
                "enroll_hint": "Link at aa.com/hertz",
            },
        ],
        "hotel": [
            {
                "tiers": ["executive platinum"],
                "program": "Marriott Bonvoy",
                "benefit": "Gold Elite status",
                "detail": "Complimentary room upgrades and bonus points when available.",
                "enroll_hint": "Link your AAdvantage account in Marriott Bonvoy settings",
            },
        ],
    },

    "alaska": {
        "car": [
            {
                "tiers": ["mvp", "gold", "75k"],
                "program": "Hertz",
                "benefit": "Gold Plus Rewards status",
                "detail": "Skip the counter, go straight to your car.",
                "enroll_hint": "Link at alaskaair.com/hertz",
            },
        ],
    },

    "southwest": {
        "car": [
            {
                "tiers": ["a-list", "a-list preferred", "companion pass"],
                "program": "Hertz",
                "benefit": "Gold Plus Rewards status",
                "detail": "No counter wait on Southwest partner rentals.",
                "enroll_hint": "Link at southwest.com/hertz",
            },
        ],
    },

    # ── Hotel programs → car rental ──────────────────────────────────────────

    "hyatt": {
        "car": [
            {
                "tiers": ["globalist"],
                "program": "Hertz",
                "benefit": "President's Circle status",
                "detail": "Top-tier status — guaranteed upgrades, choice of any car in the lot.",
                "enroll_hint": "Link in your World of Hyatt account settings",
            },
        ],
        "flight": [
            {
                "tiers": ["globalist"],
                "program": "American Airlines",
                "benefit": "AAdvantage miles on Hyatt stays",
                "detail": "Earn AAdvantage miles instead of Hyatt points on qualifying stays.",
                "enroll_hint": "Link in your World of Hyatt account settings",
            },
        ],
    },

    "marriott": {
        "car": [
            {
                "tiers": ["platinum", "titanium", "ambassador"],
                "program": "Hertz",
                "benefit": "Gold Plus Rewards status",
                "detail": "Skip the counter and go directly to your car.",
                "enroll_hint": "Link in your Marriott Bonvoy account",
            },
        ],
    },

    "hilton": {
        "car": [
            {
                "tiers": ["gold", "diamond"],
                "program": "Hertz",
                "benefit": "Gold Plus Rewards status",
                "detail": "Counter-bypass on Hertz rentals.",
                "enroll_hint": "Link in your Hilton Honors account",
            },
        ],
    },

    # ── Credit cards → car rental / hotel ────────────────────────────────────
    # tiers: [] means the benefit applies to any cardholder, no status required.

    "amex_platinum": {
        "car": [
            {
                "tiers": [],
                "program": "Hertz",
                "benefit": "President's Circle status",
                "detail": "Top-tier status — guaranteed upgrades, any car in the lot.",
                "enroll_hint": "Enroll via your Amex Platinum card benefits portal",
            },
            {
                "tiers": [],
                "program": "Avis",
                "benefit": "President's Club status",
                "detail": "Complimentary upgrades and priority service at the counter.",
                "enroll_hint": "Enroll via your Amex Platinum card benefits portal",
            },
            {
                "tiers": [],
                "program": "National",
                "benefit": "Emerald Club Executive status",
                "detail": "Choose any car in the Executive Aisle — no counter, no waiting.",
                "enroll_hint": "Enroll via your Amex Platinum card benefits portal",
            },
        ],
        "hotel": [
            {
                "tiers": [],
                "program": "Hilton Honors",
                "benefit": "Gold status",
                "detail": "Complimentary breakfast at most properties, room upgrades, and 80% bonus points.",
                "enroll_hint": "Enroll via your Amex Platinum card benefits portal",
            },
            {
                "tiers": [],
                "program": "Marriott Bonvoy",
                "benefit": "Gold Elite status",
                "detail": "Complimentary room upgrades and enhanced point earning.",
                "enroll_hint": "Enroll via your Amex Platinum card benefits portal",
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
                "enroll_hint": "Enroll via your Amex Gold card benefits portal",
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
                "enroll_hint": "Activate via Chase Ultimate Rewards or chase.com/national",
            },
        ],
        "flight": [
            {
                "tiers": [],
                "program": "Priority Pass",
                "benefit": "Select membership",
                "detail": "Access 1,300+ airport lounges worldwide at no additional charge.",
                "enroll_hint": "Activate in your Chase Sapphire Reserve card benefits",
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
                "enroll_hint": "Activate via Chase Ultimate Rewards",
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
            },
            {
                "tiers": [],
                "program": "National",
                "benefit": "Emerald Club Executive status",
                "detail": "Choose any car in the Executive Aisle.",
                "enroll_hint": "Link via your Citi Prestige card benefits",
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
                "enroll_hint": "Activate via your Citi AAdvantage Executive card",
            },
        ],
    },

    "delta_reserve": {
        "flight": [
            {
                "tiers": [],
                "program": "Delta Sky Club",
                "benefit": "Access when flying Delta",
                "detail": "Access to Delta Sky Club lounges on days you fly Delta-operated flights.",
                "enroll_hint": "Show your card at any Delta Sky Club entrance",
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
            },
        ],
    },
}


# ── Card recommendations ─────────────────────────────────────────────────────
# Each entry is surfaced when the user has a trigger_source account but
# does not appear to have the card (based on skip_if_sources).
# Benefits are factual, published descriptions — no speculative value estimates.

CARD_RECOMMENDATIONS: list[dict] = [
    {
        "id": "jetblue_plus",
        "card_name": "JetBlue Plus Card",
        "issuer": "Barclays",
        "trigger_sources": ["jetblue"],
        "skip_if_sources": ["jetblue_plus", "jetblue_card", "jetblue_mastercard", "jetblue_biz"],
        "contexts": ["flight"],
        "benefits": [
            "Free first checked bag for you and up to 3 companions on every JetBlue-operated flight",
            "Annual companion certificate after your account anniversary",
            "6x points on JetBlue purchases, 2x on dining and grocery store purchases",
            "50% savings on eligible inflight food and drink purchases",
            "Mosaic 1 status — priority boarding and security where available",
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
        "benefits": [
            "7,500 anniversary bonus points each year",
            "4 upgraded boardings per year when available",
            "20% back on inflight purchases as a statement credit",
            "3x points on Southwest purchases",
            "Each card purchase counts toward Companion Pass earning",
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
        "benefits": [
            "Companion fare each account anniversary — a second ticket for a companion on any Alaska flight",
            "Free first checked bag for you and up to 6 guests on Alaska flights",
            "3x miles on Alaska Airlines purchases",
            "Priority boarding and 20% back on Alaska inflight purchases",
        ],
    },
    {
        "id": "aa_aviator_red",
        "card_name": "Citi AAdvantage Platinum Select Card",
        "issuer": "Citi",
        "trigger_sources": ["american", "aadvantage"],
        "skip_if_sources": ["aa_card", "aadvantage_card", "citi_aadvantage",
                            "aviator", "aa_platinum", "aa_executive"],
        "contexts": ["flight"],
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
        "benefits": [
            "One free night award each account anniversary (up to 35,000 points redemption value)",
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
        "benefits": [
            "Annual free night at IHG hotels after each account anniversary",
            "Automatic Platinum Elite status",
            "10x points at IHG Hotels and Resorts",
            "4th reward night free when you redeem points for 3 consecutive nights",
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
        "benefits": [
            "5% back on Amazon.com and Whole Foods Market purchases (Prime membership required)",
            "5% back on Chase Travel purchases",
            "2% back at restaurants, gas stations, and on local transit and commuting",
            "No foreign transaction fees",
        ],
    },
    {
        "id": "apple_card",
        "card_name": "Apple Card",
        "issuer": "Goldman Sachs",
        "trigger_sources": ["apple"],
        "skip_if_sources": ["apple_card"],
        "contexts": ["shopping"],
        "benefits": [
            "3% Daily Cash back on Apple purchases and select partner merchants",
            "2% Daily Cash back on all Apple Pay purchases",
            "No annual fee, no foreign transaction fees, no late fees",
            "Daily Cash deposited every day, not at end of month",
        ],
    },
    {
        "id": "costco_anywhere_visa",
        "card_name": "Costco Anywhere Visa Card",
        "issuer": "Citi",
        "trigger_sources": ["costco"],
        "skip_if_sources": ["costco_card", "costco_visa", "costco_anywhere"],
        "contexts": ["shopping"],
        "benefits": [
            "4% cash back on eligible gas and EV charging purchases (up to $7,000/year, then 1%)",
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
      program, benefit, detail, enroll_hint, _score, _why
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

    # Also consider card-type sources with no status check (tiers=[])
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
                tier_ok     = False
                status_label = ""
                status_val   = ""
                display_name = user_src.replace("_", " ").title()

                if not rule["tiers"]:
                    # Card benefit — no tier needed
                    tier_ok = True
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
                    "account":      display_name,
                    "source":       user_src,
                    "label":        f"{rule['program']} — {rule['benefit']}",
                    "value":        status_val or "Included",
                    "derived":      True,
                    "via_status":   status_label or None,
                    "program":      rule["program"],
                    "benefit":      rule["benefit"],
                    "detail":       rule.get("detail", ""),
                    "enroll_hint":  rule.get("enroll_hint", ""),
                    "_score":       0.85,
                    "_why": {
                        "intent_factor":      0.9,
                        "value_factor":       0.7,
                        "urgency_factor":     0.0,
                        "confidence_factor":  0.9,
                    },
                })

    return results


def get_card_recommendations(connected_sources: list[str]) -> list[dict]:
    """
    Return factual card/account options the user may not have,
    triggered by accounts they do have.

    Does NOT include any speculative dollar-value estimates.
    """
    sources_lc = [s.lower().replace("-", "_") for s in connected_sources]
    results: list[dict] = []

    for rec in CARD_RECOMMENDATIONS:
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
        })

    return results
