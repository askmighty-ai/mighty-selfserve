"""Canonical User Policy model (Milestone 12).

Durable representation of user intent for privacy, approval, execution,
monitoring, opportunities, and notifications. Not an Attention ranker.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from mighty.authorization_policy import (
    LEVEL_CONSEQUENTIAL,
    LEVEL_CRITICAL,
    LEVEL_INFORMATIONAL,
    LEVEL_ROUTINE,
    normalize_consequence_level,
)

POLICY_VERSION = 1

LEVEL_RANK: dict[str, int] = {
    LEVEL_INFORMATIONAL: 0,
    LEVEL_ROUTINE: 1,
    LEVEL_CONSEQUENTIAL: 2,
    LEVEL_CRITICAL: 3,
}


@dataclass(frozen=True)
class UserPolicy:
    """Canonical durable user intent."""

    user_id: str
    require_human_at_or_above: str = LEVEL_ROUTINE
    auto_execute_informational: bool = True
    auto_execute_routine: bool = False
    monitor_providers: bool = True
    suppress_opportunity_kinds: tuple[str, ...] = ()
    minimal_logging: bool = False
    delete_raw_after_extract: bool = False
    retention_days: int | None = None
    notify_email: bool = True
    notify_push: bool = True
    notify_ntfy: bool = True
    alert_expiry_emails: bool = True
    notification_pref: str = "quiet"
    provider_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    version: int = POLICY_VERSION
    updated_at: str | None = None
    source: str = "default"  # default | users | store | merged

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "require_human_at_or_above": self.require_human_at_or_above,
            "auto_execute_informational": self.auto_execute_informational,
            "auto_execute_routine": self.auto_execute_routine,
            "monitor_providers": self.monitor_providers,
            "suppress_opportunity_kinds": list(self.suppress_opportunity_kinds),
            "minimal_logging": self.minimal_logging,
            "delete_raw_after_extract": self.delete_raw_after_extract,
            "retention_days": self.retention_days,
            "notify_email": self.notify_email,
            "notify_push": self.notify_push,
            "notify_ntfy": self.notify_ntfy,
            "alert_expiry_emails": self.alert_expiry_emails,
            "notification_pref": self.notification_pref,
            "provider_overrides": dict(self.provider_overrides),
            "version": self.version,
            "updated_at": self.updated_at,
            "source": self.source,
        }


def default_user_policy(user_id: str) -> UserPolicy:
    return UserPolicy(user_id=str(user_id).strip(), source="default")


def level_rank(level: str | None) -> int:
    return LEVEL_RANK.get(normalize_consequence_level(level), 1)


def opportunity_kind_suppressed(policy: UserPolicy, kind: str) -> bool:
    return str(kind or "").strip().lower() in {
        k.lower() for k in policy.suppress_opportunity_kinds
    }


def provider_monitoring_enabled(policy: UserPolicy, provider: str | None) -> bool:
    """True when monitoring is enabled globally and not disabled by override."""
    if not policy.monitor_providers:
        return False
    key = str(provider or "").strip().lower()
    if not key:
        return True
    override = policy.provider_overrides.get(key) or {}
    if "monitor" in override:
        return bool(override.get("monitor"))
    if "monitor_providers" in override:
        return bool(override.get("monitor_providers"))
    return True


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def policy_from_user_row(user_id: str, row: Mapping[str, Any] | None) -> UserPolicy:
    """Project legacy ``users`` columns into Policy (no parallel settings)."""
    base = default_user_policy(user_id)
    if not row:
        return base
    try:
        mapping = dict(row)
    except Exception:
        mapping = {k: row[k] for k in row.keys()}  # type: ignore[attr-defined]

    pref = str(mapping.get("notification_pref") or base.notification_pref).strip() or "quiet"
    return UserPolicy(
        user_id=str(user_id).strip(),
        require_human_at_or_above=base.require_human_at_or_above,
        auto_execute_informational=base.auto_execute_informational,
        auto_execute_routine=base.auto_execute_routine,
        monitor_providers=base.monitor_providers,
        suppress_opportunity_kinds=base.suppress_opportunity_kinds,
        minimal_logging=_as_bool(mapping.get("minimal_logging"), False),
        delete_raw_after_extract=_as_bool(mapping.get("delete_raw_after_extract"), False),
        retention_days=base.retention_days,
        notify_email=_as_bool(mapping.get("notify_email"), True),
        notify_push=_as_bool(mapping.get("notify_push"), True),
        notify_ntfy=_as_bool(mapping.get("notify_ntfy"), True),
        alert_expiry_emails=_as_bool(mapping.get("alert_expiry_emails"), True),
        notification_pref=pref,
        provider_overrides={},
        version=POLICY_VERSION,
        source="users",
    )


def merge_policies(base: UserPolicy, overlay: UserPolicy | None) -> UserPolicy:
    """Merge users-projected base with store overlay (store wins when present)."""
    if overlay is None or overlay.source == "default":
        return base
    return UserPolicy(
        user_id=base.user_id or overlay.user_id,
        require_human_at_or_above=overlay.require_human_at_or_above,
        auto_execute_informational=overlay.auto_execute_informational,
        auto_execute_routine=overlay.auto_execute_routine,
        monitor_providers=overlay.monitor_providers,
        suppress_opportunity_kinds=overlay.suppress_opportunity_kinds
        or base.suppress_opportunity_kinds,
        minimal_logging=overlay.minimal_logging,
        delete_raw_after_extract=overlay.delete_raw_after_extract,
        retention_days=overlay.retention_days
        if overlay.retention_days is not None
        else base.retention_days,
        notify_email=overlay.notify_email,
        notify_push=overlay.notify_push,
        notify_ntfy=overlay.notify_ntfy,
        alert_expiry_emails=overlay.alert_expiry_emails,
        notification_pref=overlay.notification_pref or base.notification_pref,
        provider_overrides={**base.provider_overrides, **overlay.provider_overrides},
        version=max(base.version, overlay.version),
        updated_at=overlay.updated_at or base.updated_at,
        source="merged",
    )


def policy_from_dict(payload: Mapping[str, Any], *, user_id: str) -> UserPolicy:
    kinds = payload.get("suppress_opportunity_kinds") or ()
    if isinstance(kinds, str):
        try:
            kinds = json.loads(kinds)
        except Exception:
            kinds = [kinds]
    overrides = payload.get("provider_overrides") or {}
    if isinstance(overrides, str):
        try:
            overrides = json.loads(overrides)
        except Exception:
            overrides = {}
    retention = payload.get("retention_days")
    retention_i: int | None
    try:
        retention_i = int(retention) if retention is not None else None
    except (TypeError, ValueError):
        retention_i = None
    return UserPolicy(
        user_id=str(user_id).strip(),
        require_human_at_or_above=normalize_consequence_level(
            payload.get("require_human_at_or_above") or LEVEL_ROUTINE
        ),
        auto_execute_informational=_as_bool(
            payload.get("auto_execute_informational"), True
        ),
        auto_execute_routine=_as_bool(payload.get("auto_execute_routine"), False),
        monitor_providers=_as_bool(payload.get("monitor_providers"), True),
        suppress_opportunity_kinds=tuple(
            str(k).strip().lower() for k in kinds if str(k).strip()
        ),
        minimal_logging=_as_bool(payload.get("minimal_logging"), False),
        delete_raw_after_extract=_as_bool(
            payload.get("delete_raw_after_extract"), False
        ),
        retention_days=retention_i,
        notify_email=_as_bool(payload.get("notify_email"), True),
        notify_push=_as_bool(payload.get("notify_push"), True),
        notify_ntfy=_as_bool(payload.get("notify_ntfy"), True),
        alert_expiry_emails=_as_bool(payload.get("alert_expiry_emails"), True),
        notification_pref=str(payload.get("notification_pref") or "quiet"),
        provider_overrides={
            str(k).lower(): dict(v) for k, v in overrides.items() if isinstance(v, dict)
        },
        version=int(payload.get("version") or POLICY_VERSION),
        updated_at=payload.get("updated_at"),
        source=str(payload.get("source") or "store"),
    )
