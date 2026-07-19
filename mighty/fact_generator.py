"""
Human-readable factual summaries from snapshot facts.

Descriptive only — never advice, recommendations, rankings, or optimization.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from mighty.provider_connector import MoneyAmount
from mighty.snapshot_diff import (
    PRESCRIPTIVE_FACT_FRAGMENTS,
    Fact,
    FactType,
)


def _as_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, MoneyAmount):
        return value.amount
    if isinstance(value, dict) and "amount" in value:
        try:
            return Decimal(str(value["amount"]))
        except Exception:
            return None
    if isinstance(value, dict) and "balance" in value:
        try:
            return Decimal(str(value["balance"]))
        except Exception:
            return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _format_money_delta(old: Decimal, new: Decimal) -> str:
    delta = new - old
    magnitude = abs(delta)
    signed = f"${magnitude:,.2f}"
    if delta > 0:
        return f"increased by {signed}"
    if delta < 0:
        return f"decreased by {signed}"
    return "unchanged"


def _format_points_delta(old: Decimal, new: Decimal, unit: str) -> str:
    delta = new - old
    magnitude = abs(delta)
    if magnitude == magnitude.to_integral_value():
        amount = f"{int(magnitude):,}"
    else:
        amount = f"{magnitude:,.2f}"
    label = unit or "points"
    if delta > 0:
        return f"increased by {amount} {label}"
    if delta < 0:
        return f"decreased by {amount} {label}"
    return "unchanged"


def _account_name_from_fact(fact: Fact) -> str | None:
    for value in (fact.new_value, fact.old_value):
        if isinstance(value, dict):
            name = value.get("display_name") or value.get("product_name")
            if name:
                return str(name)
    return None


def fact_to_bullet(fact: Fact) -> str:
    """Convert one fact into a single descriptive summary bullet."""
    if fact.fact_type == FactType.NEW_ACCOUNT:
        name = _account_name_from_fact(fact) or "Account"
        return f"New account detected:\n  {name}."

    if fact.fact_type == FactType.ACCOUNT_REMOVED:
        name = _account_name_from_fact(fact) or "Account"
        return f"Account removed:\n  {name}."

    if fact.fact_type == FactType.REWARDS_CHANGED:
        old_balance = _as_decimal(fact.old_value)
        new_balance = _as_decimal(fact.new_value)
        program = "Rewards"
        unit = "points"
        if isinstance(fact.new_value, dict):
            program = str(fact.new_value.get("program_name") or program)
            unit = str(fact.new_value.get("unit") or unit)
        elif isinstance(fact.old_value, dict):
            program = str(fact.old_value.get("program_name") or program)
            unit = str(fact.old_value.get("unit") or unit)
        if old_balance is not None and new_balance is not None:
            return f"{program} {_format_points_delta(old_balance, new_balance, unit)}."
        return fact.explanation

    if fact.fact_type == FactType.BALANCE_CHANGED:
        old_amount = _as_decimal(fact.old_value)
        new_amount = _as_decimal(fact.new_value)
        if old_amount is not None and new_amount is not None:
            return f"Current balance {_format_money_delta(old_amount, new_amount)}."
        return fact.explanation

    if fact.fact_type == FactType.AVAILABLE_CREDIT_CHANGED:
        old_amount = _as_decimal(fact.old_value)
        new_amount = _as_decimal(fact.new_value)
        if old_amount is not None and new_amount is not None:
            return f"Available credit {_format_money_delta(old_amount, new_amount)}."
        return fact.explanation

    if fact.fact_type == FactType.PAYMENT_DUE_CHANGED:
        old_amount = _as_decimal(fact.old_value)
        new_amount = _as_decimal(fact.new_value)
        if old_amount is not None and new_amount is not None:
            return (
                f"Payment due changed from ${old_amount:,.2f} "
                f"to ${new_amount:,.2f}."
            )
        return fact.explanation

    if fact.fact_type == FactType.PAYMENT_DATE_CHANGED:
        if fact.old_value and fact.new_value:
            return (
                f"Payment due date changed from {fact.old_value} "
                f"to {fact.new_value}."
            )
        return "Payment due date changed."

    if fact.fact_type == FactType.ACCOUNT_RENAMED:
        return (
            f"Account renamed from {fact.old_value or 'unnamed'} "
            f"to {fact.new_value or 'unnamed'}."
        )

    if fact.fact_type == FactType.PRODUCT_CHANGED:
        return (
            f"Product changed from {fact.old_value or 'unavailable'} "
            f"to {fact.new_value or 'unavailable'}."
        )

    if fact.fact_type == FactType.FIELD_BECAME_AVAILABLE:
        return fact.explanation.rstrip(".") + "."

    if fact.fact_type == FactType.FIELD_BECAME_UNAVAILABLE:
        return fact.explanation.rstrip(".") + "."

    if fact.fact_type == FactType.LAST_VERIFIED_CHANGED:
        return "Last verified timestamp changed."

    return fact.explanation


def assert_no_advice_language(text: str) -> None:
    lowered = str(text or "").lower()
    for fragment in PRESCRIPTIVE_FACT_FRAGMENTS:
        if fragment in lowered:
            raise ValueError(f"advice_language_detected:{fragment}")


def format_facts_summary(facts: Iterable[Fact]) -> str:
    """
    Build the terminal summary block for changes since the previous refresh.

    Empty fact lists mean no detectable changes (not a first-snapshot case).
    """
    items = list(facts)
    if not items:
        return "No changes since previous refresh."

    lines = ["Changes since previous refresh", ""]
    for fact in items:
        bullet = fact_to_bullet(fact)
        assert_no_advice_language(bullet)
        parts = bullet.split("\n")
        lines.append(f"• {parts[0]}")
        for continuation in parts[1:]:
            lines.append(continuation)
        lines.append("")
    # Trim trailing blank line for cleaner terminal output.
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def format_persisted_refresh_summary(
    *,
    provider_label: str,
    status: str,
    persist_result: object | None,
) -> str:
    """
    Terminal summary for ``connector-refresh --persist``.

    Example shape:

        Amex connector refresh

        Status:
        success

        Snapshot:
        stored

        Changes since previous refresh
        ...
    """
    lines = [
        provider_label,
        "",
        "Status:",
        str(status or "unknown"),
        "",
        "Snapshot:",
    ]
    if persist_result is None:
        lines.append("not stored")
        return "\n".join(lines) + "\n"

    first_snapshot = bool(getattr(persist_result, "first_snapshot", False))
    lines.append("stored")
    lines.append("")
    if first_snapshot:
        lines.append("First snapshot recorded.")
    else:
        summary = str(getattr(persist_result, "summary", "") or "").rstrip()
        if summary:
            lines.append(summary)
        else:
            lines.append("No changes since previous refresh.")
    return "\n".join(lines) + "\n"
