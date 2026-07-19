"""Tests for the generic provider connector contract and canonical models."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from mighty.provider_connector import (
    AccountSnapshot,
    AccountType,
    Completeness,
    ConnectorCapabilities,
    ConnectorErrorReason,
    ConnectorRefreshResult,
    ConnectorTelemetry,
    ConnectorVerificationResult,
    FieldConfidence,
    FieldObservation,
    FieldSource,
    FieldStatus,
    FinancialAccount,
    ProviderConnector,
    RefreshStatus,
    RewardsBalance,
    assert_no_provider_raw_objects,
    classify_refresh_status,
    is_data_quality_warning,
    parse_money,
    summarize_field_observations,
)


def _money(amount: str):
    parsed = parse_money(amount)
    assert parsed is not None
    return parsed


def test_capabilities_declare_read_only():
    caps = ConnectorCapabilities(
        provider="amex",
        read_only=True,
        initial_fields=("rewards_balance",),
    )
    payload = caps.to_dict()
    assert payload["read_only"] is True
    assert payload["supports_mutations"] is False
    assert payload["supports_payments"] is False


def test_canonical_serialization_round_trip_shape():
    snapshot = AccountSnapshot(
        provider="amex",
        accounts=(
            FinancialAccount(
                provider_account_id="amex_abc",
                display_name="Gold Card",
                account_type=AccountType.CREDIT_CARD,
                currency="USD",
                observed_at="2026-01-01T00:00:00+00:00",
                last_four="1234",
                current_balance=_money("100.50"),
                payment_due_date=date(2026, 2, 1),
            ),
        ),
        rewards=(
            RewardsBalance(
                program_name="Membership Rewards",
                balance=Decimal("124350"),
                unit="points",
                observed_at="2026-01-01T00:00:00+00:00",
            ),
        ),
        observed_at="2026-01-01T00:00:00+00:00",
        verified_at="2026-01-01T00:00:00+00:00",
        completeness=Completeness.PARTIAL,
        warnings=("payment_due_date_unavailable",),
    )
    result = ConnectorRefreshResult(
        provider="amex",
        status=RefreshStatus.PARTIAL_SUCCESS,
        snapshot=snapshot,
        field_observations=(
            FieldObservation(
                field_name="rewards_balance",
                status=FieldStatus.SUCCESS,
                source=FieldSource.DOM_FALLBACK,
                observed_at="2026-01-01T00:00:00+00:00",
                confidence=FieldConfidence.MEDIUM,
            ),
        ),
        telemetry=ConnectorTelemetry(
            provider="amex",
            refresh_id="r1",
            started_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:00:01+00:00",
            duration_ms=1000,
            fields_succeeded=1,
            snapshot_account_count=1,
            rewards_program_count=1,
        ),
        warnings=("payment_due_date_unavailable",),
    )
    payload = result.to_sanitized_dict()
    assert payload["status"] == "partial_success"
    assert payload["snapshot"]["accounts"][0]["current_balance"]["amount"] == "100.50"
    assert payload["snapshot"]["accounts"][0]["payment_due_date"] == "2026-02-01"
    assert "amex_raw" not in payload
    assert_no_provider_raw_objects(payload)


def test_status_enums_and_verification_result():
    assert RefreshStatus.PARTIAL_SUCCESS.value == "partial_success"
    assert ConnectorErrorReason.NO_USEFUL_DATA.value == "no_useful_data"
    verification = ConnectorVerificationResult(
        provider="chase",
        authentication_state="SIGNED_IN",
        ok=True,
    )
    assert verification.to_dict()["ok"] is True


def test_partial_success_semantics():
    observations = [
        FieldObservation(
            field_name="rewards_balance",
            status=FieldStatus.SUCCESS,
            source=FieldSource.NETWORK,
            observed_at="t",
            confidence=FieldConfidence.HIGH,
        ),
        FieldObservation(
            field_name="payment_due_date",
            status=FieldStatus.UNAVAILABLE,
            source=FieldSource.DOM_FALLBACK,
            observed_at="t",
            confidence=FieldConfidence.LOW,
            reason="payment_due_date_unavailable",
        ),
    ]
    snapshot = AccountSnapshot(
        provider="amex",
        accounts=(),
        rewards=(
            RewardsBalance(
                program_name="Membership Rewards",
                balance=Decimal("10"),
                unit="points",
                observed_at="t",
            ),
        ),
        observed_at="t",
        verified_at="t",
        completeness=Completeness.PARTIAL,
    )
    status, reason, error = classify_refresh_status(
        authentication_state="SIGNED_IN",
        snapshot=snapshot,
        field_observations=observations,
    )
    assert status == RefreshStatus.PARTIAL_SUCCESS
    assert reason is None
    assert error is None


def test_public_result_rejects_provider_raw_objects():
    with pytest.raises(ValueError, match="forbidden_key"):
        assert_no_provider_raw_objects({"raw_payload": {"x": 1}})


def test_money_retains_decimal_precision():
    amount = parse_money("1,234.50")
    assert amount is not None
    assert amount.amount == Decimal("1234.50")
    assert amount.currency == "USD"
    assert amount.to_dict()["amount"] == "1234.50"


def test_data_quality_warnings_allowed_prescriptive_rejected():
    assert is_data_quality_warning("payment_due_date_unavailable")
    assert is_data_quality_warning("authentication_required")
    assert not is_data_quality_warning("you should pay this amount")
    assert not is_data_quality_warning("redeem now for bonus")


def test_summarize_field_observations():
    counts = summarize_field_observations(
        [
            FieldObservation(
                field_name="a",
                status=FieldStatus.SUCCESS,
                source=FieldSource.NETWORK,
                observed_at="t",
                confidence=FieldConfidence.HIGH,
            ),
            FieldObservation(
                field_name="b",
                status=FieldStatus.UNAVAILABLE,
                source=FieldSource.DOM_FALLBACK,
                observed_at="t",
                confidence=FieldConfidence.LOW,
            ),
            FieldObservation(
                field_name="c",
                status=FieldStatus.FAILED,
                source=FieldSource.DOM_FALLBACK,
                observed_at="t",
                confidence=FieldConfidence.LOW,
            ),
        ]
    )
    assert counts == {
        "fields_attempted": 3,
        "fields_succeeded": 1,
        "fields_unavailable": 1,
        "fields_failed": 1,
    }


def test_provider_connector_is_abstract():
    class Incomplete(ProviderConnector):
        provider = "x"

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]
