"""Tests for the developer-only Amex expiration recorder."""

from __future__ import annotations

import argparse
import io
import json
import threading
from http import HTTPStatus
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

from mighty.provider_runtime import (
    AUTH_STATE_SOURCE_BROWSER_OBSERVATION,
    AUTH_STATE_SOURCE_FRESH_VERIFICATION,
    BROWSER_RECORD_EXPIRATION_REQUEST_FIELDS,
    DEFAULT_BROWSER_RECORD_SEARCH_TERMS,
    ProviderRuntime,
    ProviderRuntimeHTTPError,
    RollingExpirationObservationWindow,
    RuntimeHTTPServer,
    RuntimeHandler,
    VerificationResult,
    browser_record_expiration_http_status,
    build_browser_record_expiration_cli_payload,
    collect_expiration_recording_observation,
    parse_browser_record_expiration_request,
    perform_keepalive_action,
    record_amex_expiration_in_browser_context,
    record_amex_expiration_on_page,
    request_json,
    summarize_browser_targets_for_recording,
    summarize_frame_tree_for_recording,
    verify_amex_canonical_on_page,
)
from tests.test_provider_runtime_browser_inspector import (
    CdpSessionMock,
    PAGE_URL,
    _amex_page,
    _bind_cdp,
    _document,
    _element,
    _text_node,
)


def _clock(steps: list[float]):
    values = list(steps)
    values.append(values[-1] + 10_000)

    def monotonic() -> float:
        if len(values) == 1:
            return values[0]
        return values.pop(0)

    return monotonic


def _obs(
    browser_state: str = "LOGIN_UNKNOWN",
    *,
    observed_at: str = "t",
    screenshot_path: str | None = None,
    extra: dict | None = None,
) -> dict:
    """Browser-observation payload only; recorder fills canonical_* fields."""
    payload = {
        "observed_at": observed_at,
        "browser_observation_authentication_state": browser_state,
        "browser_observation_authentication_state_source": (
            AUTH_STATE_SOURCE_BROWSER_OBSERVATION
        ),
        "browser_observation_reason": f"browser:{browser_state}",
        "selected_page_url": PAGE_URL,
        "selected_page_title": "Amex",
        "login_url_detected": browser_state == "SIGNED_OUT",
        "browser_targets": [],
        "frame_tree": [],
        "browser_inspector": {"candidate_count": 0, "candidates": [], "errors": []},
        "accessibility_text_summary": None,
        "dom_text_summary": None,
        "optional_text_searches": [],
        "screenshot_path": screenshot_path,
        "collection_errors": [],
    }
    if extra:
        payload.update(extra)
    return payload


def _state_sequence_collector(states: list[str], *, write_screenshots: bool = False):
    """Yield browser-observation states (not canonical lifecycle states)."""
    remaining = list(states)

    def collect(page, **kwargs):
        state = remaining.pop(0) if remaining else states[-1]
        path = kwargs.get("screenshot_path")
        screenshot = None
        if write_screenshots and path is not None:
            Path(path).write_bytes(b"png-bytes")
            screenshot = str(path)
        return _obs(state, screenshot_path=screenshot)

    return collect


def _verify_sequence(states: list[str]):
    remaining = list(states)

    def verify(page):
        state = remaining.pop(0) if remaining else states[-1]
        return VerificationResult(
            provider="amex",
            authentication_state=state,
            reason=f"verify:{state}",
            observed_at="t",
            final_url=PAGE_URL,
            page_title="Amex",
            login_url_detected=state == "SIGNED_OUT",
            login_marker_count=0,
            authenticated_marker_count=0,
            session_api_200_count=1 if state == "SIGNED_IN" else 0,
            session_api_denied_count=1 if state == "SIGNED_OUT" else 0,
        )

    return verify


def _browser_unknown_collector(*, write_screenshots: bool = False):
    return _state_sequence_collector(
        ["LOGIN_UNKNOWN"] * 50,
        write_screenshots=write_screenshots,
    )


def test_refuses_to_start_when_initially_signed_out(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "rec-out"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        timeout_seconds=10,
        verify_fn=_verify_sequence(["SIGNED_OUT"]),
        collect_fn=_browser_unknown_collector(),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 0.1]),
    )
    assert payload["ok"] is False
    assert payload["outcome"] == "initial_not_signed_in"
    assert payload["initial_canonical_authentication_state"] == "SIGNED_OUT"
    assert payload["initial_authentication_state"] == "SIGNED_OUT"
    assert (out / "recording.json").is_file()
    assert "SIGNED_OUT" in " ".join(payload["run_errors"])


def test_canonical_signed_in_with_browser_unknown_starts_recording(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "start-ok"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=3,
        screenshot_every_seconds=10_000,
        verification_interval_seconds=5,
        verify_fn=_verify_sequence(["SIGNED_IN"]),
        collect_fn=_browser_unknown_collector(),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 0.0, 1.0, 2.0, 3.0]),
    )
    assert payload["outcome"] == "timeout"
    assert payload["initial_canonical_authentication_state"] == "SIGNED_IN"
    assert payload["observation_count"] >= 1
    assert all(
        item["canonical_authentication_state"] == "SIGNED_IN"
        for item in payload["observations"]
    )
    assert all(
        item["browser_observation_authentication_state"] == "LOGIN_UNKNOWN"
        for item in payload["observations"]
    )


def test_initial_login_unknown_retries_then_unknown_outcome(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "initial-unknown"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        timeout_seconds=30,
        startup_retry_seconds=3,
        verify_fn=_verify_sequence(["LOGIN_UNKNOWN"] * 10),
        collect_fn=_browser_unknown_collector(),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 1.0, 2.0, 3.0, 3.1]),
    )
    assert payload["ok"] is False
    assert payload["outcome"] == "initial_authentication_unknown"
    assert payload["initial_canonical_authentication_state"] == "LOGIN_UNKNOWN"
    assert payload["verification_call_count"] >= 2
    assert any("LOGIN_UNKNOWN" in err for err in payload["run_errors"])


def test_initial_login_unknown_recovers_to_signed_in(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "recover"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=2,
        screenshot_every_seconds=10_000,
        verification_interval_seconds=0,
        startup_retry_seconds=10,
        verify_fn=_verify_sequence(["LOGIN_UNKNOWN", "SIGNED_IN", "SIGNED_IN", "SIGNED_IN"]),
        collect_fn=_browser_unknown_collector(),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 1.0, 1.0, 1.1, 2.0]),
    )
    assert payload["outcome"] == "timeout"
    assert payload["initial_canonical_authentication_state"] == "SIGNED_IN"
    assert payload["verification_call_count"] >= 2


def test_records_repeated_signed_in_observations(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "signed-in"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=3,
        screenshot_every_seconds=10_000,
        verification_interval_seconds=0,
        verify_fn=_verify_sequence(["SIGNED_IN"] * 10),
        collect_fn=_browser_unknown_collector(),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 0.0, 1.0, 1.1, 2.0, 2.1, 3.0]),
    )
    assert payload["outcome"] == "timeout"
    assert payload["observation_count"] >= 2
    assert all(
        item["canonical_authentication_state"] == "SIGNED_IN"
        for item in payload["observations"]
    )


def test_completes_on_canonical_signed_in_to_signed_out_transition(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "logout"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=30,
        screenshot_every_seconds=10_000,
        verification_interval_seconds=0,
        verify_fn=_verify_sequence(["SIGNED_IN", "SIGNED_IN", "SIGNED_OUT"]),
        collect_fn=_browser_unknown_collector(),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 0.0, 1.0, 1.1, 2.0, 2.1, 3.0, 3.1]),
    )
    assert payload["ok"] is True
    assert payload["outcome"] == "logged_out"
    assert payload["logout_detected_at"] is not None
    assert payload["final_canonical_authentication_state"] == "SIGNED_OUT"
    assert payload["final_authentication_state"] == "SIGNED_OUT"
    assert payload["observations"][-1]["canonical_authentication_state"] == "SIGNED_OUT"
    assert payload["last_definitive_canonical_authentication_state"] == "SIGNED_OUT"
    assert (out / "recording.json").is_file()


def test_browser_signed_out_alone_does_not_complete_recording(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "browser-out"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=3,
        screenshot_every_seconds=10_000,
        verification_interval_seconds=0,
        verify_fn=_verify_sequence(["SIGNED_IN"] * 10),
        collect_fn=_state_sequence_collector(["SIGNED_OUT"] * 10),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 0.0, 1.0, 1.1, 2.0, 2.1, 3.0]),
    )
    assert payload["outcome"] == "timeout"
    assert any(
        item["browser_observation_authentication_state"] == "SIGNED_OUT"
        for item in payload["observations"]
    )
    assert all(
        item["canonical_authentication_state"] == "SIGNED_IN"
        for item in payload["observations"]
    )


def test_canonical_login_unknown_after_startup_does_not_complete(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "canonical-unknown"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=5,
        screenshot_every_seconds=10_000,
        verification_interval_seconds=0,
        verify_fn=_verify_sequence(
            ["SIGNED_IN", "LOGIN_UNKNOWN", "LOGIN_UNKNOWN", "LOGIN_UNKNOWN"]
        ),
        collect_fn=_browser_unknown_collector(),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 0.0, 1.0, 1.1, 2.0, 2.1, 3.0, 3.1, 5.0]),
    )
    assert payload["outcome"] == "timeout"
    assert payload["last_definitive_canonical_authentication_state"] == "SIGNED_IN"
    assert any(
        item["canonical_authentication_state"] == "LOGIN_UNKNOWN"
        for item in payload["observations"]
    )


def test_browser_login_unknown_does_not_complete_recording(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "unknown"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=5,
        screenshot_every_seconds=10_000,
        verification_interval_seconds=0,
        verify_fn=_verify_sequence(["SIGNED_IN"] * 10),
        collect_fn=_state_sequence_collector(
            ["LOGIN_UNKNOWN", "LOGIN_UNKNOWN", "LOGIN_UNKNOWN"]
        ),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 0.0, 1.0, 1.1, 2.0, 2.1, 3.0, 3.1, 5.0]),
    )
    assert payload["outcome"] == "timeout"
    assert payload["outcome"] != "logged_out"
    assert any(
        item["browser_observation_authentication_state"] == "LOGIN_UNKNOWN"
        for item in payload["observations"]
    )


def test_timeout_without_logout(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "timeout"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=2,
        screenshot_every_seconds=10_000,
        verification_interval_seconds=0,
        verify_fn=_verify_sequence(["SIGNED_IN"] * 10),
        collect_fn=_browser_unknown_collector(),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 0.0, 1.0, 1.1, 2.0]),
    )
    assert payload["ok"] is True
    assert payload["outcome"] == "timeout"
    assert payload["logout_detected_at"] is None
    assert (out / "recording.json").is_file()


def test_rolling_observations_are_bounded_by_time(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "window"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=10,
        rolling_window_seconds=2,
        screenshot_every_seconds=10_000,
        verification_interval_seconds=0,
        verify_fn=_verify_sequence(["SIGNED_IN"] * 20),
        collect_fn=_browser_unknown_collector(),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 0.0, 1.0, 1.1, 2.0, 2.1, 3.0, 3.1, 4.0, 4.1, 10.0]),
    )
    assert payload["outcome"] == "timeout"
    assert payload["observation_count"] <= 3
    assert payload["observation_count"] >= 1


def test_aged_out_screenshots_are_deleted(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "shot-age"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=10,
        rolling_window_seconds=1.5,
        screenshot_every_seconds=1,
        verification_interval_seconds=0,
        verify_fn=_verify_sequence(["SIGNED_IN"] * 20),
        collect_fn=_browser_unknown_collector(write_screenshots=True),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 0.0, 1.0, 1.1, 2.0, 2.1, 3.0, 3.1, 10.0]),
    )
    assert payload["outcome"] == "timeout"
    retained = [
        Path(item["screenshot_path"])
        for item in payload["observations"]
        if item.get("screenshot_path")
    ]
    assert retained
    for path in retained:
        assert path.is_file()
    # Early screenshot files should have been pruned from disk.
    all_png = list((out / "screenshots").glob("*.png"))
    assert len(all_png) == len(retained)


def test_retained_screenshots_are_preserved(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "shot-keep"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=30,
        rolling_window_seconds=90,
        screenshot_every_seconds=1,
        verification_interval_seconds=0,
        verify_fn=_verify_sequence(["SIGNED_IN", "SIGNED_OUT"]),
        collect_fn=_browser_unknown_collector(write_screenshots=True),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 0.0, 1.0, 1.1, 2.0, 2.1]),
    )
    assert payload["outcome"] == "logged_out"
    shots = list((out / "screenshots").glob("*.png"))
    assert shots
    for item in payload["observations"]:
        if item.get("screenshot_path"):
            assert Path(item["screenshot_path"]).is_file()


def test_screenshot_errors_do_not_crash_recorder(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "shot-err"

    def collect(page, **kwargs):
        return _obs(
            "SIGNED_IN",
            screenshot_path=None,
            extra={"collection_errors": ["Page.captureScreenshot:RuntimeError: boom"]},
        )

    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=2,
        screenshot_every_seconds=1,
        verification_interval_seconds=0,
        verify_fn=_verify_sequence(["SIGNED_IN"] * 10),
        collect_fn=collect,
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 0.0, 1.0, 1.1, 2.0]),
    )
    assert payload["outcome"] == "timeout"
    assert payload["ok"] is True


def test_target_get_targets_output_is_included(tmp_path: Path):
    page = _amex_page()
    page.title.return_value = "American Express"
    session = CdpSessionMock(
        document=_document(
            _element(
                10,
                10,
                "HTML",
                children=[
                    _element(
                        20,
                        20,
                        "BODY",
                        children=[
                            _text_node(21, 21, "Membership Rewards"),
                            _text_node(22, 22, "Account Home"),
                        ],
                    )
                ],
            )
        ),
        target_infos=[
            {
                "targetId": "abc",
                "type": "page",
                "title": "Amex Overview",
                "url": PAGE_URL,
                "attached": True,
                "openerId": "op-9",
            }
        ],
    )
    _bind_cdp(page, session)
    observation = collect_expiration_recording_observation(
        page,
        screenshot_path=None,
        find_text_fn=lambda _page, _query: {
            "ok": True,
            "match_count": 0,
            "matches": [],
        },
        inspect_fn=lambda _page: {
            "candidate_count": 0,
            "candidates": [],
            "errors": [],
        },
    )
    assert observation["browser_targets"]
    assert observation["browser_targets"][0]["targetId"] == "abc"
    assert observation["browser_targets"][0]["openerId"] == "op-9"
    assert "browser_observation_authentication_state" in observation
    assert "canonical_authentication_state" not in observation
    assert any(method == "Target.getTargets" for method, _ in session.calls)


def test_frame_tree_output_is_included():
    tree = {
        "frameTree": {
            "frame": {
                "id": "main",
                "url": PAGE_URL,
                "securityOrigin": "https://global.americanexpress.com",
                "mimeType": "text/html",
            },
            "childFrames": [
                {
                    "frame": {
                        "id": "child",
                        "url": "https://global.americanexpress.com/frame",
                        "securityOrigin": "https://global.americanexpress.com",
                        "mimeType": "text/html",
                    },
                    "childFrames": [],
                }
            ],
        }
    }
    summary = summarize_frame_tree_for_recording(tree)
    assert summary[0]["frame_id"] == "main"
    assert summary[0]["parent_frame_id"] is None
    assert summary[0]["security_origin"] == "https://global.americanexpress.com"
    assert summary[0]["mime_type"] == "text/html"
    assert summary[1]["frame_id"] == "child"
    assert summary[1]["parent_frame_id"] == "main"


def test_browser_inspector_output_is_included(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "inspector"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        timeout_seconds=1,
        screenshot_every_seconds=10_000,
        verification_interval_seconds=0,
        verify_fn=_verify_sequence(["SIGNED_IN", "SIGNED_IN"]),
        collect_fn=lambda _page, **_kwargs: _obs(
            "LOGIN_UNKNOWN",
            extra={
                "browser_inspector": {
                    "candidate_count": 1,
                    "candidates": [{"role": "dialog"}],
                    "errors": [],
                }
            },
        ),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 0.0, 1.0]),
    )
    assert payload["observations"][0]["browser_inspector"]["candidate_count"] == 1
    assert payload["observations"][0]["canonical_authentication_state"] == "SIGNED_IN"
    assert (
        payload["observations"][0]["browser_observation_authentication_state"]
        == "LOGIN_UNKNOWN"
    )


def test_optional_text_searches_do_not_trigger_completion(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "text-no-trigger"

    def collect(page, **kwargs):
        return _obs(
            "SIGNED_IN",
            extra={
                "optional_text_searches": [
                    {
                        "term": "expire",
                        "match_count": 3,
                        "match_summaries": [{"matched_text": "expire"}],
                    }
                ]
            },
        )

    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=2,
        screenshot_every_seconds=10_000,
        verification_interval_seconds=0,
        verify_fn=_verify_sequence(["SIGNED_IN"] * 10),
        collect_fn=collect,
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 0.0, 1.0, 1.1, 2.0]),
    )
    assert payload["outcome"] == "timeout"
    assert payload["observations"][0]["optional_text_searches"][0]["match_count"] == 3


def test_no_log_out_search_term():
    lowered = [term.lower() for term in DEFAULT_BROWSER_RECORD_SEARCH_TERMS]
    assert "log out" not in lowered
    assert "Log Out" not in DEFAULT_BROWSER_RECORD_SEARCH_TERMS


def test_no_evaluate_calls_and_no_page_mutation(tmp_path: Path):
    page = _amex_page()
    page.title.return_value = "American Express"
    session = CdpSessionMock(
        document=_document(
            _element(
                10,
                10,
                "HTML",
                children=[
                    _element(
                        20,
                        20,
                        "BODY",
                        children=[
                            _text_node(21, 21, "Membership Rewards"),
                            _text_node(22, 22, "Account Home"),
                            _text_node(23, 23, "session expire continue"),
                        ],
                    )
                ],
            )
        )
    )
    _bind_cdp(page, session)
    out = tmp_path / "no-mutate"
    keepalive_calls: list[tuple] = []

    def _keepalive_probe(*_args, **_kwargs):
        keepalive_calls.append((_args, _kwargs))
        raise AssertionError("keepalive action must not run during recorder")

    with patch(
        "mighty.provider_runtime.perform_keepalive_action",
        side_effect=_keepalive_probe,
    ):
        payload = record_amex_expiration_on_page(
            page,
            output_dir=out,
            interval_seconds=1,
            timeout_seconds=1,
            screenshot_every_seconds=1,
            verification_interval_seconds=0,
            verify_fn=_verify_sequence(["SIGNED_IN", "SIGNED_IN", "SIGNED_IN"]),
            sleep_fn=lambda _s: None,
            monotonic_fn=_clock([0.0, 0.0, 1.0]),
            find_text_fn=lambda _page, _query: {
                "ok": True,
                "match_count": 0,
                "matches": [],
            },
            inspect_fn=lambda _page: {
                "candidate_count": 0,
                "candidates": [],
                "errors": [],
            },
        )
    assert payload["outcome"] in {
        "timeout",
        "logged_out",
        "initial_not_signed_in",
        "initial_authentication_unknown",
    }
    assert keepalive_calls == []
    assert page.evaluate.call_count == 0
    page.goto.assert_not_called()
    page.reload.assert_not_called()
    page.click.assert_not_called()
    page.fill.assert_not_called()
    page.type.assert_not_called()
    assert not any(
        method.startswith("Input.") or method in {"Page.navigate", "Page.reload"}
        for method, _ in session.calls
    )


def test_verification_cadence_separate_from_screenshot_cadence(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "cadence"

    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=10,
        screenshot_every_seconds=1,
        verification_interval_seconds=5,
        verify_fn=_verify_sequence(["SIGNED_IN"] * 20),
        collect_fn=_browser_unknown_collector(write_screenshots=True),
        sleep_fn=lambda _s: None,
        # startup verify at t=0; browser polls every 1s; next verifies at t=5 and t=10
        monotonic_fn=_clock(
            [
                0.0,
                0.0,
                1.0,
                1.1,
                2.0,
                2.1,
                3.0,
                3.1,
                4.0,
                4.1,
                5.0,
                5.1,
                6.0,
                6.1,
                7.0,
                7.1,
                8.0,
                8.1,
                9.0,
                9.1,
                10.0,
            ]
        ),
    )
    assert payload["outcome"] == "timeout"
    # Startup + later 5s ticks — far fewer than one verify per screenshot/poll.
    assert payload["verification_call_count"] <= 4
    assert payload["verification_call_count"] >= 2
    assert payload["observation_count"] >= 5
    verified_flags = [
        item.get("canonical_verified_this_poll") for item in payload["observations"]
    ]
    assert any(verified_flags) and not all(verified_flags)


def test_passive_canonical_verify_uses_session_api_without_navigation():
    page = _amex_page()
    page.url = PAGE_URL
    page.title.return_value = "Amex"
    response = MagicMock()
    response.status = 200
    page.context.request.get.return_value = response
    result = verify_amex_canonical_on_page(page)
    assert result.authentication_state == "SIGNED_IN"
    assert "session API returned 200" in result.reason
    page.context.request.get.assert_called_once()
    assert page.evaluate.call_count == 0
    page.goto.assert_not_called()
    page.reload.assert_not_called()


def test_output_sanitization_redacts_long_numbers(tmp_path: Path):
    page = _amex_page()
    page.title.return_value = "card 1234567890123456"
    session = CdpSessionMock(
        document=_document(
            _element(
                10,
                10,
                "HTML",
                children=[
                    _element(
                        20,
                        20,
                        "BODY",
                        children=[
                            _text_node(21, 21, "Membership Rewards"),
                            _text_node(22, 22, "Account Home"),
                            _text_node(23, 23, "account 9876543210987654"),
                        ],
                    )
                ],
            )
        )
    )
    _bind_cdp(page, session)
    observation = collect_expiration_recording_observation(
        page,
        screenshot_path=None,
        find_text_fn=lambda _page, _query: {
            "ok": True,
            "match_count": 0,
            "matches": [],
        },
        inspect_fn=lambda _page: {
            "candidate_count": 0,
            "candidates": [],
            "errors": [],
        },
    )
    blob = json.dumps(observation)
    assert "9876543210987654" not in blob
    assert "1234567890123456" not in blob
    assert "[REDACTED_NUMBER]" in blob


def test_one_final_json_bundle(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "one-bundle"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        timeout_seconds=30,
        screenshot_every_seconds=10_000,
        verification_interval_seconds=0,
        verify_fn=_verify_sequence(["SIGNED_IN", "SIGNED_OUT"]),
        collect_fn=_browser_unknown_collector(),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 0.0, 1.0, 1.1, 2.0, 2.1]),
    )
    assert payload["outcome"] == "logged_out"
    json_files = list(out.glob("*.json"))
    assert json_files == [out / "recording.json"]


def test_fatal_cdp_errors_produce_bounded_diagnostic_result(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "fatal"

    def boom(_page, **_kwargs):
        raise RuntimeError("cdp exploded")

    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        timeout_seconds=5,
        verification_interval_seconds=0,
        verify_fn=_verify_sequence(["SIGNED_IN"]),
        collect_fn=boom,
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 0.0, 0.1]),
    )
    assert payload["ok"] is False
    assert payload["outcome"] == "fatal_error"
    assert any("cdp exploded" in err for err in payload["run_errors"])
    assert (out / "recording.json").is_file()
    saved = json.loads((out / "recording.json").read_text(encoding="utf-8"))
    assert saved["outcome"] == "fatal_error"
    assert "traceback" not in saved or isinstance(saved.get("run_errors"), list)


def test_rolling_window_keeps_latest_even_when_irregular():
    window = RollingExpirationObservationWindow(1.0)
    window.add(mono_at=0.0, observation=_obs("SIGNED_IN", observed_at="a"))
    window.add(mono_at=5.0, observation=_obs("SIGNED_IN", observed_at="b"))
    assert len(window) == 1
    assert window.observations[0]["observed_at"] == "b"


def test_target_summary_helper_includes_opener():
    summary = summarize_browser_targets_for_recording(
        {
            "targetInfos": [
                {
                    "targetId": "1",
                    "type": "page",
                    "title": "Hello",
                    "url": PAGE_URL + "?x=1",
                    "attached": True,
                    "openerId": "2",
                }
            ]
        }
    )
    assert summary[0]["openerId"] == "2"
    assert "?" not in (summary[0]["url"] or "")


def test_context_wrapper_writes_fatal_when_no_page(tmp_path: Path):
    context = MagicMock()
    context.pages = []
    out = tmp_path / "no-page"
    payload = record_amex_expiration_in_browser_context(
        context,
        output_dir=out,
        select_page_fn=lambda _context, create_if_missing=False: None,
    )
    assert payload["outcome"] == "fatal_error"
    assert "no_provider_page_selected" in payload["run_errors"]
    assert (out / "recording.json").is_file()


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


def _start_runtime_http(runtime: ProviderRuntime):
    server = RuntimeHTTPServer(("127.0.0.1", 0), RuntimeHandler)
    server.runtime = runtime
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


def test_parse_browser_record_expiration_request_accepts_valid_fields():
    parsed = parse_browser_record_expiration_request(
        {
            "provider": "amex",
            "interval_seconds": 1,
            "timeout_seconds": 900,
            "rolling_window_seconds": 90,
            "screenshot_every_seconds": 1,
            "output_dir": None,
        }
    )
    assert parsed["provider"] == "amex"
    assert parsed["interval_seconds"] == 1.0
    assert parsed["timeout_seconds"] == 900.0
    assert parsed["rolling_window_seconds"] == 90.0
    assert parsed["screenshot_every_seconds"] == 1.0
    assert parsed["output_dir"] is None


def test_parse_browser_record_expiration_request_rejects_malformed():
    with pytest.raises(ValueError, match="interval_seconds must be a number"):
        parse_browser_record_expiration_request({"interval_seconds": "nope"})
    with pytest.raises(ValueError, match="provider must be 'amex'"):
        parse_browser_record_expiration_request({"provider": "chase"})
    with pytest.raises(ValueError, match="output_dir must be a string or null"):
        parse_browser_record_expiration_request({"output_dir": 123})


def test_cli_and_server_field_names_remain_aligned():
    args = argparse.Namespace(
        provider="amex",
        interval_seconds=1,
        timeout_seconds=900,
        rolling_window_seconds=90,
        screenshot_every_seconds=1,
        verification_interval_seconds=5,
        output_dir=None,
    )
    payload = build_browser_record_expiration_cli_payload(args)
    assert tuple(payload.keys()) == BROWSER_RECORD_EXPIRATION_REQUEST_FIELDS
    parsed = parse_browser_record_expiration_request(payload)
    assert set(parsed.keys()) == set(BROWSER_RECORD_EXPIRATION_REQUEST_FIELDS)
    assert parsed["verification_interval_seconds"] == 5.0


def test_initial_not_signed_in_is_http_ok_not_unexplained_400():
    payload = {
        "ok": False,
        "outcome": "initial_not_signed_in",
        "initial_authentication_state": "SIGNED_OUT",
    }
    assert browser_record_expiration_http_status(payload) == HTTPStatus.OK


def test_http_valid_browser_record_expiration_request(tmp_path: Path):
    runtime = _runtime(tmp_path)
    out = tmp_path / "http-valid"
    expected = {
        "ok": True,
        "outcome": "timeout",
        "recording_json": str(out / "recording.json"),
        "output_dir": str(out),
    }
    runtime.record_browser_expiration = MagicMock(return_value=expected)
    server, base = _start_runtime_http(runtime)
    try:
        payload = request_json(
            "POST",
            f"{base}/providers/amex/diagnostics/browser-record-expiration",
            {
                "provider": "amex",
                "interval_seconds": 1,
                "timeout_seconds": 5,
                "rolling_window_seconds": 90,
                "screenshot_every_seconds": 1,
                "verification_interval_seconds": 5,
                "output_dir": str(out),
            },
        )
        assert payload["outcome"] == "timeout"
        runtime.record_browser_expiration.assert_called_once()
        kwargs = runtime.record_browser_expiration.call_args.kwargs
        assert kwargs["interval_seconds"] == 1.0
        assert kwargs["timeout_seconds"] == 5.0
        assert kwargs["rolling_window_seconds"] == 90.0
        assert kwargs["screenshot_every_seconds"] == 1.0
        assert kwargs["verification_interval_seconds"] == 5.0
        assert kwargs["output_dir"] == out
    finally:
        server.shutdown()
        server.server_close()


def test_http_malformed_browser_record_expiration_returns_useful_json(
    tmp_path: Path, capsys
):
    runtime = _runtime(tmp_path)
    server, base = _start_runtime_http(runtime)
    try:
        with pytest.raises(ProviderRuntimeHTTPError) as exc_info:
            request_json(
                "POST",
                f"{base}/providers/amex/diagnostics/browser-record-expiration",
                {"interval_seconds": "not-a-number"},
            )
        err = exc_info.value
        assert err.status == 400
        assert err.path.endswith("/providers/amex/diagnostics/browser-record-expiration")
        body = json.loads(err.body)
        assert body["ok"] is False
        assert body["error_type"] == "validation_error"
        assert "interval_seconds" in body["error"]
        captured = capsys.readouterr()
        assert "HTTP 400" in captured.err
        assert "interval_seconds" in captured.err
        assert "Traceback" not in captured.err
    finally:
        server.shutdown()
        server.server_close()


def test_http_initial_not_signed_in_returns_documented_result(tmp_path: Path):
    runtime = _runtime(tmp_path)
    out = tmp_path / "initial-out"
    expected = {
        "ok": False,
        "outcome": "initial_not_signed_in",
        "initial_authentication_state": "SIGNED_OUT",
        "recording_json": str(out / "recording.json"),
        "output_dir": str(out),
        "run_errors": [
            "initial_authentication_state_was_SIGNED_OUT; "
            "recorder requires SIGNED_IN to start"
        ],
    }
    runtime.record_browser_expiration = MagicMock(return_value=expected)
    server, base = _start_runtime_http(runtime)
    try:
        payload = request_json(
            "POST",
            f"{base}/providers/amex/diagnostics/browser-record-expiration",
            build_browser_record_expiration_cli_payload(
                argparse.Namespace(
                    provider="amex",
                    interval_seconds=1,
                    timeout_seconds=5,
                    rolling_window_seconds=90,
                    screenshot_every_seconds=1,
                    verification_interval_seconds=5,
                    output_dir=out,
                )
            ),
        )
        assert payload["outcome"] == "initial_not_signed_in"
        assert payload["initial_authentication_state"] == "SIGNED_OUT"
        assert payload["ok"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_request_json_displays_400_response_body(capsys):
    class FakeHTTPError(HTTPError):
        def __init__(self):
            super().__init__(
                url="http://127.0.0.1:8765/providers/amex/diagnostics/browser-record-expiration",
                code=400,
                msg="Bad Request",
                hdrs=None,
                fp=io.BytesIO(
                    json.dumps(
                        {
                            "ok": False,
                            "error": "interval_seconds must be a number",
                            "error_type": "validation_error",
                        }
                    ).encode("utf-8")
                ),
            )

    with patch("mighty.provider_runtime.urlopen", side_effect=FakeHTTPError()):
        with pytest.raises(ProviderRuntimeHTTPError) as exc_info:
            request_json(
                "POST",
                "http://127.0.0.1:8765/providers/amex/diagnostics/browser-record-expiration",
                {"interval_seconds": "bad"},
            )
    captured = capsys.readouterr()
    assert "HTTP 400 from /providers/amex/diagnostics/browser-record-expiration" in (
        captured.err
    )
    assert "interval_seconds must be a number" in captured.err
    assert "validation_error" in captured.err
    assert "Traceback" not in captured.err
    assert "Cookie" not in captured.err
    assert exc_info.value.status == 400


def test_serve_registers_browser_record_expiration_route():
    source = Path("mighty/provider_runtime.py").read_text(encoding="utf-8")
    assert 'if self.path == "/providers/amex/diagnostics/browser-record-expiration":' in (
        source
    )
    assert "def run_server(" in source
    assert "RuntimeHTTPServer((args.host, args.port), RuntimeHandler)" in source


def test_recorder_can_start_while_keepalive_trial_exists(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.keepalive_trial_running = True
    runtime.keepalive_trial_id = "trial-alive"
    out = tmp_path / "with-keepalive"
    page = _amex_page()
    context = MagicMock()
    browser = MagicMock()
    browser.contexts = [context]
    playwright = MagicMock()
    playwright.chromium.connect_over_cdp.return_value = browser
    cm = MagicMock()
    cm.__enter__.return_value = playwright
    cm.__exit__.return_value = None

    with patch("mighty.provider_runtime.sync_playwright", return_value=cm), patch(
        "mighty.provider_runtime.record_amex_expiration_in_browser_context",
        return_value={
            "ok": True,
            "outcome": "timeout",
            "output_dir": str(out),
            "recording_json": str(out / "recording.json"),
        },
    ) as record_fn:
        payload = runtime.record_browser_expiration(
            "amex",
            interval_seconds=1,
            timeout_seconds=5,
            output_dir=out,
        )
    assert payload["outcome"] == "timeout"
    assert runtime.keepalive_trial_running is True
    record_fn.assert_called_once()
    assert page.evaluate.call_count == 0


def test_recorder_poll_loop_does_not_hold_runtime_lock(tmp_path: Path):
    runtime = _runtime(tmp_path)
    out = tmp_path / "lock-free"
    lock_acquired_during_poll = threading.Event()

    def collect(_page, **_kwargs):
        acquired = runtime.lock.acquire(blocking=False)
        if acquired:
            lock_acquired_during_poll.set()
            runtime.lock.release()
        return _obs("SIGNED_IN")

    page = _amex_page()
    context = MagicMock()
    browser = MagicMock()
    browser.contexts = [context]
    playwright = MagicMock()
    playwright.chromium.connect_over_cdp.return_value = browser
    cm = MagicMock()
    cm.__enter__.return_value = playwright
    cm.__exit__.return_value = None

    with patch("mighty.provider_runtime.sync_playwright", return_value=cm), patch(
        "mighty.provider_runtime.record_amex_expiration_in_browser_context",
        side_effect=lambda *_a, **kwargs: record_amex_expiration_on_page(
            page,
            output_dir=kwargs.get("output_dir") or out,
            interval_seconds=kwargs.get("interval_seconds", 1),
            timeout_seconds=kwargs.get("timeout_seconds", 2),
            screenshot_every_seconds=10_000,
            verification_interval_seconds=0,
            verify_fn=_verify_sequence(["SIGNED_IN"] * 10),
            collect_fn=collect,
            sleep_fn=lambda _s: None,
            monotonic_fn=_clock([0.0, 0.0, 1.0, 1.1, 2.0]),
        ),
    ):
        payload = runtime.record_browser_expiration(
            "amex",
            interval_seconds=1,
            timeout_seconds=2,
            output_dir=out,
        )
    assert payload["outcome"] == "timeout"
    assert lock_acquired_during_poll.is_set()
    assert page.evaluate.call_count == 0
    page.goto.assert_not_called()
    page.reload.assert_not_called()
    page.click.assert_not_called()
