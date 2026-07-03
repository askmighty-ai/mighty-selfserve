"""Tests for the provider-agnostic intelligence layer."""

import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.intelligence import (
    build_intelligence_input,
    infer_intelligence,
)
from mighty.provider_account import ProviderAccount


def _field(key: str, label: str, value: str, *, btype: str) -> dict:
    return {"key": key, "label": label, "value": value, "_type": btype}


def _hilton_account():
    return ProviderAccount(
        source="hilton",
        normalized_fields=[
            _field("points_balance", "Points Balance", "112,400", btype="points_balance"),
            _field("status", "Status", "Diamond", btype="elite_status"),
            _field("free_night", "Free Night Certificate", "Expires Aug 2026", btype="certificate"),
        ],
    )


def _delta_account():
    return ProviderAccount(
        source="delta",
        normalized_fields=[
            _field("miles", "SkyMiles Balance", "84,200", btype="points_balance"),
            _field("status", "Medallion Status", "Gold", btype="elite_status"),
            _field("companion", "Companion Certificate", "Expires Dec 2026", btype="certificate"),
            _field("trip", "Upcoming Trip", "ATL → JFK, Jul 12", btype="upcoming_event"),
        ],
    )


def _amex_account():
    return ProviderAccount(
        source="amex",
        normalized_fields=[
            _field("points", "Membership Rewards", "124,350", btype="points_balance"),
            _field("airline_credit", "Airline Fee Credit", "$200 remaining", btype="travel_credit"),
            _field("fhr", "Fine Hotels + Resorts", "Eligible", btype="partner_benefit"),
            _field("annual_fee", "Annual Fee", "$695 due Jan 2027", btype="renewal"),
            _field("autopay", "Autopay", "Enabled", btype="payment_due"),
        ],
    )


def _past_due_card_account():
    return ProviderAccount(
        source="chase",
        normalized_fields=[
            _field("balance", "Statement Balance", "$2,430", btype="payment_due"),
            _field("past_due", "Payment Status", "Past due", btype="payment_due"),
        ],
    )


def test_infer_intelligence_from_multi_account_traveler():
    input_data = build_intelligence_input(
        [_hilton_account(), _delta_account(), _amex_account()],
        intent_summary={"hotel": 6, "flight": 4},
        type_affinity={"certificate": 3.0, "cash_credit": 1.0},
    )
    profile = infer_intelligence(input_data)

    assert profile.travel_profile.primary_domain.value == "hotel"
    assert profile.travel_profile.travel_frequency.value in {"regular", "frequent"}
    assert profile.hotel_preferences.preferred_brands.value == "hilton"
    assert "hilton:diamond" in profile.hotel_preferences.elite_tiers.value
    assert profile.hotel_preferences.booking_approach.value == "certificate_and_portal"
    assert profile.spending_strategy.primary_mode.value in {"hybrid", "points_first"}
    assert profile.loyalty_strategy.accumulation_style.value in {
        "dual_program", "multi_program", "status_chasing"
    }
    assert profile.loyalty_strategy.redemption_pressure.value in {
        "use_awards", "time_sensitive"
    }
    assert profile.risk_profile.financial_risk.value == "low"
    assert profile.risk_profile.payment_health.value == "autopay_enabled"

    payload = profile.to_dict()
    assert set(payload.keys()) == {
        "travel_profile",
        "hotel_preferences",
        "spending_strategy",
        "loyalty_strategy",
        "risk_profile",
    }
    assert payload["travel_profile"]["primary_domain"]["confidence"] in {"low", "medium", "high"}


def test_infer_intelligence_flags_payment_risk():
    input_data = build_intelligence_input([_past_due_card_account()])
    profile = infer_intelligence(input_data)

    assert profile.risk_profile.financial_risk.value == "high"
    assert profile.risk_profile.financial_risk.confidence == "high"
    assert "past_due_payments" in profile.risk_profile.attention_areas.value


def test_infer_intelligence_empty_accounts_returns_unknowns():
    input_data = build_intelligence_input([])
    profile = infer_intelligence(input_data)

    assert profile.travel_profile.primary_domain.value == "unknown"
    assert profile.hotel_preferences.preferred_brands.value == "none_detected"
    assert profile.spending_strategy.primary_mode.value == "unknown"
    assert profile.loyalty_strategy.accumulation_style.value == "single_program"
    assert profile.risk_profile.payment_health.value == "unknown"
