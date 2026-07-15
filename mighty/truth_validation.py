"""
mighty.truth_validation
───────────────────────
Evidence / observability layer for CapabilityState.

Presentation and debugging only — does not change readiness, verification,
extraction, snapshots, provider adapters, or parser behavior.

Given any CapabilityState, answers:
  1. Why is Mighty in this state?
  2. Which evidence caused it?
  3. Which pipeline stage succeeded / failed?
  4. What changed since the previous state?
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Sequence

from mighty.capability_state import (
    CapabilityState,
    CapabilityView,
    EvidenceItem,
    PipelineStage,
    StageVerdict,
)

# Fixed pipeline order (engineering contract).
PIPELINE_STAGE_NAMES: tuple[str, ...] = (
    "Session Evidence",
    "Verification",
    "Navigation",
    "Observation",
    "Extraction",
    "Normalization",
    "Snapshot",
    "Capability State",
)


class EvidenceCategory(str, Enum):
    SESSION = "session"
    VERIFICATION = "verification"
    NAVIGATION = "navigation"
    OBSERVATION = "observation"
    NETWORK = "network"
    DOM = "dom"
    EXTRACTION = "extraction"
    SNAPSHOT = "snapshot"


class EvidenceOutcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_RUN = "NOT_RUN"


class ConfidenceLevel(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


_SENSITIVE_KEYS = frozenset({
    "cookie", "cookies", "token", "tokens", "credential", "credentials",
    "password", "authorization", "bearer", "request_body", "response_body",
    "body", "secret", "api_key",
})


def _safe_metadata(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Drop secrets / bodies — developer-safe metadata only."""
    if not raw:
        return {}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        low = str(key).lower()
        if low in _SENSITIVE_KEYS or any(s in low for s in _SENSITIVE_KEYS):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[str(key)] = value
        else:
            out[str(key)] = str(value)[:80]
    return out


@dataclass(frozen=True)
class TruthEvidence:
    id: str
    timestamp: str | None
    category: EvidenceCategory
    description: str
    outcome: EvidenceOutcome
    confidence_contribution: int  # typically -40..+40
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "category": self.category.value,
            "description": self.description,
            "outcome": self.outcome.value,
            "confidence_contribution": self.confidence_contribution,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class TruthPipelineStage:
    name: str
    verdict: EvidenceOutcome
    timestamp: str | None = None
    duration_ms: int | None = None
    evidence_ids: tuple[str, ...] = ()
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "verdict": self.verdict.value,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "evidence_ids": list(self.evidence_ids),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class TruthTransition:
    previous_state: str | None
    current_state: str
    reason: str
    timestamp: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "previous_state": self.previous_state,
            "current_state": self.current_state,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class TruthValidation:
    """Canonical debugging object for why Mighty believes a CapabilityState."""

    capability_state: str
    confidence: str  # High | Medium | Low
    confidence_score: int  # 0–100
    generated_at: str
    explanation: str
    evidence: tuple[TruthEvidence, ...]
    pipeline: tuple[TruthPipelineStage, ...]
    timeline: tuple[TruthEvidence, ...]
    transition: TruthTransition | None
    developer_ids: dict[str, str | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_state": self.capability_state,
            "confidence": self.confidence,
            "confidence_score": self.confidence_score,
            "generated_at": self.generated_at,
            "explanation": self.explanation,
            "evidence": [e.to_dict() for e in self.evidence],
            "pipeline": [s.to_dict() for s in self.pipeline],
            "timeline": [e.to_dict() for e in self.timeline],
            "transition": self.transition.to_dict() if self.transition else None,
            "developer_ids": dict(self.developer_ids),
        }


_STATE_LABELS: dict[CapabilityState, str] = {
    CapabilityState.EXTRACTION_SUCCESS: "Extraction Success",
    CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED: "Extraction Failed",
    CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA: "Logged In — No Account Data",
    CapabilityState.LOGIN_UNKNOWN: "Login Unknown",
    CapabilityState.SIGNED_OUT: "Signed Out",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _outcome_from_ok(ok: bool | None) -> EvidenceOutcome:
    if ok is True:
        return EvidenceOutcome.PASS
    if ok is False:
        return EvidenceOutcome.FAIL
    return EvidenceOutcome.UNKNOWN


def _verdict(v: StageVerdict | str) -> EvidenceOutcome:
    if v == "PASS":
        return EvidenceOutcome.PASS
    if v == "FAIL":
        return EvidenceOutcome.FAIL
    if v == "NOT_RUN":
        return EvidenceOutcome.NOT_RUN
    return EvidenceOutcome.UNKNOWN


def _category_for_text(text: str) -> EvidenceCategory:
    low = text.lower()
    if any(k in low for k in ("login page", "signed-out", "signed out", "session")):
        return EvidenceCategory.SESSION
    if "verif" in low or "timed out" in low:
        return EvidenceCategory.VERIFICATION
    if any(k in low for k in ("navigat", "opened ", "americanexpress.com")):
        return EvidenceCategory.NAVIGATION
    if any(k in low for k in ("observed", "response", "private", "surface")):
        return EvidenceCategory.OBSERVATION
    if any(k in low for k in ("extract", "parser", "membership", "field", "located")):
        return EvidenceCategory.EXTRACTION
    if any(k in low for k in ("snapshot", "persisted")):
        return EvidenceCategory.SNAPSHOT
    if "dom" in low:
        return EvidenceCategory.DOM
    if "network" in low:
        return EvidenceCategory.NETWORK
    return EvidenceCategory.OBSERVATION


def _contribution_for(outcome: EvidenceOutcome, category: EvidenceCategory) -> int:
    base = {
        EvidenceOutcome.PASS: 18,
        EvidenceOutcome.FAIL: -12,
        EvidenceOutcome.UNKNOWN: -8,
        EvidenceOutcome.NOT_RUN: -4,
    }[outcome]
    # High-signal categories weigh more when definitive.
    if category in (EvidenceCategory.SESSION, EvidenceCategory.EXTRACTION, EvidenceCategory.SNAPSHOT):
        if outcome == EvidenceOutcome.PASS:
            return base + 8
        if outcome == EvidenceOutcome.FAIL and category == EvidenceCategory.EXTRACTION:
            return -20
    return base


def explanation_for_state(
    state: CapabilityState,
    *,
    display_name: str = "American Express",
) -> str:
    """Concise capability explanation (engineering + dashboard)."""
    if state == CapabilityState.EXTRACTION_SUCCESS:
        return (
            f"Mighty confirmed your authenticated {display_name} session, "
            "observed private account data, successfully extracted account "
            "information, and saved a verified snapshot."
        )
    if state == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED:
        return (
            "Mighty confirmed your authenticated session but could not "
            "successfully extract account information."
        )
    if state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA:
        return (
            "Mighty confirmed your authenticated session but could not observe "
            "qualifying private account data."
        )
    if state == CapabilityState.SIGNED_OUT:
        return (
            f"Mighty observed definitive evidence that your {display_name} "
            "session is signed out."
        )
    # LOGIN_UNKNOWN
    return (
        "Mighty does not currently have enough trustworthy evidence to "
        "determine whether you are logged in."
    )


def _transition_reason(
    previous: CapabilityState | str | None,
    current: CapabilityState,
    evidence: Sequence[TruthEvidence],
) -> str:
    prev_val = previous.value if isinstance(previous, CapabilityState) else previous
    pass_desc = next(
        (e.description for e in evidence if e.outcome == EvidenceOutcome.PASS),
        None,
    )
    fail_desc = next(
        (e.description for e in evidence if e.outcome == EvidenceOutcome.FAIL),
        None,
    )

    if current == CapabilityState.EXTRACTION_SUCCESS:
        return (
            "Authenticated session detected and extraction completed."
            if not prev_val or prev_val == CapabilityState.LOGIN_UNKNOWN.value
            else "Authenticated session confirmed and extraction completed."
        )
    if current == CapabilityState.SIGNED_OUT:
        login_ev = next(
            (
                e.description for e in evidence
                if "login page" in e.description.lower()
                or "signed-out" in e.description.lower()
                or "signed out" in e.description.lower()
            ),
            None,
        )
        return (
            "Login page detected during verification."
            if login_ev or (
                prev_val == CapabilityState.EXTRACTION_SUCCESS.value
            )
            else (login_ev or pass_desc or "Definitive signed-out evidence observed.")
        )
    if current == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED:
        return fail_desc or "Extraction failed after authenticated session was confirmed."
    if current == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA:
        return "Authenticated session confirmed; private account data not observed."
    return pass_desc or fail_desc or "Insufficient trustworthy login evidence."


def confidence_from_evidence(
    state: CapabilityState,
    evidence: Sequence[TruthEvidence],
    *,
    session_confidence: str | None = None,
) -> tuple[ConfidenceLevel, int]:
    """Evidence-based confidence: High / Medium / Low plus 0–100 score."""
    score = 50
    for item in evidence:
        score += item.confidence_contribution
    score = max(0, min(100, score))

    # State-driven anchors (still evidence-informed).
    if state == CapabilityState.SIGNED_OUT:
        has_login_page = any(
            "login page" in e.description.lower()
            or "signed-out" in e.description.lower()
            or "signed out" in e.description.lower()
            for e in evidence
            if e.outcome == EvidenceOutcome.PASS
        )
        if has_login_page:
            return ConfidenceLevel.HIGH, max(score, 85)
        return ConfidenceLevel.MEDIUM, max(score, 60)

    if state == CapabilityState.EXTRACTION_SUCCESS:
        has_extract = any(
            e.category == EvidenceCategory.EXTRACTION and e.outcome == EvidenceOutcome.PASS
            for e in evidence
        )
        has_snap = any(
            e.category == EvidenceCategory.SNAPSHOT and e.outcome == EvidenceOutcome.PASS
            for e in evidence
        )
        if has_extract and has_snap:
            return ConfidenceLevel.HIGH, max(score, 90)
        if has_extract:
            return ConfidenceLevel.HIGH, max(score, 80)
        return ConfidenceLevel.MEDIUM, max(score, 65)

    if state == CapabilityState.LOGIN_UNKNOWN:
        timed_out = any("timed out" in e.description.lower() for e in evidence)
        stale = any("stale" in e.description.lower() for e in evidence)
        if timed_out or stale or all(e.outcome == EvidenceOutcome.UNKNOWN for e in evidence):
            return ConfidenceLevel.LOW, min(score, 35)
        return ConfidenceLevel.LOW, min(max(score, 20), 45)

    if state == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED:
        return ConfidenceLevel.HIGH, max(min(score, 88), 70)

    # LOGGED_IN_NO_ACCOUNT_DATA
    raw = (session_confidence or "").strip().lower()
    if raw == "high":
        return ConfidenceLevel.MEDIUM, max(score, 55)
    if raw == "low":
        return ConfidenceLevel.LOW, min(score, 40)
    return ConfidenceLevel.MEDIUM, max(min(score, 70), 50)


def _correlation_metadata(
    *,
    verification_id: str | None,
    access_cycle_id: str | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Developer-safe event IDs tying timeline rows to one verification cycle."""
    meta: dict[str, Any] = dict(extra or {})
    if verification_id:
        meta["verification_id"] = verification_id
    if access_cycle_id:
        meta["access_cycle_id"] = access_cycle_id
    return _safe_metadata(meta)


def _evidence_from_capability(
    capability: CapabilityView,
    *,
    base_ts: str | None,
    verification_id: str | None = None,
    access_cycle_id: str | None = None,
) -> list[TruthEvidence]:
    items: list[TruthEvidence] = []
    for i, ev in enumerate(capability.evidence):
        outcome = _outcome_from_ok(ev.ok)
        category = _category_for_text(ev.text)
        # Prefer login-page categorization for signed-out definitive evidence.
        if "login page" in ev.text.lower():
            category = EvidenceCategory.SESSION
        eid = f"ev-{i + 1}"
        items.append(
            TruthEvidence(
                id=eid,
                timestamp=base_ts,
                category=category,
                description=ev.text,
                outcome=outcome,
                confidence_contribution=_contribution_for(outcome, category),
                metadata=_correlation_metadata(
                    verification_id=verification_id,
                    access_cycle_id=access_cycle_id,
                    extra={"source": "capability_evidence"},
                ),
            )
        )
    return items


def _ensure_state_evidence(
    state: CapabilityState,
    evidence: list[TruthEvidence],
    *,
    base_ts: str | None,
    verification_id: str | None = None,
    access_cycle_id: str | None = None,
) -> list[TruthEvidence]:
    """Guarantee regression-critical evidence shapes per CapabilityState."""
    descs = {e.description.lower() for e in evidence}
    next_id = len(evidence) + 1

    def add(
        description: str,
        category: EvidenceCategory,
        outcome: EvidenceOutcome,
        contribution: int,
        meta: dict[str, Any] | None = None,
    ) -> None:
        nonlocal next_id
        if description.lower() in descs:
            return
        evidence.append(
            TruthEvidence(
                id=f"ev-{next_id}",
                timestamp=base_ts,
                category=category,
                description=description,
                outcome=outcome,
                confidence_contribution=contribution,
                metadata=_correlation_metadata(
                    verification_id=verification_id,
                    access_cycle_id=access_cycle_id,
                    extra=meta or {"source": "truth_validation"},
                ),
            )
        )
        descs.add(description.lower())
        next_id += 1

    if state == CapabilityState.EXTRACTION_SUCCESS:
        add(
            "Authenticated session detected",
            EvidenceCategory.SESSION,
            EvidenceOutcome.PASS,
            26,
        )
        add(
            "Private account endpoint observed",
            EvidenceCategory.OBSERVATION,
            EvidenceOutcome.PASS,
            20,
        )
        add(
            "Extraction succeeded",
            EvidenceCategory.EXTRACTION,
            EvidenceOutcome.PASS,
            26,
        )
        add(
            "Snapshot written",
            EvidenceCategory.SNAPSHOT,
            EvidenceOutcome.PASS,
            22,
        )
    elif state == CapabilityState.SIGNED_OUT:
        add(
            "Login page detected",
            EvidenceCategory.SESSION,
            EvidenceOutcome.PASS,
            30,
        )
    elif state == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED:
        add(
            "Authenticated session detected",
            EvidenceCategory.SESSION,
            EvidenceOutcome.PASS,
            26,
        )
        if not any(e.outcome == EvidenceOutcome.FAIL for e in evidence):
            add(
                "Parser failed",
                EvidenceCategory.EXTRACTION,
                EvidenceOutcome.FAIL,
                -20,
            )
    elif state == CapabilityState.LOGIN_UNKNOWN:
        if not evidence:
            add(
                "No definitive current login evidence",
                EvidenceCategory.VERIFICATION,
                EvidenceOutcome.UNKNOWN,
                -10,
            )
    elif state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA:
        add(
            "Authenticated session detected",
            EvidenceCategory.SESSION,
            EvidenceOutcome.PASS,
            26,
        )
    return evidence


def _stage_lookup(pipeline: Sequence[PipelineStage]) -> dict[str, PipelineStage]:
    return {s.name: s for s in pipeline}


def _expand_pipeline(
    capability: CapabilityView,
    evidence: Sequence[TruthEvidence],
) -> tuple[TruthPipelineStage, ...]:
    """Fixed 8-stage pipeline; map existing CapabilityView stages + fill gaps."""
    by_name = _stage_lookup(capability.pipeline)
    state = capability.state
    ts = capability.last_verified

    def ids_for(*categories: EvidenceCategory) -> tuple[str, ...]:
        return tuple(
            e.id for e in evidence if e.category in categories
        )

    def from_existing(
        name: str,
        *,
        fallback: EvidenceOutcome,
        detail: str | None = None,
        evidence_ids: tuple[str, ...] = (),
        timestamp: str | None = None,
    ) -> TruthPipelineStage:
        stage = by_name.get(name)
        if stage:
            return TruthPipelineStage(
                name=name,
                verdict=_verdict(stage.verdict),
                timestamp=stage.timestamp or timestamp or ts,
                duration_ms=None,
                evidence_ids=evidence_ids or ids_for(),
                detail=stage.detail or detail,
            )
        return TruthPipelineStage(
            name=name,
            verdict=fallback,
            timestamp=timestamp or ts,
            duration_ms=None,
            evidence_ids=evidence_ids,
            detail=detail,
        )

    # Derive Navigation / Normalization / Capability State from overall state.
    if state == CapabilityState.EXTRACTION_SUCCESS:
        nav = EvidenceOutcome.PASS
        norm = EvidenceOutcome.PASS
        cap = EvidenceOutcome.PASS
    elif state == CapabilityState.SIGNED_OUT:
        nav = EvidenceOutcome.PASS  # reached login surface
        norm = EvidenceOutcome.UNKNOWN
        cap = EvidenceOutcome.PASS  # definitive classification
    elif state == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED:
        nav = EvidenceOutcome.PASS
        norm = EvidenceOutcome.FAIL
        cap = EvidenceOutcome.PASS
    elif state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA:
        nav = EvidenceOutcome.PASS
        norm = EvidenceOutcome.UNKNOWN
        cap = EvidenceOutcome.PASS
    else:
        nav = EvidenceOutcome.UNKNOWN
        norm = EvidenceOutcome.UNKNOWN
        cap = EvidenceOutcome.UNKNOWN

    session = from_existing(
        "Session Evidence",
        fallback=(
            EvidenceOutcome.PASS
            if state != CapabilityState.LOGIN_UNKNOWN
            else EvidenceOutcome.UNKNOWN
        ),
        evidence_ids=ids_for(EvidenceCategory.SESSION),
    )
    verify = from_existing(
        "Verification",
        fallback=(
            EvidenceOutcome.UNKNOWN
            if state == CapabilityState.LOGIN_UNKNOWN
            else EvidenceOutcome.PASS
        ),
        evidence_ids=ids_for(EvidenceCategory.VERIFICATION, EvidenceCategory.SESSION),
    )
    observation = from_existing(
        "Observation",
        fallback=(
            EvidenceOutcome.PASS
            if state == CapabilityState.EXTRACTION_SUCCESS
            else EvidenceOutcome.FAIL
            if state in (
                CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED,
                CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA,
            )
            else EvidenceOutcome.UNKNOWN
        ),
        evidence_ids=ids_for(
            EvidenceCategory.OBSERVATION,
            EvidenceCategory.NETWORK,
            EvidenceCategory.DOM,
        ),
    )
    extraction = from_existing(
        "Extraction",
        fallback=(
            EvidenceOutcome.PASS
            if state == CapabilityState.EXTRACTION_SUCCESS
            else EvidenceOutcome.FAIL
            if state == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED
            else EvidenceOutcome.NOT_RUN
            if state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
            else EvidenceOutcome.UNKNOWN
        ),
        evidence_ids=ids_for(EvidenceCategory.EXTRACTION),
        detail=(
            "Parser failed"
            if state == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED
            else "NOT RUN"
            if state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
            else None
        ),
    )
    snapshot = from_existing(
        "Snapshot",
        fallback=(
            EvidenceOutcome.PASS
            if state == CapabilityState.EXTRACTION_SUCCESS
            else EvidenceOutcome.FAIL
            if state == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED
            else EvidenceOutcome.NOT_RUN
            if state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
            else EvidenceOutcome.UNKNOWN
        ),
        evidence_ids=ids_for(EvidenceCategory.SNAPSHOT),
        detail=(
            "NOT RUN"
            if state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
            else None
        ),
    )

    navigation = TruthPipelineStage(
        name="Navigation",
        verdict=nav,
        timestamp=ts,
        evidence_ids=ids_for(EvidenceCategory.NAVIGATION),
        detail="Provider surface reached" if nav == EvidenceOutcome.PASS else None,
    )
    normalization = TruthPipelineStage(
        name="Normalization",
        verdict=norm,
        timestamp=ts,
        evidence_ids=ids_for(EvidenceCategory.EXTRACTION),
        detail=(
            "Fields normalized"
            if norm == EvidenceOutcome.PASS
            else "Normalization skipped / failed"
            if norm == EvidenceOutcome.FAIL
            else None
        ),
    )
    capability_stage = TruthPipelineStage(
        name="Capability State",
        verdict=cap,
        timestamp=ts,
        evidence_ids=tuple(e.id for e in evidence),
        detail=_STATE_LABELS.get(state, state.value),
    )

    ordered = (
        session,
        verify,
        navigation,
        observation,
        extraction,
        normalization,
        snapshot,
        capability_stage,
    )
    assert tuple(s.name for s in ordered) == PIPELINE_STAGE_NAMES
    return ordered


def sort_timeline_events(
    events: Sequence[TruthEvidence],
) -> tuple[TruthEvidence, ...]:
    """Sort by occurred_at; equal timestamps keep stable original order."""
    indexed = list(enumerate(events))
    indexed.sort(key=lambda pair: (pair[1].timestamp or "", pair[0]))
    return tuple(event for _, event in indexed)


def _timeline_events(
    state: CapabilityState,
    evidence: Sequence[TruthEvidence],
    *,
    base_ts: str | None,
    display_name: str,
    verification_id: str | None = None,
    access_cycle_id: str | None = None,
) -> tuple[TruthEvidence, ...]:
    """Chronological Truth Timeline (synthetic offsets when only one clock)."""
    # Prefer existing evidence ordered as built; prepend navigation open if useful.
    events: list[TruthEvidence] = []
    open_id = "tl-nav"
    if state != CapabilityState.LOGIN_UNKNOWN or evidence:
        events.append(
            TruthEvidence(
                id=open_id,
                timestamp=base_ts,
                category=EvidenceCategory.NAVIGATION,
                description=(
                    "Opened americanexpress.com"
                    if "american" in display_name.lower()
                    else f"Opened {display_name}"
                ),
                outcome=(
                    EvidenceOutcome.PASS
                    if state != CapabilityState.LOGIN_UNKNOWN
                    else EvidenceOutcome.UNKNOWN
                ),
                confidence_contribution=5 if state != CapabilityState.LOGIN_UNKNOWN else -2,
                metadata=_correlation_metadata(
                    verification_id=verification_id,
                    access_cycle_id=access_cycle_id,
                    extra={"source": "timeline"},
                ),
            )
        )
    # Deduplicate by description; keep chronological order of evidence list.
    seen: set[str] = set()
    for e in evidence:
        key = e.description.lower()
        if key in seen:
            continue
        seen.add(key)
        events.append(e)

    # Capability terminal event.
    events.append(
        TruthEvidence(
            id="tl-capability",
            timestamp=base_ts,
            category=EvidenceCategory.VERIFICATION,
            description=f"Capability · {_STATE_LABELS.get(state, state.value)}",
            outcome=(
                EvidenceOutcome.PASS
                if state != CapabilityState.LOGIN_UNKNOWN
                else EvidenceOutcome.UNKNOWN
            ),
            confidence_contribution=0,
            metadata=_correlation_metadata(
                verification_id=verification_id,
                access_cycle_id=access_cycle_id,
                extra={"capability_state": state.value, "source": "timeline"},
            ),
        )
    )
    return sort_timeline_events(events)


def _developer_ids(
    access_view: Any | None,
    *,
    verification_id: str | None,
    snapshot_id: str | None,
    correlation_id: str | None,
    access_cycle_id: str | None,
) -> dict[str, str | None]:
    cycle = access_cycle_id
    if cycle is None and access_view is not None:
        cycle = (
            getattr(access_view, "access_cycle_id", None)
            or getattr(access_view, "last_confirmed_access_cycle_id", None)
        )
    return {
        "access_cycle_id": cycle,
        "verification_id": verification_id,
        "snapshot_id": snapshot_id,
        "correlation_id": correlation_id,
    }


def build_truth_validation(
    capability: CapabilityView,
    *,
    access_view: Any | None = None,
    previous_state: CapabilityState | str | None = None,
    session_confidence: str | None = None,
    verification_id: str | None = None,
    snapshot_id: str | None = None,
    correlation_id: str | None = None,
    access_cycle_id: str | None = None,
    generated_at: str | None = None,
) -> TruthValidation:
    """Build the canonical debugging object from an existing CapabilityView."""
    state = capability.state
    base_ts = capability.last_verified
    generated = generated_at or _now_iso()
    ids = _developer_ids(
        access_view,
        verification_id=verification_id,
        snapshot_id=snapshot_id,
        correlation_id=correlation_id,
        access_cycle_id=access_cycle_id,
    )
    vid = ids.get("verification_id")
    cycle = ids.get("access_cycle_id")

    evidence = _evidence_from_capability(
        capability,
        base_ts=base_ts,
        verification_id=vid,
        access_cycle_id=cycle,
    )
    evidence = _ensure_state_evidence(
        state,
        evidence,
        base_ts=base_ts,
        verification_id=vid,
        access_cycle_id=cycle,
    )

    # Chronological: keep construction order (already stage-aligned).
    evidence_tuple = tuple(evidence)

    pipeline = _expand_pipeline(capability, evidence_tuple)
    level, score = confidence_from_evidence(
        state,
        evidence_tuple,
        session_confidence=session_confidence or capability.confidence,
    )
    expl = explanation_for_state(state, display_name=capability.display_name)

    prev_val: str | None
    if previous_state is None:
        prev_val = None
    elif isinstance(previous_state, CapabilityState):
        prev_val = previous_state.value
    else:
        prev_val = str(previous_state)

    transition = TruthTransition(
        previous_state=prev_val,
        current_state=state.value,
        reason=_transition_reason(previous_state, state, evidence_tuple),
        timestamp=base_ts or generated,
    )

    timeline = _timeline_events(
        state,
        evidence_tuple,
        base_ts=base_ts,
        display_name=capability.display_name,
        verification_id=vid,
        access_cycle_id=cycle,
    )

    return TruthValidation(
        capability_state=state.value,
        confidence=level.value,
        confidence_score=score,
        generated_at=generated,
        explanation=expl,
        evidence=evidence_tuple,
        pipeline=pipeline,
        timeline=timeline,
        transition=transition,
        developer_ids=ids,
    )


def attach_truth_validation(
    capability: CapabilityView,
    **kwargs: Any,
) -> CapabilityView:
    """Return CapabilityView with truth_validation populated (immutable replace)."""
    tv = build_truth_validation(capability, **kwargs)
    return replace(capability, truth_validation=tv)
