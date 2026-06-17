"""
Integration tests — full Gemini pipeline (claude_discover_fields).

These tests call the real Gemini API and are SKIPPED unless GEMINI_API_KEY is set.
Run them explicitly:

    GEMINI_API_KEY=<key> pytest tests/test_integration.py -v

Each test loads a fixture, runs full discovery, and asserts that expected field
keys/values appear in the output. Expected fields are conservative — we don't
assert exact values, only that the right keys were found and their values are
non-empty and non-trivial.
"""

import os
import pytest

GEMINI_AVAILABLE = bool(os.environ.get("GEMINI_API_KEY"))
skip_without_gemini = pytest.mark.skipif(
    not GEMINI_AVAILABLE,
    reason="GEMINI_API_KEY not set — skipping integration tests"
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name: str) -> str:
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


def discover(fixture_file: str, source: str):
    """Run full discovery on a fixture and return the field list."""
    import app
    from app import claude_discover_fields, SUPPORTED_SITES
    text = load(fixture_file)
    site_name = next((n for k, n, *_ in SUPPORTED_SITES if k == source), source)
    return claude_discover_fields(text, site_name, source=source)


def keys(fields):
    return {f["key"] for f in fields}


def value_of(fields, key):
    for f in fields:
        if f["key"] == key:
            return f["value"]
    return None


# ── Delta companion certificate ───────────────────────────────────────────────

@skip_without_gemini
def test_delta_finds_medallion_status():
    fields = discover("delta_companion_cert.txt", "delta")
    assert any("medallion" in f.get("value", "").lower() or
               "diamond" in f.get("value", "").lower()
               for f in fields), f"No medallion status found. Got: {fields}"


@skip_without_gemini
def test_delta_finds_miles_balance():
    fields = discover("delta_companion_cert.txt", "delta")
    assert any("45" in f.get("value", "") and "320" in f.get("value", "")
               for f in fields), f"Miles balance 45,320 not found. Got: {fields}"


@skip_without_gemini
def test_delta_finds_companion_certificate():
    fields = discover("delta_companion_cert.txt", "delta")
    companion = [f for f in fields if "companion" in f.get("key", "").lower()
                 or "companion" in f.get("label", "").lower()]
    assert companion, f"No companion certificate field found. Got keys: {keys(fields)}"
    # Value should mention 2026
    assert any("2026" in f.get("value", "") for f in companion), \
        f"Companion cert missing expiry year. Got: {companion}"


@skip_without_gemini
def test_delta_finds_upgrade_certificates():
    fields = discover("delta_companion_cert.txt", "delta")
    upgrades = [f for f in fields if "upgrade" in f.get("key", "").lower()
                or "upgrade" in f.get("label", "").lower()]
    assert upgrades, f"No upgrade certificate field found. Got keys: {keys(fields)}"


@skip_without_gemini
def test_delta_hero_is_not_member_id():
    """First field must be status or balance, not a loyalty ID."""
    fields = discover("delta_companion_cert.txt", "delta")
    assert fields, "No fields returned"
    first = fields[0]
    bad_labels = ("member", "number", "id", "since", "skymiles number")
    label_lower = first.get("label", "").lower()
    assert not any(bad in label_lower for bad in bad_labels), \
        f"Hero field is an ID/metadata field: {first}"


# ── Marriott free night ───────────────────────────────────────────────────────

@skip_without_gemini
def test_marriott_finds_platinum_status():
    fields = discover("marriott_free_night.txt", "marriott")
    assert any("platinum" in f.get("value", "").lower() for f in fields), \
        f"Platinum status not found. Got: {fields}"


@skip_without_gemini
def test_marriott_finds_free_night_award():
    fields = discover("marriott_free_night.txt", "marriott")
    free_night = [f for f in fields if "free" in f.get("key", "").lower()
                  or "night" in f.get("label", "").lower()]
    assert free_night, f"No free night field. Got keys: {keys(fields)}"
    assert any("2026" in f.get("value", "") or "dec" in f.get("value", "").lower()
               for f in free_night), f"Free night missing expiry. Got: {free_night}"


@skip_without_gemini
def test_marriott_finds_points_balance():
    fields = discover("marriott_free_night.txt", "marriott")
    assert any("28,500" in f.get("value", "") or "28500" in f.get("value", "")
               for f in fields), f"Points balance 28,500 not found. Got: {fields}"


# ── Amex credits ──────────────────────────────────────────────────────────────

@skip_without_gemini
def test_amex_finds_points_balance():
    fields = discover("amex_credits.txt", "amex")
    assert any("74,250" in f.get("value", "") or "74250" in f.get("value", "")
               for f in fields), f"74,250 Membership Rewards not found. Got: {fields}"


@skip_without_gemini
def test_amex_finds_dining_credit_remaining():
    fields = discover("amex_credits.txt", "amex")
    dining = [f for f in fields if "dining" in f.get("label", "").lower()
              or "dining" in f.get("key", "").lower()]
    assert dining, f"No dining credit field. Got keys: {keys(fields)}"
    assert any("48" in f.get("value", "") for f in dining), \
        f"Dining credit remaining $48 not found. Got: {dining}"


@skip_without_gemini
def test_amex_finds_hotel_credit_remaining():
    fields = discover("amex_credits.txt", "amex")
    assert any("187" in f.get("value", "") for f in fields), \
        f"Hotel credit remaining $187 not found. Got: {fields}"


@skip_without_gemini
def test_amex_finds_autopay():
    fields = discover("amex_credits.txt", "amex")
    autopay = [f for f in fields if "autopay" in f.get("key", "").lower()
               or "autopay" in f.get("label", "").lower()
               or "auto pay" in f.get("label", "").lower()]
    assert autopay, f"No autopay field. Got keys: {keys(fields)}"


# ── Chase payment ─────────────────────────────────────────────────────────────

@skip_without_gemini
def test_chase_finds_balance():
    fields = discover("chase_payment.txt", "chase")
    assert any("2,472" in f.get("value", "") or "2472" in f.get("value", "")
               for f in fields), f"Balance $2,472.20 not found. Got: {fields}"


@skip_without_gemini
def test_chase_finds_minimum_payment():
    fields = discover("chase_payment.txt", "chase")
    minpay = [f for f in fields if "min" in f.get("key", "").lower()
              or "minimum" in f.get("label", "").lower()]
    assert minpay, f"No minimum payment field. Got keys: {keys(fields)}"


@skip_without_gemini
def test_chase_finds_due_date():
    fields = discover("chase_payment.txt", "chase")
    assert any("jul" in f.get("value", "").lower() or "12" in f.get("value", "")
               for f in fields), f"Due date Jul 12 not found. Got: {fields}"


# ── Xfinity bill ──────────────────────────────────────────────────────────────

@skip_without_gemini
def test_xfinity_finds_amount_due():
    fields = discover("xfinity_bill.txt", "xfinity")
    assert any("157" in f.get("value", "") for f in fields), \
        f"Amount due $157.43 not found. Got: {fields}"


@skip_without_gemini
def test_xfinity_finds_due_date():
    fields = discover("xfinity_bill.txt", "xfinity")
    assert any("jul" in f.get("value", "").lower() or "3" in f.get("value", "")
               for f in fields), f"Due date Jul 3 not found. Got: {fields}"


# ── Noisy marketing — should return nothing useful ────────────────────────────

@skip_without_gemini
def test_noisy_marketing_returns_few_fields():
    """Generic marketing page with no personalized data should yield ≤1 field."""
    fields = discover("noisy_marketing.txt", "delta")  # intentionally wrong source
    assert len(fields) <= 1, \
        f"Noisy marketing page returned {len(fields)} fields (expected 0-1): {fields}"


# ── Confidence and provenance ─────────────────────────────────────────────────

@skip_without_gemini
def test_fields_have_confidence():
    """Gemini should return confidence on every field (v0.3+ prompt)."""
    fields = discover("delta_companion_cert.txt", "delta")
    assert fields, "No fields returned"
    fields_with_confidence = [f for f in fields if "confidence" in f]
    assert len(fields_with_confidence) > 0, \
        "No fields have confidence score — prompt may not be requesting it"


@skip_without_gemini
def test_fields_have_source_snippet():
    """Gemini should return source_snippet on every field."""
    fields = discover("delta_companion_cert.txt", "delta")
    assert fields, "No fields returned"
    fields_with_snippet = [f for f in fields if f.get("source_snippet")]
    assert len(fields_with_snippet) > 0, \
        "No fields have source_snippet — prompt may not be requesting it"


@skip_without_gemini
def test_no_low_confidence_fields_in_amex():
    """All fields from a clean account page should have confidence ≥ 0.75."""
    fields = discover("amex_credits.txt", "amex")
    low = [f for f in fields if isinstance(f.get("confidence"), (int, float))
           and f["confidence"] < 0.75]
    assert len(low) == 0, \
        f"Low-confidence fields found (may be hallucinations): {low}"
