"""Tests for the developer-only Amex expiration recorder."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from mighty.provider_runtime import (
    DEFAULT_BROWSER_RECORD_SEARCH_TERMS,
    RollingExpirationObservationWindow,
    collect_expiration_recording_observation,
    record_amex_expiration_in_browser_context,
    record_amex_expiration_on_page,
    summarize_browser_targets_for_recording,
    summarize_frame_tree_for_recording,
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
    state: str,
    *,
    observed_at: str = "t",
    screenshot_path: str | None = None,
    extra: dict | None = None,
) -> dict:
    payload = {
        "observed_at": observed_at,
        "canonical_authentication_state": state,
        "canonical_authentication_state_source": "FRESH_VERIFICATION",
        "canonical_reason": f"test:{state}",
        "selected_page_url": PAGE_URL,
        "selected_page_title": "Amex",
        "login_url_detected": state == "SIGNED_OUT",
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


def test_refuses_to_start_when_initially_signed_out(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "rec-out"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        timeout_seconds=10,
        collect_fn=_state_sequence_collector(["SIGNED_OUT"]),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0]),
    )
    assert payload["ok"] is False
    assert payload["outcome"] == "initial_not_signed_in"
    assert payload["initial_authentication_state"] == "SIGNED_OUT"
    assert (out / "recording.json").is_file()
    assert "SIGNED_OUT" in " ".join(payload["run_errors"])


def test_records_repeated_signed_in_observations(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "signed-in"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=3,
        screenshot_every_seconds=10_000,
        collect_fn=_state_sequence_collector(["SIGNED_IN", "SIGNED_IN", "SIGNED_IN"]),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 1.0, 2.0, 3.0]),
    )
    assert payload["outcome"] == "timeout"
    assert payload["observation_count"] >= 2
    assert all(
        item["canonical_authentication_state"] == "SIGNED_IN"
        for item in payload["observations"]
    )


def test_completes_on_signed_in_to_signed_out_transition(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "logout"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=30,
        screenshot_every_seconds=10_000,
        collect_fn=_state_sequence_collector(
            ["SIGNED_IN", "SIGNED_IN", "SIGNED_OUT"]
        ),
        sleep_fn=lambda _s: None,
        # started + per-poll (now, deadline-check) for 3 polls
        monotonic_fn=_clock([0.0, 1.0, 1.1, 2.0, 2.1, 3.0, 3.1]),
    )
    assert payload["ok"] is True
    assert payload["outcome"] == "logged_out"
    assert payload["logout_detected_at"] is not None
    assert payload["final_authentication_state"] == "SIGNED_OUT"
    assert payload["observations"][-1]["canonical_authentication_state"] == "SIGNED_OUT"
    assert (out / "recording.json").is_file()


def test_login_unknown_does_not_complete_recording(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "unknown"
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=5,
        screenshot_every_seconds=10_000,
        collect_fn=_state_sequence_collector(
            ["SIGNED_IN", "LOGIN_UNKNOWN", "LOGIN_UNKNOWN"]
        ),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 1.0, 2.0, 3.0, 4.0, 5.0]),
    )
    assert payload["outcome"] == "timeout"
    assert payload["outcome"] != "logged_out"
    assert any(
        item["canonical_authentication_state"] == "LOGIN_UNKNOWN"
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
        collect_fn=_state_sequence_collector(["SIGNED_IN", "SIGNED_IN", "SIGNED_IN"]),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 1.0, 2.0]),
    )
    assert payload["ok"] is True
    assert payload["outcome"] == "timeout"
    assert payload["logout_detected_at"] is None
    assert (out / "recording.json").is_file()


def test_rolling_observations_are_bounded_by_time(tmp_path: Path):
    page = _amex_page()
    out = tmp_path / "window"
    states = ["SIGNED_IN"] * 5
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=10,
        rolling_window_seconds=2,
        screenshot_every_seconds=10_000,
        collect_fn=_state_sequence_collector(states),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 1.0, 2.0, 3.0, 4.0, 10.0]),
    )
    assert payload["outcome"] == "timeout"
    # Window is 2s; at t=4 keep observations from t>=2 → roughly 3 entries.
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
        collect_fn=_state_sequence_collector(
            ["SIGNED_IN", "SIGNED_IN", "SIGNED_IN", "SIGNED_IN"],
            write_screenshots=True,
        ),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 1.0, 2.0, 3.0, 10.0]),
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
        collect_fn=_state_sequence_collector(
            ["SIGNED_IN", "SIGNED_OUT"],
            write_screenshots=True,
        ),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 1.0, 1.1, 2.0, 2.1]),
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
        collect_fn=collect,
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 1.0, 2.0]),
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
        collect_fn=lambda _page, **_kwargs: _obs(
            "SIGNED_IN",
            extra={
                "browser_inspector": {
                    "candidate_count": 1,
                    "candidates": [{"role": "dialog"}],
                    "errors": [],
                }
            },
        ),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 1.0]),
    )
    assert payload["observations"][0]["browser_inspector"]["candidate_count"] == 1


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
        collect_fn=collect,
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 1.0, 2.0]),
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
    payload = record_amex_expiration_on_page(
        page,
        output_dir=out,
        interval_seconds=1,
        timeout_seconds=1,
        screenshot_every_seconds=1,
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 1.0]),
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
    assert payload["outcome"] in {"timeout", "logged_out", "initial_not_signed_in"}
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
        collect_fn=_state_sequence_collector(["SIGNED_IN", "SIGNED_OUT"]),
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0, 1.0, 1.1, 2.0, 2.1]),
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
        collect_fn=boom,
        sleep_fn=lambda _s: None,
        monotonic_fn=_clock([0.0]),
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
