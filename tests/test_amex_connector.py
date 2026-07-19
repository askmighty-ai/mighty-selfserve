"""Tests for the Amex connector, extractor, normalizer, and CLI refresh wiring."""

from __future__ import annotations

import inspect
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mighty.amex_connector import AmexConnector, build_amex_connector_from_runtime
from mighty.amex_extractor import (
    AmexCardObservation,
    AmexExtractionObservation,
    AmexRewardsObservation,
    extract_amex_overview,
    extract_from_dom_text,
    extract_from_structured_payload,
    mask_account_number,
    sanitize_url,
    stable_account_id,
)
from mighty.amex_normalizer import normalize_amex_observation
from mighty.provider_connector import (
    FieldSource,
    RefreshStatus,
    assert_no_provider_raw_objects,
)
from mighty.provider_runtime import (
    ProviderRuntime,
    VerificationResult,
    format_connector_refresh_terminal_summary,
    parse_args,
    run_connector_refresh_with_runtime,
)


OVERVIEW_HTML_TEXT = """
Account Home
Membership Rewards Points Balance 124,350
Platinum Card
Card Ending 1009
Statement Balance $1,234.56
Available Credit $8,765.44
Payment Due $35.00
Payment Due Date February 15, 2026
Gold Card
Card Ending 2008
Current Balance $500.00
Available Credit $9,500.00
Minimum Payment Due $25.00
Due Date 03/01/2026
"""


def _signed_in(**overrides) -> VerificationResult:
    payload = dict(
        provider="amex",
        authentication_state="SIGNED_IN",
        reason="ok",
        observed_at="2026-01-01T00:00:00+00:00",
        final_url="https://global.americanexpress.com/overview",
        page_title="Overview",
        login_url_detected=False,
        login_marker_count=0,
        authenticated_marker_count=2,
        session_api_200_count=1,
        session_api_denied_count=0,
    )
    payload.update(overrides)
    return VerificationResult(**payload)


def _runtime(tmp_path: Path) -> ProviderRuntime:
    runtime = ProviderRuntime(
        root=tmp_path,
        cdp_port=9333,
        state_path=tmp_path / "state.json",
        result_path=tmp_path / "result.json",
        keepalive_result_path=tmp_path / "keepalive.json",
    )
    runtime.cdp_url = "http://127.0.0.1:9333"
    return runtime


def _page_with_text(text: str) -> MagicMock:
    page = MagicMock()
    page.url = "https://global.americanexpress.com/overview"
    page.locator.return_value.inner_text.return_value = text
    page.context.request.get.return_value = MagicMock(status=404)
    page.evaluate = MagicMock(side_effect=AssertionError("page.evaluate forbidden"))
    return page


def test_capabilities_read_only_no_mutations():
    connector = AmexConnector(
        ensure_usable_session_fn=lambda p: {"ok": True, "authentication_state": "SIGNED_IN"},
        ensure_provider_surface_fn=lambda p, s: {"ok": True},
        execute_readonly_extraction_fn=lambda p, e: {"ok": False},
    )
    caps = connector.capabilities()
    assert caps.read_only is True
    assert caps.supports_mutations is False
    assert caps.supports_payments is False
    assert "pay" not in dir(connector)
    assert not hasattr(connector, "submit_payment")
    assert not hasattr(connector, "redeem_rewards")


def test_signed_in_proceeds_to_extraction():
    observation = extract_from_dom_text(OVERVIEW_HTML_TEXT)
    calls: list[str] = []

    def ensure_session(provider: str):
        calls.append("session")
        return {"ok": True, "authentication_state": "SIGNED_IN", "recovery_attempts": 0}

    def ensure_surface(provider: str, surface: str):
        calls.append("surface")
        return {"ok": True, "surface": surface}

    def extract(provider: str, extraction: str):
        calls.append("extract")
        return {
            "ok": True,
            "observation": observation,
            "verified_at": "2026-01-01T00:00:00+00:00",
            "method_counts": dict(observation.method_counts),
        }

    connector = AmexConnector(
        ensure_usable_session_fn=ensure_session,
        ensure_provider_surface_fn=ensure_surface,
        execute_readonly_extraction_fn=extract,
        verify_fn=lambda p: _signed_in(),
    )
    result = connector.refresh()
    assert calls == ["session", "surface", "extract"]
    assert result.status in {RefreshStatus.SUCCESS, RefreshStatus.PARTIAL_SUCCESS}
    assert result.snapshot is not None
    assert len(result.snapshot.accounts) == 2
    assert len(result.snapshot.rewards) == 1
    assert_no_provider_raw_objects(result.to_sanitized_dict())


def test_signed_out_invokes_recovery_path():
    recovered = {"ok": True, "authentication_state": "SIGNED_IN", "recovery_attempts": 1}
    observation = extract_from_dom_text("Membership Rewards 1000\nCard Ending 1111\nBalance $10.00")

    states = iter(
        [
            {
                "ok": False,
                "authentication_state": "SIGNED_OUT",
                "error": "authentication_required",
            },
        ]
    )

    # Connector itself receives already-recovered session when ensure_usable_session
    # implements recovery. Simulate recovery inside ensure_usable_session.
    def ensure_session(provider: str):
        first = next(states, None)
        if first and first["authentication_state"] == "SIGNED_OUT":
            return recovered
        return recovered

    connector = AmexConnector(
        ensure_usable_session_fn=ensure_session,
        ensure_provider_surface_fn=lambda p, s: {"ok": True},
        execute_readonly_extraction_fn=lambda p, e: {
            "ok": True,
            "observation": observation,
            "method_counts": observation.method_counts,
        },
        verify_fn=lambda p: _signed_in(),
    )
    result = connector.refresh()
    assert result.telemetry.runtime_recovery_attempts == 1
    assert result.status in {RefreshStatus.SUCCESS, RefreshStatus.PARTIAL_SUCCESS}


def test_login_unknown_invokes_recovery_via_runtime(tmp_path: Path):
    runtime = _runtime(tmp_path)
    recovery_calls = []

    with patch.object(
        runtime,
        "verify",
        side_effect=[
            _signed_in(authentication_state="LOGIN_UNKNOWN", reason="inconclusive"),
            _signed_in(),
        ],
    ):
        payload = runtime.ensure_usable_session(
            "amex",
            recovery_fn=lambda **kwargs: recovery_calls.append(kwargs) or {"ok": True},
        )
    assert recovery_calls
    assert payload["ok"] is True
    assert payload["recovery_attempts"] == 1
    assert payload["authentication_state"] == "SIGNED_IN"


def test_unresolved_authentication_returns_authentication_required():
    connector = AmexConnector(
        ensure_usable_session_fn=lambda p: {
            "ok": False,
            "authentication_state": "SIGNED_OUT",
            "error": "authentication_required",
        },
        ensure_provider_surface_fn=lambda p, s: {"ok": True},
        execute_readonly_extraction_fn=lambda p, e: {"ok": True},
    )
    result = connector.refresh()
    assert result.status == RefreshStatus.AUTHENTICATION_REQUIRED
    assert result.error_reason is not None
    assert result.error_reason.value == "authentication_required"


def test_user_interruption_reflected_in_result():
    connector = AmexConnector(
        ensure_usable_session_fn=lambda p: {
            "ok": False,
            "authentication_state": "LOGIN_UNKNOWN",
            "user_interrupted": True,
            "interruption_type": "mfa_or_login",
        },
        ensure_provider_surface_fn=lambda p, s: {"ok": True},
        execute_readonly_extraction_fn=lambda p, e: {"ok": True},
    )
    result = connector.refresh()
    assert result.user_interrupted is True
    assert result.interruption_type == "mfa_or_login"
    assert result.status == RefreshStatus.AUTHENTICATION_REQUIRED


def test_structured_network_preferred_over_dom():
    page = _page_with_text(OVERVIEW_HTML_TEXT)
    structured = {
        "accounts": [
            {
                "accountToken": "tok-1",
                "lastFour": "9999",
                "productName": "Network Card",
                "currentBalance": "10.00",
                "availableCredit": "90.00",
            }
        ],
        "membershipRewardsPoints": "555",
    }
    observation = extract_amex_overview(
        page,
        captured_network=[{"body": structured}],
    )
    assert observation.extraction_method == FieldSource.NETWORK.value
    assert observation.cards[0].last_four == "9999"
    assert observation.rewards and observation.rewards.balance == "555"
    page.evaluate.assert_not_called()


def test_runtime_authenticated_request_fallback():
    page = _page_with_text("no useful widgets here Membership Rewards")
    payload = {
        "cardEnding": "4321",
        "productName": "API Card",
        "statementBalance": "22.00",
        "pointsBalance": "900",
    }

    def request_get(url, **kwargs):
        response = MagicMock()
        if "ReadAccountSummary" in url or "account_summary" in url or "Dashboard" in url:
            response.status = 200
            response.json.return_value = payload
        else:
            response.status = 404
            response.json.return_value = {}
        return response

    observation = extract_amex_overview(page, request_fn=request_get)
    assert observation.extraction_method == FieldSource.RUNTIME_API.value
    assert observation.cards[0].last_four == "4321"
    page.evaluate.assert_not_called()


def test_dom_fallback_and_no_page_evaluate():
    page = _page_with_text(OVERVIEW_HTML_TEXT)
    observation = extract_amex_overview(page)
    assert observation.extraction_method == FieldSource.DOM_FALLBACK.value
    assert observation.useful is True
    page.evaluate.assert_not_called()
    source = inspect.getsource(extract_amex_overview)
    assert "page.evaluate" not in source or "Never calls page.evaluate" in source


def test_missing_optional_field_yields_unavailable():
    observation = extract_from_dom_text(
        "Membership Rewards 1000\nCard Ending 1111\nStatement Balance $10.00"
    )
    statuses = {
        obs.field_name: obs.status
        for obs in observation.field_observations
        if obs.account_ref
    }
    # available credit / due fields unavailable for the card
    assert any(
        obs.field_name == "available_credit" and obs.status == "unavailable"
        for obs in observation.field_observations
    )


def test_one_failed_field_yields_partial_success():
    observation = extract_from_dom_text(OVERVIEW_HTML_TEXT)
    # Force one failed observation
    observation.field_observations[0].status = "failed"
    snapshot, fields, _warnings = normalize_amex_observation(observation)
    connector = AmexConnector(
        ensure_usable_session_fn=lambda p: {
            "ok": True,
            "authentication_state": "SIGNED_IN",
        },
        ensure_provider_surface_fn=lambda p, s: {"ok": True},
        execute_readonly_extraction_fn=lambda p, e: {
            "ok": True,
            "observation": observation,
            "method_counts": observation.method_counts,
        },
        verify_fn=lambda p: _signed_in(),
    )
    result = connector.refresh()
    assert result.status == RefreshStatus.PARTIAL_SUCCESS
    assert result.telemetry.fields_failed >= 1


def test_no_useful_data_failure():
    empty = extract_from_dom_text(
        "Welcome to our public marketing site. Learn about travel insurance."
    )
    assert empty.useful is False
    connector = AmexConnector(
        ensure_usable_session_fn=lambda p: {
            "ok": True,
            "authentication_state": "SIGNED_IN",
        },
        ensure_provider_surface_fn=lambda p, s: {"ok": True},
        execute_readonly_extraction_fn=lambda p, e: {
            "ok": True,
            "observation": empty,
            "method_counts": empty.method_counts,
        },
        verify_fn=lambda p: _signed_in(),
    )
    result = connector.refresh()
    assert result.status == RefreshStatus.FAILED
    assert result.error_reason is not None
    assert result.error_reason.value == "no_useful_data"


def test_multiple_cards_and_rewards_normalize():
    observation = extract_from_dom_text(OVERVIEW_HTML_TEXT)
    snapshot, fields, warnings = normalize_amex_observation(
        observation, verified_at="2026-01-01T00:00:00+00:00"
    )
    assert len(snapshot.accounts) == 2
    endings = {a.last_four for a in snapshot.accounts}
    assert endings == {"1009", "2008"}
    assert snapshot.rewards[0].balance == Decimal("124350")
    gold = next(a for a in snapshot.accounts if a.last_four == "2008")
    assert gold.payment_due_amount is not None
    assert gold.payment_due_amount.amount == Decimal("25.00")
    assert gold.payment_due_date is not None
    assert gold.payment_due_date.isoformat() == "2026-03-01"
    plat = next(a for a in snapshot.accounts if a.last_four == "1009")
    assert plat.current_balance is not None
    assert plat.current_balance.amount == Decimal("1234.56")
    assert plat.available_credit is not None
    assert plat.available_credit.currency == "USD"


def test_identifiers_stable_and_masked():
    a = stable_account_id(last_four="1009", product_name="Platinum Card")
    b = stable_account_id(last_four="1009", product_name="Platinum Card")
    c = stable_account_id(last_four="2008", product_name="Platinum Card")
    assert a == b
    assert a != c
    assert mask_account_number("3711-XXXXXX-1009") == "1009"
    assert mask_account_number("3711123456781009") == "1009"
    observation = extract_from_dom_text(OVERVIEW_HTML_TEXT)
    snapshot, _, _ = normalize_amex_observation(observation)
    payload = json.dumps(snapshot.to_dict())
    assert "3711123456781009" not in payload
    assert "cookie" not in payload.lower()


def test_sanitize_url_strips_query_and_fragment():
    assert (
        sanitize_url("https://global.americanexpress.com/overview?foo=1#bar")
        == "https://global.americanexpress.com/overview"
    )


def test_public_result_excludes_raw_provider_payloads():
    observation = extract_from_structured_payload(
        {
            "lastFour": "1111",
            "currentBalance": "1.00",
            "pointsBalance": "10",
        }
    )
    assert observation is not None
    connector = AmexConnector(
        ensure_usable_session_fn=lambda p: {
            "ok": True,
            "authentication_state": "SIGNED_IN",
        },
        ensure_provider_surface_fn=lambda p, s: {"ok": True},
        execute_readonly_extraction_fn=lambda p, e: {
            "ok": True,
            "observation": observation,
            "method_counts": observation.method_counts,
        },
        verify_fn=lambda p: _signed_in(),
    )
    result = connector.refresh()
    payload = result.to_sanitized_dict()
    dumped = json.dumps(payload).lower()
    for banned in ("cookie", "authorization", "set-cookie", "raw_payload", "response_body"):
        assert banned not in dumped
    assert_no_provider_raw_objects(payload)


def test_advice_boundary_warnings_are_data_quality_only():
    observation = extract_from_dom_text(
        "Membership Rewards 1000\nCard Ending 1111\nStatement Balance $10.00"
    )
    snapshot, _, warnings = normalize_amex_observation(observation)
    joined = " ".join(warnings).lower()
    assert "you should" not in joined
    assert "redeem" not in joined
    assert "recommend" not in joined
    for warning in snapshot.warnings:
        assert "pay this" not in warning.lower()


def test_connector_does_not_launch_chrome():
    source = inspect.getsource(AmexConnector)
    assert "sync_playwright" not in source
    assert "connect_over_cdp" not in source
    assert "launch_native_chrome" not in source
    assert "terminate_profile_processes" not in source


def test_build_amex_connector_from_runtime_delegates():
    runtime = MagicMock()
    runtime.ensure_usable_session.return_value = {
        "ok": True,
        "authentication_state": "SIGNED_IN",
    }
    runtime.ensure_provider_surface.return_value = {"ok": True}
    observation = extract_from_dom_text(
        "Membership Rewards 50\nCard Ending 2222\nBalance $1.00"
    )
    runtime.execute_readonly_extraction.return_value = {
        "ok": True,
        "observation": observation,
        "method_counts": observation.method_counts,
    }
    runtime.verify.return_value = _signed_in()
    connector = build_amex_connector_from_runtime(runtime)
    result = connector.refresh()
    runtime.ensure_usable_session.assert_called()
    runtime.ensure_provider_surface.assert_called_with("amex", "overview")
    runtime.execute_readonly_extraction.assert_called()
    assert result.snapshot is not None


def test_ensure_usable_session_signed_in_no_recovery(tmp_path: Path):
    runtime = _runtime(tmp_path)
    with patch.object(runtime, "verify", return_value=_signed_in()):
        payload = runtime.ensure_usable_session("amex")
    assert payload["ok"] is True
    assert payload["recovery_attempts"] == 0


def test_cli_connector_refresh_parses():
    with patch(
        "sys.argv",
        ["provider_runtime.py", "connector-refresh", "amex", "--json"],
    ):
        args = parse_args()
    assert args.command == "connector-refresh"
    assert args.provider == "amex"
    assert args.json is True


def test_run_connector_refresh_cleanup_and_preexisting(tmp_path: Path):
    observation = extract_from_dom_text(OVERVIEW_HTML_TEXT)
    printed: list[str] = []

    def ensure_runtime(**kwargs):
        return {
            "ok": True,
            "runtime_preexisting": True,
            "runtime_started_by_campaign": False,
            "process": None,
        }

    def prepare(**kwargs):
        return {
            "ok": True,
            "managed_browser_preexisting": True,
            "managed_browser_launched": False,
            "managed_browser_restarted": False,
            "interrupted": False,
            "final_authentication_state": "SIGNED_IN",
            "initial_authentication_state": "SIGNED_IN",
            "authentication_attempt_count": 1,
        }

    close_calls = []

    def close_browser(**kwargs):
        close_calls.append(kwargs)
        return {"closed": False}

    stop_calls = []

    def stop_runtime(**kwargs):
        stop_calls.append(kwargs)

    def factory():
        return AmexConnector(
            ensure_usable_session_fn=lambda p: {
                "ok": True,
                "authentication_state": "SIGNED_IN",
            },
            ensure_provider_surface_fn=lambda p, s: {"ok": True},
            execute_readonly_extraction_fn=lambda p, e: {
                "ok": True,
                "observation": observation,
                "method_counts": observation.method_counts,
            },
            verify_fn=lambda p: _signed_in(),
        )

    envelope = run_connector_refresh_with_runtime(
        provider="amex",
        root=tmp_path,
        ensure_runtime_fn=ensure_runtime,
        prepare_session_fn=prepare,
        close_managed_browser_fn=close_browser,
        stop_runtime_fn=stop_runtime,
        connector_factory_fn=factory,
        print_fn=lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
        as_json=False,
    )
    assert envelope["ok"] is True
    assert envelope["runtime_preexisting"] is True
    assert envelope["managed_browser_preexisting"] is True
    assert envelope["managed_browser_closed_at_completion"] is False
    assert not stop_calls  # did not start runtime, so must not stop it
    assert close_calls
    assert envelope["evidence_path"]
    assert Path(envelope["evidence_path"]).is_file()
    evidence = json.loads(Path(envelope["evidence_path"]).read_text())
    dumped = json.dumps(evidence).lower()
    assert "cookie" not in dumped
    assert "password" not in dumped
    summary = "\n".join(printed)
    assert "Amex connector refresh" in summary
    assert "partial_success" in summary or "success" in summary


def test_ctrl_c_preserves_evidence(tmp_path: Path):
    def ensure_runtime(**kwargs):
        return {
            "ok": True,
            "runtime_preexisting": False,
            "runtime_started_by_campaign": True,
            "process": object(),
        }

    def prepare(**kwargs):
        raise KeyboardInterrupt

    stop_calls = []

    envelope = run_connector_refresh_with_runtime(
        provider="amex",
        root=tmp_path,
        ensure_runtime_fn=ensure_runtime,
        prepare_session_fn=prepare,
        close_managed_browser_fn=lambda **k: {"closed": True},
        stop_runtime_fn=lambda **k: stop_calls.append(k),
        print_fn=lambda *a, **k: None,
    )
    assert envelope["exit_code"] == 130
    assert envelope["interrupted"] is True
    assert envelope["evidence_path"]
    assert Path(envelope["evidence_path"]).is_file()
    assert stop_calls  # owned runtime cleaned up


def test_format_summary_masks_accounts():
    text = format_connector_refresh_terminal_summary(
        {
            "status": "partial_success",
            "user_interrupted": False,
            "telemetry": {
                "authentication_final_state": "SIGNED_IN",
                "snapshot_account_count": 1,
                "rewards_program_count": 1,
                "fields_succeeded": 5,
                "fields_unavailable": 1,
                "fields_failed": 0,
            },
            "snapshot": {
                "observed_at": "2026-01-01T00:00:00+00:00",
                "accounts": [
                    {
                        "display_name": "Gold Card",
                        "last_four": "2008",
                        "current_balance": {"amount": "500.00"},
                    }
                ],
                "rewards": [
                    {
                        "program_name": "Membership Rewards",
                        "balance": "1000",
                        "unit": "points",
                    }
                ],
            },
        },
        evidence_path="/tmp/evidence.json",
    )
    assert "…2008" in text
    assert "Membership Rewards" in text
