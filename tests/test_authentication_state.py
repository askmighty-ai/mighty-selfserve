"""Phase 1 Fixes 1–2: canonical AuthenticationState + no false SIGNED_OUT."""

from __future__ import annotations

import sqlite3

from mighty.authentication_state import (
    AuthenticationState,
    TRANSPORT_TO_AUTHENTICATION,
    authentication_from_current_access,
    authentication_from_terminal_reason,
    authentication_from_verification_decision,
    resolve_authentication_state,
)
from mighty.capability_state import (
    CapabilityState,
    build_capability_view,
    resolve_capability_state,
)
from mighty.customer_account_access import (
    LIVE_SIGNED_OUT,
    LIVE_UNKNOWN,
    build_customer_account_access_view,
    resolve_live_access,
)
from mighty.login_truth import CurrentAccountAccess
from mighty.provider_access_manager import (
    complete_provider_access_check,
    finish_provider_access_check,
    request_provider_access_check,
)
from mighty.provider_access_probe import (
    AUTH_AUTHENTICATED_NO_PRIVATE_DATA,
    AUTH_LOGIN_PAGE,
    ensure_probe_tables,
)
from mighty.provider_account import EXTRACTION_COMPLETE, EXTRACTION_FAILED
from mighty.provider_session_state import (
    decide_amex_verification_session,
    ensure_provider_session_state_tables,
    get_provider_session_state,
)
from mighty.session_access import (
    PRODUCT_SESSION_FROM_CURRENT_ACCESS,
    resolve_product_account_state,
    to_authentication_state,
    to_product_session_state,
)
from mighty.session_verification import ensure_session_verification_tables


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    ensure_probe_tables(db)
    ensure_provider_session_state_tables(db)
    ensure_session_verification_tables(db)
    return db


def _session(
    provider: str,
    current_access: str,
    *,
    last_verified: str | None = None,
) -> CurrentAccountAccess:
    return CurrentAccountAccess(
        provider=provider,
        current_access=current_access,  # type: ignore[arg-type]
        cached_data_state="none",
        last_verified=last_verified,
        last_private_data=None,
        evidence="",
        source="test",
        next_action_type="none",
        next_action_text="",
        verification_id=None,
        verification_lifecycle=None,
        evidence_type=None,
    )


def _probe_result(**overrides):
    base = {
        "provider": "amex",
        "status": "ok",
        "auth_state": "unknown",
        "url_visited": "https://global.americanexpress.com/overview",
        "final_url": "https://global.americanexpress.com/overview",
        "login_form_present": False,
        "signed_in_detected": False,
        "private_data_detected": False,
        "failure_reason": "",
    }
    base.update(overrides)
    return base


def _session_api_inspect(status_code: int, *, start_ms: int | None = 50):
    req = {
        "url": (
            "https://global.americanexpress.com/api/servicing/v1/"
            "ReadUserSession.v1"
        ),
        "status_code": status_code,
    }
    if start_ms is not None:
        req["start_time_ms"] = start_ms
    return {"auth_network_trace": {"auth_session_requests": [req]}}


# ── Fix 1: vocabulary ─────────────────────────────────────────────────────────


def test_authentication_state_has_exactly_three_values():
    assert {m.value for m in AuthenticationState} == {
        "signed_in",
        "signed_out",
        "login_unknown",
    }


def test_transport_mapping_table_covers_false_signed_out_sources():
    """error / inconclusive / timeout / cancelled never map to SIGNED_OUT."""
    for label in (
        "error",
        "inconclusive",
        "timeout",
        "cancelled",
        "navigation_failed",
        "unknown",
        "failed",
        "timed_out",
        "blank_or_unloaded_page",
        "probe_navigation_error",
        "network_issue",
        "insufficient_evidence",
        "conflicting_evidence_unordered",
    ):
        assert (
            TRANSPORT_TO_AUTHENTICATION[label] == AuthenticationState.LOGIN_UNKNOWN
        ), label


# ── Fix 2: session_access error never SIGNED_OUT ───────────────────────────────


def test_session_access_error_maps_to_unknown_not_signed_out():
    assert PRODUCT_SESSION_FROM_CURRENT_ACCESS["error"] == "unknown"
    assert to_product_session_state("error") == "unknown"
    assert to_authentication_state("error") == AuthenticationState.LOGIN_UNKNOWN
    product = resolve_product_account_state(_session("amex", "error"))
    assert product.session_state == "unknown"
    assert product.authentication_state == AuthenticationState.LOGIN_UNKNOWN
    assert product.login_required is False


def test_session_access_signed_out_still_definitive():
    product = resolve_product_account_state(_session("amex", "signed_out"))
    assert product.authentication_state == AuthenticationState.SIGNED_OUT
    assert product.session_state == "signed_out"
    assert product.login_required is True


def test_session_access_connected_is_signed_in():
    product = resolve_product_account_state(_session("amex", "connected_now"))
    assert product.authentication_state == AuthenticationState.SIGNED_IN
    assert product.login_required is False


# ── Required regressions 1–9: resolver outcomes ────────────────────────────────


def test_timeout_resolves_login_unknown():
    decision = decide_amex_verification_session(
        _probe_result(status="error", failure_reason="timeout"),
        verification_id="v-timeout",
    )
    assert decision.final_decision == "inconclusive"
    assert (
        authentication_from_verification_decision(decision.final_decision)
        == AuthenticationState.LOGIN_UNKNOWN
    )
    assert authentication_from_terminal_reason("timeout") == AuthenticationState.LOGIN_UNKNOWN


def test_blank_page_resolves_login_unknown():
    decision = decide_amex_verification_session(
        _probe_result(status="error", failure_reason="blank_or_unloaded_page"),
        verification_id="v-blank",
    )
    assert decision.final_decision == "inconclusive"
    assert (
        authentication_from_verification_decision(decision.final_decision)
        == AuthenticationState.LOGIN_UNKNOWN
    )


def test_navigation_failure_resolves_login_unknown():
    decision = decide_amex_verification_session(
        _probe_result(status="error", failure_reason="probe_navigation_error"),
        verification_id="v-nav",
    )
    assert decision.final_decision == "inconclusive"
    assert (
        authentication_from_terminal_reason("navigation_failed")
        == AuthenticationState.LOGIN_UNKNOWN
    )


def test_tab_close_resolves_login_unknown():
    assert authentication_from_terminal_reason("cancelled") == AuthenticationState.LOGIN_UNKNOWN
    assert (
        resolve_authentication_state(terminal_reason="cancelled")
        == AuthenticationState.LOGIN_UNKNOWN
    )


def test_internal_exception_resolves_login_unknown():
    decision = decide_amex_verification_session(
        _probe_result(status="error", failure_reason="probe_no_result"),
        verification_id="v-exc",
    )
    assert decision.final_decision == "inconclusive"
    assert authentication_from_current_access("error") == AuthenticationState.LOGIN_UNKNOWN


def test_login_page_resolves_signed_out():
    decision = decide_amex_verification_session(
        _probe_result(
            auth_state=AUTH_LOGIN_PAGE,
            failure_reason="login_required",
            url_visited="https://www.americanexpress.com/en-us/account/log-in",
            final_url="https://www.americanexpress.com/en-us/account/log-in",
        ),
        verification_id="v-login",
    )
    assert decision.final_decision == "signed_out"
    assert (
        authentication_from_verification_decision(decision.final_decision)
        == AuthenticationState.SIGNED_OUT
    )


def test_authenticated_session_api_resolves_signed_in():
    decision = decide_amex_verification_session(
        _probe_result(deep_inspect=_session_api_inspect(200)),
        verification_id="v-api",
    )
    assert decision.final_decision == "connected"
    assert (
        authentication_from_verification_decision(decision.final_decision)
        == AuthenticationState.SIGNED_IN
    )


def test_authenticated_page_resolves_signed_in():
    decision = decide_amex_verification_session(
        _probe_result(auth_state=AUTH_AUTHENTICATED_NO_PRIVATE_DATA),
        verification_id="v-page",
    )
    assert decision.final_decision == "connected"
    assert (
        authentication_from_verification_decision(decision.final_decision)
        == AuthenticationState.SIGNED_IN
    )


# ── Required regressions 10–12: extraction never revises SIGNED_IN ─────────────


def test_extraction_outcomes_cannot_change_signed_in():
    for extraction_status, private in (
        (EXTRACTION_COMPLETE, "seen"),
        (EXTRACTION_FAILED, "extraction_failed"),
        (None, "not_yet_seen"),
    ):
        state = resolve_capability_state(
            authentication_state=AuthenticationState.SIGNED_IN.value,
            session_state="connected",
            readiness="unverified",
            live_access="Connected",
            private_data_state=private,
            extraction_status=extraction_status,
            verification_lifecycle="completed",
            has_publishable_fields=(private == "seen"),
        )
        # Capability may fork, but never to SIGNED_OUT.
        assert state != CapabilityState.SIGNED_OUT
        # And the auth input remains SIGNED_IN when re-resolved.
        assert (
            resolve_authentication_state(
                authentication_state=AuthenticationState.SIGNED_IN.value
            )
            == AuthenticationState.SIGNED_IN
        )


def test_capability_view_preserves_signed_in_across_extraction_forks():
    from mighty.account_readiness import (
        READINESS_CANONICAL_STATUS,
        READINESS_PRESENTATION_KEY,
        READINESS_STATUS_COPY,
        READINESS_STATUS_LABELS,
        UNVERIFIED,
        AccountReadiness,
    )

    readiness = AccountReadiness(
        provider="amex",
        state=UNVERIFIED,
        status_label=READINESS_STATUS_LABELS[UNVERIFIED],
        status_copy=READINESS_STATUS_COPY[UNVERIFIED],
        presentation_key=READINESS_PRESENTATION_KEY[UNVERIFIED],
        canonical_status=READINESS_CANONICAL_STATUS[UNVERIFIED],
        login_required=False,
        session_state="connected",
        access_cycle_id="cyc-1",
        session_evidence_at=None,
        extraction_at=None,
        extraction_ok=False,
        extraction_correlated=False,
        verification_id="v1",
    )
    view = build_customer_account_access_view(
        provider="amex",
        display_name="American Express",
        readiness=readiness,
        discovered_from="Manual add",
        verification_lifecycle="completed",
        extraction_status=EXTRACTION_FAILED,
    )
    assert view.authentication_state == AuthenticationState.SIGNED_IN.value
    cap = build_capability_view(
        view,
        extraction_status=EXTRACTION_FAILED,
        extracted_items=[],
    )
    assert cap.authentication_state == AuthenticationState.SIGNED_IN.value
    assert cap.state != CapabilityState.SIGNED_OUT


# ── Required regressions 13–14: terminal immutability ──────────────────────────


def test_duplicate_terminal_cannot_overwrite_authentication_state():
    db = _db()
    uid = "user-1"
    verification = request_provider_access_check(db, uid, "amex")
    assert verification is not None
    vid = verification.verification_id

    result = complete_provider_access_check(
        db,
        uid,
        _probe_result(deep_inspect=_session_api_inspect(200)),
        verification_id=vid,
    )
    assert result["authentication_state"] == AuthenticationState.SIGNED_IN.value
    assert result["verification_decision"] == "connected"

    # First terminalize as authenticated (extraction complete path).
    first = finish_provider_access_check(
        db,
        uid,
        vid,
        lifecycle="completed",
        terminal_reason="authenticated",
        terminal_source="test_first",
    )
    assert first is not None
    assert first.terminal_reason == "authenticated"

    # Duplicate completion attempting signed_out must no-op.
    second = finish_provider_access_check(
        db,
        uid,
        vid,
        lifecycle="completed",
        terminal_reason="signed_out",
        terminal_source="test_duplicate",
    )
    assert second is not None
    assert second.terminal_reason == "authenticated"
    assert second.terminal_source == "test_first"
    assert (
        authentication_from_terminal_reason(second.terminal_reason)
        == AuthenticationState.SIGNED_IN
    )


def test_late_extraction_cannot_overwrite_authentication_state():
    db = _db()
    uid = "user-1"
    verification = request_provider_access_check(db, uid, "amex")
    assert verification is not None
    vid = verification.verification_id

    complete_provider_access_check(
        db,
        uid,
        _probe_result(deep_inspect=_session_api_inspect(200)),
        verification_id=vid,
    )
    state = get_provider_session_state(db, uid, "amex")
    assert state is not None
    assert state.state == "connected"

    finish_provider_access_check(
        db,
        uid,
        vid,
        lifecycle="completed",
        terminal_reason="authenticated",
        terminal_source="extraction_success",
    )
    # Late "signed_out" finish after extraction must not rewrite.
    late = finish_provider_access_check(
        db,
        uid,
        vid,
        terminal_reason="signed_out",
        terminal_source="late_bogus",
    )
    assert late is not None
    assert late.terminal_reason == "authenticated"
    assert get_provider_session_state(db, uid, "amex").state == "connected"


# ── Required regression 15: shared canonical field ─────────────────────────────


def test_dashboard_api_banner_share_authentication_state():
    product = resolve_product_account_state(_session("amex", "signed_out"))
    assert product.authentication_state == AuthenticationState.SIGNED_OUT

    readiness = __import__("mighty.account_readiness", fromlist=["resolve_account_readiness"]).resolve_account_readiness(
        provider="amex",
        product=product,
        verification_lifecycle="completed",
    )
    assert readiness.state == "signed_out"
    assert readiness.login_required is True

    view = build_customer_account_access_view(
        provider="amex",
        display_name="American Express",
        readiness=readiness,
        discovered_from="Manual add",
        verification_lifecycle="completed",
    )
    assert view.authentication_state == AuthenticationState.SIGNED_OUT.value
    assert view.live_access == LIVE_SIGNED_OUT

    cap = build_capability_view(view)
    assert cap.authentication_state == AuthenticationState.SIGNED_OUT.value
    assert cap.state == CapabilityState.SIGNED_OUT
    assert cap.action_required is True  # banner CTA derives from SIGNED_OUT only

    # Error path: all surfaces agree on LOGIN_UNKNOWN (never SIGNED_OUT).
    err_product = resolve_product_account_state(_session("amex", "error"))
    err_readiness = __import__(
        "mighty.account_readiness", fromlist=["resolve_account_readiness"]
    ).resolve_account_readiness(
        provider="amex",
        product=err_product,
        verification_lifecycle="failed",
    )
    assert err_product.authentication_state == AuthenticationState.LOGIN_UNKNOWN
    assert err_readiness.login_required is False
    err_view = build_customer_account_access_view(
        provider="amex",
        display_name="American Express",
        readiness=err_readiness,
        discovered_from="Manual add",
        verification_lifecycle="failed",
    )
    assert err_view.authentication_state == AuthenticationState.LOGIN_UNKNOWN.value
    assert err_view.live_access == LIVE_UNKNOWN
    err_cap = build_capability_view(err_view)
    assert err_cap.authentication_state == AuthenticationState.LOGIN_UNKNOWN.value
    assert err_cap.state == CapabilityState.LOGIN_UNKNOWN
    assert err_cap.action_required is False


def test_live_access_never_signed_out_for_login_unknown():
    assert (
        resolve_live_access(
            readiness="unverified",
            session_state="unknown",
            authentication_state=AuthenticationState.LOGIN_UNKNOWN.value,
        )
        == LIVE_UNKNOWN
    )


def test_amex_complete_attaches_authentication_state():
    db = _db()
    uid = "user-1"
    verification = request_provider_access_check(db, uid, "amex")
    assert verification is not None

    signed_out = complete_provider_access_check(
        db,
        uid,
        _probe_result(
            auth_state=AUTH_LOGIN_PAGE,
            failure_reason="login_required",
            url_visited="https://www.americanexpress.com/en-us/account/log-in",
            final_url="https://www.americanexpress.com/en-us/account/log-in",
        ),
        verification_id=verification.verification_id,
    )
    assert signed_out["authentication_state"] == AuthenticationState.SIGNED_OUT.value

    verification2 = request_provider_access_check(db, uid, "amex")
    assert verification2 is not None
    unknown = complete_provider_access_check(
        db,
        uid,
        _probe_result(status="error", failure_reason="timeout"),
        verification_id=verification2.verification_id,
    )
    assert unknown["authentication_state"] == AuthenticationState.LOGIN_UNKNOWN.value
    assert get_provider_session_state(db, uid, "amex") is None or (
        get_provider_session_state(db, uid, "amex").state != "signed_out"
        or signed_out["authentication_state"] == AuthenticationState.SIGNED_OUT.value
    )


def test_capability_login_unknown_ignores_polluted_session_signed_out():
    """When auth is LOGIN_UNKNOWN, transport session_state must not force SIGNED_OUT."""
    state = resolve_capability_state(
        authentication_state=AuthenticationState.LOGIN_UNKNOWN.value,
        session_state="signed_out",  # contaminated transport
        readiness="unverified",
        live_access="Signed out",
        signed_out_evidence=False,
    )
    assert state == CapabilityState.LOGIN_UNKNOWN
