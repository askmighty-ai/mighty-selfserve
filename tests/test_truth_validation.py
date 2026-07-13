"""Regression tests for Truth Validation & Evidence Engine (PR #98)."""

from __future__ import annotations

import html

from mighty.account_readiness import (
    AccountReadiness,
    READY,
    CHECKING,
    SIGNED_OUT,
    UNVERIFIED,
)
from mighty.capability_state import (
    CapabilityState,
    build_capability_view,
)
from mighty.customer_account_access import (
    DISCOVERED_MANUAL,
    build_customer_account_access_view,
)
from mighty.home_ui import render_capability_panel
from mighty.provider_account import EXTRACTION_FAILED
from mighty.truth_validation import (
    PIPELINE_STAGE_NAMES,
    EvidenceOutcome,
    build_truth_validation,
)
from mighty import user_copy


def _escape(value):
    return html.escape(str(value)) if value is not None else ""


def _readiness(provider: str, state: str, **kwargs) -> AccountReadiness:
    labels = {
        READY: ("Connected", user_copy.READINESS_COPY_READY, "ready", "up_to_date"),
        CHECKING: ("Checking", user_copy.READINESS_COPY_CHECKING, "checking", "checking"),
        SIGNED_OUT: (
            "Sign in required",
            user_copy.READINESS_COPY_SIGNED_OUT,
            "needs_sign_in",
            "needs_login",
        ),
        UNVERIFIED: (
            "Unable to verify",
            user_copy.READINESS_COPY_UNVERIFIED,
            "unknown",
            "unverified",
        ),
    }
    label, copy, presentation, canonical = labels[state]
    defaults = dict(
        provider=provider,
        state=state,
        status_label=label,
        status_copy=copy,
        presentation_key=presentation,
        canonical_status=canonical,
        login_required=state == SIGNED_OUT,
        session_state=(
            "connected" if state == READY else
            "signed_out" if state == SIGNED_OUT else
            "checking" if state == CHECKING else
            "unknown"
        ),
        access_cycle_id="cycle-1" if state == READY else None,
        session_evidence_at=None,
        extraction_at="2026-07-13T15:00:00+00:00" if state == READY else None,
        extraction_ok=state == READY,
        extraction_correlated=state == READY,
        verification_id="ver-1" if state == READY else None,
        cached_data_label=None,
        last_confirmed_ready_at="2026-07-13T15:48:00+00:00" if state == READY else None,
        last_confirmed_access_cycle_id="cycle-1" if state == READY else None,
        background_verification=False,
        secondary_label=None,
    )
    defaults.update(kwargs)
    return AccountReadiness(**defaults)  # type: ignore[arg-type]


AMEX_FIELDS = [
    {"label": "Membership Rewards", "value": "125,000"},
    {"label": "Card ending", "value": "1005"},
]


def _view(state: str, **kwargs):
    readiness_kwargs = {
        k: kwargs.pop(k)
        for k in list(kwargs)
        if k in {
            "session_state",
            "extraction_ok",
            "extraction_correlated",
            "cached_data_label",
            "last_confirmed_ready_at",
            "background_verification",
            "access_cycle_id",
            "last_confirmed_access_cycle_id",
        }
    }
    readiness = _readiness("amex", state, **readiness_kwargs)
    return build_customer_account_access_view(
        provider="amex",
        display_name="American Express",
        readiness=readiness,
        discovered_from=kwargs.pop("discovered_from", DISCOVERED_MANUAL),
        **kwargs,
    )


class TestTruthValidationExtractionSuccess:
    def test_pass_extraction_snapshot_high_confidence(self):
        view = _view(READY)
        cap = build_capability_view(view, extracted_items=AMEX_FIELDS)
        tv = cap.truth_validation
        assert tv is not None
        assert tv.capability_state == CapabilityState.EXTRACTION_SUCCESS.value

        extract = next(s for s in tv.pipeline if s.name == "Extraction")
        snap = next(s for s in tv.pipeline if s.name == "Snapshot")
        assert extract.verdict == EvidenceOutcome.PASS
        assert snap.verdict == EvidenceOutcome.PASS
        assert tv.confidence == "High"
        assert tv.confidence_score >= 80
        assert "extracted" in tv.explanation.lower()
        assert "snapshot" in tv.explanation.lower()

        assert any(
            e.category.value == "extraction" and e.outcome == EvidenceOutcome.PASS
            for e in tv.evidence
        )
        assert any(
            e.category.value == "snapshot" and e.outcome == EvidenceOutcome.PASS
            for e in tv.evidence
        )


class TestTruthValidationSignedOut:
    def test_login_page_evidence_high_confidence(self):
        view = _view(SIGNED_OUT)
        cap = build_capability_view(view)
        tv = cap.truth_validation
        assert tv is not None
        assert tv.capability_state == CapabilityState.SIGNED_OUT.value
        assert any(
            "login page" in e.description.lower()
            and e.outcome == EvidenceOutcome.PASS
            for e in tv.evidence
        )
        assert tv.confidence == "High"
        assert "signed out" in tv.explanation.lower()


class TestTruthValidationLoginUnknown:
    def test_unknown_stages_low_confidence(self):
        view = _view(UNVERIFIED)
        cap = build_capability_view(view)
        tv = cap.truth_validation
        assert tv is not None
        assert tv.capability_state == CapabilityState.LOGIN_UNKNOWN.value
        unknown_stages = [
            s for s in tv.pipeline if s.verdict == EvidenceOutcome.UNKNOWN
        ]
        assert len(unknown_stages) >= 3
        assert tv.confidence == "Low"
        assert "enough trustworthy evidence" in tv.explanation.lower()


class TestTruthValidationExtractionFailed:
    def test_extraction_fail(self):
        view = _view(
            UNVERIFIED,
            session_state="connected",
            extraction_status=EXTRACTION_FAILED,
        )
        cap = build_capability_view(view, extraction_status=EXTRACTION_FAILED)
        assert cap.state == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED
        tv = cap.truth_validation
        assert tv is not None
        extract = next(s for s in tv.pipeline if s.name == "Extraction")
        assert extract.verdict == EvidenceOutcome.FAIL
        assert any(e.outcome == EvidenceOutcome.FAIL for e in tv.evidence)
        assert "could not successfully extract" in tv.explanation.lower()


class TestTruthValidationTransitions:
    def test_unknown_to_success(self):
        view = _view(READY)
        cap = build_capability_view(
            view,
            extracted_items=AMEX_FIELDS,
            previous_capability_state=CapabilityState.LOGIN_UNKNOWN,
        )
        tv = cap.truth_validation
        assert tv is not None
        assert tv.transition is not None
        assert tv.transition.previous_state == CapabilityState.LOGIN_UNKNOWN.value
        assert tv.transition.current_state == CapabilityState.EXTRACTION_SUCCESS.value
        assert "extraction completed" in tv.transition.reason.lower()

    def test_success_to_signed_out(self):
        view = _view(SIGNED_OUT)
        cap = build_capability_view(
            view,
            previous_capability_state=CapabilityState.EXTRACTION_SUCCESS,
        )
        tv = cap.truth_validation
        assert tv is not None
        assert tv.transition is not None
        assert tv.transition.previous_state == CapabilityState.EXTRACTION_SUCCESS.value
        assert tv.transition.current_state == CapabilityState.SIGNED_OUT.value
        assert "login page" in tv.transition.reason.lower()


class TestTruthValidationOrdering:
    def test_pipeline_order_fixed(self):
        view = _view(READY)
        cap = build_capability_view(view, extracted_items=AMEX_FIELDS)
        tv = cap.truth_validation
        assert tv is not None
        names = tuple(s.name for s in tv.pipeline)
        assert names == PIPELINE_STAGE_NAMES

    def test_evidence_ordering_chronological(self):
        view = _view(READY)
        cap = build_capability_view(view, extracted_items=AMEX_FIELDS)
        tv = build_truth_validation(cap)
        # Timeline starts with navigation open, ends with capability.
        assert "Opened americanexpress.com" in tv.timeline[0].description
        assert "Capability" in tv.timeline[-1].description
        # Evidence list preserves construction order (no reverse).
        ids = [e.id for e in tv.evidence]
        assert ids == sorted(ids, key=lambda x: int(x.split("-")[1]))


class TestTruthValidationAPIAndUI:
    def test_capability_to_dict_includes_truth_validation(self):
        view = _view(READY)
        cap = build_capability_view(
            view,
            extracted_items=AMEX_FIELDS,
            verification_id="ver-1",
            correlation_id="corr-1",
            snapshot_id="snap-1",
        )
        payload = cap.to_dict()
        assert "truth_validation" in payload
        tv = payload["truth_validation"]
        assert tv["capability_state"] == "extraction_success"
        assert tv["confidence"] == "High"
        assert set(tv["developer_ids"]) == {
            "access_cycle_id",
            "verification_id",
            "snapshot_id",
            "correlation_id",
        }
        assert tv["developer_ids"]["verification_id"] == "ver-1"
        # Never expose secrets
        blob = str(tv).lower()
        assert "cookie" not in blob
        assert "password" not in blob
        assert "bearer" not in blob

    def test_ui_renders_timeline_and_tech_sections(self):
        view = _view(READY)
        cap = build_capability_view(view, extracted_items=AMEX_FIELDS)
        rendered = render_capability_panel(cap, escape=_escape)
        assert "Truth Timeline" in rendered
        assert "Technical Details" in rendered
        assert "Pipeline" in rendered
        assert "Developer ids" in rendered
        assert "access_cycle_id" in rendered
        assert "Opened americanexpress.com" in rendered
        assert "Capability State" in rendered


class TestTruthValidationCurrentCycleSemantics:
    def test_logged_in_no_data_extraction_and_snapshot_not_run(self):
        from mighty.provider_account import EXTRACTION_COMPLETE

        view = _view(
            UNVERIFIED,
            session_state="connected",
            extraction_ok=True,
            extraction_correlated=False,
            cached_data_label="Last saved data: 2 hours ago",
            verification_lifecycle="completed",
        )
        cap = build_capability_view(
            view,
            extracted_items=AMEX_FIELDS,
            extraction_status=EXTRACTION_COMPLETE,
        )
        tv = build_truth_validation(cap)
        by_name = {s.name: s for s in tv.pipeline}
        assert by_name["Extraction"].verdict == EvidenceOutcome.NOT_RUN
        assert by_name["Snapshot"].verdict == EvidenceOutcome.NOT_RUN
        assert "Previous data available" in (by_name["Snapshot"].detail or "")
        blob = str(tv.to_dict()).lower()
        assert "cookie" not in blob
        assert "password" not in blob
        assert "125,000" not in blob  # field values not dumped into truth metadata

    def test_ui_renders_not_run_label(self):
        from mighty.provider_account import EXTRACTION_COMPLETE

        view = _view(
            UNVERIFIED,
            session_state="connected",
            verification_lifecycle="completed",
        )
        cap = build_capability_view(
            view,
            extracted_items=[],
            extraction_status=EXTRACTION_COMPLETE,
        )
        assert cap.state.value == "logged_in_no_account_data"
        rendered = render_capability_panel(cap, escape=_escape)
        assert "NOT RUN" in rendered
