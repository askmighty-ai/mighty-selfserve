"""
mighty.customer_capability_presentation
───────────────────────────────────────
Presentation-layer gate for the Truth Dashboard.

Capability resolution (resolve_capability_state / build_capability_view) still
computes live pipeline truth. This module decides *when* that truth becomes
customer-visible:

  • While verification is in flight and a prior stable card exists → hold it
    and show only a subtle refresh indicator.
  • When verification reaches a terminal conclusion → atomic swap to the new
    card (or keep the prior card visually identical if the result is unchanged).
  • Login Unknown is exceptional during refresh — never the intermediate face
    of a normal re-check.

Does not change capability precedence, verification FSM, extraction, snapshots,
provider adapters, or truth-validation scoring.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from mighty.capability_state import (
    CapabilityState,
    CapabilityView,
    EvidenceItem,
    ExtractedField,
    PipelineStage,
)
from mighty.session_verification import (
    ACTIVE_VERIFICATION_LIFECYCLES,
    TERMINAL_VERIFICATION_LIFECYCLES,
)

REFRESH_LABEL = "Refreshing…"
REFRESH_LABEL_VERBOSE = "Verifying current account state…"

_LIVE_CHECKING = "Checking"


def is_customer_refresh_in_flight(
    access_view: Any | None,
    *,
    verification_lifecycle: str | None = None,
    background_verification: bool | None = None,
) -> bool:
    """True while a verification/extraction cycle has not reached a terminal outcome."""
    lifecycle = (
        verification_lifecycle
        if verification_lifecycle is not None
        else (getattr(access_view, "active_verification_lifecycle", None) if access_view else None)
    )
    lifecycle = (lifecycle or "").strip()
    if lifecycle in TERMINAL_VERIFICATION_LIFECYCLES:
        return False

    bg = (
        background_verification
        if background_verification is not None
        else bool(getattr(access_view, "background_verification", False) if access_view else False)
    )
    if bg:
        return True
    if lifecycle in ACTIVE_VERIFICATION_LIFECYCLES:
        return True
    if access_view is None:
        return False

    readiness = (getattr(access_view, "readiness", None) or "").strip()
    live_access = (getattr(access_view, "live_access", None) or "").strip()
    session_state = (getattr(access_view, "session_state", None) or "").strip()
    if readiness == "checking" or live_access == _LIVE_CHECKING or session_state == "checking":
        return True
    return False


def customer_visible_signature(view: CapabilityView) -> tuple[Any, ...]:
    """Identity of customer-visible card content (excludes timestamps / IDs)."""
    return (
        view.state.value,
        view.headline,
        view.explanations,
        tuple((e.text, e.ok) for e in view.evidence),
        view.confidence,
        tuple((f.label, f.value) for f in view.extracted_fields),
        view.action_required,
        view.action_label,
        view.action_url,
    )


def customer_visible_same(left: CapabilityView, right: CapabilityView) -> bool:
    return customer_visible_signature(left) == customer_visible_signature(right)


def _with_refresh(view: CapabilityView, *, first_ever: bool) -> CapabilityView:
    return replace(
        view,
        is_refreshing=True,
        refresh_label=REFRESH_LABEL_VERBOSE if first_ever else REFRESH_LABEL,
    )


def _as_stable(view: CapabilityView) -> CapabilityView:
    return replace(view, is_refreshing=False, refresh_label=None)


def merge_unchanged_presentation(
    previous: CapabilityView,
    live: CapabilityView,
) -> CapabilityView:
    """Keep prior visual card; refresh only last-verified / ID-bearing meta."""
    prev_tv = previous.truth_validation
    live_tv = live.truth_validation
    truth = prev_tv
    if prev_tv is not None and live_tv is not None:
        truth = replace(
            prev_tv,
            generated_at=live_tv.generated_at,
            developer_ids=dict(live_tv.developer_ids),
            transition=live_tv.transition,
        )
    elif live_tv is not None:
        truth = live_tv

    # Prefer live pipeline (IDs/timestamps) when stage names+verdicts match;
    # otherwise keep previous pipeline so technical details stay stable.
    pipeline = live.pipeline
    if len(previous.pipeline) == len(live.pipeline) and all(
        a.name == b.name and a.verdict == b.verdict
        for a, b in zip(previous.pipeline, live.pipeline)
    ):
        pipeline = live.pipeline
    else:
        pipeline = previous.pipeline

    return replace(
        previous,
        last_verified=live.last_verified or previous.last_verified,
        pipeline=pipeline,
        truth_validation=truth,
        is_refreshing=False,
        refresh_label=None,
    )


def present_customer_capability(
    live: CapabilityView,
    *,
    previous_stable: CapabilityView | None = None,
    access_view: Any | None = None,
    verification_lifecycle: str | None = None,
    background_verification: bool | None = None,
    force_unknown: bool = False,
) -> CapabilityView:
    """Gate live capability into the customer-visible Truth card.

    force_unknown: developer override — publish live Login Unknown immediately.
    """
    if force_unknown:
        return _as_stable(live)

    refreshing = is_customer_refresh_in_flight(
        access_view,
        verification_lifecycle=verification_lifecycle,
        background_verification=background_verification,
    )

    if refreshing:
        if previous_stable is not None:
            # Hold prior stable truth; never flash Login Unknown mid-refresh.
            return _with_refresh(previous_stable, first_ever=False)
        # First-ever verification — Login Unknown (or whatever live says) is allowed.
        return _with_refresh(live, first_ever=True)

    # Terminal / idle — atomic publish.
    if previous_stable is not None and customer_visible_same(previous_stable, live):
        return merge_unchanged_presentation(previous_stable, live)
    return _as_stable(live)


# ── Persistence (last published stable card) ─────────────────────────────────


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_customer_capability_presentation_tables(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_capability_presentation (
            user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            capability_state TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, provider)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ccp_user "
        "ON customer_capability_presentation(user_id, updated_at DESC)"
    )
    db.commit()


def capability_view_to_payload(view: CapabilityView) -> dict[str, Any]:
    """Serialize a stable CapabilityView for persistence."""
    payload = view.to_dict()
    payload["is_refreshing"] = False
    payload["refresh_label"] = None
    return payload


def capability_view_from_payload(payload: dict[str, Any]) -> CapabilityView:
    """Rehydrate a CapabilityView from a stored payload (presentation only)."""
    from mighty.truth_validation import (
        ConfidenceLevel,
        EvidenceCategory,
        EvidenceOutcome,
        TruthEvidence,
        TruthPipelineStage,
        TruthTransition,
        TruthValidation,
    )

    state_raw = payload.get("capability_state") or payload.get("state") or "login_unknown"
    try:
        state = CapabilityState(state_raw)
    except ValueError:
        state = CapabilityState.LOGIN_UNKNOWN

    evidence = tuple(
        EvidenceItem(text=str(e.get("text") or ""), ok=e.get("ok"))
        for e in (payload.get("evidence") or [])
        if isinstance(e, dict)
    )
    extracted = tuple(
        ExtractedField(label=str(f.get("label") or ""), value=str(f.get("value") or ""))
        for f in (payload.get("extracted_fields") or [])
        if isinstance(f, dict) and f.get("label")
    )
    pipeline_stages: list[PipelineStage] = []
    for s in payload.get("pipeline") or []:
        if not isinstance(s, dict):
            continue
        verdict = str(s.get("verdict") or "UNKNOWN")
        if verdict not in ("PASS", "FAIL", "UNKNOWN", "NOT_RUN"):
            verdict = "UNKNOWN"
        pipeline_stages.append(
            PipelineStage(
                name=str(s.get("name") or ""),
                verdict=verdict,  # type: ignore[arg-type]
                timestamp=s.get("timestamp"),
                detail=s.get("detail"),
                id_label=s.get("id_label"),
            )
        )

    truth = None
    tv_raw = payload.get("truth_validation")
    if isinstance(tv_raw, dict):
        def _ev(item: dict[str, Any]) -> TruthEvidence:
            try:
                cat = EvidenceCategory(str(item.get("category") or "session"))
            except ValueError:
                cat = EvidenceCategory.SESSION
            try:
                outcome = EvidenceOutcome(str(item.get("outcome") or "UNKNOWN"))
            except ValueError:
                outcome = EvidenceOutcome.UNKNOWN
            return TruthEvidence(
                id=str(item.get("id") or ""),
                timestamp=item.get("timestamp"),
                category=cat,
                description=str(item.get("description") or ""),
                outcome=outcome,
                confidence_contribution=int(item.get("confidence_contribution") or 0),
                metadata=dict(item.get("metadata") or {}),
            )

        transition = None
        tr = tv_raw.get("transition")
        if isinstance(tr, dict) and tr.get("current_state"):
            transition = TruthTransition(
                previous_state=tr.get("previous_state"),
                current_state=str(tr["current_state"]),
                reason=str(tr.get("reason") or ""),
                timestamp=tr.get("timestamp"),
            )

        pipe: list[TruthPipelineStage] = []
        for s in tv_raw.get("pipeline") or []:
            if not isinstance(s, dict):
                continue
            try:
                verdict = EvidenceOutcome(str(s.get("verdict") or "UNKNOWN"))
            except ValueError:
                verdict = EvidenceOutcome.UNKNOWN
            pipe.append(
                TruthPipelineStage(
                    name=str(s.get("name") or ""),
                    verdict=verdict,
                    timestamp=s.get("timestamp"),
                    duration_ms=s.get("duration_ms"),
                    evidence_ids=tuple(s.get("evidence_ids") or ()),
                    detail=s.get("detail"),
                )
            )

        conf = str(tv_raw.get("confidence") or "Low")
        if conf not in {c.value for c in ConfidenceLevel}:
            conf = "Low"

        truth = TruthValidation(
            capability_state=str(tv_raw.get("capability_state") or state.value),
            confidence=conf,
            confidence_score=int(tv_raw.get("confidence_score") or 0),
            generated_at=str(tv_raw.get("generated_at") or ""),
            explanation=str(tv_raw.get("explanation") or ""),
            evidence=tuple(
                _ev(e) for e in (tv_raw.get("evidence") or []) if isinstance(e, dict)
            ),
            pipeline=tuple(pipe),
            timeline=tuple(
                _ev(e) for e in (tv_raw.get("timeline") or []) if isinstance(e, dict)
            ),
            transition=transition,
            developer_ids={
                str(k): (None if v is None else str(v))
                for k, v in dict(tv_raw.get("developer_ids") or {}).items()
            },
        )

    explanations = payload.get("explanations") or payload.get("explanation") or ()
    if isinstance(explanations, str):
        explanations = (explanations,)
    explanations = tuple(str(x) for x in explanations)

    return CapabilityView(
        provider=str(payload.get("provider") or "amex"),
        display_name=str(payload.get("display_name") or "American Express"),
        state=state,
        headline=str(payload.get("headline") or payload.get("title") or ""),
        explanations=explanations,
        evidence=evidence,
        last_verified=payload.get("last_verified"),
        confidence=payload.get("confidence"),
        action_label=payload.get("action_label"),
        action_url=payload.get("action_url"),
        action_required=bool(payload.get("action_required")),
        extracted_fields=extracted,
        pipeline=tuple(pipeline_stages),
        truth_validation=truth,
        is_refreshing=False,
        refresh_label=None,
    )


def load_stable_capability(
    db: Any,
    user_id: str,
    provider: str,
) -> CapabilityView | None:
    ensure_customer_capability_presentation_tables(db)
    row = db.execute(
        """
        SELECT payload_json FROM customer_capability_presentation
        WHERE user_id = ? AND provider = ?
        """,
        (user_id, provider),
    ).fetchone()
    if row is None:
        return None
    raw = row["payload_json"] if hasattr(row, "keys") else row[0]
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return capability_view_from_payload(payload)
    except Exception:  # noqa: BLE001 — corrupt row must not break dashboard
        return None


def save_stable_capability(
    db: Any,
    user_id: str,
    view: CapabilityView,
) -> None:
    """Persist a terminal customer-visible card (never store in-flight holds)."""
    if view.is_refreshing:
        return
    ensure_customer_capability_presentation_tables(db)
    stable = _as_stable(view)
    payload = capability_view_to_payload(stable)
    db.execute(
        """
        INSERT INTO customer_capability_presentation
            (user_id, provider, capability_state, payload_json, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, provider) DO UPDATE SET
            capability_state = excluded.capability_state,
            payload_json = excluded.payload_json,
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            stable.provider,
            stable.state.value,
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            _utc_now_iso(),
        ),
    )
    db.commit()


def build_presented_capability_view(
    access_view: Any | None,
    *,
    previous_stable: CapabilityView | None = None,
    force_unknown: bool = False,
    persist_db: Any | None = None,
    persist_user_id: str | None = None,
    **build_kwargs: Any,
) -> CapabilityView:
    """Build live capability, apply presentation gate, optionally persist terminal."""
    from mighty.capability_state import build_capability_view

    live = build_capability_view(access_view, **build_kwargs)
    presented = present_customer_capability(
        live,
        previous_stable=previous_stable,
        access_view=access_view,
        force_unknown=force_unknown,
    )
    if (
        persist_db is not None
        and persist_user_id
        and not presented.is_refreshing
    ):
        save_stable_capability(persist_db, persist_user_id, presented)
    return presented
