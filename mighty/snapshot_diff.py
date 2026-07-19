"""
Provider-independent snapshot diff → factual change records.

Compares two canonical AccountSnapshot objects from the same provider/customer.
Produces descriptive facts only — never advice, rankings, or recommendations.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from mighty.provider_connector import (
    AccountSnapshot,
    FinancialAccount,
    MoneyAmount,
    RewardsBalance,
)


class FactType(str, Enum):
    NEW_ACCOUNT = "NEW_ACCOUNT"
    ACCOUNT_REMOVED = "ACCOUNT_REMOVED"
    BALANCE_CHANGED = "BALANCE_CHANGED"
    AVAILABLE_CREDIT_CHANGED = "AVAILABLE_CREDIT_CHANGED"
    PAYMENT_DUE_CHANGED = "PAYMENT_DUE_CHANGED"
    PAYMENT_DATE_CHANGED = "PAYMENT_DATE_CHANGED"
    REWARDS_CHANGED = "REWARDS_CHANGED"
    ACCOUNT_RENAMED = "ACCOUNT_RENAMED"
    PRODUCT_CHANGED = "PRODUCT_CHANGED"
    FIELD_BECAME_AVAILABLE = "FIELD_BECAME_AVAILABLE"
    FIELD_BECAME_UNAVAILABLE = "FIELD_BECAME_UNAVAILABLE"
    LAST_VERIFIED_CHANGED = "LAST_VERIFIED_CHANGED"


# Language that must never appear in fact explanations (descriptive only).
PRESCRIPTIVE_FACT_FRAGMENTS = (
    "you should",
    "we recommend",
    "recommend",
    "pay now",
    "pay this",
    "redeem",
    "optimize",
    "optimization",
    "best card",
    "rank",
)


@dataclass(frozen=True)
class Fact:
    """One provider-independent factual change between two snapshots."""

    fact_id: str
    snapshot_before: str
    snapshot_after: str
    provider: str
    fact_type: FactType
    observed_at: str
    old_value: Any
    new_value: Any
    confidence: str
    explanation: str
    account_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "snapshot_before": self.snapshot_before,
            "snapshot_after": self.snapshot_after,
            "provider": self.provider,
            "account_id": self.account_id,
            "fact_type": self.fact_type.value,
            "observed_at": self.observed_at,
            "old_value": _jsonable(self.old_value),
            "new_value": _jsonable(self.new_value),
            "confidence": self.confidence,
            "explanation": self.explanation,
        }


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, MoneyAmount):
        return value.to_dict()
    if isinstance(value, Decimal):
        return format(value, "f")
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


def _money_equal(left: MoneyAmount | None, right: MoneyAmount | None) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return left.amount == right.amount and left.currency == right.currency


def _format_money(money: MoneyAmount | None) -> str:
    if money is None:
        return "unavailable"
    return f"${money.amount:,.2f}"


def _format_points(balance: Decimal, unit: str) -> str:
    quantized = balance
    if quantized == quantized.to_integral_value():
        return f"{int(quantized):,} {unit}"
    return f"{quantized:,.2f} {unit}"


def _account_label(account: FinancialAccount) -> str:
    name = (account.display_name or account.product_name or "Account").strip()
    return name or "Account"


def _make_fact(
    *,
    before_id: str,
    after_id: str,
    provider: str,
    fact_type: FactType,
    observed_at: str,
    old_value: Any,
    new_value: Any,
    explanation: str,
    account_id: str | None = None,
    confidence: str = "high",
) -> Fact:
    text = str(explanation or "").strip()
    lowered = text.lower()
    if any(fragment in lowered for fragment in PRESCRIPTIVE_FACT_FRAGMENTS):
        raise ValueError(f"prescriptive_fact_language:{text}")
    return Fact(
        fact_id=str(uuid.uuid4()),
        snapshot_before=before_id,
        snapshot_after=after_id,
        provider=provider,
        account_id=account_id,
        fact_type=fact_type,
        observed_at=observed_at,
        old_value=old_value,
        new_value=new_value,
        confidence=confidence,
        explanation=text,
    )


def _index_accounts(
    accounts: tuple[FinancialAccount, ...],
) -> dict[str, FinancialAccount]:
    """Stable matching by opaque provider_account_id only."""
    indexed: dict[str, FinancialAccount] = {}
    for account in accounts:
        key = str(account.provider_account_id or "").strip()
        if not key:
            continue
        indexed[key] = account
    return indexed


def _index_rewards(
    rewards: tuple[RewardsBalance, ...],
) -> dict[str, RewardsBalance]:
    indexed: dict[str, RewardsBalance] = {}
    for reward in rewards:
        key = str(reward.program_name or "").strip().lower()
        if not key:
            continue
        indexed[key] = reward
    return indexed


def _field_presence_facts(
    *,
    before: FinancialAccount,
    after: FinancialAccount,
    before_id: str,
    after_id: str,
    provider: str,
    observed_at: str,
    account_id: str,
    label: str,
) -> list[Fact]:
    facts: list[Fact] = []
    fields = (
        ("current_balance", before.current_balance, after.current_balance, "Current balance"),
        ("available_credit", before.available_credit, after.available_credit, "Available credit"),
        (
            "payment_due_amount",
            before.payment_due_amount,
            after.payment_due_amount,
            "Payment due amount",
        ),
        (
            "payment_due_date",
            before.payment_due_date,
            after.payment_due_date,
            "Payment due date",
        ),
    )
    for field_name, old, new, field_label in fields:
        if old is None and new is not None:
            facts.append(
                _make_fact(
                    before_id=before_id,
                    after_id=after_id,
                    provider=provider,
                    fact_type=FactType.FIELD_BECAME_AVAILABLE,
                    observed_at=observed_at,
                    old_value=None,
                    new_value={"field": field_name, "value": _jsonable(new)},
                    explanation=f"{field_label} became available for {label}.",
                    account_id=account_id,
                )
            )
        elif old is not None and new is None:
            facts.append(
                _make_fact(
                    before_id=before_id,
                    after_id=after_id,
                    provider=provider,
                    fact_type=FactType.FIELD_BECAME_UNAVAILABLE,
                    observed_at=observed_at,
                    old_value={"field": field_name, "value": _jsonable(old)},
                    new_value=None,
                    explanation=f"{field_label} became unavailable for {label}.",
                    account_id=account_id,
                )
            )
    return facts


def _diff_matched_account(
    *,
    before: FinancialAccount,
    after: FinancialAccount,
    before_id: str,
    after_id: str,
    provider: str,
    observed_at: str,
) -> list[Fact]:
    facts: list[Fact] = []
    account_id = after.provider_account_id or before.provider_account_id
    label = _account_label(after) or _account_label(before)

    if (before.display_name or "") != (after.display_name or ""):
        facts.append(
            _make_fact(
                before_id=before_id,
                after_id=after_id,
                provider=provider,
                fact_type=FactType.ACCOUNT_RENAMED,
                observed_at=observed_at,
                old_value=before.display_name,
                new_value=after.display_name,
                explanation=(
                    f"Account renamed from {before.display_name or 'unnamed'} "
                    f"to {after.display_name or 'unnamed'}."
                ),
                account_id=account_id,
            )
        )

    if (before.product_name or "") != (after.product_name or ""):
        facts.append(
            _make_fact(
                before_id=before_id,
                after_id=after_id,
                provider=provider,
                fact_type=FactType.PRODUCT_CHANGED,
                observed_at=observed_at,
                old_value=before.product_name,
                new_value=after.product_name,
                explanation=(
                    f"Product changed from {before.product_name or 'unavailable'} "
                    f"to {after.product_name or 'unavailable'} for {label}."
                ),
                account_id=account_id,
            )
        )

    facts.extend(
        _field_presence_facts(
            before=before,
            after=after,
            before_id=before_id,
            after_id=after_id,
            provider=provider,
            observed_at=observed_at,
            account_id=account_id,
            label=label,
        )
    )

    if (
        before.current_balance is not None
        and after.current_balance is not None
        and not _money_equal(before.current_balance, after.current_balance)
    ):
        facts.append(
            _make_fact(
                before_id=before_id,
                after_id=after_id,
                provider=provider,
                fact_type=FactType.BALANCE_CHANGED,
                observed_at=observed_at,
                old_value=before.current_balance,
                new_value=after.current_balance,
                explanation=(
                    f"Current balance for {label} changed from "
                    f"{_format_money(before.current_balance)} to "
                    f"{_format_money(after.current_balance)}."
                ),
                account_id=account_id,
            )
        )

    if (
        before.available_credit is not None
        and after.available_credit is not None
        and not _money_equal(before.available_credit, after.available_credit)
    ):
        facts.append(
            _make_fact(
                before_id=before_id,
                after_id=after_id,
                provider=provider,
                fact_type=FactType.AVAILABLE_CREDIT_CHANGED,
                observed_at=observed_at,
                old_value=before.available_credit,
                new_value=after.available_credit,
                explanation=(
                    f"Available credit for {label} changed from "
                    f"{_format_money(before.available_credit)} to "
                    f"{_format_money(after.available_credit)}."
                ),
                account_id=account_id,
            )
        )

    if (
        before.payment_due_amount is not None
        and after.payment_due_amount is not None
        and not _money_equal(before.payment_due_amount, after.payment_due_amount)
    ):
        facts.append(
            _make_fact(
                before_id=before_id,
                after_id=after_id,
                provider=provider,
                fact_type=FactType.PAYMENT_DUE_CHANGED,
                observed_at=observed_at,
                old_value=before.payment_due_amount,
                new_value=after.payment_due_amount,
                explanation=(
                    f"Payment due for {label} changed from "
                    f"{_format_money(before.payment_due_amount)} to "
                    f"{_format_money(after.payment_due_amount)}."
                ),
                account_id=account_id,
            )
        )

    if (
        before.payment_due_date is not None
        and after.payment_due_date is not None
        and before.payment_due_date != after.payment_due_date
    ):
        facts.append(
            _make_fact(
                before_id=before_id,
                after_id=after_id,
                provider=provider,
                fact_type=FactType.PAYMENT_DATE_CHANGED,
                observed_at=observed_at,
                old_value=before.payment_due_date.isoformat(),
                new_value=after.payment_due_date.isoformat(),
                explanation=(
                    f"Payment due date for {label} changed from "
                    f"{before.payment_due_date.isoformat()} to "
                    f"{after.payment_due_date.isoformat()}."
                ),
                account_id=account_id,
            )
        )

    return facts


def diff_snapshots(
    before: AccountSnapshot,
    after: AccountSnapshot,
    *,
    previous_id: str | None = None,
    after_id: str | None = None,
) -> list[Fact]:
    """
    Compare two snapshots and return provider-independent facts.

    Matching uses opaque ``provider_account_id`` (accounts) and ``program_name``
    (rewards). Display names / last-four are never used as match keys.
    """
    if before.provider != after.provider:
        raise ValueError(
            f"snapshot_provider_mismatch:{before.provider}:{after.provider}"
        )

    before_id = previous_id or f"before:{before.observed_at}"
    after_snapshot_id = after_id or f"after:{after.observed_at}"
    observed_at = after.observed_at or before.observed_at
    provider = after.provider
    facts: list[Fact] = []

    before_accounts = _index_accounts(before.accounts)
    after_accounts = _index_accounts(after.accounts)

    for account_id, account in after_accounts.items():
        if account_id not in before_accounts:
            label = _account_label(account)
            facts.append(
                _make_fact(
                    before_id=before_id,
                    after_id=after_snapshot_id,
                    provider=provider,
                    fact_type=FactType.NEW_ACCOUNT,
                    observed_at=observed_at,
                    old_value=None,
                    new_value={
                        "provider_account_id": account.provider_account_id,
                        "display_name": account.display_name,
                        "product_name": account.product_name,
                    },
                    explanation=f"New account detected: {label}.",
                    account_id=account_id,
                )
            )

    for account_id, account in before_accounts.items():
        if account_id not in after_accounts:
            label = _account_label(account)
            facts.append(
                _make_fact(
                    before_id=before_id,
                    after_id=after_snapshot_id,
                    provider=provider,
                    fact_type=FactType.ACCOUNT_REMOVED,
                    observed_at=observed_at,
                    old_value={
                        "provider_account_id": account.provider_account_id,
                        "display_name": account.display_name,
                        "product_name": account.product_name,
                    },
                    new_value=None,
                    explanation=f"Account removed: {label}.",
                    account_id=account_id,
                )
            )

    for account_id, after_account in after_accounts.items():
        before_account = before_accounts.get(account_id)
        if before_account is None:
            continue
        facts.extend(
            _diff_matched_account(
                before=before_account,
                after=after_account,
                before_id=before_id,
                after_id=after_snapshot_id,
                provider=provider,
                observed_at=observed_at,
            )
        )

    before_rewards = _index_rewards(before.rewards)
    after_rewards = _index_rewards(after.rewards)
    all_programs = set(before_rewards) | set(after_rewards)
    for program_key in sorted(all_programs):
        left = before_rewards.get(program_key)
        right = after_rewards.get(program_key)
        if left is None and right is not None:
            facts.append(
                _make_fact(
                    before_id=before_id,
                    after_id=after_snapshot_id,
                    provider=provider,
                    fact_type=FactType.FIELD_BECAME_AVAILABLE,
                    observed_at=observed_at,
                    old_value=None,
                    new_value=right.to_dict(),
                    explanation=(
                        f"{right.program_name} balance became available "
                        f"({_format_points(right.balance, right.unit)})."
                    ),
                )
            )
            continue
        if left is not None and right is None:
            facts.append(
                _make_fact(
                    before_id=before_id,
                    after_id=after_snapshot_id,
                    provider=provider,
                    fact_type=FactType.FIELD_BECAME_UNAVAILABLE,
                    observed_at=observed_at,
                    old_value=left.to_dict(),
                    new_value=None,
                    explanation=f"{left.program_name} balance became unavailable.",
                )
            )
            continue
        if left is None or right is None:
            continue
        if left.balance != right.balance or left.unit != right.unit:
            facts.append(
                _make_fact(
                    before_id=before_id,
                    after_id=after_snapshot_id,
                    provider=provider,
                    fact_type=FactType.REWARDS_CHANGED,
                    observed_at=observed_at,
                    old_value=left.to_dict(),
                    new_value=right.to_dict(),
                    explanation=(
                        f"{right.program_name} balance changed from "
                        f"{_format_points(left.balance, left.unit)} to "
                        f"{_format_points(right.balance, right.unit)}."
                    ),
                )
            )

    if (before.verified_at or None) != (after.verified_at or None):
        if before.verified_at or after.verified_at:
            facts.append(
                _make_fact(
                    before_id=before_id,
                    after_id=after_snapshot_id,
                    provider=provider,
                    fact_type=FactType.LAST_VERIFIED_CHANGED,
                    observed_at=observed_at,
                    old_value=before.verified_at,
                    new_value=after.verified_at,
                    explanation=(
                        "Last verified timestamp changed from "
                        f"{before.verified_at or 'unavailable'} to "
                        f"{after.verified_at or 'unavailable'}."
                    ),
                    confidence="medium",
                )
            )

    return facts
