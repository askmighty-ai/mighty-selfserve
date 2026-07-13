"""Regression tests for Truth Dashboard CapabilityState (PR #97)."""

from __future__ import annotations

import html

from mighty.account_readiness import (
    AccountReadiness,
    READY,
    CHECKING,
    SIGNED_OUT,
    UNVERIFIED,
)
from mighty.account_status import AccountStatus, build_account_status
from mighty.account_lifecycle import resolve_account_lifecycle
from mighty.capability_state import (
    CUSTOMER_VISIBLE_PROVIDERS,
    CapabilityState,
    TRUTH_PROVIDER,
    build_capability_view,
    filter_customer_accounts,
    resolve_capability_state,
)
from mighty.customer_account_access import (
    DISCOVERED_GMAIL,
    DISCOVERED_MANUAL,
    LIVE_CONNECTED,
    LIVE_SIGNED_OUT,
    LIVE_UNKNOWN,
    build_customer_account_access_view,
)
from mighty.home_state import resolve_home_state
from mighty.home_ui import render_home_page
from mighty.login_truth import CurrentAccountAccess
from mighty.provider_account import (
    EXTRACTION_COMPLETE,
    EXTRACTION_FAILED,
    EXTRACTION_NOT_STARTED,
    EXTRACTION_PENDING,
    ProviderAccount,
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


AMEX_FIELDS = [
    {"label": "Membership Rewards", "value": "125,000", "key": "points_balance"},
    {"label": "Card ending", "value": "1005", "key": "card_ending"},
    {"label": "Statement balance", "value": "$1,234.56", "key": "statement_balance"},
]


class TestPrecedence:
    def test_1_authenticated_successful_nonempty_extraction(self):
        view = _view(READY)
        cap = build_capability_view(view, extracted_items=AMEX_FIELDS)
        assert cap.state == CapabilityState.EXTRACTION_SUCCESS
        assert any(f.label == "Membership Rewards" for f in cap.extracted_fields)
        assert "Authenticated session confirmed" in [e.text for e in cap.evidence]

    def test_2_authenticated_extraction_failed(self):
        view = _view(UNVERIFIED, session_state="connected", extraction_status=EXTRACTION_FAILED)
        cap = build_capability_view(view, extraction_status=EXTRACTION_FAILED)
        assert cap.state == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED
        assert cap.state != CapabilityState.SIGNED_OUT
        assert any("Extraction failed" in e.text for e in cap.evidence)

    def test_3_authenticated_no_private_data_observed(self):
        view = _view(UNVERIFIED, session_state="connected")
        cap = build_capability_view(
            view,
            extraction_status=EXTRACTION_COMPLETE,
            extracted_items=[],
        )
        assert cap.state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
        assert any("No qualifying private account data" in e.text for e in cap.evidence)

    def test_4_inconclusive_login_plus_cached_data(self):
        view = _view(
            UNVERIFIED,
            session_state="unknown",
            cached_data_label="Last saved data: 2 hours ago",
        )
        # Force saved_data_only private posture via extraction_ok uncorrelated path
        readiness = _readiness(
            "amex",
            UNVERIFIED,
            session_state="unknown",
            extraction_ok=True,
            extraction_correlated=False,
            cached_data_label="Last saved data: 2 hours ago",
        )
        view = build_customer_account_access_view(
            provider="amex",
            display_name="American Express",
            readiness=readiness,
            discovered_from=DISCOVERED_MANUAL,
        )
        assert view.private_data_state == "saved_data_only"
        assert view.live_access == LIVE_UNKNOWN
        cap = build_capability_view(view, extracted_items=AMEX_FIELDS)
        assert cap.state == CapabilityState.LOGIN_UNKNOWN
        # Cached fields must not produce extraction_success
        assert not cap.extracted_fields

    def test_5_definitive_signed_out_plus_cached_data(self):
        view = _view(
            SIGNED_OUT,
            cached_data_label="Last saved data: 2 hours ago",
        )
        cap = build_capability_view(view, extracted_items=AMEX_FIELDS)
        assert cap.state == CapabilityState.SIGNED_OUT
        assert cap.action_required is True
        assert cap.action_label == "Open American Express"
        assert not cap.extracted_fields

    def test_6_gmail_discovery_only(self):
        view = _view(UNVERIFIED, session_state="unknown", discovered_from=DISCOVERED_GMAIL)
        cap = build_capability_view(view)
        assert cap.state == CapabilityState.LOGIN_UNKNOWN

    def test_7_credential_only_no_view(self):
        cap = build_capability_view(None)
        assert cap.state == CapabilityState.LOGIN_UNKNOWN
        assert cap.action_required is False

    def test_8_empty_placeholder_fields_not_success(self):
        view = _view(READY)
        cap = build_capability_view(
            view,
            extracted_items=[
                {"label": "Membership Rewards", "value": "—"},
                {"label": "Balance", "value": "n/a"},
                {"label": "Empty", "value": ""},
            ],
        )
        assert cap.state != CapabilityState.EXTRACTION_SUCCESS
        assert cap.state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA

    def test_9_extraction_failure_not_signed_out(self):
        assert resolve_capability_state(
            readiness="unverified",
            live_access=LIVE_CONNECTED,
            session_state="connected",
            private_data_state="extraction_failed",
            extraction_status=EXTRACTION_FAILED,
        ) == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED

    def test_10_timeout_not_signed_out(self):
        view = _view(
            CHECKING,
            session_state="checking",
            verification_lifecycle="timed_out",
        )
        cap = build_capability_view(view, extraction_status=EXTRACTION_PENDING)
        assert cap.state == CapabilityState.LOGIN_UNKNOWN
        assert any("timed out" in e.text.lower() for e in cap.evidence)

    def test_11_background_verification_preserves_success(self):
        view = _view(
            READY,
            session_state="checking",
            background_verification=True,
            verification_lifecycle="running",
        )
        cap = build_capability_view(view, extracted_items=AMEX_FIELDS)
        assert cap.state == CapabilityState.EXTRACTION_SUCCESS
        assert view.background_verification is True

    def test_12_dashboard_and_api_capability_agree(self):
        view = _view(READY)
        items = AMEX_FIELDS
        dash = build_capability_view(view, extracted_items=items)
        api_status = AccountStatus(
            source="amex",
            display_name="American Express",
            status="up_to_date",
            presentation_key="ready",
            presentation_label="Connected",
            last_successful_sync_at=view.last_confirmed_at,
            current_attempt_at=None,
            last_error=None,
            user_action_label=None,
            user_action_url=None,
            customer_access=view,
            capability=dash,
        )
        payload = api_status.to_dict()
        assert payload["capability_state"] == dash.state.value
        assert payload["capability"]["headline"] == dash.headline
        assert payload["capability"]["extracted_fields"][0]["label"] == "Membership Rewards"

        home = resolve_home_state(
            accounts=[api_status],
            extracted_items=items,
            session_confidence="high",
        )
        assert home.capability is not None
        assert home.capability.state == dash.state
        rendered = render_home_page(
            home, first_name="Alex", today_label="Mon", escape=_escape,
        )
        assert 'data-capability="extraction_success"' in rendered
        assert "Membership Rewards" in rendered

    def test_13_only_amex_on_customer_surfaces(self):
        assert CUSTOMER_VISIBLE_PROVIDERS == frozenset({TRUTH_PROVIDER})
        accounts = [
            AccountStatus(
                source="amex", display_name="American Express", status="up_to_date",
                presentation_key="ready", presentation_label="Ready",
                last_successful_sync_at=None, current_attempt_at=None,
                last_error=None, user_action_label=None, user_action_url=None,
            ),
            AccountStatus(
                source="delta", display_name="Delta", status="up_to_date",
                presentation_key="ready", presentation_label="Ready",
                last_successful_sync_at=None, current_attempt_at=None,
                last_error=None, user_action_label=None, user_action_url=None,
            ),
        ]
        assert [a.source for a in filter_customer_accounts(accounts)] == ["amex"]

    def test_14_all_providers_remain_in_supported_sites(self):
        # Backend registry still has multiple providers; customer filter is separate.
        import app as app_module
        keys = {k for k, *_ in app_module.SUPPORTED_SITES}
        assert "amex" in keys
        assert "delta" in keys
        assert "hilton" in keys
        assert keys - CUSTOMER_VISIBLE_PROVIDERS  # non-empty remainder

    def test_15_legacy_sync_connection_cannot_decide_capability(self):
        # resolve_capability_state has no sync_status / connection_status params.
        import inspect
        params = set(inspect.signature(resolve_capability_state).parameters)
        assert "sync_status" not in params
        assert "connection_status" not in params

        # Gmail + legacy connected sync still cannot invent EXTRACTION_SUCCESS without readiness.
        lc = resolve_account_lifecycle(
            "amex", in_credentials=True, from_email=True,
            account=ProviderAccount(source="amex", sync_status="ok"),
        )
        status = build_account_status(
            "amex",
            "American Express",
            lc,
            ProviderAccount(
                source="amex",
                sync_status="ok",
                connection_status="connected",
                extraction_status=EXTRACTION_COMPLETE,
                normalized_fields=AMEX_FIELDS,
            ),
            sync_status="ok",
            updating_source=None,
            connection_status="connected",
            session_access=CurrentAccountAccess(
                provider="amex",
                current_access="unknown",
                cached_data_state="fresh",
                last_verified=None,
                last_private_data="2026-07-01T00:00:00+00:00",
                evidence="test",
                source="test",
                next_action_type="none",
                next_action_text="",
            ),
        )
        assert status.capability is not None
        assert status.capability.state != CapabilityState.EXTRACTION_SUCCESS
        assert status.capability.state == CapabilityState.LOGIN_UNKNOWN


class TestEvidenceAndPipeline:
    def test_pipeline_has_five_stages_with_verdicts(self):
        view = _view(READY)
        cap = build_capability_view(
            view,
            extracted_items=AMEX_FIELDS,
            verification_id="ver-99",
        )
        assert [s.name for s in cap.pipeline] == [
            "Session Evidence",
            "Verification",
            "Observation",
            "Extraction",
            "Snapshot",
        ]
        assert all(s.verdict in ("PASS", "FAIL", "UNKNOWN", "NOT_RUN") for s in cap.pipeline)
        assert any(
            s.id_label and "access_cycle_id" in s.id_label
            for s in cap.pipeline
        )
        assert any(
            s.id_label and "verification_id" in s.id_label
            for s in cap.pipeline
        )

    def test_no_secrets_in_evidence(self):
        view = _view(SIGNED_OUT)
        cap = build_capability_view(view)
        blob = " ".join(e.text for e in cap.evidence).lower()
        assert "cookie" not in blob
        assert "token" not in blob
        assert "password" not in blob


class TestUiStates:
    def test_empty_is_login_unknown_not_signed_out(self):
        result = resolve_home_state(accounts=[])
        assert result.capability.state == CapabilityState.LOGIN_UNKNOWN
        rendered = render_home_page(
            result, first_name="Alex", today_label="Mon", escape=_escape,
        )
        assert "cannot determine whether you are logged in" in rendered.lower()
        assert "Open American Express" not in rendered
        assert "Summary" not in rendered
        assert "System Health" not in rendered

    def test_signed_out_ui(self):
        view = _view(SIGNED_OUT)
        status = AccountStatus(
            source="amex", display_name="American Express", status="needs_login",
            presentation_key="needs_sign_in", presentation_label="Sign in",
            last_successful_sync_at=None, current_attempt_at=None, last_error=None,
            user_action_label="Sign in",
            user_action_url="https://www.americanexpress.com/en-us/account/login",
            customer_access=view,
            capability=build_capability_view(view),
        )
        result = resolve_home_state(accounts=[status])
        rendered = render_home_page(
            result, first_name="Alex", today_label="Mon", escape=_escape,
        )
        assert "You are signed out" in rendered
        assert "Open American Express" in rendered
        assert "Technical Details" in rendered


class TestCurrentCycleVsHistoricalPipeline:
    """PR #99: historical data must not create current-cycle pipeline PASS."""

    def test_authenticated_private_data_extraction_success_snapshot_pass(self):
        """1. Auth + private data + extraction success → EXTRACTION_SUCCESS + snapshot PASS."""
        view = _view(READY, session_state="connected")
        cap = build_capability_view(view, extracted_items=AMEX_FIELDS)
        assert cap.state == CapabilityState.EXTRACTION_SUCCESS
        by_name = {s.name: s for s in cap.pipeline}
        assert by_name["Observation"].verdict == "PASS"
        assert by_name["Extraction"].verdict == "PASS"
        assert by_name["Extraction"].detail == "complete"
        assert by_name["Snapshot"].verdict == "PASS"
        assert "current-cycle" in (by_name["Snapshot"].detail or "")

    def test_authenticated_private_data_parser_fail_no_snapshot(self):
        """2. Auth + private observed + parser fail → LOGIN_VISIBLE_EXTRACTION_FAILED."""
        view = _view(
            UNVERIFIED,
            session_state="connected",
            verification_lifecycle="failed",
        )
        cap = build_capability_view(view, extraction_status=EXTRACTION_FAILED)
        assert cap.state == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED
        by_name = {s.name: s for s in cap.pipeline}
        assert by_name["Extraction"].verdict == "FAIL"
        assert by_name["Snapshot"].verdict == "FAIL"
        assert by_name["Snapshot"].verdict != "PASS"

    def test_authenticated_no_qualifying_private_data_extraction_not_run(self):
        """3. Auth + no qualifying private data → LOGGED_IN_NO_ACCOUNT_DATA, extraction NOT RUN."""
        view = _view(
            UNVERIFIED,
            session_state="connected",
            verification_lifecycle="completed",
        )
        cap = build_capability_view(
            view,
            extraction_status=EXTRACTION_NOT_STARTED,
            extracted_items=[],
        )
        assert cap.state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
        by_name = {s.name: s for s in cap.pipeline}
        assert by_name["Observation"].verdict == "FAIL"
        assert by_name["Extraction"].verdict == "NOT_RUN"
        assert by_name["Extraction"].detail == "NOT RUN"
        assert by_name["Snapshot"].verdict == "NOT_RUN"

    def test_historical_account_data_does_not_pass_current_cycle_snapshot(self):
        """4–5. Historical fields/snapshot cannot create current-cycle Snapshot PASS."""
        view = _view(
            UNVERIFIED,
            session_state="connected",
            extraction_ok=True,
            extraction_correlated=False,
            cached_data_label="Last saved data: 1 hour ago",
            last_confirmed_access_cycle_id="cycle-old",
            access_cycle_id="cycle-new",
            verification_lifecycle="completed",
        )
        assert view.private_data_state == "saved_data_only"
        cap = build_capability_view(
            view,
            extracted_items=AMEX_FIELDS,
            extraction_status=EXTRACTION_COMPLETE,
            verification_id="cycle-new",
        )
        assert cap.state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
        by_name = {s.name: s for s in cap.pipeline}
        assert by_name["Observation"].verdict == "FAIL"
        assert by_name["Extraction"].verdict == "NOT_RUN"
        assert by_name["Extraction"].detail != "complete"
        assert by_name["Snapshot"].verdict == "NOT_RUN"
        assert "Previous data available" in (by_name["Snapshot"].detail or "")
        assert by_name["Snapshot"].verdict != "PASS"

    def test_signed_out_unchanged(self):
        """8. Signed-out behavior remains unchanged."""
        view = _view(SIGNED_OUT)
        cap = build_capability_view(view)
        assert cap.state == CapabilityState.SIGNED_OUT

    def test_inconclusive_remains_login_unknown(self):
        """9. Inconclusive verification remains LOGIN_UNKNOWN, not signed out."""
        view = _view(UNVERIFIED, session_state="unknown")
        cap = build_capability_view(view)
        assert cap.state == CapabilityState.LOGIN_UNKNOWN
        assert cap.state != CapabilityState.SIGNED_OUT
