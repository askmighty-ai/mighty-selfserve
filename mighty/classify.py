"""
mighty.classify
───────────────
Canonical benefit-type taxonomy and classifier.

Every item stored in account_data["items"] is stamped with a "_type" field
at ingestion time using classify_benefit().  All dashboard routing reads _type
— no regex scattered across views.

Usage
-----
    from mighty.classify import classify_benefit, BENEFIT_TYPES

    btype = classify_benefit("Companion Certificate", "1", "delta")
    # → "certificate"

Adding rules
------------
Append an entry to _BT_RULES. Order matters — first match wins.
Each rule is a 4-tuple:
    (type_key, label_keywords, value_regex_patterns, exclude_keywords)
"""
import re as _re

# ── Canonical benefit type taxonomy ─────────────────────────────────────────
BENEFIT_TYPES = {
    "elite_status":    "Earned status tier at an airline, hotel, or program",
    "certificate":     "Redeemable award: companion cert, free night, upgrade cert",
    "travel_credit":   "Non-cash airline/hotel credit or voucher",
    "cash_credit":     "Dollar-denominated statement or account credit",
    "points_balance":  "Loyalty points, miles, or rewards balance",
    "progress_toward": "Tracking metric toward a goal (X of Y)",
    "membership":      "Access / lounge / club membership",
    "reservation":     "Upcoming booking or itinerary item",
    "payment_due":     "A bill, balance due, or upcoming payment",
    "renewal":         "Subscription renewal, annual fee, or membership renewal",
    "partner_benefit": "Benefit derived from a cross-program partnership",
    "expiry_date":     "A date or validity field (not a redeemable benefit)",
    "other":           "Does not fit a canonical bucket",
}

# Convenience sets for routing logic
_ACTIONABLE_TYPES   = {"certificate", "travel_credit", "cash_credit"}
_BALANCE_TYPES      = {"points_balance"}
_STATUS_TYPES       = {"elite_status"}
_PROGRESS_TYPES     = {"progress_toward"}
_ATTENTION_TYPES    = {"payment_due", "renewal"}
_MEMBERSHIP_TYPES   = {"membership"}
_PARTNER_TYPES      = {"partner_benefit"}

_BT_RULES = [
    ("progress_toward",
     ["progress","qualifying","segment","toward","threshold","requalif","earned toward",
      "flights required","nights required","stays required","spend required"],
     [r"\b\d[\d,]*\s*(?:of|/)\s*\d[\d,]*\b"], []),
    ("elite_status",
     ["status","medallion","tier","elite","level","diamond","platinum","gold","silver",
      "globalist","titanium","sapphire","senator","chairman","premier","executive",
      "ambassador","rouge","velocity","bronze"],
     [],
     ["progress","qualifying","toward","credit","certificate","cert","voucher",
      "upgrade","award","free night","points","miles","balance","reservation","booking",
      "autopay","auto-pay","payment","bill","subscription","service","account status",
      "paperless","enrolled","enabled","active","loyalty number","member id"]),
    ("certificate",
     ["certificate","cert","companion","free night","award night","upgrade",
      "systemwide upgrade","global upgrade","regional upgrade","suite upgrade",
      "e-certificate","ecertificate","reward night","travel reward"],
     [],
     ["progress","qualifying","toward","balance","points","miles","credit","ecredit"]),
    ("payment_due",
     ["amount due","balance due","payment due","due date","next payment","autopay",
      "auto-pay","auto pay","bill amount","monthly bill","minimum payment",
      "statement balance","past due","current charges"],
     [], ["credit card reward","cashback","ecredit","points"]),
    ("renewal",
     ["renewal","renews","renewal date","next renewal","annual fee",
      "membership fee","membership renewal","plan renews","plan renewal"],
     [], ["progress","points","miles","status","certificate"]),
    ("partner_benefit",
     ["partner benefit","derived benefit","partnership","via status","unlocked by",
      "because of your","through your"],
     [], []),
    ("cash_credit",
     ["statement credit","annual credit","annual travel credit","cashback","cash back",
      "reward credit","hotel credit","dining credit","entertainment credit","streaming credit",
      "wireless credit","global entry credit","tsa precheck credit","annual"],
     [r"\$\d"],
     ["ecredit","e-credit","flight credit","voucher","certificate","points","miles",
      "renewal","fee","subscription"]),
    ("travel_credit",
     ["ecredit","e-credit","travel credit","flight credit","airline credit",
      "transportation credit","travel voucher","residual credit"],
     [], ["statement","cashback","cash back","reward credit","hotel credit"]),
    ("membership",
     ["priority pass","lounge","club access","admirals club","centurion lounge",
      "skyclub","united club","clear","global entry","nexus","tsa precheck",
      "precheck","membership","access"],
     [], ["progress","status","balance","points","miles"]),
    ("reservation",
     ["upcoming","reservation","booking","itinerary","check-in","check-out",
      "arrival","departure","stay","trip","hotel stay","car rental"],
     [], ["progress","balance","points","miles","status","credit","certificate"]),
    ("expiry_date",
     ["expir","valid until","valid through","valid thru","use by","good through","expires"],
     [], []),
    ("points_balance",
     ["miles","points","rewards","balance","skypass","rapid rewards","mileageplus",
      "aadvantage","skymiles","thankyou","membership rewards","cashback balance",
      "wallet","coins","cash rewards"],
     [], ["progress","toward","status","certificate","cert","credit","ecredit"]),
]

def is_actionable(btype: str) -> bool:
    """True for types users can redeem: certificates, credits."""
    return btype in _ACTIONABLE_TYPES

def is_balance(btype: str) -> bool:
    return btype in _BALANCE_TYPES

def is_status(btype: str) -> bool:
    return btype in _STATUS_TYPES

def is_needs_attention(btype: str) -> bool:
    """True for payment_due and renewal — surface in Action Center."""
    return btype in _ATTENTION_TYPES


def classify_benefit(label: str, value: str, source: str = "") -> str:
    """Return a canonical BENEFIT_TYPES key. First matching rule wins."""
    import re as _bt_re
    combined = (label + " " + value).lower()
    lbl_lc   = label.lower()
    val_lc   = value.lower()
    for btype, label_kws, val_patterns, excludes in _BT_RULES:
        if any(ex in combined for ex in excludes):
            continue
        lbl_match = any(kw in lbl_lc for kw in label_kws)
        val_match  = any(_bt_re.search(p, val_lc) for p in val_patterns) if val_patterns else False
        if lbl_match or (val_patterns and val_match):
            return btype
    return "other"

