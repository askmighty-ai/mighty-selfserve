"""Internal catalog of recommendation types and their observation requirements.

Each recommendation defines required observation groups: every group must be
satisfied, and within a group any one observation is enough (OR).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecommendationType:
    id: str
    title: str
    description: str
    required_groups: tuple[tuple[str, ...], ...]
    optional: tuple[str, ...] = ()
    category: str = "general"
    value_score: int = 0


RECOMMENDATION_TYPES: dict[str, RecommendationType] = {
    "payment_due": RecommendationType(
        id="payment_due",
        title="Payment due",
        description="Remind user before a credit card or bill payment is due",
        required_groups=(
            ("payment_due_date",),
            ("statement_balance", "amount_due"),
        ),
        category="credit_card",
        value_score=90,
    ),
    "expiring_value": RecommendationType(
        id="expiring_value",
        title="Expiring value",
        description="Alert when points, miles, or status benefits are expiring",
        required_groups=(
            ("expiration_date",),
            ("rewards_balance", "points_balance", "miles_balance"),
        ),
        category="loyalty",
        value_score=85,
    ),
    "status_progress": RecommendationType(
        id="status_progress",
        title="Status progress",
        description="Show progress toward next elite tier or status milestone",
        required_groups=(
            ("tier",),
            ("points_balance", "miles_balance"),
        ),
        category="loyalty",
        value_score=70,
    ),
    "upcoming_trip": RecommendationType(
        id="upcoming_trip",
        title="Upcoming trip",
        description="Surface helpful actions before an upcoming reservation",
        required_groups=(("next_trip",),),
        category="travel",
        value_score=75,
    ),
    "autopay_missing": RecommendationType(
        id="autopay_missing",
        title="Auto-pay missing",
        description="Suggest enabling auto-pay when a balance is due",
        required_groups=(
            ("auto_pay",),
            ("amount_due", "statement_balance"),
        ),
        category="credit_card",
        value_score=60,
    ),
    "credit_limit_warning": RecommendationType(
        id="credit_limit_warning",
        title="Credit limit warning",
        description="Warn when available credit is running low",
        required_groups=(
            ("credit_limit",),
            ("available_credit",),
        ),
        category="credit_card",
        value_score=80,
    ),
    "subscription_renewal": RecommendationType(
        id="subscription_renewal",
        title="Subscription renewal",
        description="Heads-up before a subscription or membership renews",
        required_groups=(
            ("renewal_date",),
            ("plan",),
        ),
        category="subscription",
        value_score=65,
    ),
    "insurance_payment_due": RecommendationType(
        id="insurance_payment_due",
        title="Insurance payment due",
        description="Remind user before an insurance premium is due",
        required_groups=(
            ("premium",),
            ("due_date",),
        ),
        category="insurance",
        value_score=85,
    ),
}


def all_recommendation_types() -> list[RecommendationType]:
    return list(RECOMMENDATION_TYPES.values())


def recommendation_title(rec_id: str) -> str:
    rec = RECOMMENDATION_TYPES.get(rec_id)
    return rec.title if rec else rec_id.replace("_", " ").title()
