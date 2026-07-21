"""Attention shadow comparison + agreement metrics (Milestone 3).

Compares a read-only legacy attention probe to AttentionState without inventing
a second permanent policy. Failures are swallowed at the recorder boundary.

See docs/ATTENTION_PLATFORM_ADOPTION.md.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Mapping

from mighty.attention_state import AttentionState, SilenceVerdict

logger = logging.getLogger(__name__)

AttentionSurface = Literal["home", "worker"]


class AttentionAgreement(str, Enum):
    EXACT_AGREEMENT = "exact_agreement"
    SAME_CLASS_DIFF_PRIMARY = "same_class_diff_primary"
    OLD_SILENT_NEW_ACTIVE = "old_silent_new_active"
    OLD_ACTIVE_NEW_SILENT = "old_active_new_silent"
    BOTH_ACTIVE_DIFF_PROVIDER_OR_REASON = "both_active_diff_provider_or_reason"
    PLATFORM_FAILURE = "platform_failure"


@dataclass(frozen=True)
class LegacyAttentionSignal:
    """Read-only probe of the pre-cutover attention decision.

    Not a product policy owner — used only for shadow comparison.
    """

    active: bool
    attention_class: str | None = None
    provider: str | None = None
    reason: str | None = None
    attention_id: str | None = None
    source: str = "legacy"

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "attention_class": self.attention_class,
            "provider": self.provider,
            "reason": self.reason,
            "attention_id": self.attention_id,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> LegacyAttentionSignal:
        if not payload:
            return cls(active=False, source="legacy")
        return cls(
            active=bool(payload.get("active")),
            attention_class=_optional_str(payload.get("attention_class")),
            provider=_optional_str(payload.get("provider")),
            reason=_optional_str(payload.get("reason")),
            attention_id=_optional_str(payload.get("attention_id")),
            source=str(payload.get("source") or "legacy"),
        )


@dataclass(frozen=True)
class AttentionCompareResult:
    agreement: AttentionAgreement
    legacy_active: bool
    new_active: bool
    legacy_class: str | None
    new_class: str | None
    legacy_provider: str | None
    new_provider: str | None
    legacy_reason: str | None
    new_reason: str | None
    new_primary_id: str | None
    new_silence: str | None
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "agreement": self.agreement.value,
            "legacy_active": self.legacy_active,
            "new_active": self.new_active,
            "legacy_class": self.legacy_class,
            "new_class": self.new_class,
            "legacy_provider": self.legacy_provider,
            "new_provider": self.new_provider,
            "legacy_reason": self.legacy_reason,
            "new_reason": self.new_reason,
            "new_primary_id": self.new_primary_id,
            "new_silence": self.new_silence,
            "detail": self.detail,
        }


def is_attention_interrupt_active(state: AttentionState | None) -> bool:
    """True when AttentionState has visible ranks 1–5 (silence is None)."""
    if state is None:
        return False
    return state.silence is None and state.primary is not None


def compare_attention(
    legacy: LegacyAttentionSignal | None,
    state: AttentionState | None,
    *,
    platform_failed: bool = False,
) -> AttentionCompareResult:
    """Classify agreement between legacy probe and AttentionState."""
    legacy = legacy or LegacyAttentionSignal(active=False)
    if platform_failed or state is None:
        return AttentionCompareResult(
            agreement=AttentionAgreement.PLATFORM_FAILURE,
            legacy_active=legacy.active,
            new_active=False,
            legacy_class=legacy.attention_class,
            new_class=None,
            legacy_provider=legacy.provider,
            new_provider=None,
            legacy_reason=legacy.reason,
            new_reason=None,
            new_primary_id=None,
            new_silence=None,
            detail="attention_platform_failure",
        )

    new_active = is_attention_interrupt_active(state)
    primary = state.primary
    new_class = primary.attention_class.value if primary is not None else None
    new_provider = primary.provider if primary is not None else None
    new_reason = primary.reason.code if primary is not None else None
    new_primary_id = primary.attention_id if primary is not None else None
    new_silence = state.silence.value if state.silence is not None else None

    if not legacy.active and not new_active:
        return AttentionCompareResult(
            agreement=AttentionAgreement.EXACT_AGREEMENT,
            legacy_active=False,
            new_active=False,
            legacy_class=legacy.attention_class,
            new_class=new_class,
            legacy_provider=legacy.provider,
            new_provider=new_provider,
            legacy_reason=legacy.reason,
            new_reason=new_reason,
            new_primary_id=new_primary_id,
            new_silence=new_silence,
            detail="both_silent",
        )

    if not legacy.active and new_active:
        return AttentionCompareResult(
            agreement=AttentionAgreement.OLD_SILENT_NEW_ACTIVE,
            legacy_active=False,
            new_active=True,
            legacy_class=legacy.attention_class,
            new_class=new_class,
            legacy_provider=legacy.provider,
            new_provider=new_provider,
            legacy_reason=legacy.reason,
            new_reason=new_reason,
            new_primary_id=new_primary_id,
            new_silence=new_silence,
            detail="false_interruption_candidate",
        )

    if legacy.active and not new_active:
        return AttentionCompareResult(
            agreement=AttentionAgreement.OLD_ACTIVE_NEW_SILENT,
            legacy_active=True,
            new_active=False,
            legacy_class=legacy.attention_class,
            new_class=new_class,
            legacy_provider=legacy.provider,
            new_provider=new_provider,
            legacy_reason=legacy.reason,
            new_reason=new_reason,
            new_primary_id=new_primary_id,
            new_silence=new_silence,
            detail="false_silence_candidate",
        )

    # Both active.
    legacy_class = (legacy.attention_class or "").strip().lower() or None
    same_class = (
        legacy_class is not None
        and new_class is not None
        and legacy_class == new_class
    )
    same_provider = _norm_provider(legacy.provider) == _norm_provider(new_provider)
    same_reason = _norm_reason(legacy.reason) == _norm_reason(new_reason)

    if same_class and same_provider and same_reason:
        if legacy.attention_id and new_primary_id and legacy.attention_id != new_primary_id:
            agreement = AttentionAgreement.SAME_CLASS_DIFF_PRIMARY
            detail = "same_class_provider_reason_diff_id"
        else:
            agreement = AttentionAgreement.EXACT_AGREEMENT
            detail = "both_active_match"
    elif same_class:
        agreement = AttentionAgreement.SAME_CLASS_DIFF_PRIMARY
        detail = "same_class_diff_primary"
    else:
        agreement = AttentionAgreement.BOTH_ACTIVE_DIFF_PROVIDER_OR_REASON
        detail = "both_active_diff_provider_or_reason"

    return AttentionCompareResult(
        agreement=agreement,
        legacy_active=True,
        new_active=True,
        legacy_class=legacy.attention_class,
        new_class=new_class,
        legacy_provider=legacy.provider,
        new_provider=new_provider,
        legacy_reason=legacy.reason,
        new_reason=new_reason,
        new_primary_id=new_primary_id,
        new_silence=new_silence,
        detail=detail,
    )


def ensure_attention_compare_tables(db: Any, *, commit: bool = True) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS attention_compare (
            user_id          TEXT NOT NULL,
            surface          TEXT NOT NULL,
            generated_at     TEXT NOT NULL,
            agreement        TEXT NOT NULL,
            legacy_active    INTEGER NOT NULL DEFAULT 0,
            new_active       INTEGER NOT NULL DEFAULT 0,
            legacy_class     TEXT,
            new_class        TEXT,
            legacy_provider  TEXT,
            new_provider     TEXT,
            legacy_reason    TEXT,
            new_reason       TEXT,
            new_primary_id   TEXT,
            new_silence      TEXT,
            detail           TEXT,
            legacy_json      TEXT NOT NULL,
            compare_json     TEXT NOT NULL,
            PRIMARY KEY (user_id, surface)
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_attention_compare_agreement
        ON attention_compare (agreement, generated_at)
        """
    )
    if commit:
        db.commit()


def persist_attention_compare(
    db: Any,
    user_id: str,
    surface: AttentionSurface,
    result: AttentionCompareResult,
    legacy: LegacyAttentionSignal,
    *,
    generated_at: str | None = None,
    commit: bool = True,
) -> None:
    ensure_attention_compare_tables(db, commit=False)
    ts = generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    db.execute(
        """
        INSERT INTO attention_compare (
            user_id, surface, generated_at, agreement,
            legacy_active, new_active, legacy_class, new_class,
            legacy_provider, new_provider, legacy_reason, new_reason,
            new_primary_id, new_silence, detail, legacy_json, compare_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, surface) DO UPDATE SET
            generated_at=excluded.generated_at,
            agreement=excluded.agreement,
            legacy_active=excluded.legacy_active,
            new_active=excluded.new_active,
            legacy_class=excluded.legacy_class,
            new_class=excluded.new_class,
            legacy_provider=excluded.legacy_provider,
            new_provider=excluded.new_provider,
            legacy_reason=excluded.legacy_reason,
            new_reason=excluded.new_reason,
            new_primary_id=excluded.new_primary_id,
            new_silence=excluded.new_silence,
            detail=excluded.detail,
            legacy_json=excluded.legacy_json,
            compare_json=excluded.compare_json
        """,
        (
            str(user_id),
            surface,
            ts,
            result.agreement.value,
            1 if result.legacy_active else 0,
            1 if result.new_active else 0,
            result.legacy_class,
            result.new_class,
            result.legacy_provider,
            result.new_provider,
            result.legacy_reason,
            result.new_reason,
            result.new_primary_id,
            result.new_silence,
            result.detail,
            json.dumps(legacy.to_dict(), separators=(",", ":"), sort_keys=True),
            json.dumps(result.to_dict(), separators=(",", ":"), sort_keys=True),
        ),
    )
    if commit:
        db.commit()


def load_attention_compare(
    db: Any,
    user_id: str,
    surface: AttentionSurface,
) -> dict[str, Any] | None:
    ensure_attention_compare_tables(db, commit=False)
    row = db.execute(
        """
        SELECT user_id, surface, generated_at, agreement, legacy_active, new_active,
               legacy_class, new_class, legacy_provider, new_provider,
               legacy_reason, new_reason, new_primary_id, new_silence,
               detail, legacy_json, compare_json
        FROM attention_compare
        WHERE user_id = ? AND surface = ?
        """,
        (str(user_id), surface),
    ).fetchone()
    if not row:
        return None
    mapping = dict(row) if not isinstance(row, dict) else row
    try:
        compare = json.loads(mapping["compare_json"])
    except Exception:
        compare = None
    try:
        legacy = json.loads(mapping["legacy_json"])
    except Exception:
        legacy = None
    return {
        "user_id": mapping["user_id"],
        "surface": mapping["surface"],
        "generated_at": mapping["generated_at"],
        "agreement": mapping["agreement"],
        "legacy_active": bool(mapping["legacy_active"]),
        "new_active": bool(mapping["new_active"]),
        "legacy_class": mapping.get("legacy_class"),
        "new_class": mapping.get("new_class"),
        "legacy_provider": mapping.get("legacy_provider"),
        "new_provider": mapping.get("new_provider"),
        "legacy_reason": mapping.get("legacy_reason"),
        "new_reason": mapping.get("new_reason"),
        "new_primary_id": mapping.get("new_primary_id"),
        "new_silence": mapping.get("new_silence"),
        "detail": mapping.get("detail"),
        "legacy": legacy,
        "compare": compare,
    }


def record_attention_compare(
    db: Any,
    user_id: str,
    surface: AttentionSurface,
    *,
    legacy: LegacyAttentionSignal | None,
    state: AttentionState | None,
    platform_failed: bool = False,
    generated_at: str | None = None,
    commit: bool = True,
) -> AttentionCompareResult | None:
    """Compare and persist. Never raises — returns None on recorder failure."""
    try:
        result = compare_attention(
            legacy, state, platform_failed=platform_failed
        )
        persist_attention_compare(
            db,
            user_id,
            surface,
            result,
            legacy or LegacyAttentionSignal(active=False),
            generated_at=generated_at,
            commit=commit,
        )
        return result
    except Exception:
        logger.exception(
            "attention_compare_failed user_id=%s surface=%s",
            user_id,
            surface,
        )
        return None


def legacy_signal_from_home(
    *,
    home_state: str | None,
    action_required: bool = False,
    provider: str | None = None,
) -> LegacyAttentionSignal:
    """Probe Home legacy attention (LOGIN / capability sign-in CTA)."""
    state = (home_state or "").strip().lower()
    active = state == "login" or bool(action_required)
    if not active:
        return LegacyAttentionSignal(active=False, source="home_legacy")
    return LegacyAttentionSignal(
        active=True,
        attention_class="auth_blocker",
        provider=_optional_str(provider),
        reason="login",
        source="home_legacy",
    )


def legacy_signal_from_worker(
    *,
    needs_login_count: int = 0,
    needs_sign_in: int = 0,
    provider: str | None = None,
) -> LegacyAttentionSignal:
    """Probe Worker legacy interrupt from account-status counts."""
    active = int(needs_login_count or 0) > 0 or int(needs_sign_in or 0) > 0
    if not active:
        return LegacyAttentionSignal(active=False, source="worker_legacy")
    return LegacyAttentionSignal(
        active=True,
        attention_class="auth_blocker",
        provider=_optional_str(provider),
        reason="login",
        source="worker_legacy",
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _norm_provider(value: str | None) -> str:
    return (value or "").strip().lower()


def _norm_reason(value: str | None) -> str:
    return (value or "").strip().lower()
