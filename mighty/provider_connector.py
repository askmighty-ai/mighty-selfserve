"""
Provider connector contract and canonical models.

Mighty orchestration → ProviderConnector → Provider Runtime → extractor →
normalizer → AccountSnapshot.

Connectors are observational and read-only. They never launch browsers,
implement MFA, or mutate provider accounts. Authentication lifecycle belongs
to Provider Runtime.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RefreshStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    AUTHENTICATION_REQUIRED = "authentication_required"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class FieldStatus(str, Enum):
    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    STALE = "stale"
    FAILED = "failed"


class FieldSource(str, Enum):
    NETWORK = "network"
    RUNTIME_API = "runtime_api"
    DOM_FALLBACK = "dom_fallback"


class FieldConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ConnectorErrorReason(str, Enum):
    AUTHENTICATION_REQUIRED = "authentication_required"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    SURFACE_UNAVAILABLE = "surface_unavailable"
    EXTRACTION_FAILED = "extraction_failed"
    NORMALIZATION_FAILED = "normalization_failed"
    NO_USEFUL_DATA = "no_useful_data"
    UNSUPPORTED = "unsupported"


class AccountType(str, Enum):
    CREDIT_CARD = "credit_card"
    CHARGE_CARD = "charge_card"
    DEBIT = "debit"
    CHECKING = "checking"
    SAVINGS = "savings"
    INVESTMENT = "investment"
    LOAN = "loan"
    OTHER = "other"
    UNKNOWN = "unknown"


class Completeness(str, Enum):
    FULL = "full"
    PARTIAL = "partial"
    EMPTY = "empty"


@dataclass(frozen=True)
class ConnectorCapabilities:
    """Provider-independent capability declaration."""

    provider: str
    read_only: bool = True
    supports_verify: bool = True
    supports_refresh: bool = True
    supports_transactions: bool = False
    supports_payments: bool = False
    supports_offers: bool = False
    supports_statements: bool = False
    supports_transfers: bool = False
    supports_mutations: bool = False
    initial_fields: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "read_only": self.read_only,
            "supports_verify": self.supports_verify,
            "supports_refresh": self.supports_refresh,
            "supports_transactions": self.supports_transactions,
            "supports_payments": self.supports_payments,
            "supports_offers": self.supports_offers,
            "supports_statements": self.supports_statements,
            "supports_transfers": self.supports_transfers,
            "supports_mutations": self.supports_mutations,
            "initial_fields": list(self.initial_fields),
        }


@dataclass(frozen=True)
class FieldObservation:
    """Field-level provenance and status for one extracted observation."""

    field_name: str
    status: FieldStatus
    source: FieldSource
    observed_at: str
    confidence: FieldConfidence
    reason: str | None = None
    account_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "field_name": self.field_name,
            "status": self.status.value,
            "source": self.source.value,
            "observed_at": self.observed_at,
            "confidence": self.confidence.value,
        }
        if self.reason:
            payload["reason"] = self.reason
        if self.account_ref:
            payload["account_ref"] = self.account_ref
        return payload


@dataclass(frozen=True)
class MoneyAmount:
    """Monetary value with currency and decimal precision."""

    amount: Decimal
    currency: str = "USD"

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount": format(self.amount, "f"),
            "currency": self.currency,
        }


def parse_money(raw: Any, *, currency: str = "USD") -> MoneyAmount | None:
    """Parse a display or numeric money value into MoneyAmount."""
    if raw is None:
        return None
    if isinstance(raw, MoneyAmount):
        return raw
    if isinstance(raw, Decimal):
        return MoneyAmount(amount=raw, currency=currency)
    if isinstance(raw, (int, float)):
        return MoneyAmount(amount=Decimal(str(raw)), currency=currency)
    text = str(raw).strip()
    if not text:
        return None
    cleaned = (
        text.replace("$", "")
        .replace(",", "")
        .replace("USD", "")
        .replace("usd", "")
        .strip()
    )
    if not cleaned:
        return None
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    if amount < 0:
        return None
    return MoneyAmount(amount=amount, currency=currency)


@dataclass(frozen=True)
class FinancialAccount:
    """Provider-independent financial account observation."""

    provider_account_id: str
    display_name: str
    account_type: AccountType
    currency: str
    observed_at: str
    product_name: str | None = None
    last_four: str | None = None
    current_balance: MoneyAmount | None = None
    available_credit: MoneyAmount | None = None
    payment_due_amount: MoneyAmount | None = None
    payment_due_date: date | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider_account_id": self.provider_account_id,
            "display_name": self.display_name,
            "account_type": self.account_type.value,
            "currency": self.currency,
            "observed_at": self.observed_at,
        }
        if self.product_name:
            payload["product_name"] = self.product_name
        if self.last_four:
            payload["last_four"] = self.last_four
        if self.current_balance is not None:
            payload["current_balance"] = self.current_balance.to_dict()
        if self.available_credit is not None:
            payload["available_credit"] = self.available_credit.to_dict()
        if self.payment_due_amount is not None:
            payload["payment_due_amount"] = self.payment_due_amount.to_dict()
        if self.payment_due_date is not None:
            payload["payment_due_date"] = self.payment_due_date.isoformat()
        return payload


@dataclass(frozen=True)
class RewardsBalance:
    """Provider-independent rewards program balance."""

    program_name: str
    balance: Decimal
    unit: str
    observed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "program_name": self.program_name,
            "balance": format(self.balance, "f"),
            "unit": self.unit,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True)
class AccountSnapshot:
    """Canonical connector snapshot — no provider-specific raw objects."""

    provider: str
    accounts: tuple[FinancialAccount, ...]
    rewards: tuple[RewardsBalance, ...]
    observed_at: str
    verified_at: str | None
    completeness: Completeness
    warnings: tuple[str, ...] = ()
    provider_customer_id: str | None = None
    provider_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "provider_customer_id": self.provider_customer_id,
            "accounts": [account.to_dict() for account in self.accounts],
            "rewards": [reward.to_dict() for reward in self.rewards],
            "observed_at": self.observed_at,
            "verified_at": self.verified_at,
            "completeness": self.completeness.value,
            "warnings": list(self.warnings),
            "provider_metadata": dict(self.provider_metadata),
        }


@dataclass(frozen=True)
class ConnectorTelemetry:
    """Sanitized refresh telemetry — never credentials, cookies, or bodies."""

    provider: str
    refresh_id: str
    started_at: str
    completed_at: str
    duration_ms: int
    authentication_initial_state: str | None = None
    authentication_final_state: str | None = None
    extraction_method_counts: dict[str, int] = field(default_factory=dict)
    fields_attempted: int = 0
    fields_succeeded: int = 0
    fields_unavailable: int = 0
    fields_failed: int = 0
    runtime_recovery_attempts: int = 0
    user_interrupted: bool = False
    interruption_type: str | None = None
    snapshot_account_count: int = 0
    rewards_program_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "refresh_id": self.refresh_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "authentication_initial_state": self.authentication_initial_state,
            "authentication_final_state": self.authentication_final_state,
            "extraction_method_counts": dict(self.extraction_method_counts),
            "fields_attempted": self.fields_attempted,
            "fields_succeeded": self.fields_succeeded,
            "fields_unavailable": self.fields_unavailable,
            "fields_failed": self.fields_failed,
            "runtime_recovery_attempts": self.runtime_recovery_attempts,
            "user_interrupted": self.user_interrupted,
            "interruption_type": self.interruption_type,
            "snapshot_account_count": self.snapshot_account_count,
            "rewards_program_count": self.rewards_program_count,
        }


@dataclass(frozen=True)
class ConnectorVerificationResult:
    """Result of connector-level authentication verification."""

    provider: str
    authentication_state: str
    ok: bool
    reason: str | None = None
    observed_at: str | None = None
    user_interrupted: bool = False
    interruption_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "authentication_state": self.authentication_state,
            "ok": self.ok,
            "reason": self.reason,
            "observed_at": self.observed_at,
            "user_interrupted": self.user_interrupted,
            "interruption_type": self.interruption_type,
        }


@dataclass(frozen=True)
class ConnectorRefreshResult:
    """Structured connector refresh outcome."""

    provider: str
    status: RefreshStatus
    field_observations: tuple[FieldObservation, ...]
    telemetry: ConnectorTelemetry
    user_interrupted: bool = False
    interruption_type: str | None = None
    warnings: tuple[str, ...] = ()
    snapshot: AccountSnapshot | None = None
    error: str | None = None
    error_reason: ConnectorErrorReason | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status.value,
            "snapshot": self.snapshot.to_dict() if self.snapshot else None,
            "field_observations": [obs.to_dict() for obs in self.field_observations],
            "telemetry": self.telemetry.to_dict(),
            "user_interrupted": self.user_interrupted,
            "interruption_type": self.interruption_type,
            "warnings": list(self.warnings),
            "error": self.error,
            "error_reason": self.error_reason.value if self.error_reason else None,
        }

    def to_sanitized_dict(self) -> dict[str, Any]:
        """Public serialization — identical to to_dict (already sanitized)."""
        return self.to_dict()


# Data-quality / access-state warnings only (never advice).
ALLOWED_WARNING_PREFIXES = (
    "payment_due_date_unavailable",
    "payment_due_amount_unavailable",
    "available_credit_unavailable",
    "current_balance_unavailable",
    "rewards_balance_unavailable",
    "rewards_balance_stale",
    "overview_partially_loaded",
    "authentication_required",
    "surface_unavailable",
    "extraction_partial",
    "no_card_accounts_observed",
    "identifier_derived",
)


PRESCRIPTIVE_WARNING_FRAGMENTS = (
    "you should",
    "pay this",
    "pay now",
    "redeem now",
    "use this card",
    "recommend",
    "optimize",
    "risk score",
)


def is_data_quality_warning(warning: str) -> bool:
    text = str(warning or "").strip().lower()
    if not text:
        return False
    if any(fragment in text for fragment in PRESCRIPTIVE_WARNING_FRAGMENTS):
        return False
    return True


def assert_no_provider_raw_objects(payload: dict[str, Any]) -> None:
    """Raise if a public result embeds provider-specific raw structures."""
    forbidden_keys = {
        "raw_response",
        "raw_payload",
        "response_body",
        "request_body",
        "cookies",
        "headers",
        "page",
        "playwright_page",
        "html",
        "dom",
        "amex_raw",
        "network_bodies",
    }
    stack = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if str(key).lower() in forbidden_keys:
                    raise ValueError(f"public_result_contains_forbidden_key:{key}")
                stack.append(value)
        elif isinstance(current, (list, tuple)):
            stack.extend(current)


class ProviderConnector(ABC):
    """Provider-independent connector contract for future Chase/Delta/etc."""

    provider: str

    @abstractmethod
    def verify(self) -> ConnectorVerificationResult:
        """Request canonical authentication verification via Provider Runtime."""

    @abstractmethod
    def refresh(self) -> ConnectorRefreshResult:
        """Run a read-only refresh and return a structured result."""

    @abstractmethod
    def capabilities(self) -> ConnectorCapabilities:
        """Declare connector capabilities (always read_only for v1)."""


def summarize_field_observations(
    observations: list[FieldObservation] | tuple[FieldObservation, ...],
) -> dict[str, int]:
    succeeded = sum(1 for o in observations if o.status == FieldStatus.SUCCESS)
    unavailable = sum(
        1
        for o in observations
        if o.status in {FieldStatus.UNAVAILABLE, FieldStatus.UNSUPPORTED, FieldStatus.STALE}
    )
    failed = sum(1 for o in observations if o.status == FieldStatus.FAILED)
    return {
        "fields_attempted": len(observations),
        "fields_succeeded": succeeded,
        "fields_unavailable": unavailable,
        "fields_failed": failed,
    }


def classify_refresh_status(
    *,
    authentication_state: str | None,
    snapshot: AccountSnapshot | None,
    field_observations: list[FieldObservation] | tuple[FieldObservation, ...],
    user_interrupted: bool = False,
    runtime_error: str | None = None,
) -> tuple[RefreshStatus, ConnectorErrorReason | None, str | None]:
    """Derive refresh status from auth + snapshot usefulness."""
    if user_interrupted:
        return (
            RefreshStatus.AUTHENTICATION_REQUIRED,
            ConnectorErrorReason.AUTHENTICATION_REQUIRED,
            "user_interrupted",
        )
    state = str(authentication_state or "").upper()
    if state in {"SIGNED_OUT", "LOGIN_UNKNOWN"}:
        return (
            RefreshStatus.AUTHENTICATION_REQUIRED,
            ConnectorErrorReason.AUTHENTICATION_REQUIRED,
            f"authentication_state_{state.lower()}",
        )
    if runtime_error == "runtime_unavailable":
        return (
            RefreshStatus.UNAVAILABLE,
            ConnectorErrorReason.RUNTIME_UNAVAILABLE,
            "runtime_unavailable",
        )
    if runtime_error == "surface_unavailable":
        return (
            RefreshStatus.UNAVAILABLE,
            ConnectorErrorReason.SURFACE_UNAVAILABLE,
            "surface_unavailable",
        )
    if runtime_error == "provider_unavailable":
        return (
            RefreshStatus.UNAVAILABLE,
            ConnectorErrorReason.PROVIDER_UNAVAILABLE,
            "provider_unavailable",
        )

    counts = summarize_field_observations(field_observations)
    has_accounts = bool(snapshot and snapshot.accounts)
    has_rewards = bool(snapshot and snapshot.rewards)
    # Metadata-only successes (e.g. last_verified_timestamp) are not useful data.
    content_success = any(
        obs.status == FieldStatus.SUCCESS
        and obs.field_name
        not in {"last_verified_timestamp", "verified_at", "observed_at"}
        for obs in field_observations
    )
    useful = has_accounts or has_rewards or content_success

    if not useful:
        if counts["fields_failed"] > 0 or runtime_error == "extraction_failed":
            return (
                RefreshStatus.FAILED,
                ConnectorErrorReason.EXTRACTION_FAILED,
                "extraction_failed",
            )
        return (
            RefreshStatus.FAILED,
            ConnectorErrorReason.NO_USEFUL_DATA,
            "no_useful_data",
        )

    if (
        counts["fields_unavailable"] > 0
        or counts["fields_failed"] > 0
        or (snapshot and snapshot.completeness == Completeness.PARTIAL)
    ):
        return RefreshStatus.PARTIAL_SUCCESS, None, None
    return RefreshStatus.SUCCESS, None, None


def model_to_dict(value: Any) -> Any:
    """Best-effort dict conversion for dataclasses used in tests."""
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return value
