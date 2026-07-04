"""Canonical observation-type catalog for provider coverage analysis.

Adding a new observation type:
  1. Add an entry to OBSERVATION_TYPES.
  2. Map relevant field keys in FIELD_KEY_TO_OBSERVATION.
  3. Add the type to CATEGORY_EXPECTED_OBSERVATIONS (and optional PROVIDER_OVERRIDES).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ObservationType:
    id: str
    label: str
    description: str = ""


OBSERVATION_TYPES: dict[str, ObservationType] = {
    "points_balance": ObservationType(
        "points_balance", "Points balance", "Loyalty or rewards points balance",
    ),
    "miles_balance": ObservationType(
        "miles_balance", "Miles balance", "Airline or travel miles balance",
    ),
    "rewards_balance": ObservationType(
        "rewards_balance", "Rewards balance", "Cashback or generic rewards balance",
    ),
    "membership_status": ObservationType(
        "membership_status", "Membership status", "Active/inactive membership state",
    ),
    "tier": ObservationType(
        "tier", "Tier / elite status", "Elite tier or status level",
    ),
    "expiration_date": ObservationType(
        "expiration_date", "Expiration date", "Status, cert, or benefit expiry",
    ),
    "renewal_date": ObservationType(
        "renewal_date", "Renewal date", "Subscription or membership renewal",
    ),
    "statement_balance": ObservationType(
        "statement_balance", "Statement balance", "Current account or card balance",
    ),
    "minimum_payment": ObservationType(
        "minimum_payment", "Minimum payment", "Minimum payment due amount",
    ),
    "payment_due_date": ObservationType(
        "payment_due_date", "Payment due date", "Next payment due date",
    ),
    "credit_limit": ObservationType(
        "credit_limit", "Credit limit", "Total credit limit",
    ),
    "available_credit": ObservationType(
        "available_credit", "Available credit", "Remaining available credit",
    ),
    "recent_transactions": ObservationType(
        "recent_transactions", "Recent transactions", "Recent account activity",
    ),
    "next_trip": ObservationType(
        "next_trip", "Next trip", "Upcoming travel reservation",
    ),
    "reservation_count": ObservationType(
        "reservation_count", "Reservation count", "Number of active reservations",
    ),
    "account_balance": ObservationType(
        "account_balance", "Account balance", "General account balance",
    ),
    "amount_due": ObservationType(
        "amount_due", "Amount due", "Bill or payment amount due",
    ),
    "due_date": ObservationType(
        "due_date", "Due date", "Bill or payment due date",
    ),
    "policy_status": ObservationType(
        "policy_status", "Policy status", "Insurance policy number or status",
    ),
    "premium": ObservationType(
        "premium", "Premium", "Insurance premium amount",
    ),
    "coverage_detail": ObservationType(
        "coverage_detail", "Coverage detail", "Insurance coverage type or limits",
    ),
    "usage": ObservationType(
        "usage", "Usage", "Service usage this billing period",
    ),
    "plan": ObservationType(
        "plan", "Plan", "Current service or subscription plan",
    ),
    "auto_pay": ObservationType(
        "auto_pay", "Auto-pay", "Auto-pay enrollment status",
    ),
}


# Maps extracted field keys (from pipeline trusted_keys / account items) → observation type.
FIELD_KEY_TO_OBSERVATION: dict[str, str] = {
    "elite_status": "tier",
    "medallion_status": "tier",
    "tier_status": "tier",
    "premier_status": "tier",
    "points_balance": "points_balance",
    "miles_balance": "miles_balance",
    "skymiles": "miles_balance",
    "rewards_balance": "rewards_balance",
    "rewards_points": "rewards_balance",
    "cashback_balance": "rewards_balance",
    "membership_status": "membership_status",
    "membership": "membership_status",
    "expiry_date": "expiration_date",
    "expiration_date": "expiration_date",
    "status_expiry": "expiration_date",
    "renewal_date": "renewal_date",
    "annual_fee": "renewal_date",
    "membership_fee": "renewal_date",
    "current_balance": "statement_balance",
    "statement_balance": "statement_balance",
    "balance": "statement_balance",
    "minimum_payment": "minimum_payment",
    "min_payment": "minimum_payment",
    "payment_due_date": "payment_due_date",
    "due_date": "payment_due_date",
    "credit_limit": "credit_limit",
    "available_credit": "available_credit",
    "recent_transactions": "recent_transactions",
    "transactions": "recent_transactions",
    "upcoming_trips": "next_trip",
    "next_trip": "next_trip",
    "upcoming_trip": "next_trip",
    "reservation_count": "reservation_count",
    "reservations": "reservation_count",
    "checking_balance": "account_balance",
    "savings_balance": "account_balance",
    "account_balance": "account_balance",
    "amount_due": "amount_due",
    "policy_number": "policy_status",
    "policy_status": "policy_status",
    "premium": "premium",
    "next_payment": "due_date",
    "coverage": "coverage_detail",
    "usage": "usage",
    "plan": "plan",
    "auto_pay": "auto_pay",
    "autopay": "auto_pay",
    "certificates": "expiration_date",
    "travel_credits": "rewards_balance",
    "ecredit_balance": "rewards_balance",
    "statement_credits": "rewards_balance",
    "upgrades": "expiration_date",
}


# Default expected observation types per account category.
CATEGORY_EXPECTED_OBSERVATIONS: dict[str, list[str]] = {
    "travel_loyalty": [
        "tier",
        "miles_balance",
        "points_balance",
        "expiration_date",
        "next_trip",
        "reservation_count",
    ],
    "credit_card": [
        "statement_balance",
        "payment_due_date",
        "credit_limit",
        "available_credit",
        "rewards_balance",
        "minimum_payment",
    ],
    "banking": [
        "account_balance",
    ],
    "utilities": [
        "amount_due",
        "due_date",
        "auto_pay",
        "plan",
        "usage",
    ],
    "insurance": [
        "policy_status",
        "premium",
        "due_date",
        "coverage_detail",
        "expiration_date",
    ],
    "shopping": [
        "membership_status",
        "rewards_balance",
        "renewal_date",
    ],
    "automotive": [
        "account_balance",
        "expiration_date",
    ],
    "subscription": [
        "plan",
        "renewal_date",
    ],
    "health": [
        "next_trip",
    ],
}


# Per-provider additions or replacements (engineering overrides).
PROVIDER_OBSERVATION_OVERRIDES: dict[str, dict[str, list[str]]] = {
    "amex": {
        "replace": [
            "points_balance",
            "statement_balance",
            "payment_due_date",
            "credit_limit",
        ],
    },
    "delta": {
        "add": ["rewards_balance"],
    },
}


def observation_label(obs_id: str) -> str:
    entry = OBSERVATION_TYPES.get(obs_id)
    return entry.label if entry else obs_id.replace("_", " ").title()


def field_key_to_observation(field_key: str) -> str | None:
    """Map a single extracted field key to an observation type, if recognized."""
    key = (field_key or "").strip().lower()
    if not key:
        return None
    if key in FIELD_KEY_TO_OBSERVATION:
        return FIELD_KEY_TO_OBSERVATION[key]
    for fragment, obs_id in FIELD_KEY_TO_OBSERVATION.items():
        if fragment in key or key in fragment:
            return obs_id
    return None


def field_keys_to_observations(field_keys: list[str]) -> set[str]:
    """Map extracted field keys to the set of observation types they represent."""
    found: set[str] = set()
    for key in field_keys:
        obs = field_key_to_observation(key)
        if obs:
            found.add(obs)
    return found


def expected_observations_for_provider(source: str, category: str | None) -> list[str]:
    """Return ordered expected observation type ids for a provider."""
    overrides = PROVIDER_OBSERVATION_OVERRIDES.get(source, {})
    if "replace" in overrides:
        return list(overrides["replace"])

    base = list(CATEGORY_EXPECTED_OBSERVATIONS.get(category or "", []))
    for extra in overrides.get("add", []):
        if extra not in base:
            base.append(extra)
    for removed in overrides.get("remove", []):
        if removed in base:
            base.remove(removed)
    return base
