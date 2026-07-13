"""
mighty.capability_state
───────────────────────
Canonical customer-facing capability state for the Truth Dashboard.

This is the ONLY customer-facing translator for product state.
Presentation only — does not change readiness, verification, extraction,
session evidence, access-cycle, or snapshot writers.

Precedence (mutually exclusive):
  A. SIGNED_OUT — definitive current signed-out evidence only
  B. EXTRACTION_SUCCESS — authenticated + successful publishable private data
  C. LOGIN_VISIBLE_EXTRACTION_FAILED — authenticated + observable surface + extract failed
  D. LOGGED_IN_NO_ACCOUNT_DATA — authenticated + no qualifying private data observed
  E. LOGIN_UNKNOWN — inconclusive / stale / checking / missing login evidence
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Sequence

from mighty.connection_state import AMEX_SOURCE
from mighty.provider_account import (
    EXTRACTION_COMPLETE,
    EXTRACTION_FAILED,
    EXTRACTION_PENDING,
    EXTRACTION_NOT_STARTED,
)
from mighty.session_verification import ACTIVE_VERIFICATION_LIFECYCLES

# Temporary single-provider customer surface — one explicit boundary.
TRUTH_PROVIDER = AMEX_SOURCE
TRUTH_PROVIDER_DISPLAY = "American Express"
CUSTOMER_VISIBLE_PROVIDERS = frozenset({AMEX_SOURCE})
AMEX_OPEN_URL = "https://www.americanexpress.com/en-us/account/login"

# Mirror customer_account_access live-access labels (avoid circular import).
_LIVE_CONNECTED = "Connected"
_LIVE_SIGNED_OUT = "Signed out"
_LIVE_CHECKING = "Checking"

_PLACEHOLDER_VALUES = frozenset({
    "", "—", "–", "-", "n/a", "none", "null", "undefined", "0", "no data",
})

StageVerdict = Literal["PASS", "FAIL", "UNKNOWN"]


class CapabilityState(str, Enum):
    EXTRACTION_SUCCESS = "extraction_success"
    LOGIN_VISIBLE_EXTRACTION_FAILED = "login_visible_extraction_failed"
    LOGGED_IN_NO_ACCOUNT_DATA = "logged_in_no_account_data"
    LOGIN_UNKNOWN = "login_unknown"
    SIGNED_OUT = "signed_out"


@dataclass(frozen=True)
class EvidenceItem:
    text: str
    ok: bool | None  # True=✓, False=✗, None=neutral


@dataclass(frozen=True)
class PipelineStage:
    name: str
    verdict: StageVerdict
    timestamp: str | None = None
    detail: str | None = None
    id_label: str | None = None


@dataclass(frozen=True)
class ExtractedField:
    label: str
    value: str


@dataclass(frozen=True)
class CapabilityView:
    """Everything the customer Truth Dashboard / API need for one provider."""

    provider: str
    display_name: str
    state: CapabilityState
    headline: str
    explanations: tuple[str, ...]
    evidence: tuple[EvidenceItem, ...]
    last_verified: str | None
    confidence: str | None
    action_label: str | None
    action_url: str | None
    action_required: bool
    extracted_fields: tuple[ExtractedField, ...]
    pipeline: tuple[PipelineStage, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "display_name": self.display_name,
            "capability_state": self.state.value,
            "state": self.state.value,
            "title": self.headline,
            "headline": self.headline,
            "explanation": list(self.explanations),
            "explanations": list(self.explanations),
            "evidence": [
                {"text": e.text, "ok": e.ok} for e in self.evidence
            ],
            "evidence_summary": [
                (("✓ " if e.ok is True else "✗ " if e.ok is False else "") + e.text)
                for e in self.evidence
            ],
            "last_verified": self.last_verified,
            "confidence": self.confidence,
            "action_required": self.action_required,
            "action_label": self.action_label,
            "action_url": self.action_url,
            "extracted_fields": [
                {"label": f.label, "value": f.value} for f in self.extracted_fields
            ],
            "pipeline": [
                {
                    "name": s.name,
                    "verdict": s.verdict,
                    "timestamp": s.timestamp,
                    "detail": s.detail,
                    "id_label": s.id_label,
                }
                for s in self.pipeline
            ],
        }


_HEADLINES: dict[CapabilityState, str] = {
    CapabilityState.EXTRACTION_SUCCESS: (
        "✅ Mighty can see and extract your logged-in account data."
    ),
    CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED: (
        "⚠️ Mighty can tell you are logged in, but could not extract your account information."
    ),
    CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA: (
        "⚠️ Mighty can tell you are logged in, but cannot see your account information."
    ),
    CapabilityState.LOGIN_UNKNOWN: (
        "⚪ Mighty cannot determine whether you are logged in."
    ),
    CapabilityState.SIGNED_OUT: "🔒 You are signed out.",
}

_EXPLANATIONS: dict[CapabilityState, tuple[str, ...]] = {
    CapabilityState.EXTRACTION_SUCCESS: (),
    CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED: (
        "Login verified.",
        "Extraction failed.",
        "No customer action required.",
    ),
    CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA: (
        "Logged-in session detected.",
        "Private account data not observable.",
    ),
    CapabilityState.LOGIN_UNKNOWN: (
        "Current evidence is insufficient.",
        "Any cached data may be stale.",
    ),
    CapabilityState.SIGNED_OUT: (
        "Please sign in to American Express.",
    ),
}

# Short labels for Accounts list — derived from CapabilityState only.
CAPABILITY_STATUS_LABELS: dict[CapabilityState, str] = {
    CapabilityState.EXTRACTION_SUCCESS: "Extraction success",
    CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED: "Extraction failed",
    CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA: "Logged in — no account data",
    CapabilityState.LOGIN_UNKNOWN: "Login unknown",
    CapabilityState.SIGNED_OUT: "Signed out",
}


def _is_authenticated(
    *,
    readiness: str | None,
    live_access: str | None,
    session_state: str | None,
) -> bool:
    if readiness == "signed_out" or live_access == _LIVE_SIGNED_OUT or session_state == "signed_out":
        return False
    if readiness == "ready":
        return True
    if live_access == _LIVE_CONNECTED or session_state == "connected":
        return True
    return False


def _extraction_in_flight(
    *,
    readiness: str | None,
    live_access: str | None,
    extraction_status: str | None,
    verification_lifecycle: str | None,
    background_verification: bool,
) -> bool:
    lifecycle = (verification_lifecycle or "").strip()
    if readiness == "checking" or live_access == _LIVE_CHECKING:
        return True
    if background_verification:
        return True
    if lifecycle in ACTIVE_VERIFICATION_LIFECYCLES:
        return True
    if extraction_status == EXTRACTION_PENDING:
        return True
    return False


def resolve_capability_state(
    *,
    readiness: str | None = None,
    live_access: str | None = None,
    private_data_state: str | None = None,
    session_state: str | None = None,
    user_action_required: bool = False,
    extraction_status: str | None = None,
    has_snapshot: bool = False,
    has_publishable_fields: bool = False,
    signed_out_evidence: bool = False,
    verification_lifecycle: str | None = None,
    background_verification: bool = False,
) -> CapabilityState:
    """Translate access signals into exactly one CapabilityState.

    Does not mutate readiness or lifecycle. Does not read legacy sync_status
    or connection_status — callers must not pass those as inputs.
    """
    del has_snapshot  # Snapshot informs pipeline/evidence, not the state fork.
    del user_action_required  # Never decide SIGNED_OUT from CTA flags alone.

    # A. SIGNED_OUT — definitive signed-out evidence only.
    if (
        signed_out_evidence
        or readiness == "signed_out"
        or live_access == _LIVE_SIGNED_OUT
        or session_state == "signed_out"
    ):
        return CapabilityState.SIGNED_OUT

    authenticated = _is_authenticated(
        readiness=readiness,
        live_access=live_access,
        session_state=session_state,
    )
    in_flight = _extraction_in_flight(
        readiness=readiness,
        live_access=live_access,
        extraction_status=extraction_status,
        verification_lifecycle=verification_lifecycle,
        background_verification=background_verification,
    )
    extraction_failed = (
        private_data_state == "extraction_failed"
        or extraction_status == EXTRACTION_FAILED
    )

    # B. EXTRACTION_SUCCESS — confirmed access + publishable private data.
    # readiness==ready already encodes SWR: background recheck does not erase it.
    if has_publishable_fields and (
        readiness == "ready"
        or (authenticated and private_data_state == "seen")
    ):
        return CapabilityState.EXTRACTION_SUCCESS

    # readiness ready / seen without publishable fields cannot claim success.
    if readiness == "ready" or private_data_state == "seen":
        if authenticated and not has_publishable_fields:
            return CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
        return CapabilityState.LOGIN_UNKNOWN

    if not authenticated:
        # Inconclusive / checking / unknown — never invent signed_out.
        return CapabilityState.LOGIN_UNKNOWN

    # Authenticated from here.

    # C. LOGIN_VISIBLE_EXTRACTION_FAILED
    if extraction_failed:
        return CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED

    # Do not use LOGGED_IN_NO_ACCOUNT_DATA merely because extraction has not run.
    if in_flight or extraction_status in (EXTRACTION_PENDING, EXTRACTION_NOT_STARTED, None, ""):
        if private_data_state == "saved_data_only":
            # Authenticated; only uncorrelated cache — current private data not observable.
            return CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
        return CapabilityState.LOGIN_UNKNOWN

    # D. LOGGED_IN_NO_ACCOUNT_DATA — authenticated, extraction finished, no qualifying data.
    return CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA


def resolve_capability_state_from_view(
    view: Any | None,
    *,
    extraction_status: str | None = None,
    has_snapshot: bool = False,
    has_publishable_fields: bool = False,
) -> CapabilityState:
    if view is None:
        # No account row / credentials-only / discovery-only → unknown, not signed out.
        return CapabilityState.LOGIN_UNKNOWN
    return resolve_capability_state(
        readiness=view.readiness,
        live_access=view.live_access,
        private_data_state=view.private_data_state,
        session_state=view.session_state,
        user_action_required=view.user_action_required,
        extraction_status=extraction_status,
        has_snapshot=has_snapshot,
        has_publishable_fields=has_publishable_fields,
        signed_out_evidence=view.readiness == "signed_out",
        verification_lifecycle=view.active_verification_lifecycle,
        background_verification=bool(view.background_verification),
    )


def _fmt_ts(value: str | None) -> str | None:
    if not value:
        return None
    text = value.replace("T", " ").replace("+00:00", " UTC")
    if len(text) > 19:
        text = text[:19]
    return text


def _confidence_label(raw: str | None, state: CapabilityState) -> str | None:
    if state in (CapabilityState.LOGIN_UNKNOWN, CapabilityState.SIGNED_OUT):
        return None if state == CapabilityState.LOGIN_UNKNOWN else (raw and raw[:1].upper() + raw[1:].lower())
    if not raw:
        if state == CapabilityState.EXTRACTION_SUCCESS:
            return "High"
        return None
    return raw[:1].upper() + raw[1:].lower()


def _normalize_extracted_fields(
    items: Sequence[dict[str, Any]] | None,
) -> tuple[ExtractedField, ...]:
    fields: list[ExtractedField] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("key") or "").strip()
        value = str(item.get("value") or "").strip()
        if not label or value.lower() in _PLACEHOLDER_VALUES:
            continue
        fields.append(ExtractedField(label=label, value=value))
    return tuple(fields)


def _sanitized_failure_reason(view: Any | None, extraction_status: str | None) -> str:
    for candidate in (
        getattr(view, "meaning", None) if view else None,
        getattr(view, "secondary_label", None) if view else None,
        "Parser failed" if extraction_status == EXTRACTION_FAILED else None,
        "Extraction failed",
    ):
        text = (candidate or "").strip()
        if not text:
            continue
        low = text.lower()
        if any(bad in low for bad in ("cookie", "token", "password", "authorization", "bearer")):
            continue
        if len(text) > 120:
            text = text[:117] + "…"
        return text
    return "Extraction failed"


def _evidence_for_state(
    state: CapabilityState,
    view: Any | None,
    *,
    extracted_fields: Sequence[ExtractedField],
    has_snapshot: bool,
    extraction_status: str | None,
    verification_lifecycle: str | None,
) -> tuple[EvidenceItem, ...]:
    if state == CapabilityState.EXTRACTION_SUCCESS:
        items: list[EvidenceItem] = [
            EvidenceItem("Authenticated session confirmed", True),
            EvidenceItem("Private Amex response/page observed", True),
            EvidenceItem("Extraction succeeded", True),
        ]
        if has_snapshot or extracted_fields:
            items.append(EvidenceItem("Extracted fields persisted", True))
        for field in extracted_fields[:4]:
            items.append(EvidenceItem(f"{field.label} located", True))
        return tuple(items)

    if state == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED:
        reason = _sanitized_failure_reason(view, extraction_status)
        return (
            EvidenceItem("Authenticated session confirmed", True),
            EvidenceItem("Private account surface observed", True),
            EvidenceItem(f"Extraction failed: {reason}", False),
        )

    if state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA:
        return (
            EvidenceItem("Authenticated session confirmed", True),
            EvidenceItem("No qualifying private account data observed", False),
        )

    if state == CapabilityState.SIGNED_OUT:
        return (
            EvidenceItem("Definitive login page or signed-out session observed", True),
            EvidenceItem("No authenticated session", False),
        )

    # LOGIN_UNKNOWN
    lifecycle = (verification_lifecycle or "").strip()
    if lifecycle == "timed_out":
        return (EvidenceItem("Verification timed out — login inconclusive", None),)
    if lifecycle == "failed":
        return (EvidenceItem("Verification inconclusive", None),)
    if lifecycle in ACTIVE_VERIFICATION_LIFECYCLES:
        return (EvidenceItem("Verification in progress — login not yet confirmed", None),)
    return (EvidenceItem("No definitive current login evidence", None),)


def _pipeline_stages(
    view: Any | None,
    state: CapabilityState,
    *,
    extraction_status: str | None,
    has_snapshot: bool,
    has_publishable_fields: bool,
    verification_id: str | None = None,
) -> tuple[PipelineStage, ...]:
    session_state = (view.session_state if view else None) or ""
    lifecycle = (view.active_verification_lifecycle if view else None) or ""
    private = (view.private_data_state if view else None) or ""
    last_confirmed = _fmt_ts(view.last_confirmed_at if view else None)
    snapshot_at = _fmt_ts(view.cached_snapshot_at if view else None)
    cycle_id = (view.access_cycle_id if view else None) or (
        view.last_confirmed_access_cycle_id if view else None
    )
    evidence_src = view.evidence_source if view else None

    if session_state == "connected" or state in (
        CapabilityState.EXTRACTION_SUCCESS,
        CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED,
        CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA,
    ):
        session_verdict: StageVerdict = "PASS"
        session_detail = "Authenticated session evidence"
    elif session_state == "signed_out" or state == CapabilityState.SIGNED_OUT:
        session_verdict = "PASS"
        session_detail = "Signed-out / login-page evidence"
    elif session_state == "checking":
        session_verdict = "UNKNOWN"
        session_detail = "Verification in flight"
    else:
        session_verdict = "UNKNOWN"
        session_detail = "Insufficient session evidence"

    if lifecycle in ("completed", "session_verified", "extracting"):
        verify_verdict: StageVerdict = "PASS"
    elif lifecycle in ("failed", "timed_out"):
        verify_verdict = "FAIL"
    elif lifecycle in ("requested", "running") or session_state == "checking":
        verify_verdict = "UNKNOWN"
    elif state in (CapabilityState.EXTRACTION_SUCCESS, CapabilityState.SIGNED_OUT):
        verify_verdict = "PASS"
    else:
        verify_verdict = "UNKNOWN"

    if private == "seen" or state == CapabilityState.EXTRACTION_SUCCESS:
        obs_verdict: StageVerdict = "PASS"
    elif (
        private == "extraction_failed"
        or state == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED
        or state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
    ):
        obs_verdict = "FAIL"
    else:
        obs_verdict = "UNKNOWN"

    if state == CapabilityState.EXTRACTION_SUCCESS or (
        has_publishable_fields and private == "seen"
    ):
        extract_verdict: StageVerdict = "PASS"
    elif (
        extraction_status == EXTRACTION_FAILED
        or private == "extraction_failed"
        or state == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED
    ):
        extract_verdict = "FAIL"
    elif extraction_status == EXTRACTION_PENDING:
        extract_verdict = "UNKNOWN"
    else:
        extract_verdict = "UNKNOWN"

    if has_publishable_fields or (has_snapshot and state == CapabilityState.EXTRACTION_SUCCESS):
        snap_verdict: StageVerdict = "PASS"
    elif state in (
        CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED,
        CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA,
    ):
        snap_verdict = "FAIL"
    elif has_snapshot:
        snap_verdict = "PASS" if state == CapabilityState.EXTRACTION_SUCCESS else "UNKNOWN"
    else:
        snap_verdict = "UNKNOWN"

    verify_ids = []
    if verification_id:
        verify_ids.append(f"verification_id:{verification_id}")
    if cycle_id:
        verify_ids.append(f"access_cycle_id:{cycle_id}")

    snap_id = None
    if snapshot_at:
        snap_id = f"snapshot_at:{snapshot_at}"
    if cycle_id and snap_verdict == "PASS":
        snap_id = f"access_cycle_id:{cycle_id}"

    return (
        PipelineStage(
            name="Session Evidence",
            verdict=session_verdict,
            timestamp=last_confirmed,
            detail=session_detail,
            id_label=f"evidence_source:{evidence_src}" if evidence_src else None,
        ),
        PipelineStage(
            name="Verification",
            verdict=verify_verdict,
            timestamp=last_confirmed,
            detail=lifecycle or None,
            id_label=" · ".join(verify_ids) if verify_ids else None,
        ),
        PipelineStage(
            name="Observation",
            verdict=obs_verdict,
            timestamp=last_confirmed,
            detail=private or None,
        ),
        PipelineStage(
            name="Extraction",
            verdict=extract_verdict,
            timestamp=snapshot_at or last_confirmed,
            detail=extraction_status or private or None,
        ),
        PipelineStage(
            name="Snapshot",
            verdict=snap_verdict,
            timestamp=snapshot_at,
            detail="persisted account_data" if snap_verdict == "PASS" else None,
            id_label=snap_id,
        ),
    )


def build_capability_view(
    view: Any | None,
    *,
    display_name: str = TRUTH_PROVIDER_DISPLAY,
    provider: str = TRUTH_PROVIDER,
    extracted_items: Sequence[dict[str, Any]] | None = None,
    session_confidence: str | None = None,
    extraction_status: str | None = None,
    login_url: str | None = None,
    verification_id: str | None = None,
) -> CapabilityView:
    """Assemble the customer Truth Dashboard / API model.

    extracted_items:
      - None  → caller did not load snapshot fields; readiness==ready may still
                yield EXTRACTION_SUCCESS (accounts list / status without payload).
      - list  → must contain at least one non-placeholder field for EXTRACTION_SUCCESS.
    """
    fields_provided = extracted_items is not None
    extracted_fields = _normalize_extracted_fields(extracted_items)
    has_publishable = bool(extracted_fields) if fields_provided else False
    # When fields are not supplied, allow readiness-ready to count as publishable.
    publishable_for_state = has_publishable or (
        not fields_provided and view is not None and view.readiness == "ready"
    )
    has_snapshot = bool(
        has_publishable
        or (view and view.cached_snapshot_at)
        or (view and view.private_data_state == "seen")
        or publishable_for_state
    )
    state = resolve_capability_state_from_view(
        view,
        extraction_status=extraction_status,
        has_snapshot=has_snapshot,
        has_publishable_fields=publishable_for_state,
    )
    # Explicit empty/placeholder-only payload can never claim success.
    if state == CapabilityState.EXTRACTION_SUCCESS and fields_provided and not has_publishable:
        state = (
            CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
            if view and _is_authenticated(
                readiness=view.readiness,
                live_access=view.live_access,
                session_state=view.session_state,
            )
            else CapabilityState.LOGIN_UNKNOWN
        )

    show_fields = (
        extracted_fields if state == CapabilityState.EXTRACTION_SUCCESS else ()
    )
    evidence = _evidence_for_state(
        state,
        view,
        extracted_fields=show_fields,
        has_snapshot=has_snapshot,
        extraction_status=extraction_status,
        verification_lifecycle=(
            view.active_verification_lifecycle if view else None
        ),
    )
    pipeline = _pipeline_stages(
        view,
        state,
        extraction_status=extraction_status,
        has_snapshot=has_snapshot,
        has_publishable_fields=has_publishable or publishable_for_state,
        verification_id=verification_id,
    )

    action_required = state == CapabilityState.SIGNED_OUT
    action_label = None
    action_url = None
    if action_required:
        action_label = "Open American Express"
        action_url = (
            (view.user_action_url if view and view.user_action_url else None)
            or login_url
            or AMEX_OPEN_URL
        )

    return CapabilityView(
        provider=provider,
        display_name=display_name,
        state=state,
        headline=_HEADLINES[state],
        explanations=_EXPLANATIONS[state],
        evidence=evidence,
        last_verified=_fmt_ts(view.last_confirmed_at if view else None),
        confidence=_confidence_label(session_confidence, state),
        action_label=action_label,
        action_url=action_url,
        action_required=action_required,
        extracted_fields=show_fields,
        pipeline=pipeline,
    )


def filter_customer_accounts(accounts: Sequence[Any]) -> list[Any]:
    """Keep only providers visible on customer surfaces."""
    return [
        a for a in accounts
        if getattr(a, "source", None) in CUSTOMER_VISIBLE_PROVIDERS
        or getattr(a, "provider", None) in CUSTOMER_VISIBLE_PROVIDERS
    ]


def is_customer_visible_provider(source: str | None) -> bool:
    return (source or "") in CUSTOMER_VISIBLE_PROVIDERS
