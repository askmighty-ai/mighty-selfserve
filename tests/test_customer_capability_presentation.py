"""Regression: temporally accurate Truth Dashboard presentation (PR #103)."""

from __future__ import annotations

import html
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from mighty.account_readiness import CHECKING, READY, SIGNED_OUT, UNVERIFIED
from mighty.account_status import AccountStatus
from mighty.capability_state import (
    CapabilityState,
    build_capability_view,
)
from mighty.customer_account_access import (
    DISCOVERED_MANUAL,
    build_customer_account_access_view,
)
from mighty.customer_capability_presentation import (
    CUSTOMER_TRUTH_FRESHNESS_SECONDS,
    DETERMINING_BODY,
    DETERMINING_HEADLINE,
    DETERMINING_HEADLINE_CURRENT,
    EMPTY_CORRELATED_TIMELINE_MESSAGE,
    FIRST_EVER_CHECKING_HEADLINE,
    REFRESHING_STATUS_HEADLINE,
    STALE_RECONFIRM_EXPLANATION,
    PresentationOrderMeta,
    apply_selected_verification_timestamp,
    build_presented_capability_view,
    build_timeline_correlation_record,
    clear_stable_capability,
    correlate_presentation_timeline,
    customer_visible_same,
    ensure_customer_capability_presentation_tables,
    event_matches_presentation,
    filter_correlated_timeline_events,
    fingerprint_account_identity,
    is_newer_presentation,
    is_result_within_customer_truth_freshness,
    load_stable_capability,
    load_stable_order_meta,
    present_customer_capability,
    resolve_customer_presentation_mode,
    resolve_order_meta_for_view,
    save_stable_capability,
)
from mighty.session_verification import (
    READY_RESULT_GRACE_SECONDS,
    ensure_session_verification_tables,
)
from mighty.home_state import resolve_home_state
from mighty.home_ui import render_capability_panel, render_home_page
from mighty.provider_account import EXTRACTION_COMPLETE, EXTRACTION_PENDING
from mighty import user_copy


AMEX_FIELDS = [
    {"label": "Membership Rewards", "value": "125,000"},
    {"label": "Card", "value": "Gold Card"},
]


def _escape(value):
    return html.escape(str(value)) if value is not None else ""


def _readiness(provider: str, state: str, **kwargs):
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
        # Keep fixtures inside the customer-truth freshness window for terminal tests.
        last_confirmed_ready_at="2026-07-14T15:48:00+00:00" if state == READY else None,
        last_confirmed_access_cycle_id="cycle-1" if state == READY else None,
        background_verification=False,
        secondary_label=None,
    )
    defaults.update(kwargs)
    from mighty.account_readiness import AccountReadiness
    return AccountReadiness(**defaults)  # type: ignore[arg-type]


def _view(state: str, **kwargs):
    readiness_kwargs = {
        k: kwargs.pop(k)
        for k in list(kwargs)
        if k in {
            "session_state",
            "background_verification",
            "cached_data_label",
            "last_confirmed_ready_at",
            "extraction_ok",
            "extraction_correlated",
            "verification_id",
            "access_cycle_id",
            "secondary_label",
        }
    }
    verification_lifecycle = kwargs.pop("verification_lifecycle", None)
    discovered_from = kwargs.pop("discovered_from", DISCOVERED_MANUAL)
    readiness = _readiness("amex", state, **readiness_kwargs)
    return build_customer_account_access_view(
        provider="amex",
        display_name="American Express",
        readiness=readiness,
        discovered_from=discovered_from,
        verification_lifecycle=verification_lifecycle,
        **kwargs,
    )


def _stable(state: str, **kwargs):
    """Terminal stable CapabilityView for a completed verification."""
    if state == READY:
        view = _view(READY, verification_lifecycle="completed", **kwargs)
        return build_capability_view(
            view, extracted_items=AMEX_FIELDS, verification_id=kwargs.get("verification_id", "ver-1"),
        )
    if state == SIGNED_OUT:
        view = _view(SIGNED_OUT, verification_lifecycle="completed", **kwargs)
        return build_capability_view(
            view, verification_id=kwargs.get("verification_id", "ver-so"),
        )
    if state == "logged_in_no_data":
        view = _view(
            UNVERIFIED,
            session_state="connected",
            verification_lifecycle="completed",
            **kwargs,
        )
        return build_capability_view(
            view,
            extracted_items=[],
            extraction_status=EXTRACTION_COMPLETE,
            verification_id=kwargs.get("verification_id", "ver-nodata"),
        )
    raise AssertionError(state)


def _inflight_checking(**kwargs):
    return _view(
        CHECKING,
        session_state="checking",
        verification_lifecycle=kwargs.pop("verification_lifecycle", "running"),
        **kwargs,
    )


def _memory_db():
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_customer_capability_presentation_tables(db)
    return db


def _meta(
    *,
    verification_id: str,
    completed_at: str,
    access_cycle_id: str | None = None,
    lifecycle: str = "completed",
    terminal_reason: str | None = "authenticated",
    account_identity: str | None = None,
) -> PresentationOrderMeta:
    return PresentationOrderMeta(
        verification_id=verification_id,
        access_cycle_id=access_cycle_id or verification_id,
        verification_completed_at=completed_at,
        lifecycle=lifecycle,
        terminal_reason=terminal_reason,
        account_identity=account_identity,
    )


def _assert_determining(presented, *, previous_state: CapabilityState | None = None):
    assert presented.presentation_phase == "determining"
    assert presented.current_verification_active is True
    assert presented.terminal_capability_state is None
    assert presented.is_refreshing is True
    assert presented.state == CapabilityState.LOGIN_UNKNOWN
    assert presented.status_is_historical is False
    assert presented.historical_summary is None
    assert "cannot determine" not in (presented.primary_headline or "").lower()
    assert "you are signed out" not in (presented.primary_headline or "").lower()
    assert "you are logged in" not in (presented.primary_headline or "").lower()
    assert "last confirmed" not in (presented.primary_headline or "").lower()
    assert "checking your login state" in (presented.primary_headline or "").lower()
    if previous_state is not None:
        assert presented.previous_capability_state == previous_state.value
        assert any(
            s.label == "Previous completed check" for s in presented.timeline_sections
        )
    else:
        assert presented.previous_capability_state is None
        assert not any(
            s.label == "Previous completed check" for s in presented.timeline_sections
        )


class TestCustomerTruthFreshness:
    def test_freshness_window_matches_ready_grace(self):
        assert CUSTOMER_TRUTH_FRESHNESS_SECONDS == READY_RESULT_GRACE_SECONDS

    def test_freshness_helper_bounds(self):
        now = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)
        fresh = (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        stale = (now - timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert is_result_within_customer_truth_freshness(fresh, now=now) is True
        assert is_result_within_customer_truth_freshness(stale, now=now) is False
        assert is_result_within_customer_truth_freshness(None, now=now) is False

    def _success_at_age(self, *, age: timedelta, now: datetime):
        confirmed = (now - age).strftime("%Y-%m-%dT%H:%M:%SZ")
        live_view = _view(
            READY,
            verification_lifecycle="completed",
            last_confirmed_ready_at=(now - age).isoformat(),
        )
        live = replace(
            build_capability_view(live_view, extracted_items=AMEX_FIELDS),
            last_verified=confirmed,
        )
        return present_customer_capability(
            live,
            previous_stable=None,
            access_view=live_view,
            now=now,
        )

    def test_freshness_boundary_29_59_still_present_tense(self):
        now = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)
        presented = self._success_at_age(
            age=timedelta(seconds=CUSTOMER_TRUTH_FRESHNESS_SECONDS - 1),
            now=now,
        )
        assert presented.presentation_phase == "terminal"
        assert presented.status_is_historical is False
        assert "can see and extract" in presented.headline.lower()
        assert presented.timestamp_label == "Latest check completed"

    def test_freshness_boundary_exactly_30_00_still_present_tense(self):
        now = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)
        presented = self._success_at_age(
            age=timedelta(seconds=CUSTOMER_TRUTH_FRESHNESS_SECONDS),
            now=now,
        )
        assert is_result_within_customer_truth_freshness(
            presented.last_verified, now=now,
        ) is True
        assert presented.status_is_historical is False
        assert "can see and extract" in presented.headline.lower()

    def test_freshness_boundary_30_01_demotes_to_historical(self):
        now = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)
        presented = self._success_at_age(
            age=timedelta(seconds=CUSTOMER_TRUTH_FRESHNESS_SECONDS + 1),
            now=now,
        )
        assert presented.presentation_phase == "terminal"
        assert presented.status_is_historical is True
        assert presented.current_verification_active is False
        assert "can see and extract your logged-in" not in presented.headline.lower()
        assert "could access and extract" in presented.primary_headline.lower()
        assert presented.historical_summary is None  # primary carries the claim
        assert presented.primary_explanation == STALE_RECONFIRM_EXPLANATION
        assert presented.timestamp_label == "Last confirmed"
        assert not presented.extracted_fields
        rendered = render_capability_panel(presented, escape=_escape)
        assert "Mighty can see and extract your logged-in account data" not in rendered
        assert "Last confirmed" in rendered
        assert "No field values in the latest snapshot" not in rendered


class TestTemporallyAccuratePresentation:
    def test_no_prior_plus_active_verification(self):
        live_view = _inflight_checking()
        presented = present_customer_capability(
            build_capability_view(live_view),
            previous_stable=None,
            access_view=live_view,
        )
        _assert_determining(presented, previous_state=None)
        assert presented.primary_headline == DETERMINING_HEADLINE
        assert presented.primary_explanation == DETERMINING_BODY
        assert DETERMINING_BODY in presented.explanations

    def test_prior_signed_out_plus_active_verification(self):
        previous = _stable(SIGNED_OUT)
        live_view = _inflight_checking()
        presented = present_customer_capability(
            build_capability_view(live_view),
            previous_stable=previous,
            access_view=live_view,
        )
        _assert_determining(presented, previous_state=CapabilityState.SIGNED_OUT)
        assert presented.primary_headline == DETERMINING_HEADLINE_CURRENT
        assert presented.historical_summary is None
        assert "You are signed out" not in presented.headline
        previous_section = next(
            s for s in presented.timeline_sections if s.label == "Previous completed check"
        )
        assert previous_section.events[0].description == "Signed out"

    def test_prior_extraction_success_plus_active_verification(self):
        previous = _stable(READY)
        live_view = _inflight_checking()
        presented = present_customer_capability(
            build_capability_view(live_view, extracted_items=AMEX_FIELDS),
            previous_stable=previous,
            access_view=live_view,
        )
        _assert_determining(
            presented, previous_state=CapabilityState.EXTRACTION_SUCCESS,
        )
        assert presented.primary_headline == REFRESHING_STATUS_HEADLINE
        assert presented.historical_summary is None
        assert not presented.extracted_fields
        previous_section = next(
            s for s in presented.timeline_sections if s.label == "Previous completed check"
        )
        assert "Connected" in previous_section.events[0].description

    def test_prior_terminal_unknown_plus_active_verification(self):
        unknown = build_capability_view(
            _view(
                CHECKING,
                session_state="checking",
                verification_lifecycle="timed_out",
                last_confirmed_ready_at="2026-07-13T04:48:00+00:00",
            ),
            extraction_status=EXTRACTION_PENDING,
        )
        assert unknown.state == CapabilityState.LOGIN_UNKNOWN
        live_view = _inflight_checking()
        presented = present_customer_capability(
            build_capability_view(live_view),
            previous_stable=unknown,
            access_view=live_view,
        )
        _assert_determining(presented, previous_state=CapabilityState.LOGIN_UNKNOWN)
        assert presented.historical_summary is None
        assert "cannot determine" not in presented.headline.lower()
        assert "could not determine" not in presented.headline.lower()
        previous_section = next(
            s for s in presented.timeline_sections if s.label == "Previous completed check"
        )
        assert previous_section.events[0].description == "Check inconclusive"

    def test_active_to_signed_out_terminal(self):
        previous = _stable(SIGNED_OUT)
        mid = present_customer_capability(
            build_capability_view(_inflight_checking()),
            previous_stable=previous,
            access_view=_inflight_checking(),
        )
        assert mid.presentation_phase == "determining"
        clock = datetime(2026, 7, 14, 15, 12, tzinfo=timezone.utc)
        done_view = _view(
            SIGNED_OUT,
            verification_lifecycle="completed",
            last_confirmed_ready_at="2026-07-14T15:10:00+00:00",
        )
        final = present_customer_capability(
            build_capability_view(done_view),
            previous_stable=mid,
            access_view=done_view,
            now=clock,
        )
        assert final.presentation_phase == "terminal"
        assert final.terminal_capability_state == CapabilityState.SIGNED_OUT.value
        assert "You are signed out" in final.headline
        assert final.last_verified == "2026-07-14T15:10:00Z"
        assert final.timestamp_label == "Latest check completed"
        assert final.status_is_historical is False

    def test_active_to_authenticated_no_data_terminal(self):
        previous = _stable(READY)
        mid = present_customer_capability(
            build_capability_view(_inflight_checking()),
            previous_stable=previous,
            access_view=_inflight_checking(),
        )
        clock = datetime(2026, 7, 14, 16, 5, tzinfo=timezone.utc)
        done_view = _view(
            UNVERIFIED,
            session_state="connected",
            verification_lifecycle="completed",
            last_confirmed_ready_at="2026-07-14T16:00:00+00:00",
        )
        final = present_customer_capability(
            build_capability_view(done_view, extracted_items=[]),
            previous_stable=mid,
            access_view=done_view,
            now=clock,
        )
        assert final.presentation_phase == "terminal"
        assert final.state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
        assert "cannot see your account information" in final.headline
        assert final.last_verified == "2026-07-14T16:00:00Z"

    def test_active_to_extraction_success_terminal(self):
        previous = _stable(SIGNED_OUT)
        mid = present_customer_capability(
            build_capability_view(_inflight_checking()),
            previous_stable=previous,
            access_view=_inflight_checking(),
        )
        clock = datetime(2026, 7, 14, 17, 5, tzinfo=timezone.utc)
        done_view = _view(
            READY,
            verification_lifecycle="completed",
            last_confirmed_ready_at="2026-07-14T17:00:00+00:00",
        )
        final = present_customer_capability(
            build_capability_view(done_view, extracted_items=AMEX_FIELDS),
            previous_stable=mid,
            access_view=done_view,
            now=clock,
        )
        assert final.presentation_phase == "terminal"
        assert final.state == CapabilityState.EXTRACTION_SUCCESS
        assert "can see and extract" in final.headline.lower()
        assert final.last_verified == "2026-07-14T17:00:00Z"

    def test_active_to_terminal_timeout(self):
        previous = _stable(SIGNED_OUT)
        mid = present_customer_capability(
            build_capability_view(_inflight_checking()),
            previous_stable=previous,
            access_view=_inflight_checking(),
        )
        timeout_view = _view(
            CHECKING,
            session_state="checking",
            verification_lifecycle="timed_out",
            last_confirmed_ready_at="2026-07-14T18:00:00+00:00",
        )
        final = present_customer_capability(
            build_capability_view(timeout_view, extraction_status=EXTRACTION_PENDING),
            previous_stable=mid,
            access_view=timeout_view,
        )
        assert final.presentation_phase == "terminal"
        assert final.state == CapabilityState.LOGIN_UNKNOWN
        assert "could not determine your login state during the latest check" in final.headline
        assert "cannot determine" not in final.headline.lower()
        assert "Verification completed without sufficient evidence." in final.explanations

    def test_previous_timestamp_labeled_historical(self):
        previous = replace(
            _stable(SIGNED_OUT),
            last_verified="2026-07-13T04:48:00Z",
        )
        presented = present_customer_capability(
            build_capability_view(_inflight_checking()),
            previous_stable=previous,
            access_view=_inflight_checking(),
        )
        rendered = render_capability_panel(presented, escape=_escape)
        # Previous outcome lives in the timeline — not as primary “Last confirmed”.
        assert "Previous completed check" in rendered
        assert "Signed out" in rendered
        assert 'datetime="2026-07-13T04:48:00Z"' in rendered
        assert "Last confirmed: Signed out" not in rendered
        assert "You are signed out" not in rendered
        assert DETERMINING_HEADLINE in rendered

    def test_current_terminal_uses_current_completion_time(self):
        previous = replace(
            _stable(SIGNED_OUT),
            last_verified="2026-07-13T04:48:00Z",
        )
        clock = datetime(2026, 7, 14, 15, 25, tzinfo=timezone.utc)
        done_view = _view(
            SIGNED_OUT,
            verification_lifecycle="completed",
            last_confirmed_ready_at="2026-07-14T15:22:00+00:00",
        )
        final = present_customer_capability(
            build_capability_view(done_view),
            previous_stable=previous,
            access_view=done_view,
            now=clock,
        )
        assert final.last_verified == "2026-07-14T15:22:00Z"
        assert final.previous_confirmed_at is None
        rendered = render_capability_panel(final, escape=_escape)
        assert "Latest check completed:" in rendered
        assert 'datetime="2026-07-14T15:22:00Z"' in rendered

    def test_swr_live_success_is_historical_not_present_tense(self):
        live_view = _view(
            READY,
            session_state="checking",
            background_verification=True,
            verification_lifecycle="running",
        )
        live = build_capability_view(live_view, extracted_items=AMEX_FIELDS)
        assert live.state == CapabilityState.EXTRACTION_SUCCESS
        presented = present_customer_capability(
            live, previous_stable=None, access_view=live_view,
        )
        _assert_determining(
            presented, previous_state=CapabilityState.EXTRACTION_SUCCESS,
        )
        assert presented.primary_headline == REFRESHING_STATUS_HEADLINE
        assert "mighty can see and extract" not in presented.headline.lower()

    def test_timelines_not_mixed_during_determining(self):
        previous = _stable(READY)
        assert previous.truth_validation is not None
        mid_view = _inflight_checking()
        held = present_customer_capability(
            build_capability_view(mid_view, extracted_items=AMEX_FIELDS),
            previous_stable=previous,
            access_view=mid_view,
        )
        labels = [s.label for s in held.timeline_sections]
        assert "Previous completed check" in labels
        assert "Current check" in labels
        current = next(s for s in held.timeline_sections if s.label == "Current check")
        assert [e.description for e in current.events] == [
            "Verification started",
            "Checking login state",
        ]
        # Current-check truth timeline must not carry prior terminal capability event.
        assert held.truth_validation is not None
        current_descs = {e.description for e in held.truth_validation.timeline}
        assert "Checking login state" in current_descs
        assert not any(d.startswith("Capability ·") for d in current_descs)
        # Previous section leads with prior outcome and stays separate.
        previous_section = next(
            s for s in held.timeline_sections if s.label == "Previous completed check"
        )
        assert previous_section.events[0].description == (
            "Connected — account data extracted"
        )

        done_view = _view(SIGNED_OUT, verification_lifecycle="completed")
        final = present_customer_capability(
            build_capability_view(done_view),
            previous_stable=held,
            access_view=done_view,
        )
        assert final.presentation_phase == "terminal"
        assert final.timeline_sections == ()
        assert final.truth_validation is not None
        assert final.truth_validation.timeline != held.truth_validation.timeline

    def test_stale_prior_never_present_tense_while_active(self):
        previous = _stable(SIGNED_OUT)
        for lifecycle in ("requested", "running", "session_verified", "extracting"):
            mid_view = _inflight_checking(verification_lifecycle=lifecycle)
            presented = present_customer_capability(
                build_capability_view(mid_view),
                previous_stable=previous,
                access_view=mid_view,
            )
            assert presented.presentation_phase == "determining"
            assert "You are signed out" not in presented.headline
            assert "cannot determine" not in presented.headline.lower()

    def test_force_unknown_bypasses_determining(self):
        previous = _stable(READY)
        live_view = _inflight_checking()
        forced = present_customer_capability(
            build_capability_view(live_view),
            previous_stable=previous,
            access_view=live_view,
            force_unknown=True,
        )
        assert forced.state == CapabilityState.LOGIN_UNKNOWN
        assert forced.presentation_phase == "terminal"
        assert forced.is_refreshing is False

    def test_render_determining_not_present_tense_unknown(self):
        previous = _stable(SIGNED_OUT)
        held = present_customer_capability(
            build_capability_view(_inflight_checking()),
            previous_stable=previous,
            access_view=_inflight_checking(),
        )
        rendered = render_capability_panel(held, escape=_escape)
        assert 'data-presentation-phase="determining"' in rendered
        assert 'data-capability="login_unknown"' in rendered
        assert 'data-status-historical="1"' not in rendered
        assert DETERMINING_HEADLINE in rendered
        assert "Previous completed check" in rendered
        assert "Signed out" in rendered
        assert "Last confirmed: Signed out" not in rendered
        assert "cannot determine" not in rendered.lower()
        assert "You are signed out" not in rendered

    def test_historical_signed_out_suppresses_signin_cta_while_determining(self):
        previous = _stable(SIGNED_OUT)
        assert previous.action_required is True
        held = present_customer_capability(
            build_capability_view(_inflight_checking()),
            previous_stable=previous,
            access_view=_inflight_checking(),
        )
        assert held.action_required is False
        assert held.action_label is None
        assert held.action_url is None
        rendered = render_capability_panel(held, escape=_escape)
        assert "Open American Express" not in rendered

    def test_terminal_cta_matrix(self):
        signed_out = present_customer_capability(
            build_capability_view(
                _view(SIGNED_OUT, verification_lifecycle="completed")
            ),
            access_view=_view(SIGNED_OUT, verification_lifecycle="completed"),
        )
        assert signed_out.action_required is True
        assert signed_out.action_label == "Open American Express"

        unknown = present_customer_capability(
            build_capability_view(
                _view(
                    CHECKING,
                    session_state="checking",
                    verification_lifecycle="timed_out",
                ),
                extraction_status=EXTRACTION_PENDING,
            ),
            access_view=_view(
                CHECKING,
                session_state="checking",
                verification_lifecycle="timed_out",
            ),
        )
        assert unknown.state == CapabilityState.LOGIN_UNKNOWN
        assert unknown.action_required is False

        no_data = present_customer_capability(
            build_capability_view(
                _view(
                    UNVERIFIED,
                    session_state="connected",
                    verification_lifecycle="completed",
                ),
                extracted_items=[],
            ),
            access_view=_view(
                UNVERIFIED,
                session_state="connected",
                verification_lifecycle="completed",
            ),
        )
        assert no_data.state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
        assert no_data.action_required is False

        success = present_customer_capability(
            build_capability_view(
                _view(READY, verification_lifecycle="completed"),
                extracted_items=AMEX_FIELDS,
            ),
            access_view=_view(READY, verification_lifecycle="completed"),
        )
        assert success.state == CapabilityState.EXTRACTION_SUCCESS
        assert success.action_required is False

    def test_stale_prior_not_relabeled_current_when_lifecycle_missing(self):
        previous = _stable(SIGNED_OUT)
        idle = _view(
            UNVERIFIED,
            session_state="unknown",
            verification_lifecycle=None,
        )
        presented = present_customer_capability(
            build_capability_view(idle),
            previous_stable=previous,
            access_view=idle,
            verification_lifecycle=None,
            background_verification=False,
        )
        # Live inconclusive terminal wins; prior signed-out must not become current.
        assert presented.presentation_phase == "terminal"
        assert presented.state == CapabilityState.LOGIN_UNKNOWN
        assert "You are signed out" not in presented.headline
        assert presented.action_required is False

    def test_timeline_developer_ids_follow_current_cycle(self):
        previous = build_capability_view(
            _view(READY, verification_lifecycle="completed", verification_id="ver-old"),
            extracted_items=AMEX_FIELDS,
            verification_id="ver-old",
        )
        live_view = _inflight_checking(verification_id="ver-new")
        live = build_capability_view(
            live_view, extracted_items=AMEX_FIELDS, verification_id="ver-new",
        )
        held = present_customer_capability(
            live, previous_stable=previous, access_view=live_view,
        )
        assert held.presentation_phase == "determining"
        assert held.truth_validation is not None
        ids = held.truth_validation.developer_ids
        assert ids.get("verification_id") == "ver-new"
        assert ids.get("verification_id") != "ver-old"
        labels = [s.label for s in held.timeline_sections]
        assert labels == ["Previous completed check", "Current check"]

    def test_production_sequence_unknown_then_signed_out_then_no_data(self):
        """Exact production sequence from PR #103."""
        # A. Previous night's terminal unknown.
        prior_unknown = replace(
            build_capability_view(
                _view(
                    CHECKING,
                    session_state="checking",
                    verification_lifecycle="timed_out",
                    last_confirmed_ready_at="2026-07-13T04:48:00+00:00",
                ),
                extraction_status=EXTRACTION_PENDING,
            ),
            last_verified="2026-07-13T04:48:00Z",
        )
        assert prior_unknown.state == CapabilityState.LOGIN_UNKNOWN

        # B. New verification begins next morning.
        morning = _inflight_checking(verification_id="ver-morning")
        determining = present_customer_capability(
            build_capability_view(morning),
            previous_stable=prior_unknown,
            access_view=morning,
        )
        assert determining.presentation_phase == "determining"
        assert determining.primary_headline == DETERMINING_HEADLINE_CURRENT
        assert determining.historical_summary is None
        assert determining.previous_capability_state == (
            CapabilityState.LOGIN_UNKNOWN.value
        )
        assert "cannot determine" not in determining.headline.lower()
        rendered_b = render_capability_panel(determining, escape=_escape)
        assert "Checking your login state" in rendered_b
        assert "Previous completed check" in rendered_b
        assert "Check inconclusive" in rendered_b
        assert "Mighty cannot determine" not in rendered_b

        # C. Current cycle terminals signed_out.
        clock_c = datetime(2026, 7, 14, 15, 6, tzinfo=timezone.utc)
        signed_out_view = _view(
            SIGNED_OUT,
            verification_lifecycle="completed",
            verification_id="ver-so",
            last_confirmed_ready_at="2026-07-14T15:05:00+00:00",
        )
        signed_out = present_customer_capability(
            build_capability_view(signed_out_view),
            previous_stable=determining,
            access_view=signed_out_view,
            now=clock_c,
        )
        assert signed_out.presentation_phase == "terminal"
        assert "You are signed out" in signed_out.headline
        assert signed_out.last_verified == "2026-07-14T15:05:00Z"

        # D. User logs in; new cycle begins.
        recheck = _inflight_checking(verification_id="ver-recheck")
        determining2 = present_customer_capability(
            build_capability_view(recheck),
            previous_stable=signed_out,
            access_view=recheck,
        )
        assert determining2.presentation_phase == "determining"
        assert determining2.historical_summary is None
        assert determining2.previous_capability_state == CapabilityState.SIGNED_OUT.value
        assert "You are signed out" not in determining2.headline
        rendered_d = render_capability_panel(determining2, escape=_escape)
        assert "Previous completed check" in rendered_d
        assert "Signed out" in rendered_d
        assert "Last confirmed: Signed out" not in rendered_d

        # E. Current cycle terminals authenticated/no-data.
        clock_e = datetime(2026, 7, 14, 15, 13, tzinfo=timezone.utc)
        no_data_view = _view(
            UNVERIFIED,
            session_state="connected",
            verification_lifecycle="completed",
            verification_id="ver-nodata",
            last_confirmed_ready_at="2026-07-14T15:12:00+00:00",
        )
        no_data = present_customer_capability(
            build_capability_view(no_data_view, extracted_items=[]),
            previous_stable=determining2,
            access_view=no_data_view,
            now=clock_e,
        )
        assert no_data.presentation_phase == "terminal"
        assert no_data.state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
        assert "cannot see your account information" in no_data.headline
        assert no_data.last_verified == "2026-07-14T15:12:00Z"

        # Dashboard HTML and API dict agree exactly across the sequence.
        for step in (determining, signed_out, determining2, no_data):
            payload = step.to_dict()
            html_panel = render_capability_panel(step, escape=_escape)
            assert payload["presentation_phase"] == step.presentation_phase
            assert payload["primary_headline"] == (step.primary_headline or step.headline)
            assert payload["status_is_historical"] == step.status_is_historical
            assert payload["primary_headline"] in html_panel
            assert f'data-presentation-phase="{step.presentation_phase}"' in html_panel
            if step.status_is_historical and step.historical_summary:
                assert payload["historical_summary"] == step.historical_summary
                assert step.historical_summary in html_panel


class TestCustomerPresentationStateMachine:
    """Product state machine: one presentation per lifecycle+capability pair."""

    def test_truth_table_active_lifecycle_always_checking(self):
        for lifecycle in ("requested", "running", "session_verified", "extracting"):
            mode = resolve_customer_presentation_mode(
                refreshing=True,
                capability_state=CapabilityState.SIGNED_OUT,
                has_previous=True,
                is_stale=False,
                ever_checked=True,
            )
            assert mode.mode == "checking"
            assert mode.is_checking is True
            assert mode.shows_previous_on_card is False
            assert mode.shows_previous_in_timeline is True
            live = present_customer_capability(
                build_capability_view(
                    _view(SIGNED_OUT, verification_lifecycle=lifecycle),
                ),
                previous_stable=_stable(SIGNED_OUT),
                access_view=_view(SIGNED_OUT, verification_lifecycle=lifecycle),
                verification_lifecycle=lifecycle,
            )
            assert live.presentation_phase == "determining"
            assert live.state == CapabilityState.LOGIN_UNKNOWN
            assert live.historical_summary is None
            assert "signed out" not in (live.primary_headline or "").lower()

    def test_illegal_flash_signed_out_during_active_check_blocked(self):
        """Checking must not briefly present Signed Out as current truth."""
        live_view = _inflight_checking()
        # Live capability may already look signed-out from stale readiness.
        live = build_capability_view(
            _view(SIGNED_OUT, verification_lifecycle="running"),
        )
        presented = present_customer_capability(
            live,
            previous_stable=_stable(READY),
            access_view=live_view,
        )
        assert presented.presentation_phase == "determining"
        assert presented.state != CapabilityState.SIGNED_OUT
        assert "You are signed out" not in (presented.primary_headline or "")
        rendered = render_capability_panel(presented, escape=_escape)
        assert "You are signed out" not in rendered
        assert "Last confirmed: Signed out" not in rendered

    def test_checking_with_prior_keeps_previous_only_in_timeline(self):
        presented = present_customer_capability(
            build_capability_view(_inflight_checking()),
            previous_stable=_stable(SIGNED_OUT),
            access_view=_inflight_checking(),
        )
        assert presented.historical_summary is None
        assert presented.status_is_historical is False
        labels = [s.label for s in presented.timeline_sections]
        assert labels == ["Previous completed check", "Current check"]
        previous = next(s for s in presented.timeline_sections if s.label == "Previous completed check")
        assert previous.events[0].description == "Signed out"

    def test_terminal_modes_are_unique(self):
        cases = [
            (SIGNED_OUT, "signed_out", CapabilityState.SIGNED_OUT),
            (READY, "connected", CapabilityState.EXTRACTION_SUCCESS),
            ("logged_in_no_data", "logged_in_no_account_data", CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA),
        ]
        for fixture, mode_name, state in cases:
            if fixture == READY:
                view = _view(READY, verification_lifecycle="completed")
                live = build_capability_view(view, extracted_items=AMEX_FIELDS)
            elif fixture == "logged_in_no_data":
                live = _stable("logged_in_no_data")
                view = _view(
                    UNVERIFIED,
                    session_state="connected",
                    verification_lifecycle="completed",
                )
            else:
                view = _view(SIGNED_OUT, verification_lifecycle="completed")
                live = build_capability_view(view)
            mode = resolve_customer_presentation_mode(
                refreshing=False,
                capability_state=state,
                has_previous=False,
                is_stale=False,
                ever_checked=True,
            )
            assert mode.mode == mode_name
            presented = present_customer_capability(
                live, previous_stable=None, access_view=view,
                now=datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc),
            )
            assert presented.presentation_phase == "terminal"
            assert presented.state == state
            assert presented.status_is_historical is False

    def test_stale_connected_does_not_show_empty_extracted(self):
        now = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)
        access = _view(
            READY,
            verification_lifecycle="completed",
            last_confirmed_ready_at=(
                now - timedelta(seconds=CUSTOMER_TRUTH_FRESHNESS_SECONDS + 5)
            ).isoformat(),
        )
        presented = present_customer_capability(
            build_capability_view(access, extracted_items=AMEX_FIELDS),
            previous_stable=None,
            access_view=access,
            now=now,
        )
        assert presented.status_is_historical is True
        assert presented.state == CapabilityState.EXTRACTION_SUCCESS
        assert not presented.extracted_fields
        rendered = render_capability_panel(presented, escape=_escape)
        assert "No field values in the latest snapshot" not in rendered
        assert rendered.count("Last confirmed") == 1




class TestSelectedVerificationTimestampWiring:
    """Production: correct verification selected, Last confirmed showed 11:29 AM.

    capability.last_verified was sourced from last_confirmed_ready_at while
    verification_completed_at on the selected row was ~3:11 PM.
    """

    STALE_1129 = "2026-07-14T18:29:00Z"  # 11:29 AM PT
    SELECTED_311 = "2026-07-14T22:11:00Z"  # ~3:11 PM PT
    PRIOR_HISTORICAL = "2026-07-14T17:00:00Z"

    def _seed_verification(
        self,
        db,
        *,
        vid: str,
        completed_at: str | None,
        lifecycle: str = "completed",
        requested_at: str | None = None,
        started_at: str | None = None,
        terminal_reason: str = "signed_out",
    ) -> None:
        ensure_session_verification_tables(db)
        req = requested_at or completed_at or "2026-07-14T22:00:00Z"
        start = started_at or req
        db.execute(
            """
            INSERT INTO provider_session_verification (
                verification_id, user_id, provider, lifecycle,
                requested_at, started_at, completed_at,
                terminal_reason, terminal_source
            ) VALUES (?, ?, 'amex', ?, ?, ?, ?, ?, 'test')
            """,
            (vid, "u1", lifecycle, req, start, completed_at, terminal_reason),
        )
        db.commit()

    def _stale_readiness_access(self, *, verification_id: str = "ver-311"):
        return _view(
            SIGNED_OUT,
            verification_lifecycle="completed",
            verification_id=verification_id,
            last_confirmed_ready_at="2026-07-14T18:29:00+00:00",
        )

    def _seed_stale_prior(self, db) -> None:
        prior = replace(
            _stable(SIGNED_OUT, verification_id="ver-1129"),
            last_verified=self.STALE_1129,
        )
        save_stable_capability(
            db,
            "u1",
            prior,
            order_meta=_meta(
                verification_id="ver-1129",
                completed_at=self.STALE_1129,
                terminal_reason="signed_out",
            ),
        )

    def test_last_verified_follows_selected_verification_completed_at(self):
        db = _memory_db()
        self._seed_verification(
            db, vid="ver-311", completed_at=self.SELECTED_311,
        )
        self._seed_stale_prior(db)
        access = self._stale_readiness_access()
        clock = datetime(2026, 7, 14, 22, 36, tzinfo=timezone.utc)
        live = build_capability_view(access, verification_id="ver-311")
        assert live.last_verified == self.STALE_1129  # from last_confirmed_ready_at
        meta = resolve_order_meta_for_view(
            live, db=db, user_id="u1", access_view=access,
        )
        live = apply_selected_verification_timestamp(live, meta)
        assert live.last_verified == self.SELECTED_311
        previous = load_stable_capability(db, "u1", "amex")
        presented = present_customer_capability(
            live,
            previous_stable=previous,
            access_view=access,
            now=clock,
        )
        assert presented.last_verified == self.SELECTED_311
        assert presented.timestamp_label == "Latest check completed"
        assert presented.status_is_historical is False
        rendered = render_capability_panel(presented, escape=_escape)
        assert "Latest check completed" in rendered
        assert "Last confirmed" not in rendered

        # Persist path (Dashboard / API) must store the selected completion time.
        presented_persisted = build_presented_capability_view(
            access,
            previous_stable=previous,
            persist_db=db,
            persist_user_id="u1",
            verification_id="ver-311",
        )
        assert presented_persisted.last_verified == self.SELECTED_311
        loaded = load_stable_capability(db, "u1", "amex")
        assert loaded is not None
        assert loaded.last_verified == self.SELECTED_311
        stored_meta = load_stable_order_meta(db, "u1", "amex")
        assert stored_meta is not None
        assert stored_meta.verification_id == "ver-311"
        assert stored_meta.verification_completed_at == self.SELECTED_311

    def test_dashboard_and_api_both_return_selected_completed_at(self):
        """Dashboard build_presented + API _apply_stable share 3:11 PM."""
        from mighty.account_status import _apply_stable_customer_capability

        db = _memory_db()
        self._seed_verification(
            db, vid="ver-311", completed_at=self.SELECTED_311,
        )
        self._seed_stale_prior(db)
        access = self._stale_readiness_access()
        live = build_capability_view(access, verification_id="ver-311")
        assert live.last_verified == self.STALE_1129

        dash = build_presented_capability_view(
            access,
            persist_db=db,
            persist_user_id="u1",
            verification_id="ver-311",
        )
        assert dash.last_verified == self.SELECTED_311

        # Reset persist so API path re-applies independently.
        clear_stable_capability(db, "u1", "amex")
        self._seed_stale_prior(db)
        api_live = build_capability_view(access, verification_id="ver-311")
        accounts = [
            AccountStatus(
                source="amex",
                display_name="American Express",
                status="needs_login",
                presentation_key="needs_sign_in",
                presentation_label="Sign in required",
                last_successful_sync_at=None,
                current_attempt_at=None,
                last_error=None,
                user_action_label=None,
                user_action_url=None,
                customer_access=access,
                capability=api_live,
                verification_lifecycle="completed",
                background_verification=False,
            )
        ]
        _apply_stable_customer_capability(accounts, db=db, user_id="u1", write_persist=True)
        api = accounts[0].capability
        assert api is not None
        assert api.last_verified == self.SELECTED_311
        assert dash.last_verified == api.last_verified == self.SELECTED_311
        assert dash.timestamp_label == api.timestamp_label
        assert dash.state == api.state

    def test_freshness_demotion_keeps_selected_completed_at(self):
        """Stale demotion labels Last confirmed with 3:11, never 11:29."""
        db = _memory_db()
        self._seed_verification(
            db, vid="ver-311", completed_at=self.SELECTED_311,
        )
        access = self._stale_readiness_access()
        # Clock far past the freshness window relative to 3:11 PM.
        clock = datetime(2026, 7, 14, 23, 0, tzinfo=timezone.utc)
        presented = build_presented_capability_view(
            access,
            persist_db=db,
            persist_user_id="u1",
            verification_id="ver-311",
        )
        # Re-present with an old clock via the same wired last_verified.
        live = build_capability_view(access, verification_id="ver-311")
        meta = resolve_order_meta_for_view(
            live, db=db, user_id="u1", access_view=access,
        )
        live = apply_selected_verification_timestamp(live, meta)
        demoted = present_customer_capability(
            live,
            previous_stable=None,
            access_view=access,
            now=clock + timedelta(seconds=CUSTOMER_TRUTH_FRESHNESS_SECONDS + 1),
        )
        assert demoted.status_is_historical is True
        assert demoted.timestamp_label == "Last confirmed"
        assert demoted.last_verified == self.SELECTED_311
        assert demoted.previous_confirmed_at == self.SELECTED_311
        assert demoted.last_verified != self.STALE_1129
        rendered = render_capability_panel(demoted, escape=_escape)
        assert "Last confirmed" in rendered
        # Must not surface the readiness/extraction 11:29 stamp.
        assert "11:29" not in rendered
        assert presented.last_verified == self.SELECTED_311

    def test_historical_prior_keeps_own_completion_time(self):
        """Active check retains prior card's completion, not readiness lag."""
        db = _memory_db()
        self._seed_verification(
            db,
            vid="ver-prior",
            completed_at=self.PRIOR_HISTORICAL,
        )
        self._seed_verification(
            db,
            vid="ver-active",
            completed_at=None,
            lifecycle="running",
            requested_at="2026-07-14T22:30:00Z",
            started_at="2026-07-14T22:30:05Z",
            terminal_reason="none",
        )
        prior = replace(
            _stable(SIGNED_OUT, verification_id="ver-prior"),
            last_verified=self.PRIOR_HISTORICAL,
        )
        save_stable_capability(
            db,
            "u1",
            prior,
            order_meta=_meta(
                verification_id="ver-prior",
                completed_at=self.PRIOR_HISTORICAL,
                terminal_reason="signed_out",
            ),
        )
        active_access = _inflight_checking(verification_id="ver-active")
        held = build_presented_capability_view(
            active_access,
            previous_stable=prior,
            persist_db=db,
            persist_user_id="u1",
            verification_id="ver-active",
        )
        assert held.presentation_phase == "determining"
        assert held.is_refreshing is True
        assert held.previous_confirmed_at == self.PRIOR_HISTORICAL
        # Active check must not rewrite current-check stamps from readiness.
        assert held.last_verified != self.STALE_1129

    def test_active_verification_does_not_overwrite_from_completed_at(self):
        """No completed_at → leave live last_verified alone (requested/started path)."""
        db = _memory_db()
        self._seed_verification(
            db,
            vid="ver-active",
            completed_at=None,
            lifecycle="running",
            requested_at="2026-07-14T22:30:00Z",
            started_at="2026-07-14T22:30:05Z",
            terminal_reason="none",
        )
        access = _inflight_checking(verification_id="ver-active")
        live = build_capability_view(access, verification_id="ver-active")
        before = live.last_verified
        meta = resolve_order_meta_for_view(
            live, db=db, user_id="u1", access_view=access,
        )
        assert meta.verification_completed_at is None
        wired = apply_selected_verification_timestamp(live, meta)
        assert wired.last_verified == before
        # Active checks may still receive correlation tagging; clock stays put.
        assert wired.last_verified == live.last_verified

    def test_legacy_without_verification_completion_keeps_readiness_timestamp(self):
        """No verification identity/completed_at → degrade to readiness timestamp."""
        live = replace(
            build_capability_view(
                _view(
                    SIGNED_OUT,
                    verification_lifecycle="completed",
                    last_confirmed_ready_at="2026-07-14T18:29:00+00:00",
                ),
            ),
            last_verified=self.STALE_1129,
            current_verification_id=None,
        )
        # Clear truth IDs so legacy path has no selected verification identity.
        if live.truth_validation is not None:
            live = replace(
                live,
                truth_validation=replace(
                    live.truth_validation,
                    developer_ids={},
                ),
            )
        meta = PresentationOrderMeta(
            verification_id=None,
            access_cycle_id=None,
            verification_completed_at=None,
            lifecycle=None,
            terminal_reason=None,
            account_identity=None,
        )
        wired = apply_selected_verification_timestamp(live, meta)
        assert wired.last_verified == self.STALE_1129

    def test_apply_selected_verification_timestamp_helper(self):
        view = replace(
            _stable(SIGNED_OUT, verification_id="ver-311"),
            last_verified=self.STALE_1129,
        )
        meta = _meta(
            verification_id="ver-311",
            completed_at=self.SELECTED_311,
            terminal_reason="signed_out",
        )
        wired = apply_selected_verification_timestamp(view, meta)
        assert wired.last_verified == self.SELECTED_311
        assert view.last_verified == self.STALE_1129

    def test_resolve_order_meta_reads_verification_completed_at(self):
        db = _memory_db()
        self._seed_verification(
            db, vid="ver-311", completed_at=self.SELECTED_311,
        )
        live = replace(
            build_capability_view(
                _view(
                    SIGNED_OUT,
                    verification_lifecycle="completed",
                    verification_id="ver-311",
                    last_confirmed_ready_at="2026-07-14T18:29:00+00:00",
                ),
                verification_id="ver-311",
            ),
            last_verified=self.STALE_1129,
        )
        meta = resolve_order_meta_for_view(
            live, db=db, user_id="u1",
        )
        assert meta.verification_completed_at == self.SELECTED_311
        assert meta.verification_id == "ver-311"
        assert meta.access_cycle_id == "ver-311"
        assert meta.lifecycle == "completed"
        assert apply_selected_verification_timestamp(live, meta).last_verified == (
            self.SELECTED_311
        )


class TestTruthTimelineSelectedVerificationCorrelation:
    """Headline and current Truth Timeline must share one verification.

    Production defect: Latest check completed 9:13 PM while Truth Timeline
    still showed 11:29 AM events from an older verification.
    """

    STALE_1129 = "2026-07-14T18:29:00Z"  # 11:29 AM PT
    SELECTED_913 = "2026-07-15T04:13:00Z"  # 9:13 PM PT Jul 14

    def _seed_verification(self, db, *, vid: str, completed_at: str) -> None:
        ensure_session_verification_tables(db)
        db.execute(
            """
            INSERT INTO provider_session_verification (
                verification_id, user_id, provider, lifecycle,
                requested_at, started_at, completed_at,
                terminal_reason, terminal_source
            ) VALUES (?, ?, 'amex', 'completed', ?, ?, ?, 'signed_out', 'test')
            """,
            (vid, "u1", completed_at, completed_at, completed_at),
        )
        db.commit()

    def _assert_timeline_matches_selected(self, presented, *, vid: str, completed_at: str):
        assert presented.last_verified == completed_at
        assert presented.current_verification_id == vid
        assert presented.truth_validation is not None
        ids = presented.truth_validation.developer_ids
        assert ids.get("verification_id") == vid
        assert ids.get("access_cycle_id") == vid or ids.get("access_cycle_id")
        for event in presented.truth_validation.timeline:
            assert event.metadata.get("verification_id") == vid
            if event.timestamp and event.id != "tl-empty-correlated":
                assert event.timestamp == completed_at
                assert event.timestamp != self.STALE_1129 or completed_at == self.STALE_1129
        corr = build_timeline_correlation_record(presented)
        assert corr["presentation_verification_id"] == vid
        assert corr["mismatched_event_count"] == 0
        assert set(corr["current_timeline_verification_ids"]) <= {vid}

    def test_production_1129_timeline_replaced_by_913_signed_out(self):
        db = _memory_db()
        self._seed_verification(db, vid="ver-a-1129", completed_at=self.STALE_1129)
        self._seed_verification(db, vid="ver-b-913", completed_at=self.SELECTED_913)

        access_a = _view(
            SIGNED_OUT,
            verification_lifecycle="completed",
            verification_id="ver-a-1129",
            last_confirmed_ready_at="2026-07-14T18:29:00+00:00",
        )
        prior = build_capability_view(access_a, verification_id="ver-a-1129")
        prior = apply_selected_verification_timestamp(
            prior,
            resolve_order_meta_for_view(prior, db=db, user_id="u1", access_view=access_a),
        )
        assert prior.last_verified == self.STALE_1129
        save_stable_capability(
            db,
            "u1",
            prior,
            order_meta=_meta(
                verification_id="ver-a-1129",
                completed_at=self.STALE_1129,
                terminal_reason="signed_out",
            ),
        )

        access_b = _view(
            SIGNED_OUT,
            verification_lifecycle="completed",
            verification_id="ver-b-913",
            last_confirmed_ready_at="2026-07-14T18:29:00+00:00",
        )
        presented = build_presented_capability_view(
            access_b,
            persist_db=db,
            persist_user_id="u1",
            verification_id="ver-b-913",
        )
        self._assert_timeline_matches_selected(
            presented, vid="ver-b-913", completed_at=self.SELECTED_913,
        )
        rendered = render_capability_panel(presented, escape=_escape)
        assert self.STALE_1129 not in rendered
        for event in presented.truth_validation.timeline:
            assert event.timestamp != self.STALE_1129

        loaded = load_stable_capability(db, "u1", "amex")
        assert loaded is not None
        self._assert_timeline_matches_selected(
            loaded, vid="ver-b-913", completed_at=self.SELECTED_913,
        )

    def test_production_defect_also_swaps_for_logged_in_no_data(self):
        db = _memory_db()
        self._seed_verification(db, vid="ver-a-1129", completed_at=self.STALE_1129)
        ensure_session_verification_tables(db)
        db.execute(
            """
            INSERT INTO provider_session_verification (
                verification_id, user_id, provider, lifecycle,
                requested_at, started_at, completed_at,
                terminal_reason, terminal_source
            ) VALUES (?, ?, 'amex', 'completed', ?, ?, ?, 'authenticated', 'test')
            """,
            ("ver-b-913", "u1", self.SELECTED_913, self.SELECTED_913, self.SELECTED_913),
        )
        db.commit()

        prior = replace(
            _stable("logged_in_no_data", verification_id="ver-a-1129"),
            last_verified=self.STALE_1129,
        )
        save_stable_capability(
            db,
            "u1",
            prior,
            order_meta=_meta(
                verification_id="ver-a-1129",
                completed_at=self.STALE_1129,
                terminal_reason="authenticated",
            ),
        )
        access_b = _view(
            UNVERIFIED,
            session_state="connected",
            verification_lifecycle="completed",
            verification_id="ver-b-913",
            last_confirmed_ready_at="2026-07-14T18:29:00+00:00",
        )
        presented = build_presented_capability_view(
            access_b,
            persist_db=db,
            persist_user_id="u1",
            verification_id="ver-b-913",
            extracted_items=[],
            extraction_status=EXTRACTION_COMPLETE,
        )
        assert presented.state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
        self._assert_timeline_matches_selected(
            presented, vid="ver-b-913", completed_at=self.SELECTED_913,
        )

    def test_dashboard_and_api_timelines_identical(self):
        from mighty.account_status import _apply_stable_customer_capability

        db = _memory_db()
        self._seed_verification(db, vid="ver-b-913", completed_at=self.SELECTED_913)
        access = _view(
            SIGNED_OUT,
            verification_lifecycle="completed",
            verification_id="ver-b-913",
            last_confirmed_ready_at="2026-07-14T18:29:00+00:00",
        )
        dash = build_presented_capability_view(
            access,
            persist_db=db,
            persist_user_id="u1",
            verification_id="ver-b-913",
        )
        clear_stable_capability(db, "u1", "amex")
        api_live = build_capability_view(access, verification_id="ver-b-913")
        accounts = [
            AccountStatus(
                source="amex",
                display_name="American Express",
                status="needs_login",
                presentation_key="needs_sign_in",
                presentation_label="Sign in required",
                last_successful_sync_at=None,
                current_attempt_at=None,
                last_error=None,
                user_action_label=None,
                user_action_url=None,
                customer_access=access,
                capability=api_live,
                verification_lifecycle="completed",
                background_verification=False,
            )
        ]
        _apply_stable_customer_capability(accounts, db=db, user_id="u1", write_persist=True)
        api = accounts[0].capability
        assert api is not None
        assert dash.last_verified == api.last_verified == self.SELECTED_913
        assert dash.current_verification_id == api.current_verification_id
        assert [
            (e.id, e.timestamp, e.metadata.get("verification_id"))
            for e in dash.truth_validation.timeline
        ] == [
            (e.id, e.timestamp, e.metadata.get("verification_id"))
            for e in api.truth_validation.timeline
        ]

    def test_mismatched_and_legacy_events_omitted(self):
        from mighty.truth_validation import (
            EvidenceCategory,
            EvidenceOutcome,
            TruthEvidence,
        )

        live = build_capability_view(
            _view(
                SIGNED_OUT,
                verification_lifecycle="completed",
                verification_id="ver-b-913",
                last_confirmed_ready_at="2026-07-15T04:13:00+00:00",
            ),
            verification_id="ver-b-913",
        )
        assert live.truth_validation is not None
        stale = TruthEvidence(
            id="stale-a",
            timestamp=self.STALE_1129,
            category=EvidenceCategory.SESSION,
            description="Stale A event",
            outcome=EvidenceOutcome.PASS,
            confidence_contribution=0,
            metadata={"verification_id": "ver-a-1129", "access_cycle_id": "ver-a-1129"},
        )
        legacy = TruthEvidence(
            id="legacy-no-id",
            timestamp=self.STALE_1129,
            category=EvidenceCategory.SESSION,
            description="Legacy uncorrelated",
            outcome=EvidenceOutcome.PASS,
            confidence_contribution=0,
            metadata={},
        )
        polluted = replace(
            live,
            truth_validation=replace(
                live.truth_validation,
                timeline=live.truth_validation.timeline + (stale, legacy),
            ),
        )
        cleaned = correlate_presentation_timeline(
            polluted,
            verification_id="ver-b-913",
            access_cycle_id="ver-b-913",
        )
        ids = {e.id for e in cleaned.truth_validation.timeline}
        assert "stale-a" not in ids
        assert "legacy-no-id" not in ids

    def test_no_fallback_to_provider_history_uses_empty_state(self):
        from mighty.truth_validation import EvidenceCategory, EvidenceOutcome, TruthEvidence

        live = build_capability_view(
            _view(
                SIGNED_OUT,
                verification_lifecycle="completed",
                verification_id="ver-b-913",
            ),
            verification_id="ver-b-913",
        )
        only_foreign = (
            TruthEvidence(
                id="foreign",
                timestamp=self.STALE_1129,
                category=EvidenceCategory.SESSION,
                description="Other verification",
                outcome=EvidenceOutcome.PASS,
                confidence_contribution=0,
                metadata={"verification_id": "ver-other", "access_cycle_id": "ver-other"},
            ),
        )
        polluted = replace(
            live,
            last_verified=self.SELECTED_913,
            truth_validation=replace(
                live.truth_validation,
                timeline=only_foreign,
                developer_ids={
                    "verification_id": "ver-b-913",
                    "access_cycle_id": "ver-b-913",
                },
            ),
        )
        cleaned = correlate_presentation_timeline(
            polluted,
            verification_id="ver-b-913",
            access_cycle_id="ver-b-913",
        )
        assert len(cleaned.truth_validation.timeline) == 1
        assert cleaned.truth_validation.timeline[0].description == (
            EMPTY_CORRELATED_TIMELINE_MESSAGE
        )

    def test_events_sorted_by_occurred_at_stable_for_ties(self):
        from mighty.truth_validation import (
            EvidenceCategory,
            EvidenceOutcome,
            TruthEvidence,
            sort_timeline_events,
        )

        events = (
            TruthEvidence(
                id="b",
                timestamp="2026-07-15T04:13:00Z",
                category=EvidenceCategory.SESSION,
                description="Second",
                outcome=EvidenceOutcome.PASS,
                confidence_contribution=0,
                metadata={"verification_id": "ver-b-913"},
            ),
            TruthEvidence(
                id="a",
                timestamp="2026-07-15T04:12:00Z",
                category=EvidenceCategory.NAVIGATION,
                description="First",
                outcome=EvidenceOutcome.PASS,
                confidence_contribution=0,
                metadata={"verification_id": "ver-b-913"},
            ),
            TruthEvidence(
                id="c",
                timestamp="2026-07-15T04:13:00Z",
                category=EvidenceCategory.VERIFICATION,
                description="Third",
                outcome=EvidenceOutcome.PASS,
                confidence_contribution=0,
                metadata={"verification_id": "ver-b-913"},
            ),
        )
        sorted_events = sort_timeline_events(events)
        assert [e.id for e in sorted_events] == ["a", "b", "c"]
        matched, omitted = filter_correlated_timeline_events(
            events,
            verification_id="ver-b-913",
            access_cycle_id="ver-b-913",
        )
        assert omitted == 0
        assert [e.id for e in matched] == ["a", "b", "c"]

    def test_determining_sections_keep_active_and_previous_separate(self):
        prior = apply_selected_verification_timestamp(
            build_capability_view(
                _view(
                    SIGNED_OUT,
                    verification_lifecycle="completed",
                    verification_id="ver-prior",
                    last_confirmed_ready_at="2026-07-14T18:29:00+00:00",
                ),
                verification_id="ver-prior",
            ),
            _meta(
                verification_id="ver-prior",
                completed_at=self.STALE_1129,
                terminal_reason="signed_out",
            ),
        )
        active = _inflight_checking(verification_id="ver-active")
        held = present_customer_capability(
            build_capability_view(active, verification_id="ver-active"),
            previous_stable=prior,
            access_view=active,
        )
        assert held.presentation_phase == "determining"
        labels = [s.label for s in held.timeline_sections]
        assert labels == ["Previous completed check", "Current check"]
        previous = next(
            s for s in held.timeline_sections if s.label == "Previous completed check"
        )
        current = next(s for s in held.timeline_sections if s.label == "Current check")
        assert all(
            (e.timestamp == self.STALE_1129) for e in previous.events if e.timestamp
        )
        assert all(
            e.description in ("Verification started", "Checking login state")
            for e in current.events
        )
        assert held.truth_validation is not None
        assert not any(
            d.startswith("Capability ·")
            for d in (e.description for e in held.truth_validation.timeline)
        )

    def test_terminal_presentation_has_no_mixed_sections(self):
        db = _memory_db()
        self._seed_verification(db, vid="ver-b-913", completed_at=self.SELECTED_913)
        presented = build_presented_capability_view(
            _view(
                SIGNED_OUT,
                verification_lifecycle="completed",
                verification_id="ver-b-913",
                last_confirmed_ready_at="2026-07-14T18:29:00+00:00",
            ),
            persist_db=db,
            persist_user_id="u1",
            verification_id="ver-b-913",
        )
        assert presented.timeline_sections == ()
        self._assert_timeline_matches_selected(
            presented, vid="ver-b-913", completed_at=self.SELECTED_913,
        )

    def test_legacy_uncorrelated_timeline_cannot_attach_to_newer_card(self):
        from mighty.truth_validation import EvidenceCategory, EvidenceOutcome, TruthEvidence

        db = _memory_db()
        self._seed_verification(db, vid="ver-b-913", completed_at=self.SELECTED_913)
        live = build_capability_view(
            _view(
                SIGNED_OUT,
                verification_lifecycle="completed",
                verification_id="ver-b-913",
                last_confirmed_ready_at="2026-07-15T04:13:00+00:00",
            ),
            verification_id="ver-b-913",
        )
        legacy_timeline = (
            TruthEvidence(
                id="legacy",
                timestamp=self.STALE_1129,
                category=EvidenceCategory.SESSION,
                description="Legacy 11:29 event",
                outcome=EvidenceOutcome.PASS,
                confidence_contribution=0,
                metadata={},
            ),
        )
        polluted = replace(
            live,
            last_verified=self.SELECTED_913,
            truth_validation=replace(
                live.truth_validation,
                timeline=legacy_timeline,
                developer_ids={
                    "verification_id": "ver-b-913",
                    "access_cycle_id": "ver-b-913",
                },
            ),
        )
        save_stable_capability(
            db,
            "u1",
            polluted,
            order_meta=_meta(
                verification_id="ver-b-913",
                completed_at=self.SELECTED_913,
                terminal_reason="signed_out",
            ),
        )
        loaded = load_stable_capability(db, "u1", "amex")
        assert loaded is not None
        assert loaded.last_verified == self.SELECTED_913
        descs = [e.description for e in loaded.truth_validation.timeline]
        assert "Legacy 11:29 event" not in descs
        assert EMPTY_CORRELATED_TIMELINE_MESSAGE in descs



class TestMonotonicPersistence:
    def test_newer_then_older_late_write_is_noop(self):
        db = _memory_db()
        newer = _stable(SIGNED_OUT, verification_id="ver-new")
        older = _stable(READY, verification_id="ver-old")
        assert save_stable_capability(
            db, "u1", newer,
            order_meta=_meta(
                verification_id="ver-new",
                completed_at="2026-07-14T12:00:00Z",
                terminal_reason="signed_out",
            ),
        )
        assert save_stable_capability(
            db, "u1", older,
            order_meta=_meta(
                verification_id="ver-old",
                completed_at="2026-07-14T11:00:00Z",
            ),
        ) is False
        loaded = load_stable_capability(db, "u1", "amex")
        assert loaded is not None
        assert loaded.state == CapabilityState.SIGNED_OUT
        meta = load_stable_order_meta(db, "u1", "amex")
        assert meta is not None
        assert meta.verification_id == "ver-new"

    def test_older_then_newer_write_accepted(self):
        db = _memory_db()
        older = _stable(READY, verification_id="ver-old")
        newer = _stable(SIGNED_OUT, verification_id="ver-new")
        assert save_stable_capability(
            db, "u1", older,
            order_meta=_meta(
                verification_id="ver-old",
                completed_at="2026-07-14T11:00:00Z",
            ),
        )
        assert save_stable_capability(
            db, "u1", newer,
            order_meta=_meta(
                verification_id="ver-new",
                completed_at="2026-07-14T12:00:00Z",
                terminal_reason="signed_out",
            ),
        )
        loaded = load_stable_capability(db, "u1", "amex")
        assert loaded is not None
        assert loaded.state == CapabilityState.SIGNED_OUT

    def test_duplicate_write_same_verification_idempotent(self):
        db = _memory_db()
        card = _stable(READY, verification_id="ver-1")
        meta = _meta(verification_id="ver-1", completed_at="2026-07-14T12:00:00Z")
        assert save_stable_capability(db, "u1", card, order_meta=meta)
        # Same cycle, slightly different last_verified — still accepted.
        card2 = replace(card, last_verified="2026-07-14T12:00:05Z")
        assert save_stable_capability(db, "u1", card2, order_meta=meta)

    def test_is_newer_presentation_rule(self):
        older = _meta(verification_id="a", completed_at="2026-07-14T10:00:00Z")
        newer = _meta(verification_id="b", completed_at="2026-07-14T11:00:00Z")
        assert is_newer_presentation(newer, older) is True
        assert is_newer_presentation(older, newer) is False
        assert is_newer_presentation(newer, None) is True
        assert is_newer_presentation(older, older) is True  # same verification_id

    def test_concurrent_requests_cannot_regress(self):
        """Interleaved newer/stale writers must never leave the older card.

        Uses a shared on-disk SQLite DB with one connection per task — matching
        production request isolation better than a shared :memory: handle.
        """
        import tempfile
        import os

        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        try:
            bootstrap = sqlite3.connect(path)
            bootstrap.row_factory = sqlite3.Row
            ensure_customer_capability_presentation_tables(bootstrap)
            bootstrap.close()

            success = _stable(READY, verification_id="ver-success")
            signed_out = _stable(SIGNED_OUT, verification_id="ver-so")

            def _conn():
                c = sqlite3.connect(path, timeout=10)
                c.row_factory = sqlite3.Row
                return c

            seed = _conn()
            save_stable_capability(
                seed, "u1", success,
                order_meta=_meta(
                    verification_id="ver-success",
                    completed_at="2026-07-14T10:00:00Z",
                ),
            )
            seed.close()

            def write_newer():
                db = _conn()
                try:
                    return save_stable_capability(
                        db, "u1", signed_out,
                        order_meta=_meta(
                            verification_id="ver-so",
                            completed_at="2026-07-14T12:00:00Z",
                            terminal_reason="signed_out",
                        ),
                    )
                finally:
                    db.close()

            def write_stale():
                db = _conn()
                try:
                    return save_stable_capability(
                        db, "u1", success,
                        order_meta=_meta(
                            verification_id="ver-success",
                            completed_at="2026-07-14T10:00:00Z",
                        ),
                    )
                finally:
                    db.close()

            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = []
                for _ in range(20):
                    futures.append(pool.submit(write_newer))
                    futures.append(pool.submit(write_stale))
                for f in futures:
                    f.result()

            check = _conn()
            loaded = load_stable_capability(check, "u1", "amex")
            meta = load_stable_order_meta(check, "u1", "amex")
            check.close()
            assert loaded is not None
            assert loaded.state == CapabilityState.SIGNED_OUT
            assert meta is not None
            assert meta.verification_id == "ver-so"
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_interleaved_serial_stale_writes_are_noop(self):
        db = _memory_db()
        success = _stable(READY, verification_id="ver-success")
        signed_out = _stable(SIGNED_OUT, verification_id="ver-so")
        save_stable_capability(
            db, "u1", signed_out,
            order_meta=_meta(
                verification_id="ver-so",
                completed_at="2026-07-14T12:00:00Z",
                terminal_reason="signed_out",
            ),
        )
        for _ in range(5):
            assert save_stable_capability(
                db, "u1", success,
                order_meta=_meta(
                    verification_id="ver-success",
                    completed_at="2026-07-14T10:00:00Z",
                ),
            ) is False
        assert load_stable_capability(db, "u1", "amex").state == CapabilityState.SIGNED_OUT


class TestInvalidationAndDebug:
    def test_disconnect_clears_presentation(self):
        db = _memory_db()
        card = _stable(READY)
        save_stable_capability(
            db, "u1", card,
            order_meta=_meta(
                verification_id="ver-1",
                completed_at="2026-07-14T12:00:00Z",
            ),
        )
        assert load_stable_capability(db, "u1", "amex") is not None
        clear_stable_capability(db, "u1", "amex")
        assert load_stable_capability(db, "u1", "amex") is None

    def test_reconnect_starts_clean(self):
        db = _memory_db()
        save_stable_capability(
            db, "u1", _stable(READY),
            order_meta=_meta(
                verification_id="ver-old",
                completed_at="2026-07-14T10:00:00Z",
                account_identity=fingerprint_account_identity("alice@example.com"),
            ),
        )
        clear_stable_capability(db, "u1", "amex")
        # First-ever after reconnect — no prior card.
        presented = build_presented_capability_view(
            _inflight_checking(),
            previous_stable=None,
            persist_db=db,
            persist_user_id="u1",
        )
        assert presented.headline == FIRST_EVER_CHECKING_HEADLINE
        assert presented.presentation_phase == "determining"
        assert presented.is_refreshing is True
        # In-flight must not persist.
        assert load_stable_capability(db, "u1", "amex") is None

    def test_debug_override_not_persisted(self):
        db = _memory_db()
        real = _stable(READY, verification_id="ver-real")
        save_stable_capability(
            db, "u1", real,
            order_meta=_meta(
                verification_id="ver-real",
                completed_at="2026-07-14T12:00:00Z",
            ),
        )
        forced = build_presented_capability_view(
            _inflight_checking(),
            previous_stable=real,
            force_unknown=True,
            persist_db=db,
            persist_user_id="u1",
        )
        assert forced.state == CapabilityState.LOGIN_UNKNOWN
        assert forced.presentation_phase == "terminal"
        stored = load_stable_capability(db, "u1", "amex")
        assert stored is not None
        assert stored.state == CapabilityState.EXTRACTION_SUCCESS
        assert stored.presentation_phase == "terminal"

        # Normal request after debug still uses last real stable as historical.
        held = build_presented_capability_view(
            _inflight_checking(),
            force_unknown=False,
            persist_db=db,
            persist_user_id="u1",
        )
        assert held.presentation_phase == "determining"
        assert held.previous_capability_state == CapabilityState.EXTRACTION_SUCCESS.value
        assert held.is_refreshing is True
        assert held.status_is_historical is False
        assert held.historical_summary is None
        assert any(
            s.label == "Previous completed check" for s in held.timeline_sections
        )

    def test_account_identity_change_invalidates(self):
        db = _memory_db()
        alice = fingerprint_account_identity("alice@amex.com")
        bob = fingerprint_account_identity("bob@amex.com")
        save_stable_capability(
            db, "u1", _stable(READY),
            order_meta=_meta(
                verification_id="ver-alice",
                completed_at="2026-07-14T12:00:00Z",
                account_identity=alice,
            ),
        )
        # Load with bob identity → no prior card.
        assert load_stable_capability(
            db, "u1", "amex", account_identity=bob,
        ) is None
        # Alice still present until cleared by identity mismatch policy for legacy…
        # bob load with stored alice returns None without deleting when stored differs.
        assert load_stable_capability(
            db, "u1", "amex", account_identity=alice,
        ) is not None

    def test_legacy_row_without_identity_invalidated_when_identity_required(self):
        db = _memory_db()
        save_stable_capability(
            db, "u1", _stable(READY),
            order_meta=_meta(
                verification_id="ver-1",
                completed_at="2026-07-14T12:00:00Z",
                account_identity=None,
            ),
        )
        # Force legacy null identity
        db.execute(
            "UPDATE customer_capability_presentation SET account_identity=NULL "
            "WHERE user_id=? AND provider=?",
            ("u1", "amex"),
        )
        db.commit()
        assert load_stable_capability(
            db, "u1", "amex",
            account_identity=fingerprint_account_identity("alice"),
        ) is None


class TestApiDashboardParity:
    def _status(self, capability, *, view=None, lifecycle=None, bg=False):
        return AccountStatus(
            source="amex",
            display_name="American Express",
            status="up_to_date",
            presentation_key="ready",
            presentation_label="Connected",
            last_successful_sync_at=None,
            current_attempt_at=None,
            last_error=None,
            user_action_label=None,
            user_action_url=None,
            customer_access=view,
            capability=capability,
            verification_lifecycle=lifecycle,
            background_verification=bg,
        )

    def _compare_surfaces(self, access_view, *, extracted=None, lifecycle=None):
        live = build_capability_view(
            access_view,
            extracted_items=extracted,
            extraction_status=EXTRACTION_COMPLETE if extracted else None,
        )
        # API path: present from live account capability
        api = present_customer_capability(
            live,
            previous_stable=None,
            access_view=access_view,
            verification_lifecycle=lifecycle or access_view.active_verification_lifecycle,
            background_verification=access_view.background_verification,
        )
        # Dashboard path: home_state presentation
        status = self._status(
            live,
            view=access_view,
            lifecycle=lifecycle or access_view.active_verification_lifecycle,
            bg=access_view.background_verification,
        )
        home = resolve_home_state(
            accounts=[status],
            extracted_items=list(extracted or []),
            extraction_status=EXTRACTION_COMPLETE if extracted else None,
            previous_stable_capability=None,
        )
        assert home.capability is not None
        dash = home.capability
        assert dash.state == api.state
        assert dash.presentation_phase == api.presentation_phase
        assert dash.is_refreshing == api.is_refreshing
        assert dash.headline == api.headline
        assert dash.primary_headline == api.primary_headline
        assert dash.primary_explanation == api.primary_explanation
        assert dash.historical_summary == api.historical_summary
        assert dash.previous_capability_state == api.previous_capability_state
        assert dash.terminal_capability_state == api.terminal_capability_state
        assert dash.status_is_historical == api.status_is_historical
        assert dash.evidence == api.evidence
        assert dash.confidence == api.confidence
        assert dash.last_verified == api.last_verified
        assert dash.action_required == api.action_required
        assert dash.timestamp_label == api.timestamp_label
        assert dash.timeline_sections == api.timeline_sections
        if dash.truth_validation and api.truth_validation:
            assert dash.truth_validation.timeline == api.truth_validation.timeline
        return dash, api

    def test_parity_refreshing_with_prior(self):
        previous = _stable(READY)
        live_view = _inflight_checking()
        live = build_capability_view(live_view, extracted_items=AMEX_FIELDS)
        api = present_customer_capability(
            live, previous_stable=previous, access_view=live_view,
        )
        home = resolve_home_state(
            accounts=[self._status(live, view=live_view, lifecycle="running")],
            extracted_items=AMEX_FIELDS,
            previous_stable_capability=previous,
        )
        assert home.capability is not None
        assert home.capability.presentation_phase == api.presentation_phase == "determining"
        assert home.capability.previous_capability_state == (
            CapabilityState.EXTRACTION_SUCCESS.value
        )
        assert home.capability.is_refreshing is True
        assert api.is_refreshing is True
        assert home.capability.headline == api.headline == REFRESHING_STATUS_HEADLINE
        assert home.capability.primary_headline == api.primary_headline
        assert home.capability.historical_summary == api.historical_summary
        assert home.capability.timeline_sections == api.timeline_sections

    def test_parity_signed_out(self):
        view = _view(SIGNED_OUT, verification_lifecycle="completed")
        self._compare_surfaces(view)

    def test_parity_logged_in(self):
        view = _view(READY, verification_lifecycle="completed")
        self._compare_surfaces(view, extracted=AMEX_FIELDS)

    def test_parity_timeout(self):
        view = _view(
            CHECKING,
            session_state="checking",
            verification_lifecycle="timed_out",
        )
        dash, api = self._compare_surfaces(view)
        assert dash.state == CapabilityState.LOGIN_UNKNOWN
        assert dash.presentation_phase == "terminal"
        assert dash.is_refreshing is False

    def test_parity_first_verification(self):
        view = _inflight_checking()
        dash, api = self._compare_surfaces(view)
        assert dash.headline == FIRST_EVER_CHECKING_HEADLINE
        assert api.headline == FIRST_EVER_CHECKING_HEADLINE
        assert dash.presentation_phase == api.presentation_phase == "determining"
        assert dash.is_refreshing is True
        rendered = render_home_page(
            resolve_home_state(
                accounts=[self._status(build_capability_view(view), view=view, lifecycle="running")],
                previous_stable_capability=None,
            ),
            first_name="Alex",
            today_label="Mon",
            escape=_escape,
        )
        assert FIRST_EVER_CHECKING_HEADLINE in rendered
        assert "cannot determine" not in rendered.lower()
        assert 'data-presentation-phase="determining"' in rendered


class TestPersistenceRoundtrip:
    def test_save_load_roundtrip(self):
        db = _memory_db()
        stable = _stable(READY)
        save_stable_capability(
            db, "user-1", stable,
            order_meta=_meta(
                verification_id="ver-1",
                completed_at="2026-07-13T15:48:00Z",
            ),
        )
        loaded = load_stable_capability(db, "user-1", "amex")
        assert loaded is not None
        assert customer_visible_same(stable, loaded)

    def test_build_presented_persists_only_terminal(self):
        db = _memory_db()
        first = build_presented_capability_view(
            _view(SIGNED_OUT, verification_lifecycle="completed"),
            previous_stable=None,
            persist_db=db,
            persist_user_id="user-1",
        )
        assert first.state == CapabilityState.SIGNED_OUT
        assert load_stable_capability(db, "user-1", "amex") is not None

        mid = build_presented_capability_view(
            _inflight_checking(),
            previous_stable=first,
            persist_db=db,
            persist_user_id="user-1",
        )
        assert mid.is_refreshing is True
        assert mid.presentation_phase == "determining"
        assert mid.previous_capability_state == CapabilityState.SIGNED_OUT.value
        assert "You are signed out" not in mid.headline
        stored = load_stable_capability(db, "user-1", "amex")
        assert stored is not None
        assert stored.state == CapabilityState.SIGNED_OUT
        assert stored.presentation_phase == "terminal"
        assert stored.is_refreshing is False
