"""AttentionCompiler — platform facts → AttentionItem (PR 2B / 2F).

Pure deterministic producers:

* AuthTruth → optional auth_blocker (PR 2B)
* AuthorizeRow → optional agent_authorization (PR 2F)

No ranking, overlays, persistence, Home, or notifications.

See docs/ATTENTION_COMPILER.md and docs/ATTENTION_COMPILER_AUTHORIZE.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    REASON_CAPTCHA,
    REASON_CONSENT,
    REASON_LOGIN,
    REASON_MFA,
    REASON_PENDING_AUTHORIZATION,
    REASON_UNKNOWN_HUMAN,
    AttentionClass,
    AttentionCtaKey,
    AttentionItem,
    AttentionReason,
    AttentionSourceKind,
    AttentionUrgency,
)
from mighty.auth_truth import (
    ACCESS_BROWSER_SESSION,
    ACCESS_MANAGED_RUNTIME,
    AuthInterruption,
    AuthTruth,
)

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
