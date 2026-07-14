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
    FIRST_EVER_CHECKING_HEADLINE,
    REFRESHING_STATUS_HEADLINE,
    PresentationOrderMeta,
    build_presented_capability_view,
    clear_stable_capability,
    customer_visible_same,
    ensure_customer_capability_presentation_tables,
    fingerprint_account_identity,
    is_newer_presentation,
    is_result_within_customer_truth_freshness,
    load_stable_capability,
    load_stable_order_meta,
    present_customer_capability,
    save_stable_capability,
)
from mighty.session_verification import READY_RESULT_GRACE_SECONDS
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
    assert "cannot determine" not in (presented.primary_headline or "").lower()
    assert "you are signed out" not in (presented.primary_headline or "").lower()
    assert "you are logged in" not in (presented.primary_headline or "").lower()
    if previous_state is not None:
        assert presented.previous_capability_state == previous_state.value
        assert presented.status_is_historical is True
        assert presented.historical_summary
    else:
        assert presented.previous_capability_state is None
        assert presented.status_is_historical is False


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
        assert presented.historical_summary == "Last confirmed: Signed out"
        assert "You are signed out" not in presented.headline

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
        assert "could access and extract" in presented.historical_summary.lower()
        assert not presented.extracted_fields

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
        assert presented.historical_summary == "Previous check was inconclusive"
        assert "cannot determine" not in presented.headline.lower()
        assert "could not determine" not in presented.headline.lower()

    def test_active_to_signed_out_terminal(self):
        previous = _stable(SIGNED_OUT)
        mid = present_customer_capability(
            build_capability_view(_inflight_checking()),
            previous_stable=previous,
            access_view=_inflight_checking(),
        )
        assert mid.presentation_phase == "determining"
        done_view = _view(
            SIGNED_OUT,
            verification_lifecycle="completed",
            last_confirmed_ready_at="2026-07-14T15:10:00+00:00",
        )
        final = present_customer_capability(
            build_capability_view(done_view),
            previous_stable=mid,
            access_view=done_view,
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
        done_view = _view(
            READY,
            verification_lifecycle="completed",
            last_confirmed_ready_at="2026-07-14T17:00:00+00:00",
        )
        final = present_customer_capability(
            build_capability_view(done_view, extracted_items=AMEX_FIELDS),
            previous_stable=mid,
            access_view=done_view,
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
        assert "Last confirmed: Signed out" in rendered
        assert "Confirmed" in rendered
        assert 'datetime="2026-07-13T04:48:00Z"' in rendered
        assert "Last verified:" not in rendered
        assert "You are signed out" not in rendered

    def test_current_terminal_uses_current_completion_time(self):
        previous = replace(
            _stable(SIGNED_OUT),
            last_verified="2026-07-13T04:48:00Z",
        )
        done_view = _view(
            SIGNED_OUT,
            verification_lifecycle="completed",
            last_confirmed_ready_at="2026-07-14T15:22:00+00:00",
        )
        final = present_customer_capability(
            build_capability_view(done_view),
            previous_stable=previous,
            access_view=done_view,
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
        prev_descs = {e.description for e in previous.truth_validation.timeline}
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
            "Determining login state",
        ]
        # Current-check truth timeline must not carry prior terminal capability event.
        assert held.truth_validation is not None
        current_descs = {e.description for e in held.truth_validation.timeline}
        assert "Determining login state" in current_descs
        assert not any(d.startswith("Capability ·") for d in current_descs)
        # Previous section preserves prior cycle events separately.
        previous_section = next(
            s for s in held.timeline_sections if s.label == "Previous completed check"
        )
        assert {e.description for e in previous_section.events} == prev_descs

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
        assert 'data-status-historical="1"' in rendered
        assert DETERMINING_HEADLINE_CURRENT in rendered
        assert "Last confirmed: Signed out" in rendered
        assert "cannot determine" not in rendered.lower()
        assert "You are signed out" not in rendered

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
        assert determining.historical_summary == "Previous check was inconclusive"
        assert "cannot determine" not in determining.headline.lower()
        rendered_b = render_capability_panel(determining, escape=_escape)
        assert "Determining your current login state" in rendered_b
        assert "Previous check was inconclusive" in rendered_b
        assert "Mighty cannot determine" not in rendered_b

        # C. Current cycle terminals signed_out.
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
        assert determining2.historical_summary == "Last confirmed: Signed out"
        assert "You are signed out" not in determining2.headline

        # E. Current cycle terminals authenticated/no-data.
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
        )
        assert no_data.presentation_phase == "terminal"
        assert no_data.state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
        assert "cannot see your account information" in no_data.headline
        assert no_data.last_verified == "2026-07-14T15:12:00Z"


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
        assert held.status_is_historical is True

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
