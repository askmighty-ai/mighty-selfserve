"""AuthTruth projector — product-facing authentication read model (RFC v2 §3).

AuthTruth is a pure projection of Access Manager (PSS) and Runtime AccessState
publications for an account's primary access method. It is never an authority
and never accepts client-supplied auth terminals.

See docs/AUTH_TRUTH.md for why this is a projection rather than a write store.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from mighty.authentication_state import (
    AuthenticationState,
    authentication_from_transport,
    normalize_authentication_state,
)
from mighty.provider_session_state import (
    ProviderSessionState,
    get_provider_session_state,
)
from mighty.runtime_access_state import (
    DEFAULT_STALE_AFTER_SECONDS,
    get_runtime_access_state,
)
from mighty.session_verification import session_evidence_age_seconds

AUTH_TRUTH_SCHEMA_VERSION = 2

# Primary access methods (RFC AuthTruth vocabulary).
ACCESS_BROWSER_SESSION = "browser_session"
ACCESS_MANAGED_RUNTIME = "managed_runtime"
ACCESS_API = "api"
ACCESS_MANUAL = "manual"

# AccountState historically stores railway-backed access as mighty_login.
# AuthTruth uses managed_runtime; normalize at the projector boundary.
_ACCESS_METHOD_ALIASES = {
    "mighty_login": ACCESS_MANAGED_RUNTIME,
}

EVIDENCE_SOURCE_ACCESS_MANAGER = "access_manager"
EVIDENCE_SOURCE_RUNTIME = "runtime_publication"

# Provider-agnostic evidence TTL for the stale label (does not flip state).
DEFAULT_EVIDENCE_TTL_SECONDS = 24 * 3600

# Interface gap (documented, not extended here):
# Runtime AccessState publication schema v2 does not yet require or emit
# needs_human / needs_human_reason / interruption_expected (RFC §3.4).
# The projector reads those keys when present on the stored payload; otherwise
# managed_runtime needs_human defaults to false. Do not re-derive from
# recovery_state / awaiting_user.
RUNTIME_NEEDS_HUMAN_FIELDS = (
    "needs_human",
    "needs_human_reason",
    "interruption_expected",
)


class EvidenceClass(str, Enum):
    DEFINITIVE = "definitive"
    WEAK = "weak"
    NONE = "none"


class AuthInterruption(str, Enum):
    NONE = "none"
    LOGIN = "login"
    MFA = "mfa"
    CAPTCHA = "captcha"
    CONSENT = "consent"
    UNKNOWN_HUMAN = "unknown_human"


_INTERRUPTION_FROM_EVIDENCE_TYPE: dict[str, AuthInterruption] = {
    "login_page": AuthInterruption.LOGIN,
    "login_required": AuthInterruption.LOGIN,
    "session_expired": AuthInterruption.LOGIN,
    "mfa_required": AuthInterruption.MFA,
    "captcha": AuthInterruption.CAPTCHA,
    "consent": AuthInterruption.CONSENT,
}

_VALID_INTERRUPTIONS = frozenset(item.value for item in AuthInterruption)
_VALID_ACCESS_METHODS = frozenset(
    {
        ACCESS_BROWSER_SESSION,
        ACCESS_MANAGED_RUNTIME,
        ACCESS_API,
        ACCESS_MANUAL,
    }
)


@dataclass(frozen=True)
class AuthTruth:
    """Projected authentication truth for one (user, provider) primary method."""

    schema_version: int
    user_id: str
    provider: str

    state: AuthenticationState
    access_method: str

    evidence_class: EvidenceClass
    evidence_source: str
    evidence_id: str | None

    observed_at: str | None
    projected_at: str

    interruption: AuthInterruption
    interruption_expected: bool
    needs_human: bool
    needs_human_reason: str | None

    evidence_age_seconds: float | None
    stale: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload["evidence_class"] = self.evidence_class.value
        payload["interruption"] = self.interruption.value
        return payload


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _age_seconds(observed_at: str | None, *, now: datetime) -> float | None:
    when = _parse_iso(observed_at)
    if when is None:
        return None
    return (now - when).total_seconds()


def normalize_access_method(value: str | None) -> str:
    """Normalize enrollment / AccountState access_method to AuthTruth vocabulary."""
    text = str(value or "").strip().lower()
    if not text:
        return ACCESS_BROWSER_SESSION
    text = _ACCESS_METHOD_ALIASES.get(text, text)
    if text not in _VALID_ACCESS_METHODS:
        return ACCESS_BROWSER_SESSION
    return text


def normalize_auth_interruption(value: str | None) -> AuthInterruption:
    if value is None:
        return AuthInterruption.NONE
    text = str(value).strip().lower()
    if not text or text in {"null", "none"}:
        return AuthInterruption.NONE
    if text in _VALID_INTERRUPTIONS:
        return AuthInterruption(text)
    return AuthInterruption.UNKNOWN_HUMAN


def evidence_class_from_pss_confidence(
    confidence: str | None,
    *,
    has_evidence: bool,
) -> EvidenceClass:
    if not has_evidence:
        return EvidenceClass.NONE
    rank = str(confidence or "").strip().lower()
    if rank == "high":
        return EvidenceClass.DEFINITIVE
    if rank in {"medium", "low"}:
        return EvidenceClass.WEAK
    return EvidenceClass.WEAK


def _interruption_from_pss(session: ProviderSessionState) -> AuthInterruption:
    evidence_type = str(session.evidence_type or "").strip().lower()
    return _INTERRUPTION_FROM_EVIDENCE_TYPE.get(evidence_type, AuthInterruption.NONE)


def _terminal_state_from_evidence(
    transport_state: str | None,
    evidence_class: EvidenceClass,
) -> AuthenticationState:
    """Only definitive evidence may yield signed_in / signed_out."""
    mapped = authentication_from_transport(transport_state)
    if evidence_class == EvidenceClass.DEFINITIVE:
        return mapped
    # Weak / none never invent terminals.
    return AuthenticationState.LOGIN_UNKNOWN


def resolve_primary_access_method(
    db: Any,
    user_id: str,
    provider: str,
    *,
    access_method: str | None = None,
) -> str:
    """Resolve primary access_method (enrollment config, not auth evidence)."""
    if access_method is not None:
        return normalize_access_method(access_method)
    try:
        from mighty.account_state import load_account_state

        state = load_account_state(db, user_id, provider)
    except Exception:
        state = None
    if state is not None and getattr(state, "access_method", None):
        return normalize_access_method(state.access_method)
    return ACCESS_BROWSER_SESSION


def _project_browser_session(
    *,
    user_id: str,
    provider: str,
    session: ProviderSessionState | None,
    now: datetime,
    projected_at: str,
    evidence_ttl_seconds: float,
) -> AuthTruth:
    if session is None:
        return AuthTruth(
            schema_version=AUTH_TRUTH_SCHEMA_VERSION,
            user_id=user_id,
            provider=provider,
            state=AuthenticationState.LOGIN_UNKNOWN,
            access_method=ACCESS_BROWSER_SESSION,
            evidence_class=EvidenceClass.NONE,
            evidence_source=EVIDENCE_SOURCE_ACCESS_MANAGER,
            evidence_id=None,
            observed_at=None,
            projected_at=projected_at,
            interruption=AuthInterruption.NONE,
            interruption_expected=False,
            needs_human=False,
            needs_human_reason=None,
            evidence_age_seconds=None,
            stale=False,
        )

    evidence_class = evidence_class_from_pss_confidence(
        session.confidence,
        has_evidence=True,
    )
    state = _terminal_state_from_evidence(session.state, evidence_class)
    interruption = AuthInterruption.NONE
    if evidence_class == EvidenceClass.DEFINITIVE:
        interruption = _interruption_from_pss(session)
        if state == AuthenticationState.SIGNED_OUT and interruption == AuthInterruption.NONE:
            interruption = AuthInterruption.LOGIN

    needs_human = False
    needs_human_reason: str | None = None
    if state == AuthenticationState.SIGNED_OUT:
        needs_human = True
        needs_human_reason = AuthInterruption.LOGIN.value
        if interruption == AuthInterruption.NONE:
            interruption = AuthInterruption.LOGIN
    elif interruption != AuthInterruption.NONE:
        needs_human = True
        needs_human_reason = interruption.value

    age = session_evidence_age_seconds(session, now=now)
    # Stale is a freshness label only — never flips state to signed_out.
    stale = age is not None and age > float(evidence_ttl_seconds)
    evidence_id = None
    if session.evidence_type or session.observed_at:
        evidence_id = f"{session.evidence_type}:{session.observed_at or ''}"

    return AuthTruth(
        schema_version=AUTH_TRUTH_SCHEMA_VERSION,
        user_id=user_id,
        provider=provider,
        state=state,
        access_method=ACCESS_BROWSER_SESSION,
        evidence_class=evidence_class,
        evidence_source=EVIDENCE_SOURCE_ACCESS_MANAGER,
        evidence_id=evidence_id,
        observed_at=session.observed_at,
        projected_at=projected_at,
        interruption=interruption,
        interruption_expected=False,
        needs_human=needs_human,
        needs_human_reason=needs_human_reason,
        evidence_age_seconds=age,
        stale=stale,
    )


def _project_managed_runtime(
    *,
    user_id: str,
    provider: str,
    row: dict[str, Any] | None,
    now: datetime,
    projected_at: str,
    evidence_ttl_seconds: float,
    stale_after_seconds: float,
) -> AuthTruth:
    if row is None:
        return AuthTruth(
            schema_version=AUTH_TRUTH_SCHEMA_VERSION,
            user_id=user_id,
            provider=provider,
            state=AuthenticationState.LOGIN_UNKNOWN,
            access_method=ACCESS_MANAGED_RUNTIME,
            evidence_class=EvidenceClass.NONE,
            evidence_source=EVIDENCE_SOURCE_RUNTIME,
            evidence_id=None,
            observed_at=None,
            projected_at=projected_at,
            interruption=AuthInterruption.NONE,
            interruption_expected=False,
            needs_human=False,
            needs_human_reason=None,
            evidence_age_seconds=None,
            stale=False,
        )

    payload = dict(row.get("payload") or {})
    observed_at = (
        payload.get("authentication_state_changed_at")
        or payload.get("last_verified_at")
        or payload.get("updated_at")
        or row.get("updated_at")
    )
    # Publications with authentication_state are definitive product signals
    # from Runtime; absence of the field is none.
    auth_raw = payload.get("authentication_state")
    if auth_raw in (None, ""):
        evidence_class = EvidenceClass.NONE
        state = AuthenticationState.LOGIN_UNKNOWN
    else:
        evidence_class = EvidenceClass.DEFINITIVE
        state = (
            normalize_authentication_state(auth_raw)
            or authentication_from_transport(str(auth_raw))
        )

    # Optional RFC §3.4 fields — read when present; do not invent from recovery
    # or from authentication_state alone (needs_human is Runtime's product signal).
    needs_human = bool(payload.get("needs_human")) if "needs_human" in payload else False
    interruption_expected = bool(payload.get("interruption_expected") or False)
    reason_raw = payload.get("needs_human_reason")
    interruption = AuthInterruption.NONE
    needs_human_reason: str | None = None
    if needs_human:
        interruption = normalize_auth_interruption(
            str(reason_raw)
            if reason_raw not in (None, "")
            else AuthInterruption.UNKNOWN_HUMAN.value
        )
        if interruption == AuthInterruption.NONE:
            interruption = AuthInterruption.UNKNOWN_HUMAN
        needs_human_reason = interruption.value

    age = _age_seconds(str(observed_at) if observed_at else None, now=now)
    publish_age = _age_seconds(str(row.get("updated_at") or ""), now=now)
    stale = False
    if age is not None and age > float(evidence_ttl_seconds):
        stale = True
    elif publish_age is not None and publish_age > float(stale_after_seconds):
        stale = True

    evidence_id = None
    instance_id = row.get("runtime_instance_id") or payload.get("runtime_instance_id")
    if instance_id or observed_at:
        evidence_id = f"{instance_id or ''}:{observed_at or ''}"

    return AuthTruth(
        schema_version=AUTH_TRUTH_SCHEMA_VERSION,
        user_id=user_id,
        provider=provider,
        state=state,
        access_method=ACCESS_MANAGED_RUNTIME,
        evidence_class=evidence_class,
        evidence_source=EVIDENCE_SOURCE_RUNTIME,
        evidence_id=evidence_id,
        observed_at=str(observed_at) if observed_at else None,
        projected_at=projected_at,
        interruption=interruption,
        interruption_expected=interruption_expected,
        needs_human=needs_human,
        needs_human_reason=needs_human_reason,
        evidence_age_seconds=age,
        stale=stale,
    )


def _project_passive_method(
    *,
    user_id: str,
    provider: str,
    access_method: str,
    projected_at: str,
) -> AuthTruth:
    """api / manual — no Access Manager or Runtime auth publication yet."""
    return AuthTruth(
        schema_version=AUTH_TRUTH_SCHEMA_VERSION,
        user_id=user_id,
        provider=provider,
        state=AuthenticationState.LOGIN_UNKNOWN,
        access_method=access_method,
        evidence_class=EvidenceClass.NONE,
        evidence_source=EVIDENCE_SOURCE_ACCESS_MANAGER,
        evidence_id=None,
        observed_at=None,
        projected_at=projected_at,
        interruption=AuthInterruption.NONE,
        interruption_expected=False,
        needs_human=False,
        needs_human_reason=None,
        evidence_age_seconds=None,
        stale=False,
    )


def project_auth_truth(
    db: Any,
    user_id: str,
    provider: str,
    *,
    access_method: str | None = None,
    now: datetime | None = None,
    projected_at: str | None = None,
    evidence_ttl_seconds: float = DEFAULT_EVIDENCE_TTL_SECONDS,
    runtime_stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> AuthTruth:
    """Project AuthTruth from Access Manager PSS or Runtime AccessState.

    Pure read of existing publications. Does not write auth evidence.
    ``access_method`` selects which publication stream is authoritative for
    this account; it is enrollment config, not a second auth ledger.
    """
    now = now or datetime.now(timezone.utc)
    projected_at = projected_at or now.replace(microsecond=0).isoformat()
    provider = str(provider).strip().lower()
    method = resolve_primary_access_method(
        db, user_id, provider, access_method=access_method
    )

    if method == ACCESS_BROWSER_SESSION:
        session = get_provider_session_state(db, user_id, provider)
        return _project_browser_session(
            user_id=user_id,
            provider=provider,
            session=session,
            now=now,
            projected_at=projected_at,
            evidence_ttl_seconds=evidence_ttl_seconds,
        )

    if method == ACCESS_MANAGED_RUNTIME:
        row = get_runtime_access_state(db, user_id, provider)
        return _project_managed_runtime(
            user_id=user_id,
            provider=provider,
            row=row,
            now=now,
            projected_at=projected_at,
            evidence_ttl_seconds=evidence_ttl_seconds,
            stale_after_seconds=runtime_stale_after_seconds,
        )

    return _project_passive_method(
        user_id=user_id,
        provider=provider,
        access_method=method,
        projected_at=projected_at,
    )


def ensure_auth_truth_tables(db: Any, *, commit: bool = True) -> None:
    """Create the AuthTruth projection table (materialized read model only)."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_truth (
            user_id      TEXT NOT NULL,
            provider     TEXT NOT NULL,
            state_json   TEXT NOT NULL,
            projected_at TEXT NOT NULL,
            PRIMARY KEY (user_id, provider)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_auth_truth_user ON auth_truth(user_id)"
    )
    if commit:
        db.commit()


def _persist_auth_truth(db: Any, truth: AuthTruth) -> None:
    """Materialize a projected AuthTruth (projector-internal only)."""
    ensure_auth_truth_tables(db, commit=False)
    db.execute(
        """
        INSERT INTO auth_truth (user_id, provider, state_json, projected_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, provider) DO UPDATE SET
            state_json=excluded.state_json,
            projected_at=excluded.projected_at
        """,
        (
            truth.user_id,
            truth.provider,
            json.dumps(truth.to_dict(), separators=(",", ":"), sort_keys=True),
            truth.projected_at,
        ),
    )
    db.commit()


def auth_truth_from_dict(payload: dict[str, Any]) -> AuthTruth:
    state = (
        normalize_authentication_state(payload.get("state"))
        or AuthenticationState.LOGIN_UNKNOWN
    )
    evidence_class_raw = str(payload.get("evidence_class") or EvidenceClass.NONE.value)
    try:
        evidence_class = EvidenceClass(evidence_class_raw)
    except ValueError:
        evidence_class = EvidenceClass.NONE
    interruption = normalize_auth_interruption(payload.get("interruption"))
    age = payload.get("evidence_age_seconds")
    return AuthTruth(
        schema_version=int(payload.get("schema_version") or AUTH_TRUTH_SCHEMA_VERSION),
        user_id=str(payload["user_id"]),
        provider=str(payload["provider"]),
        state=state,
        access_method=normalize_access_method(payload.get("access_method")),
        evidence_class=evidence_class,
        evidence_source=str(
            payload.get("evidence_source") or EVIDENCE_SOURCE_ACCESS_MANAGER
        ),
        evidence_id=payload.get("evidence_id"),
        observed_at=payload.get("observed_at"),
        projected_at=str(payload.get("projected_at") or utc_now_iso()),
        interruption=interruption,
        interruption_expected=bool(payload.get("interruption_expected")),
        needs_human=bool(payload.get("needs_human")),
        needs_human_reason=payload.get("needs_human_reason"),
        evidence_age_seconds=float(age) if age is not None else None,
        stale=bool(payload.get("stale")),
    )


def load_auth_truth(db: Any, user_id: str, provider: str) -> AuthTruth | None:
    ensure_auth_truth_tables(db, commit=False)
    row = db.execute(
        "SELECT state_json FROM auth_truth WHERE user_id=? AND provider=?",
        (user_id, str(provider).strip().lower()),
    ).fetchone()
    if not row or not row["state_json"]:
        return None
    try:
        payload = json.loads(row["state_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    return auth_truth_from_dict(payload)


def recompute_auth_truth(
    db: Any,
    user_id: str,
    provider: str,
    *,
    access_method: str | None = None,
    now: datetime | None = None,
    projected_at: str | None = None,
    evidence_ttl_seconds: float = DEFAULT_EVIDENCE_TTL_SECONDS,
    runtime_stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> AuthTruth:
    """Project from source publications and persist the read model."""
    truth = project_auth_truth(
        db,
        user_id,
        provider,
        access_method=access_method,
        now=now,
        projected_at=projected_at,
        evidence_ttl_seconds=evidence_ttl_seconds,
        runtime_stale_after_seconds=runtime_stale_after_seconds,
    )
    _persist_auth_truth(db, truth)
    return truth


def replay_auth_truth(
    db: Any,
    user_id: str,
    provider: str,
    *,
    access_method: str | None = None,
    now: datetime | None = None,
    projected_at: str | None = None,
    evidence_ttl_seconds: float = DEFAULT_EVIDENCE_TTL_SECONDS,
    runtime_stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> AuthTruth:
    """Rebuild AuthTruth from current source publications (idempotent replay).

    Replaying the same PSS / AccessState rows with the same ``now`` /
    ``projected_at`` yields a byte-identical ``to_dict()`` payload.
    """
    return recompute_auth_truth(
        db,
        user_id,
        provider,
        access_method=access_method,
        now=now,
        projected_at=projected_at,
        evidence_ttl_seconds=evidence_ttl_seconds,
        runtime_stale_after_seconds=runtime_stale_after_seconds,
    )
