"""Regression: customer Truth Dashboard holds stable truth during refresh (PR #102)."""

from __future__ import annotations

import html
import sqlite3

from mighty.account_readiness import CHECKING, READY, SIGNED_OUT, UNVERIFIED
from mighty.capability_state import (
    CapabilityState,
    build_capability_view,
)
from mighty.customer_account_access import (
    DISCOVERED_MANUAL,
    build_customer_account_access_view,
)
from mighty.customer_capability_presentation import (
    REFRESH_LABEL,
    REFRESH_LABEL_VERBOSE,
    build_presented_capability_view,
    customer_visible_same,
    ensure_customer_capability_presentation_tables,
    load_stable_capability,
    present_customer_capability,
    save_stable_capability,
)
from mighty.home_ui import render_capability_panel
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
        last_confirmed_ready_at="2026-07-13T15:48:00+00:00" if state == READY else None,
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
        view = _view(READY, verification_lifecycle="completed")
        return build_capability_view(view, extracted_items=AMEX_FIELDS)
    if state == SIGNED_OUT:
        view = _view(SIGNED_OUT, verification_lifecycle="completed")
        return build_capability_view(view)
    if state == "logged_in_no_data":
        view = _view(
            UNVERIFIED,
            session_state="connected",
            verification_lifecycle="completed",
        )
        # Authenticated, observation finished, no publishable fields.
        return build_capability_view(
            view,
            extracted_items=[],
            extraction_status=EXTRACTION_COMPLETE,
        )
    raise AssertionError(state)


def _inflight_checking(**kwargs):
    return _view(
        CHECKING,
        session_state="checking",
        verification_lifecycle=kwargs.pop("verification_lifecycle", "running"),
        **kwargs,
    )


class TestHoldPreviousDuringRefresh:
    def test_signed_out_to_signed_out_refresh(self):
        previous = _stable(SIGNED_OUT)
        live_view = _inflight_checking()
        live = build_capability_view(live_view)
        assert live.state == CapabilityState.LOGIN_UNKNOWN  # raw live is transient

        presented = present_customer_capability(
            live, previous_stable=previous, access_view=live_view,
        )
        assert presented.state == CapabilityState.SIGNED_OUT
        assert presented.headline == previous.headline
        assert presented.evidence == previous.evidence
        assert presented.is_refreshing is True
        assert presented.refresh_label == REFRESH_LABEL
        assert presented.last_verified == previous.last_verified
        assert not any("in progress" in e.text.lower() for e in presented.evidence)

        # Terminal same result — no visual redraw beyond meta.
        done_view = _view(SIGNED_OUT, verification_lifecycle="completed")
        done = build_capability_view(done_view)
        final = present_customer_capability(
            done, previous_stable=presented, access_view=done_view,
        )
        assert final.state == CapabilityState.SIGNED_OUT
        assert final.is_refreshing is False
        assert customer_visible_same(previous, final)

    def test_logged_in_to_logged_in_refresh(self):
        previous = _stable(READY)
        live_view = _view(
            READY,
            session_state="checking",
            background_verification=True,
            verification_lifecycle="running",
        )
        live = build_capability_view(live_view, extracted_items=AMEX_FIELDS)
        presented = present_customer_capability(
            live, previous_stable=previous, access_view=live_view,
        )
        assert presented.state == CapabilityState.EXTRACTION_SUCCESS
        assert presented.is_refreshing is True
        assert presented.extracted_fields == previous.extracted_fields
        assert presented.headline == previous.headline

        done_view = _view(READY, verification_lifecycle="completed")
        done = build_capability_view(done_view, extracted_items=AMEX_FIELDS)
        final = present_customer_capability(
            done, previous_stable=previous, access_view=done_view,
        )
        assert final.state == CapabilityState.EXTRACTION_SUCCESS
        assert final.is_refreshing is False
        assert customer_visible_same(previous, final)

    def test_logged_in_to_logged_in_no_account_data(self):
        previous = _stable(READY)
        live_view = _inflight_checking()
        live = build_capability_view(live_view, extracted_items=AMEX_FIELDS)
        held = present_customer_capability(
            live, previous_stable=previous, access_view=live_view,
        )
        assert held.state == CapabilityState.EXTRACTION_SUCCESS
        assert held.is_refreshing is True
        assert "cannot determine whether you are logged in" not in held.headline.lower()

        done_view = _view(
            UNVERIFIED,
            session_state="connected",
            verification_lifecycle="completed",
        )
        done = build_capability_view(done_view, extracted_items=[])
        assert done.state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
        final = present_customer_capability(
            done, previous_stable=held, access_view=done_view,
        )
        assert final.state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
        assert final.is_refreshing is False
        assert final.headline != previous.headline

    def test_logged_in_to_signed_out(self):
        previous = _stable(READY)
        mid = present_customer_capability(
            build_capability_view(_inflight_checking()),
            previous_stable=previous,
            access_view=_inflight_checking(),
        )
        assert mid.state == CapabilityState.EXTRACTION_SUCCESS

        done_view = _view(SIGNED_OUT, verification_lifecycle="completed")
        done = build_capability_view(done_view)
        final = present_customer_capability(
            done, previous_stable=mid, access_view=done_view,
        )
        assert final.state == CapabilityState.SIGNED_OUT
        assert final.is_refreshing is False

    def test_signed_out_to_logged_in(self):
        previous = _stable(SIGNED_OUT)
        mid_view = _inflight_checking()
        mid = present_customer_capability(
            build_capability_view(mid_view),
            previous_stable=previous,
            access_view=mid_view,
        )
        assert mid.state == CapabilityState.SIGNED_OUT
        assert mid.is_refreshing is True

        done_view = _view(READY, verification_lifecycle="completed")
        done = build_capability_view(done_view, extracted_items=AMEX_FIELDS)
        final = present_customer_capability(
            done, previous_stable=mid, access_view=done_view,
        )
        assert final.state == CapabilityState.EXTRACTION_SUCCESS
        assert final.is_refreshing is False

    def test_timeout_during_refresh(self):
        previous = _stable(SIGNED_OUT)
        mid_view = _inflight_checking()
        mid = present_customer_capability(
            build_capability_view(mid_view),
            previous_stable=previous,
            access_view=mid_view,
        )
        assert mid.state == CapabilityState.SIGNED_OUT

        timeout_view = _view(
            CHECKING,
            session_state="checking",
            verification_lifecycle="timed_out",
        )
        timeout = build_capability_view(timeout_view, extraction_status=EXTRACTION_PENDING)
        assert timeout.state == CapabilityState.LOGIN_UNKNOWN
        final = present_customer_capability(
            timeout, previous_stable=mid, access_view=timeout_view,
        )
        assert final.state == CapabilityState.LOGIN_UNKNOWN
        assert final.is_refreshing is False
        assert any("timed out" in e.text.lower() for e in final.evidence)
        assert not any("in progress" in e.text.lower() for e in final.evidence)

    def test_refresh_crash(self):
        previous = _stable(READY)
        mid_view = _inflight_checking()
        mid = present_customer_capability(
            build_capability_view(mid_view, extracted_items=AMEX_FIELDS),
            previous_stable=previous,
            access_view=mid_view,
        )
        assert mid.state == CapabilityState.EXTRACTION_SUCCESS

        fail_view = _view(
            UNVERIFIED,
            session_state="unknown",
            verification_lifecycle="failed",
        )
        fail = build_capability_view(fail_view)
        final = present_customer_capability(
            fail, previous_stable=mid, access_view=fail_view,
        )
        assert final.state == CapabilityState.LOGIN_UNKNOWN
        assert final.is_refreshing is False
        assert any("inconclusive" in e.text.lower() for e in final.evidence)

    def test_first_ever_verification(self):
        live_view = _inflight_checking()
        live = build_capability_view(live_view)
        assert live.state == CapabilityState.LOGIN_UNKNOWN
        presented = present_customer_capability(
            live, previous_stable=None, access_view=live_view,
        )
        assert presented.state == CapabilityState.LOGIN_UNKNOWN
        assert presented.is_refreshing is True
        assert presented.refresh_label == REFRESH_LABEL_VERBOSE
        # First-ever may still include in-progress evidence from live resolution.
        assert any("in progress" in e.text.lower() for e in presented.evidence)

    def test_never_flash_login_unknown_during_successful_refresh(self):
        """Customer sequence must not observe Login Unknown mid successful refresh."""
        previous = _stable(SIGNED_OUT)
        observed_states: list[CapabilityState] = []

        # Mid-flight: live would be LOGIN_UNKNOWN / brief signed_out churn.
        for lifecycle in ("requested", "running", "session_verified", "extracting"):
            mid_view = _inflight_checking(verification_lifecycle=lifecycle)
            live = build_capability_view(mid_view)
            presented = present_customer_capability(
                live, previous_stable=previous, access_view=mid_view,
            )
            observed_states.append(presented.state)
            assert presented.state == CapabilityState.SIGNED_OUT
            assert presented.state != CapabilityState.LOGIN_UNKNOWN
            assert "Verification in progress" not in " ".join(e.text for e in presented.evidence)

        # Brief definitive signed-out evidence mid-cycle still held until terminal.
        brief_so = _view(
            SIGNED_OUT,
            verification_lifecycle="running",
            session_state="signed_out",
        )
        # Even if live says signed out while running, holding previous is fine;
        # if previous was signed out, state stays signed out.
        held = present_customer_capability(
            build_capability_view(brief_so),
            previous_stable=previous,
            access_view=_inflight_checking(verification_lifecycle="running"),
        )
        observed_states.append(held.state)
        assert CapabilityState.LOGIN_UNKNOWN not in observed_states

        done_view = _view(SIGNED_OUT, verification_lifecycle="completed")
        final = present_customer_capability(
            build_capability_view(done_view),
            previous_stable=held,
            access_view=done_view,
        )
        assert final.state == CapabilityState.SIGNED_OUT
        assert final.is_refreshing is False

    def test_force_unknown_bypasses_hold(self):
        previous = _stable(READY)
        live_view = _inflight_checking()
        live = build_capability_view(live_view)
        forced = present_customer_capability(
            live,
            previous_stable=previous,
            access_view=live_view,
            force_unknown=True,
        )
        assert forced.state == CapabilityState.LOGIN_UNKNOWN
        assert forced.is_refreshing is False

    def test_timeline_and_pipeline_held_during_refresh(self):
        previous = _stable(READY)
        assert previous.truth_validation is not None
        prev_timeline = previous.truth_validation.timeline
        prev_pipeline = previous.pipeline

        mid_view = _inflight_checking()
        held = present_customer_capability(
            build_capability_view(mid_view, extracted_items=AMEX_FIELDS),
            previous_stable=previous,
            access_view=mid_view,
        )
        assert held.truth_validation is not None
        assert held.truth_validation.timeline == prev_timeline
        assert held.pipeline == prev_pipeline

    def test_render_shows_refresh_not_login_unknown(self):
        previous = _stable(SIGNED_OUT)
        mid_view = _inflight_checking()
        held = present_customer_capability(
            build_capability_view(mid_view),
            previous_stable=previous,
            access_view=mid_view,
        )
        rendered = render_capability_panel(held, escape=_escape)
        assert 'data-capability="signed_out"' in rendered
        assert 'data-refreshing="1"' in rendered
        assert REFRESH_LABEL in rendered
        assert "Login unknown" not in rendered
        assert "Verification in progress" not in rendered
        assert "You are signed out" in rendered


class TestPersistence:
    def test_save_load_roundtrip(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        ensure_customer_capability_presentation_tables(db)
        stable = _stable(READY)
        save_stable_capability(db, "user-1", stable)
        loaded = load_stable_capability(db, "user-1", "amex")
        assert loaded is not None
        assert loaded.state == CapabilityState.EXTRACTION_SUCCESS
        assert customer_visible_same(stable, loaded)
        assert loaded.is_refreshing is False

    def test_build_presented_persists_only_terminal(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        ensure_customer_capability_presentation_tables(db)

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
        assert mid.state == CapabilityState.SIGNED_OUT
        # Stored payload still the prior stable signed-out card.
        stored = load_stable_capability(db, "user-1", "amex")
        assert stored is not None
        assert stored.state == CapabilityState.SIGNED_OUT
        assert stored.is_refreshing is False
