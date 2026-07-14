"""Regression: customer Truth Dashboard holds stable truth during refresh (PR #102)."""

from __future__ import annotations

import html
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

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
    FIRST_EVER_CHECKING_HEADLINE,
    REFRESH_LABEL,
    REFRESH_LABEL_VERBOSE,
    PresentationOrderMeta,
    build_presented_capability_view,
    clear_stable_capability,
    customer_visible_same,
    ensure_customer_capability_presentation_tables,
    fingerprint_account_identity,
    is_newer_presentation,
    load_stable_capability,
    load_stable_order_meta,
    present_customer_capability,
    save_stable_capability,
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


class TestHoldPreviousDuringRefresh:
    def test_signed_out_to_signed_out_refresh(self):
        previous = _stable(SIGNED_OUT)
        live_view = _inflight_checking()
        live = build_capability_view(live_view)
        assert live.state == CapabilityState.LOGIN_UNKNOWN

        presented = present_customer_capability(
            live, previous_stable=previous, access_view=live_view,
        )
        assert presented.state == CapabilityState.SIGNED_OUT
        assert presented.is_refreshing is True
        assert presented.refresh_label == REFRESH_LABEL
        assert not any("in progress" in e.text.lower() for e in presented.evidence)

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

        done_view = _view(READY, verification_lifecycle="completed")
        done = build_capability_view(done_view, extracted_items=AMEX_FIELDS)
        final = present_customer_capability(
            done, previous_stable=previous, access_view=done_view,
        )
        assert final.is_refreshing is False
        assert customer_visible_same(previous, final)

    def test_logged_in_to_logged_in_no_account_data(self):
        previous = _stable(READY)
        live_view = _inflight_checking()
        held = present_customer_capability(
            build_capability_view(live_view, extracted_items=AMEX_FIELDS),
            previous_stable=previous,
            access_view=live_view,
        )
        assert held.state == CapabilityState.EXTRACTION_SUCCESS
        assert held.is_refreshing is True

        done_view = _view(
            UNVERIFIED,
            session_state="connected",
            verification_lifecycle="completed",
        )
        done = build_capability_view(done_view, extracted_items=[])
        final = present_customer_capability(
            done, previous_stable=held, access_view=done_view,
        )
        assert final.state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
        assert final.is_refreshing is False

    def test_logged_in_to_signed_out(self):
        previous = _stable(READY)
        mid = present_customer_capability(
            build_capability_view(_inflight_checking()),
            previous_stable=previous,
            access_view=_inflight_checking(),
        )
        assert mid.state == CapabilityState.EXTRACTION_SUCCESS

        done_view = _view(SIGNED_OUT, verification_lifecycle="completed")
        final = present_customer_capability(
            build_capability_view(done_view),
            previous_stable=mid,
            access_view=done_view,
        )
        assert final.state == CapabilityState.SIGNED_OUT
        assert final.action_required is True

    def test_signed_out_to_logged_in(self):
        previous = _stable(SIGNED_OUT)
        mid_view = _inflight_checking()
        mid = present_customer_capability(
            build_capability_view(mid_view),
            previous_stable=previous,
            access_view=mid_view,
        )
        assert mid.state == CapabilityState.SIGNED_OUT

        done_view = _view(READY, verification_lifecycle="completed")
        final = present_customer_capability(
            build_capability_view(done_view, extracted_items=AMEX_FIELDS),
            previous_stable=mid,
            access_view=done_view,
        )
        assert final.state == CapabilityState.EXTRACTION_SUCCESS

    def test_timeout_during_refresh(self):
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
        )
        final = present_customer_capability(
            build_capability_view(timeout_view, extraction_status=EXTRACTION_PENDING),
            previous_stable=mid,
            access_view=timeout_view,
        )
        assert final.state == CapabilityState.LOGIN_UNKNOWN
        assert final.is_refreshing is False
        assert any("timed out" in e.text.lower() for e in final.evidence)

    def test_refresh_crash(self):
        previous = _stable(READY)
        mid = present_customer_capability(
            build_capability_view(_inflight_checking(), extracted_items=AMEX_FIELDS),
            previous_stable=previous,
            access_view=_inflight_checking(),
        )
        fail_view = _view(
            UNVERIFIED,
            session_state="unknown",
            verification_lifecycle="failed",
        )
        final = present_customer_capability(
            build_capability_view(fail_view),
            previous_stable=mid,
            access_view=fail_view,
        )
        assert final.state == CapabilityState.LOGIN_UNKNOWN
        assert final.is_refreshing is False

    def test_swr_without_persisted_prior_keeps_success(self):
        """Readiness SWR success must not become first-ever checking."""
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
        assert presented.state == CapabilityState.EXTRACTION_SUCCESS
        assert presented.is_refreshing is True
        assert "cannot determine" not in presented.headline.lower()
        assert presented.headline != FIRST_EVER_CHECKING_HEADLINE

    def test_first_ever_timeout(self):
        timeout_view = _view(
            CHECKING,
            session_state="checking",
            verification_lifecycle="timed_out",
        )
        final = present_customer_capability(
            build_capability_view(timeout_view, extraction_status=EXTRACTION_PENDING),
            previous_stable=None,
            access_view=timeout_view,
        )
        assert final.state == CapabilityState.LOGIN_UNKNOWN
        assert final.is_refreshing is False
        assert any("timed out" in e.text.lower() for e in final.evidence)

    def test_never_flash_login_unknown_during_successful_refresh(self):
        previous = _stable(SIGNED_OUT)
        observed: list[CapabilityState] = []
        for lifecycle in ("requested", "running", "session_verified", "extracting"):
            mid_view = _inflight_checking(verification_lifecycle=lifecycle)
            presented = present_customer_capability(
                build_capability_view(mid_view),
                previous_stable=previous,
                access_view=mid_view,
            )
            observed.append(presented.state)
            assert presented.state == CapabilityState.SIGNED_OUT
            assert CapabilityState.LOGIN_UNKNOWN not in observed

    def test_force_unknown_bypasses_hold(self):
        previous = _stable(READY)
        live_view = _inflight_checking()
        forced = present_customer_capability(
            build_capability_view(live_view),
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
        mid_view = _inflight_checking()
        held = present_customer_capability(
            build_capability_view(mid_view, extracted_items=AMEX_FIELDS),
            previous_stable=previous,
            access_view=mid_view,
        )
        assert held.truth_validation is not None
        assert held.truth_validation.timeline == prev_timeline
        assert held.pipeline == previous.pipeline

        done_view = _view(SIGNED_OUT, verification_lifecycle="completed")
        final = present_customer_capability(
            build_capability_view(done_view),
            previous_stable=held,
            access_view=done_view,
        )
        assert final.truth_validation is not None
        assert final.truth_validation.timeline != prev_timeline

    def test_render_shows_refresh_not_login_unknown(self):
        previous = _stable(SIGNED_OUT)
        held = present_customer_capability(
            build_capability_view(_inflight_checking()),
            previous_stable=previous,
            access_view=_inflight_checking(),
        )
        rendered = render_capability_panel(held, escape=_escape)
        assert 'data-capability="signed_out"' in rendered
        assert 'data-refreshing="1"' in rendered
        assert REFRESH_LABEL in rendered
        assert "cannot determine" not in rendered.lower()


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
        stored = load_stable_capability(db, "u1", "amex")
        assert stored is not None
        assert stored.state == CapabilityState.EXTRACTION_SUCCESS

        # Normal request after debug still uses last real stable.
        held = build_presented_capability_view(
            _inflight_checking(),
            force_unknown=False,
            persist_db=db,
            persist_user_id="u1",
        )
        assert held.state == CapabilityState.EXTRACTION_SUCCESS
        assert held.is_refreshing is True

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
        assert dash.is_refreshing == api.is_refreshing
        assert dash.headline == api.headline
        assert dash.evidence == api.evidence
        assert dash.confidence == api.confidence
        assert dash.last_verified == api.last_verified
        assert dash.action_required == api.action_required
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
        assert home.capability.state == api.state == CapabilityState.EXTRACTION_SUCCESS
        assert home.capability.is_refreshing is True
        assert api.is_refreshing is True
        assert home.capability.headline == api.headline

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
        assert dash.is_refreshing is False

    def test_parity_first_verification(self):
        view = _inflight_checking()
        dash, api = self._compare_surfaces(view)
        assert dash.headline == FIRST_EVER_CHECKING_HEADLINE
        assert api.headline == FIRST_EVER_CHECKING_HEADLINE
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
        assert mid.state == CapabilityState.SIGNED_OUT
        stored = load_stable_capability(db, "user-1", "amex")
        assert stored is not None
        assert stored.state == CapabilityState.SIGNED_OUT
        assert stored.is_refreshing is False
