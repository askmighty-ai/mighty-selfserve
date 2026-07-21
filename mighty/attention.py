"""AttentionItem — frozen output contract for Attention candidates (PR 2A).

This module defines the immutable AttentionItem model produced by the
Attention Engine / AttentionCompiler. It contains no ranking, persistence,
overlays, Home integration, notifications, or provider-specific logic.

See docs/ATTENTION_ITEM.md for field ownership, exclusions, and Part XIV
scenario coverage.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping

ATTENTION_ITEM_SCHEMA_VERSION = 1

# Stable machine codes for AttentionReason.code when the candidate is auth-derived.
# Aligned with AuthTruth / AuthInterruption vocabulary (RFC §3 / §4).
REASON_LOGIN = "login"
REASON_MFA = "mfa"
REASON_CAPTCHA = "captcha"
REASON_CONSENT = "consent"
REASON_UNKNOWN_HUMAN = "unknown_human"
REASON_PENDING_AUTHORIZATION = "pending_authorization"
REASON_STALE = "stale"
REASON_LOGIN_UNKNOWN = "login_unknown"
REASON_DATA_GAP = "data_gap"
REASON_VALUE_AT_RISK = "value_at_risk"
REASON_OPPORTUNITY = "opportunity"
REASON_SYSTEM = "system"
REASON_TRUST = "trust"


class AttentionClass(str, Enum):
    """Kind of attention candidate (RFC §4.2 AttentionClass)."""

    AUTH_BLOCKER = "auth_blocker"
    AGENT_AUTHORIZATION = "agent_authorization"
    SYSTEM = "system"
    TRUST = "trust"
    VALUE_AT_RISK = "value_at_risk"
    ACCESS_DEGRADED = "access_degraded"
    DATA_GAP = "data_gap"
    OPPORTUNITY = "opportunity"


class AttentionUrgency(str, Enum):
    """Intrinsic urgency band (RFC §4.1 / §7)."""

    BLOCKER = "blocker"
    TIME_SENSITIVE = "time_sensitive"
    OPPORTUNITY = "opportunity"
    INFORMATIONAL = "informational"


class AttentionSourceKind(str, Enum):
    """Owning input system for the candidate (RFC §4.1 SourceKind)."""

    AUTH = "auth"
    AUTHORIZE = "authorize"
    BENEFIT = "benefit"
    WORKER = "worker"
    ACCOUNT_DATA = "account_data"
    TRUST = "trust"


class AttentionCtaKey(str, Enum):
    """Machine CTA key — not rendered label text (RFC §8 vocabulary).

    Snooze / dismiss are AttentionStore overlay commands, not item CTAs.
    """

    START_PROVIDER_LOGIN = "start_provider_login"
    OPEN_PROVIDER_SURFACE = "open_provider_surface"
    FOCUS_MANAGED_RUNTIME = "focus_managed_runtime"
    INSTALL_WORKER = "install_worker"
    OPEN_ACTIVITY_APPROVAL = "open_activity_approval"
    OPEN_ACCOUNT_DETAIL = "open_account_detail"
    CONNECT_GMAIL = "connect_gmail"
    NOOP = "noop"


_VALID_CLASSES = frozenset(item.value for item in AttentionClass)
_VALID_URGENCIES = frozenset(item.value for item in AttentionUrgency)
_VALID_SOURCE_KINDS = frozenset(item.value for item in AttentionSourceKind)
_VALID_CTA_KEYS = frozenset(item.value for item in AttentionCtaKey)

# Ranking policy (RFC §7) requires class/urgency pairing. Validation enforces
# the intrinsic pairs so illegal candidates cannot enter the contract.
_ALLOWED_CLASS_URGENCY: dict[AttentionClass, frozenset[AttentionUrgency]] = {
    AttentionClass.AUTH_BLOCKER: frozenset({AttentionUrgency.BLOCKER}),
    AttentionClass.AGENT_AUTHORIZATION: frozenset({AttentionUrgency.BLOCKER}),
    AttentionClass.SYSTEM: frozenset({AttentionUrgency.BLOCKER}),
    AttentionClass.TRUST: frozenset({AttentionUrgency.BLOCKER}),
    AttentionClass.VALUE_AT_RISK: frozenset({AttentionUrgency.TIME_SENSITIVE}),
    AttentionClass.ACCESS_DEGRADED: frozenset({AttentionUrgency.INFORMATIONAL}),
    AttentionClass.DATA_GAP: frozenset({AttentionUrgency.INFORMATIONAL}),
    AttentionClass.OPPORTUNITY: frozenset({AttentionUrgency.OPPORTUNITY}),
}


class AttentionItemValidationError(ValueError):
    """Raised when an AttentionItem payload violates the frozen contract."""


@dataclass(frozen=True)
class AttentionReason:
    """Structured, non-presentation reason for the candidate.

    ``code`` is a stable machine token (e.g. ``login``, ``mfa``). It must not
    contain English copy, HTML, or UI strings.
    """

    code: str

    def __post_init__(self) -> None:
        code = str(self.code or "").strip().lower()
        if not code:
            raise AttentionItemValidationError("reason.code must be a non-empty string")
        if any(ch.isspace() for ch in code):
            raise AttentionItemValidationError(
                "reason.code must not contain whitespace"
            )
        object.__setattr__(self, "code", code)

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | str) -> AttentionReason:
        if isinstance(payload, str):
            return cls(code=payload)
        if not isinstance(payload, Mapping):
            raise AttentionItemValidationError("reason must be a mapping or string")
        return cls(code=str(payload.get("code") or ""))


@dataclass(frozen=True)
class AttentionItem:
    """Immutable attention candidate derived entirely from platform facts.

    Produced by the Attention Engine. Contains no UI state, interaction state,
    ranking score, queue position, or rendered copy.
    """

    schema_version: int
    attention_id: str
    user_id: str
    attention_class: AttentionClass
    urgency: AttentionUrgency
    provider: str | None
    fingerprint: str
    reason: AttentionReason
    cta_key: AttentionCtaKey
    source_kind: AttentionSourceKind
    source_ref: str
    observed_at: str | None
    becomes_stale_at: str | None
    interruption_expected: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "attention_id", _require_nonempty_str(self.attention_id, "attention_id"))
        object.__setattr__(self, "user_id", _require_nonempty_str(self.user_id, "user_id"))
        object.__setattr__(self, "fingerprint", _require_nonempty_str(self.fingerprint, "fingerprint"))
        object.__setattr__(self, "source_ref", _require_nonempty_str(self.source_ref, "source_ref"))
        object.__setattr__(self, "provider", _optional_provider(self.provider))
        object.__setattr__(self, "observed_at", _optional_iso(self.observed_at, "observed_at"))
        object.__setattr__(
            self,
            "becomes_stale_at",
            _optional_iso(self.becomes_stale_at, "becomes_stale_at"),
        )
        if isinstance(self.reason, str):
            object.__setattr__(self, "reason", AttentionReason(code=self.reason))
        _validate_attention_item(self)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["attention_class"] = self.attention_class.value
        payload["urgency"] = self.urgency.value
        payload["cta_key"] = self.cta_key.value
        payload["source_kind"] = self.source_kind.value
        payload["reason"] = self.reason.to_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AttentionItem:
        if not isinstance(payload, Mapping):
            raise AttentionItemValidationError("AttentionItem payload must be a mapping")
        return cls(
            schema_version=_parse_schema_version(payload.get("schema_version")),
            attention_id=_require_nonempty_str(payload.get("attention_id"), "attention_id"),
            user_id=_require_nonempty_str(payload.get("user_id"), "user_id"),
            attention_class=_parse_enum(
                AttentionClass,
                payload.get("attention_class"),
                field_name="attention_class",
                allowed=_VALID_CLASSES,
            ),
            urgency=_parse_enum(
                AttentionUrgency,
                payload.get("urgency"),
                field_name="urgency",
                allowed=_VALID_URGENCIES,
            ),
            provider=_optional_provider(payload.get("provider")),
            fingerprint=_require_nonempty_str(payload.get("fingerprint"), "fingerprint"),
            reason=AttentionReason.from_dict(
                payload["reason"] if "reason" in payload else {}
            ),
            cta_key=_parse_enum(
                AttentionCtaKey,
                payload.get("cta_key"),
                field_name="cta_key",
                allowed=_VALID_CTA_KEYS,
            ),
            source_kind=_parse_enum(
                AttentionSourceKind,
                payload.get("source_kind"),
                field_name="source_kind",
                allowed=_VALID_SOURCE_KINDS,
            ),
            source_ref=_require_nonempty_str(payload.get("source_ref"), "source_ref"),
            observed_at=_optional_iso(payload.get("observed_at"), "observed_at"),
            becomes_stale_at=_optional_iso(
                payload.get("becomes_stale_at"), "becomes_stale_at"
            ),
            interruption_expected=bool(payload.get("interruption_expected", False)),
        )


def _validate_attention_item(item: AttentionItem) -> None:
    if item.schema_version != ATTENTION_ITEM_SCHEMA_VERSION:
        raise AttentionItemValidationError(
            f"schema_version must be {ATTENTION_ITEM_SCHEMA_VERSION}, "
            f"got {item.schema_version!r}"
        )
    _require_nonempty_str(item.attention_id, "attention_id")
    _require_nonempty_str(item.user_id, "user_id")
    _require_nonempty_str(item.fingerprint, "fingerprint")
    _require_nonempty_str(item.source_ref, "source_ref")
    if not isinstance(item.reason, AttentionReason):
        raise AttentionItemValidationError("reason must be an AttentionReason")
    if not isinstance(item.attention_class, AttentionClass):
        raise AttentionItemValidationError("attention_class must be an AttentionClass")
    if not isinstance(item.urgency, AttentionUrgency):
        raise AttentionItemValidationError("urgency must be an AttentionUrgency")
    if not isinstance(item.cta_key, AttentionCtaKey):
        raise AttentionItemValidationError("cta_key must be an AttentionCtaKey")
    if not isinstance(item.source_kind, AttentionSourceKind):
        raise AttentionItemValidationError("source_kind must be an AttentionSourceKind")
    allowed = _ALLOWED_CLASS_URGENCY[item.attention_class]
    if item.urgency not in allowed:
        raise AttentionItemValidationError(
            f"urgency {item.urgency.value!r} is not allowed for "
            f"attention_class {item.attention_class.value!r}"
        )
    if item.provider is not None:
        _optional_provider(item.provider)
    _optional_iso(item.observed_at, "observed_at")
    _optional_iso(item.becomes_stale_at, "becomes_stale_at")
    if not isinstance(item.interruption_expected, bool):
        raise AttentionItemValidationError("interruption_expected must be a bool")


def _parse_schema_version(value: Any) -> int:
    if value is None:
        raise AttentionItemValidationError("schema_version is required")
    try:
        version = int(value)
    except (TypeError, ValueError) as exc:
        raise AttentionItemValidationError(
            f"schema_version must be an int, got {value!r}"
        ) from exc
    if version != ATTENTION_ITEM_SCHEMA_VERSION:
        raise AttentionItemValidationError(
            f"schema_version must be {ATTENTION_ITEM_SCHEMA_VERSION}, got {version!r}"
        )
    return version


def _require_nonempty_str(value: Any, field_name: str) -> str:
    if value is None:
        raise AttentionItemValidationError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise AttentionItemValidationError(f"{field_name} must be a non-empty string")
    return text


def _optional_provider(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    return text


def _optional_iso(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Structural check only — do not parse timezones or invent clocks here.
    if "T" not in text and " " not in text:
        raise AttentionItemValidationError(
            f"{field_name} must be an ISO-8601 datetime string when present"
        )
    return text


def _parse_enum(
    enum_cls: type[Enum],
    value: Any,
    *,
    field_name: str,
    allowed: frozenset[str],
) -> Any:
    if value is None:
        raise AttentionItemValidationError(f"{field_name} is required")
    if isinstance(value, enum_cls):
        return value
    text = str(value).strip().lower()
    if text not in allowed:
        raise AttentionItemValidationError(
            f"{field_name} must be one of {sorted(allowed)}, got {value!r}"
        )
    return enum_cls(text)
