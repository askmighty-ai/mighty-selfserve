"""
Normalize Amex intermediate observations into canonical connector models.

Public return types never include Amex-specific raw structures.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from mighty.amex_extractor import (
    AmexExtractionObservation,
    mask_account_number,
    parse_due_date,
)
from mighty.provider_connector import (
    AccountSnapshot,
    AccountType,
    Completeness,
    FieldConfidence,
    FieldObservation,
    FieldSource,
    FieldStatus,
    FinancialAccount,
    MoneyAmount,
    RewardsBalance,
    is_data_quality_warning,
    parse_money,
    utc_now_iso,
)


def _to_date(raw: str | None) -> date | None:
    normalized = parse_due_date(raw)
    if not normalized:
        return None
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def _to_decimal_points(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return None
    try:
        value = Decimal(digits)
    except (InvalidOperation, ValueError):
        return None
    if value <= 0:
        return None
    return value


def normalize_field_observations(
    observation: AmexExtractionObservation,
) -> list[FieldObservation]:
    result: list[FieldObservation] = []
    for raw in observation.field_observations:
        try:
            status = FieldStatus(raw.status)
        except ValueError:
            status = FieldStatus.FAILED
        try:
            source = FieldSource(raw.source)
        except ValueError:
            source = FieldSource.DOM_FALLBACK
        try:
            confidence = FieldConfidence(raw.confidence)
        except ValueError:
            confidence = FieldConfidence.LOW
        reason = raw.reason
        if reason and not is_data_quality_warning(reason):
            reason = "data_quality_issue"
        result.append(
            FieldObservation(
                field_name=raw.field_name,
                status=status,
                source=source,
                observed_at=raw.observed_at,
                confidence=confidence,
                reason=reason,
                account_ref=raw.account_ref,
            )
        )
    return result


def normalize_amex_observation(
    observation: AmexExtractionObservation,
    *,
    verified_at: str | None = None,
    provider_customer_id: str | None = None,
) -> tuple[AccountSnapshot, list[FieldObservation], list[str]]:
    """Convert Amex intermediate observation → canonical snapshot + fields."""
    observed_at = observation.observed_at or utc_now_iso()
    accounts: list[FinancialAccount] = []
    for card in observation.cards:
        last_four = mask_account_number(card.last_four)
        current = parse_money(card.current_balance, currency=card.currency or "USD")
        available = parse_money(card.available_credit, currency=card.currency or "USD")
        due_amount = parse_money(
            card.payment_due_amount, currency=card.currency or "USD"
        )
        due_date = _to_date(card.payment_due_date)
        display = card.display_name or card.product_name or (
            f"Card ending {last_four}" if last_four else "Amex Card"
        )
        accounts.append(
            FinancialAccount(
                provider_account_id=card.opaque_id(),
                display_name=display,
                account_type=AccountType.CREDIT_CARD,
                currency=card.currency or "USD",
                observed_at=observed_at,
                product_name=card.product_name,
                last_four=last_four,
                current_balance=current,
                available_credit=available,
                payment_due_amount=due_amount,
                payment_due_date=due_date,
            )
        )

    rewards: list[RewardsBalance] = []
    if observation.rewards and observation.rewards.balance:
        points = _to_decimal_points(observation.rewards.balance)
        if points is not None:
            rewards.append(
                RewardsBalance(
                    program_name=observation.rewards.program_name or "Membership Rewards",
                    balance=points,
                    unit=observation.rewards.unit or "points",
                    observed_at=observed_at,
                )
            )

    field_observations = normalize_field_observations(observation)
    warnings = [
        w for w in observation.warnings if is_data_quality_warning(w)
    ]

    if accounts or rewards:
        # Partial if any optional account/rewards field is unavailable.
        unavailable = sum(
            1 for obs in field_observations if obs.status == FieldStatus.UNAVAILABLE
        )
        completeness = (
            Completeness.PARTIAL if unavailable > 0 else Completeness.FULL
        )
    else:
        completeness = Completeness.EMPTY

    metadata: dict[str, Any] = {
        "extraction_method": observation.extraction_method,
        "surface_url": observation.surface_url,
        "method_counts": dict(observation.method_counts),
    }
    # Drop empty metadata keys.
    metadata = {k: v for k, v in metadata.items() if v not in (None, "", {}, [])}

    snapshot = AccountSnapshot(
        provider="amex",
        provider_customer_id=provider_customer_id,
        accounts=tuple(accounts),
        rewards=tuple(rewards),
        observed_at=observed_at,
        verified_at=verified_at,
        completeness=completeness,
        warnings=tuple(warnings),
        provider_metadata=metadata,
    )
    return snapshot, field_observations, warnings


def money_amount_from_parts(amount: str | Decimal, currency: str = "USD") -> MoneyAmount:
    if isinstance(amount, Decimal):
        return MoneyAmount(amount=amount, currency=currency)
    parsed = parse_money(amount, currency=currency)
    if parsed is None:
        raise ValueError("invalid_money_amount")
    return parsed


def parse_iso_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
