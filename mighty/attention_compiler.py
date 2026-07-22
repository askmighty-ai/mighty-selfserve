"""AttentionCompiler — platform facts → AttentionItem.

Pure deterministic producers:

* AuthTruth → optional auth_blocker / access_degraded
* AuthorizeRow → optional agent_authorization
* TrustSignal → optional trust (Milestone 5)
* WorkerSignal / BenefitSignal / AccountState → M4 producers

No ranking, overlays, persistence, Home, or notifications.

See docs/ATTENTION_COMPILER.md and docs/ATTENTION_COMPILER_TRUST.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence

from mighty.account_state import CONN_CONNECTED, DATA_NONE, DATA_PARTIAL
from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    REASON_AWAITING_USER,
    REASON_CAPTCHA,
    REASON_CONSENT,
    REASON_DATA_GAP,
    REASON_LOGIN,
    REASON_LOGIN_UNKNOWN,
    REASON_MFA,
    REASON_NEVER_REPORTED,
    REASON_OPPORTUNITY,
    REASON_PENDING_AUTHORIZATION,
    REASON_RUNTIME_OFFLINE,
    REASON_STALE,
    REASON_TRUST,
    REASON_UNKNOWN_HUMAN,
    REASON_VALUE_AT_RISK,
    REASON_WORKER_MISSING,
    REASON_WORKER_UNREACHABLE,
    AttentionClass,
    AttentionCtaKey,
    AttentionItem,
    AttentionReason,
    AttentionSourceKind,
    AttentionUrgency,
)
from mighty.classify import is_actionable, is_needs_attention
from mighty.auth_truth import (
    ACCESS_BROWSER_SESSION,
    ACCESS_MANAGED_RUNTIME,
    AuthInterruption,
    AuthTruth,
)
from mighty.authentication_state import AuthenticationState

AUTHORIZE_STATUS_PENDING = "pending"

# Auth-blocker reason codes the compiler may emit (RFC §3 / §4).
_AUTH_BLOCKER_REASONS = frozenset(
    {
        REASON_LOGIN,
        REASON_MFA,
        REASON_CAPTCHA,
        REASON_CONSENT,
        REASON_UNKNOWN_HUMAN,
    }
)

_FINGERPRINT_SUFFIX = "needs_human"


def auth_blocker_fingerprint(provider: str) -> str:
    """Stable root-cause identity for human-needed auth on a provider.

    Intentionally independent of interruption reason so login→captcha updates
    the same candidate (RFC Part XIV scenario 3).
    """
    return f"auth:{_normalize_provider(provider)}:{_FINGERPRINT_SUFFIX}"


def auth_blocker_attention_id(user_id: str, provider: str) -> str:
    """Deterministic attention_id for auth_blocker candidates.

    Stable across reason changes; keyed only by user, class, and provider
    fingerprint identity.
    """
    uid = str(user_id).strip()
    return f"att_{uid}_auth_blocker_{_normalize_provider(provider)}_{_FINGERPRINT_SUFFIX}"


def auth_truth_source_ref(user_id: str, provider: str) -> str:
    """Join key back to the AuthTruth projection for (user, provider)."""
    return f"auth_truth:{str(user_id).strip()}:{_normalize_provider(provider)}"


def compile_auth_attention(truth: AuthTruth) -> AttentionItem | None:
    """Compile one AuthTruth into an optional auth_blocker AttentionItem.

    Mapping (RFC auth-blocker rule):

    * ``needs_human`` is false (e.g. signed_in / recovering) → ``None``
    * ``needs_human`` is true with reason login / mfa / captcha / consent /
      unknown_human → one ``auth_blocker`` candidate

    Stale-without-needs_human does **not** emit (``access_degraded`` is later).
    Dual-path Runtime pain on a non-primary method never reaches this function
    as ``needs_human=true`` — AuthTruth already projects primary only.
    """
    if not truth.needs_human:
        return None

    provider = _normalize_provider(truth.provider)
    reason_code = _resolve_auth_blocker_reason(truth)

    return AttentionItem(
        schema_version=ATTENTION_ITEM_SCHEMA_VERSION,
        attention_id=auth_blocker_attention_id(truth.user_id, provider),
        user_id=str(truth.user_id).strip(),
        attention_class=AttentionClass.AUTH_BLOCKER,
        urgency=AttentionUrgency.BLOCKER,
        provider=provider,
        fingerprint=auth_blocker_fingerprint(provider),
        reason=AttentionReason(code=reason_code),
        cta_key=_cta_for_access_method(truth.access_method),
        source_kind=AttentionSourceKind.AUTH,
        source_ref=auth_truth_source_ref(truth.user_id, provider),
        observed_at=truth.observed_at,
        becomes_stale_at=None,
        interruption_expected=bool(truth.interruption_expected),
    )


def _normalize_provider(provider: str) -> str:
    return str(provider or "").strip().lower()


def _resolve_auth_blocker_reason(truth: AuthTruth) -> str:
    """Pick a stable machine reason code for the auth_blocker candidate."""
    for candidate in (truth.needs_human_reason, truth.interruption.value):
        if not candidate:
            continue
        code = str(candidate).strip().lower()
        if code in _AUTH_BLOCKER_REASONS:
            return code
        if code == AuthInterruption.NONE.value:
            continue
    return REASON_UNKNOWN_HUMAN


def _cta_for_access_method(access_method: str) -> AttentionCtaKey:
    method = str(access_method or "").strip().lower()
    if method == ACCESS_MANAGED_RUNTIME:
        return AttentionCtaKey.FOCUS_MANAGED_RUNTIME
    if method == ACCESS_BROWSER_SESSION:
        return AttentionCtaKey.START_PROVIDER_LOGIN
    # api / manual never project needs_human today; keep a safe no-op CTA.
    return AttentionCtaKey.NOOP


# --- AuthorizeRow → agent_authorization (PR 2F) ---------------------------------


class AuthorizeRowValidationError(ValueError):
    """Raised when an AuthorizeRow is missing required identity fields."""


@dataclass(frozen=True)
class AuthorizeRow:
    """Minimal authorize-store fact for the AttentionCompiler (RFC §4.3).

    Not a second ledger — loaders map existing ``actions`` rows into this shape.
    """

    action_id: str
    user_id: str
    status: str
    created_at: str | None = None
    expires_at: str | None = None
    provider: str | None = None

    def __post_init__(self) -> None:
        action_id = str(self.action_id or "").strip()
        user_id = str(self.user_id or "").strip()
        if not action_id:
            raise AuthorizeRowValidationError("action_id must be a non-empty string")
        if not user_id:
            raise AuthorizeRowValidationError("user_id must be a non-empty string")
        if action_id != self.action_id:
            object.__setattr__(self, "action_id", action_id)
        if user_id != self.user_id:
            object.__setattr__(self, "user_id", user_id)
        status = str(self.status or "").strip().lower()
        if status != self.status:
            object.__setattr__(self, "status", status)
        if self.provider is not None:
            provider = str(self.provider).strip().lower() or None
            if provider != self.provider:
                object.__setattr__(self, "provider", provider)


def authorize_row_fingerprint(action_id: str) -> str:
    """Stable root-cause identity for a pending authorize row."""
    return f"authorize:row:{str(action_id).strip()}"


def authorize_attention_id(user_id: str, action_id: str) -> str:
    """Deterministic attention_id for agent_authorization candidates."""
    return (
        f"att_{str(user_id).strip()}_agent_authorization_row{str(action_id).strip()}"
    )


def authorize_source_ref(action_id: str) -> str:
    """Join key back to the authorize store row."""
    return f"authorize:{str(action_id).strip()}"


def access_degraded_fingerprint(provider: str) -> str:
    """Stable root-cause identity for non-blocker auth degradation."""
    return f"auth:{_normalize_provider(provider)}:access_degraded"


def access_degraded_attention_id(user_id: str, provider: str) -> str:
    """Deterministic attention_id for access_degraded candidates."""
    return (
        f"att_{str(user_id).strip()}_access_degraded_{_normalize_provider(provider)}"
    )


def compile_access_degraded_attention(truth: AuthTruth) -> AttentionItem | None:
    """Compile AuthTruth into an optional access_degraded AttentionItem.

    Emits when the primary method is stale and/or login_unknown **without**
    ``needs_human``. When ``needs_human`` is true, ``compile_auth_attention``
    owns the candidate and this returns ``None``.
    """
    if truth.needs_human:
        return None

    reason_code: str | None = None
    if truth.stale:
        reason_code = REASON_STALE
    elif truth.state == AuthenticationState.LOGIN_UNKNOWN:
        reason_code = REASON_LOGIN_UNKNOWN
    else:
        return None

    provider = _normalize_provider(truth.provider)
    return AttentionItem(
        schema_version=ATTENTION_ITEM_SCHEMA_VERSION,
        attention_id=access_degraded_attention_id(truth.user_id, provider),
        user_id=str(truth.user_id).strip(),
        attention_class=AttentionClass.ACCESS_DEGRADED,
        urgency=AttentionUrgency.INFORMATIONAL,
        provider=provider,
        fingerprint=access_degraded_fingerprint(provider),
        reason=AttentionReason(code=reason_code),
        cta_key=AttentionCtaKey.OPEN_ACCOUNT_DETAIL,
        source_kind=AttentionSourceKind.AUTH,
        source_ref=auth_truth_source_ref(truth.user_id, provider),
        observed_at=truth.observed_at,
        becomes_stale_at=None,
        interruption_expected=bool(truth.interruption_expected),
    )


def compile_authorize_attention(row: AuthorizeRow) -> AttentionItem | None:
    """Compile one AuthorizeRow into an optional agent_authorization item.

    Only ``status=pending`` emits. Terminal statuses (approved, denied, expired,
    …) return ``None`` so the candidate disappears without Store upserts
    (RFC D5 / Part XIV scenario 5).
    """
    if not isinstance(row, AuthorizeRow):
        raise AuthorizeRowValidationError("row must be an AuthorizeRow")
    if row.status != AUTHORIZE_STATUS_PENDING:
        return None

    return AttentionItem(
        schema_version=ATTENTION_ITEM_SCHEMA_VERSION,
        attention_id=authorize_attention_id(row.user_id, row.action_id),
        user_id=row.user_id,
        attention_class=AttentionClass.AGENT_AUTHORIZATION,
        urgency=AttentionUrgency.BLOCKER,
        provider=row.provider,
        fingerprint=authorize_row_fingerprint(row.action_id),
        reason=AttentionReason(code=REASON_PENDING_AUTHORIZATION),
        cta_key=AttentionCtaKey.OPEN_ACTIVITY_APPROVAL,
        source_kind=AttentionSourceKind.AUTHORIZE,
        source_ref=authorize_source_ref(row.action_id),
        observed_at=row.created_at,
        becomes_stale_at=row.expires_at,
        interruption_expected=False,
    )


# --- TrustSignal → trust (Milestone 5) ----------------------------------------


_TRUST_EMIT_STATUSES = frozenset(
    {
        "awaiting_user",
        "runtime_offline",
        "never_reported",
        "stale",
    }
)

_TRUST_REASON_BY_STATUS = {
    "awaiting_user": REASON_AWAITING_USER,
    "runtime_offline": REASON_RUNTIME_OFFLINE,
    "never_reported": REASON_NEVER_REPORTED,
    "stale": REASON_STALE,
}


@dataclass(frozen=True)
class TrustSignal:
    """Minimal Runtime trust fact for AttentionCompiler (RFC §4.2 trust)."""

    user_id: str
    provider: str
    access_method: str
    presentation_status: str
    authentication_state: str | None = None
    access_health: str | None = None
    recovery_state: str | None = None
    runtime_state: str | None = None
    escalation_reason: str | None = None
    observed_at: str | None = None
    needs_human: bool = False
    interruption_expected: bool = False

    def __post_init__(self) -> None:
        user_id = str(self.user_id or "").strip()
        provider = _normalize_provider(self.provider)
        if not user_id:
            raise ValueError("TrustSignal.user_id must be a non-empty string")
        if not provider:
            raise ValueError("TrustSignal.provider must be a non-empty string")
        if user_id != self.user_id:
            object.__setattr__(self, "user_id", user_id)
        if provider != self.provider:
            object.__setattr__(self, "provider", provider)
        method = str(self.access_method or "").strip().lower()
        if method != self.access_method:
            object.__setattr__(self, "access_method", method)
        status = str(self.presentation_status or "").strip().lower()
        if status != self.presentation_status:
            object.__setattr__(self, "presentation_status", status)


def trust_fingerprint(provider: str) -> str:
    """Stable root-cause identity for Runtime trust on a provider."""
    return f"trust:{_normalize_provider(provider)}:runtime"


def trust_attention_id(user_id: str, provider: str) -> str:
    """Deterministic attention_id for trust candidates."""
    return f"att_{str(user_id).strip()}_trust_{_normalize_provider(provider)}"


def trust_source_ref(user_id: str, provider: str) -> str:
    """Join key back to runtime_access_state."""
    return f"runtime_access_state:{str(user_id).strip()}:{_normalize_provider(provider)}"


def compile_trust_attention(signal: TrustSignal) -> AttentionItem | None:
    """Compile TrustSignal into an optional trust AttentionItem.

    Emits only for managed_runtime primary method when presentation status
    indicates broken Runtime trust. When ``needs_human`` is true, auth_blocker
    owns the candidate and this returns ``None``.
    """
    if not isinstance(signal, TrustSignal):
        raise TypeError("signal must be a TrustSignal")
    if signal.access_method != ACCESS_MANAGED_RUNTIME:
        return None
    if signal.needs_human:
        return None
    status = signal.presentation_status
    if status not in _TRUST_EMIT_STATUSES:
        return None
    # Stale healthy signed-in is freshness noise, not trust interrupt.
    if status == "stale":
        auth = str(signal.authentication_state or "").strip().upper()
        health = str(signal.access_health or "").strip().lower()
        if auth == "SIGNED_IN" and health in {"", "healthy"}:
            return None

    reason = _TRUST_REASON_BY_STATUS.get(status, REASON_TRUST)
    return AttentionItem(
        schema_version=ATTENTION_ITEM_SCHEMA_VERSION,
        attention_id=trust_attention_id(signal.user_id, signal.provider),
        user_id=signal.user_id,
        attention_class=AttentionClass.TRUST,
        urgency=AttentionUrgency.BLOCKER,
        provider=signal.provider,
        fingerprint=trust_fingerprint(signal.provider),
        reason=AttentionReason(code=reason),
        cta_key=AttentionCtaKey.FOCUS_MANAGED_RUNTIME,
        source_kind=AttentionSourceKind.TRUST,
        source_ref=trust_source_ref(signal.user_id, signal.provider),
        observed_at=signal.observed_at,
        becomes_stale_at=None,
        interruption_expected=bool(signal.interruption_expected),
    )


# --- WorkerSignal → system (Milestone 4) --------------------------------------


# Reachability SLA for extension heartbeat (design note).
WORKER_REACHABLE_SLA_SECONDS = 72 * 60 * 60


@dataclass(frozen=True)
class WorkerSignal:
    """Minimal worker/extension presence fact for AttentionCompiler (RFC §4.3)."""

    user_id: str
    installed: bool
    reachable: bool
    last_seen_at: str | None = None
    version: str | None = None
    update_required: bool = False
    enrolled_account_count: int = 0

    def __post_init__(self) -> None:
        user_id = str(self.user_id or "").strip()
        if not user_id:
            raise ValueError("WorkerSignal.user_id must be a non-empty string")
        if user_id != self.user_id:
            object.__setattr__(self, "user_id", user_id)
        if self.last_seen_at is not None:
            text = str(self.last_seen_at).strip() or None
            if text != self.last_seen_at:
                object.__setattr__(self, "last_seen_at", text)
        if self.version is not None:
            version = str(self.version).strip() or None
            if version != self.version:
                object.__setattr__(self, "version", version)


def worker_system_fingerprint() -> str:
    """Stable root-cause identity for missing/unreachable worker setup."""
    return "worker:setup"


def worker_system_attention_id(user_id: str) -> str:
    """Deterministic attention_id for the worker system candidate."""
    return f"att_{str(user_id).strip()}_system_worker"


def worker_source_ref(user_id: str) -> str:
    """Join key back to the user worker heartbeat row."""
    return f"worker:{str(user_id).strip()}"


def compile_worker_attention(signal: WorkerSignal) -> AttentionItem | None:
    """Compile WorkerSignal into an optional system AttentionItem.

    Emits only when the user has enrolled accounts and the worker is missing
    or unreachable. Empty onboarding remains enrollment UX outside Attention.
    ``update_required`` alone does not emit (not a blocker for M4).
    """
    if not isinstance(signal, WorkerSignal):
        raise TypeError("signal must be a WorkerSignal")
    if int(signal.enrolled_account_count or 0) <= 0:
        return None
    if signal.installed and signal.reachable:
        return None

    reason = (
        REASON_WORKER_MISSING
        if not signal.installed
        else REASON_WORKER_UNREACHABLE
    )
    return AttentionItem(
        schema_version=ATTENTION_ITEM_SCHEMA_VERSION,
        attention_id=worker_system_attention_id(signal.user_id),
        user_id=signal.user_id,
        attention_class=AttentionClass.SYSTEM,
        urgency=AttentionUrgency.BLOCKER,
        provider=None,
        fingerprint=worker_system_fingerprint(),
        reason=AttentionReason(code=reason),
        cta_key=AttentionCtaKey.INSTALL_WORKER,
        source_kind=AttentionSourceKind.WORKER,
        source_ref=worker_source_ref(signal.user_id),
        observed_at=signal.last_seen_at,
        becomes_stale_at=None,
        interruption_expected=False,
    )


# --- BenefitSignal → value_at_risk / opportunity (Milestone 4) ----------------


BenefitKind = Literal["expiring", "opportunity"]
BenefitUrgency = Literal["urgent", "soon", "info"]

# Design note: actionable types with days_left at or below this are value_at_risk.
VALUE_AT_RISK_DAYS_LEFT_MAX = 14


@dataclass(frozen=True)
class BenefitSignal:
    """Minimal benefit/action-item fact for AttentionCompiler (RFC §4.3)."""

    user_id: str
    provider: str
    field_key: str
    btype: str
    urgency: str = "info"
    days_left: int | None = None
    exp_date: str | None = None
    label: str | None = None
    value: str | None = None
    kind: str = "opportunity"
    observed_at: str | None = None
    source_item_id: str | None = None

    def __post_init__(self) -> None:
        user_id = str(self.user_id or "").strip()
        provider = _normalize_provider(self.provider)
        field_key = str(self.field_key or "").strip()
        if not user_id:
            raise ValueError("BenefitSignal.user_id must be a non-empty string")
        if not provider:
            raise ValueError("BenefitSignal.provider must be a non-empty string")
        if not field_key:
            raise ValueError("BenefitSignal.field_key must be a non-empty string")
        if user_id != self.user_id:
            object.__setattr__(self, "user_id", user_id)
        if provider != self.provider:
            object.__setattr__(self, "provider", provider)
        if field_key != self.field_key:
            object.__setattr__(self, "field_key", field_key)
        urgency = str(self.urgency or "info").strip().lower() or "info"
        if urgency not in {"urgent", "soon", "info"}:
            urgency = "info"
        if urgency != self.urgency:
            object.__setattr__(self, "urgency", urgency)
        btype = str(self.btype or "other").strip().lower() or "other"
        if btype != self.btype:
            object.__setattr__(self, "btype", btype)


def benefit_fingerprint(provider: str, field_key: str) -> str:
    """Stable root-cause identity for a benefit field."""
    return f"benefit:{_normalize_provider(provider)}:{str(field_key).strip()}"


def benefit_attention_id(user_id: str, attention_class: str, provider: str, field_key: str) -> str:
    """Deterministic attention_id for benefit-derived candidates."""
    return (
        f"att_{str(user_id).strip()}_{attention_class}"
        f"_{_normalize_provider(provider)}_{str(field_key).strip()}"
    )


def benefit_source_ref(signal: BenefitSignal) -> str:
    """Join key back to the owning action_item / benefit field."""
    if signal.source_item_id:
        return f"action_item:{str(signal.source_item_id).strip()}"
    return benefit_fingerprint(signal.provider, signal.field_key)


def benefit_is_value_at_risk(signal: BenefitSignal) -> bool:
    """True when the signal should compile as value_at_risk (not opportunity)."""
    if not (is_actionable(signal.btype) or is_needs_attention(signal.btype)):
        return False
    if signal.urgency in {"urgent", "soon"}:
        return True
    if signal.days_left is not None and signal.days_left <= VALUE_AT_RISK_DAYS_LEFT_MAX:
        return is_actionable(signal.btype) or is_needs_attention(signal.btype)
    return False


def compile_benefit_attention(signal: BenefitSignal) -> AttentionItem | None:
    """Compile one BenefitSignal into value_at_risk or opportunity.

    Mutually exclusive: value_at_risk wins when time pressure applies;
    otherwise actionable benefits emit opportunity. Non-actionable /
    non-attention types return ``None``.
    """
    if not isinstance(signal, BenefitSignal):
        raise TypeError("signal must be a BenefitSignal")

    actionable = is_actionable(signal.btype) or is_needs_attention(signal.btype)
    if not actionable:
        return None

    if benefit_is_value_at_risk(signal):
        return AttentionItem(
            schema_version=ATTENTION_ITEM_SCHEMA_VERSION,
            attention_id=benefit_attention_id(
                signal.user_id,
                AttentionClass.VALUE_AT_RISK.value,
                signal.provider,
                signal.field_key,
            ),
            user_id=signal.user_id,
            attention_class=AttentionClass.VALUE_AT_RISK,
            urgency=AttentionUrgency.TIME_SENSITIVE,
            provider=signal.provider,
            fingerprint=benefit_fingerprint(signal.provider, signal.field_key),
            reason=AttentionReason(code=REASON_VALUE_AT_RISK),
            cta_key=AttentionCtaKey.OPEN_ACCOUNT_DETAIL,
            source_kind=AttentionSourceKind.BENEFIT,
            source_ref=benefit_source_ref(signal),
            observed_at=signal.observed_at,
            becomes_stale_at=signal.exp_date,
            interruption_expected=False,
        )

    return AttentionItem(
        schema_version=ATTENTION_ITEM_SCHEMA_VERSION,
        attention_id=benefit_attention_id(
            signal.user_id,
            AttentionClass.OPPORTUNITY.value,
            signal.provider,
            signal.field_key,
        ),
        user_id=signal.user_id,
        attention_class=AttentionClass.OPPORTUNITY,
        urgency=AttentionUrgency.OPPORTUNITY,
        provider=signal.provider,
        fingerprint=benefit_fingerprint(signal.provider, signal.field_key),
        reason=AttentionReason(code=REASON_OPPORTUNITY),
        cta_key=AttentionCtaKey.OPEN_ACCOUNT_DETAIL,
        source_kind=AttentionSourceKind.BENEFIT,
        source_ref=benefit_source_ref(signal),
        observed_at=signal.observed_at,
        becomes_stale_at=None,
        interruption_expected=False,
    )


# --- AccountState → data_gap (Milestone 4) -------------------------------------


def data_gap_fingerprint(provider: str) -> str:
    """Stable root-cause identity for missing/partial account data."""
    return f"account_data:{_normalize_provider(provider)}:data_gap"


def data_gap_attention_id(user_id: str, provider: str) -> str:
    """Deterministic attention_id for data_gap candidates."""
    return f"att_{str(user_id).strip()}_data_gap_{_normalize_provider(provider)}"


def account_state_source_ref(user_id: str, provider: str) -> str:
    """Join key back to the AccountState row for (user, provider)."""
    return f"account_state:{str(user_id).strip()}:{_normalize_provider(provider)}"


def compile_data_gap_attention(account: Any) -> AttentionItem | None:
    """Compile AccountState into an optional data_gap AttentionItem.

    Emits when the account is ``connected`` and ``data_status`` is ``none`` or
    ``partial``. Auth blockers remain a separate producer; ranking prefers them.
    """
    connection_state = str(getattr(account, "connection_state", "") or "").strip().lower()
    data_status = str(getattr(account, "data_status", "") or "").strip().lower()
    if connection_state != CONN_CONNECTED:
        return None
    if data_status not in {DATA_NONE, DATA_PARTIAL}:
        return None

    user_id = str(getattr(account, "user_id", "") or "").strip()
    provider = _normalize_provider(getattr(account, "provider", "") or "")
    if not user_id or not provider:
        return None

    observed = getattr(account, "last_data_refresh", None) or getattr(
        account, "updated_at", None
    )
    observed_at = str(observed).strip() if observed else None

    return AttentionItem(
        schema_version=ATTENTION_ITEM_SCHEMA_VERSION,
        attention_id=data_gap_attention_id(user_id, provider),
        user_id=user_id,
        attention_class=AttentionClass.DATA_GAP,
        urgency=AttentionUrgency.INFORMATIONAL,
        provider=provider,
        fingerprint=data_gap_fingerprint(provider),
        reason=AttentionReason(code=REASON_DATA_GAP),
        cta_key=AttentionCtaKey.OPEN_PROVIDER_SURFACE,
        source_kind=AttentionSourceKind.ACCOUNT_DATA,
        source_ref=account_state_source_ref(user_id, provider),
        observed_at=observed_at or None,
        becomes_stale_at=None,
        interruption_expected=False,
    )


def compile_attention_candidates(
    *,
    auth_truths: Sequence[AuthTruth] = (),
    authorize_rows: Sequence[AuthorizeRow] = (),
    trust_signals: Sequence[TrustSignal] = (),
    worker_signal: WorkerSignal | None = None,
    benefit_signals: Sequence[BenefitSignal] = (),
    account_states: Sequence[Any] = (),
    recovery_attention_allowed: frozenset[str] | None = None,
) -> tuple[AttentionItem, ...]:
    """Gather AttentionItems from supported compiler inputs.

    Pure and order-stable within each input family. Does not rank or apply
    overlays — see ``select_attention`` / ``compose_attention``.

    ``recovery_attention_allowed`` gates auth / trust / access_degraded
    producers (Milestone 6). ``None`` disables the gate (unit-test helper).
    When provided, only listed providers may emit those human-interrupt
    classes — typically Recovery Store ``escalated`` providers.
    """
    items: list[AttentionItem] = []
    for truth in auth_truths:
        provider = _normalize_provider(truth.provider)
        if recovery_attention_allowed is not None and provider not in recovery_attention_allowed:
            continue
        blocker = compile_auth_attention(truth)
        if blocker is not None:
            items.append(blocker)
            continue
        degraded = compile_access_degraded_attention(truth)
        if degraded is not None:
            items.append(degraded)
    for row in authorize_rows:
        authorize_item = compile_authorize_attention(row)
        if authorize_item is not None:
            items.append(authorize_item)
    for signal in trust_signals:
        provider = _normalize_provider(signal.provider)
        if recovery_attention_allowed is not None and provider not in recovery_attention_allowed:
            continue
        trust_item = compile_trust_attention(signal)
        if trust_item is not None:
            items.append(trust_item)
    if worker_signal is not None:
        worker_item = compile_worker_attention(worker_signal)
        if worker_item is not None:
            items.append(worker_item)
    for signal in benefit_signals:
        benefit_item = compile_benefit_attention(signal)
        if benefit_item is not None:
            items.append(benefit_item)
    for account in account_states:
        gap = compile_data_gap_attention(account)
        if gap is not None:
            items.append(gap)
    return tuple(items)
