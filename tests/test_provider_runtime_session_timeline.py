"""Regression tests for Provider Runtime session timeline recorder/analyzer."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from mighty.provider_runtime import ProviderRuntime, VerificationResult
from mighty.provider_runtime_session_timeline import (
    SESSION_TIMELINE_EVENT_TYPES,
    SESSION_TIMELINE_JSONL,
    SESSION_TIMELINE_MD,
    SessionTimelineRecorder,
    analyze_session_timeline,
    cookie_metadata_from_playwright,
    load_session_timeline,
    render_session_timeline_markdown,
    sanitize_timeline_payload,
    write_session_timeline_analysis,
)


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


def _signed_in_result() -> VerificationResult:
    return VerificationResult(
        provider="amex",
        authentication_state="SIGNED_IN",
        reason="Amex session API returned 200",
        observed_at="2026-01-01T00:00:00+00:00",
        final_url="https://global.americanexpress.com/overview?token=secret",
        page_title="Overview",
        login_url_detected=False,
        login_marker_count=0,
        authenticated_marker_count=2,
        session_api_200_count=1,
        session_api_denied_count=0,
    )


def _signed_out_result() -> VerificationResult:
    return VerificationResult(
        provider="amex",
        authentication_state="SIGNED_OUT",
        reason="Amex login page detected",
        observed_at="2026-01-01T00:10:00+00:00",
        final_url="https://www.americanexpress.com/en-us/account/login",
        page_title="Login",
        login_url_detected=True,
        login_marker_count=2,
        authenticated_marker_count=0,
        session_api_200_count=0,
        session_api_denied_count=1,
    )


def test_recorder_writes_ndjson_envelope(tmp_path: Path):
    path = tmp_path / SESSION_TIMELINE_JSONL
    stamps = iter(
        [
            "2026-07-19T12:00:00+00:00",
            "2026-07-19T12:00:05+00:00",
        ]
    )
    recorder = SessionTimelineRecorder(
        path=path,
        session_id="sess-1",
        provider="amex",
        started_at="2026-07-19T12:00:00+00:00",
        clock=lambda: next(stamps),
    )
    event = recorder.record(
        "runtime_started",
        payload={"cdp_port": 9223},
    )
    assert event["session_id"] == "sess-1"
    assert event["provider"] == "amex"
    assert event["event_type"] == "runtime_started"
    assert event["elapsed_seconds"] == 0.0
    assert event["payload"]["cdp_port"] == 9223

    recorder.record("browser_reused", payload={"mode": "attach"})
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    second = json.loads(lines[1])
    assert second["elapsed_seconds"] == 5.0
    assert second["event_type"] == "browser_reused"


def test_recorder_rejects_unknown_event_type(tmp_path: Path):
    recorder = SessionTimelineRecorder(
        path=tmp_path / "t.jsonl",
        session_id="s",
        provider="amex",
    )
    try:
        recorder.record("not_a_real_event")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "Unsupported session timeline event_type" in str(exc)


def test_all_required_event_types_are_supported(tmp_path: Path):
    recorder = SessionTimelineRecorder(
        path=tmp_path / "t.jsonl",
        session_id="s",
        provider="amex",
        started_at="2026-07-19T12:00:00+00:00",
        clock=lambda: "2026-07-19T12:00:00+00:00",
    )
    required = {
        "runtime_started",
        "browser_started",
        "browser_reused",
        "authentication_required",
        "authentication_verified",
        "auth_state_changed",
        "keepalive_scheduled",
        "keepalive_started",
        "keepalive_completed",
        "keepalive_failed",
        "verification_started",
        "verification_completed",
        "navigation",
        "redirect",
        "page_reload",
        "http_401",
        "http_403",
        "cookie_added",
        "cookie_removed",
        "logout_detected",
        "campaign_completed",
    }
    assert required <= SESSION_TIMELINE_EVENT_TYPES
    for event_type in sorted(required):
        recorder.record(event_type, payload={"source": "test"})
    assert recorder.event_count == len(required)


def test_privacy_strips_secrets_from_payload():
    cleaned = sanitize_timeline_payload(
        {
            "url": "https://example.com/path?token=abc&session=1",
            "cookie": "should-not-appear",
            "cookies": [{"name": "x", "value": "secret"}],
            "authorization": "Bearer secret",
            "headers": {"Authorization": "Bearer x"},
            "body": '{"password":"x"}',
            "password": "hunter2",
            "credentials": {"user": "a"},
            "token": "abc",
            "authentication_state": "SIGNED_IN",
            "strategy": "SESSION_API",
        }
    )
    assert "cookie" not in cleaned
    assert "cookies" not in cleaned
    assert "authorization" not in cleaned
    assert "headers" not in cleaned
    assert "body" not in cleaned
    assert "password" not in cleaned
    assert "credentials" not in cleaned
    assert "token" not in cleaned
    assert cleaned["authentication_state"] == "SIGNED_IN"
    assert cleaned["strategy"] == "SESSION_API"
    assert cleaned["url"] == "https://example.com/path"
    assert "?" not in cleaned["url"]


def test_cookie_metadata_never_includes_value():
    meta = cookie_metadata_from_playwright(
        {
            "name": "session",
            "value": "super-secret",
            "domain": ".americanexpress.com",
            "path": "/",
            "expires": 123,
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        }
    )
    assert meta["name"] == "session"
    assert meta["domain"] == ".americanexpress.com"
    assert "value" not in meta
    assert "super-secret" not in json.dumps(meta)


def test_cookie_observe_emits_added_and_removed(tmp_path: Path):
    recorder = SessionTimelineRecorder(
        path=tmp_path / "t.jsonl",
        session_id="s",
        provider="amex",
        started_at="2026-07-19T12:00:00+00:00",
        clock=lambda: "2026-07-19T12:00:01+00:00",
    )
    recorder.observe_cookies(
        [
            {
                "name": "a",
                "value": "secret-a",
                "domain": "example.com",
                "path": "/",
            },
            {
                "name": "b",
                "value": "secret-b",
                "domain": "example.com",
                "path": "/",
            },
        ]
    )
    recorder.observe_cookies(
        [
            {
                "name": "b",
                "value": "secret-b",
                "domain": "example.com",
                "path": "/",
            },
            {
                "name": "c",
                "value": "secret-c",
                "domain": "example.com",
                "path": "/",
            },
        ]
    )
    events = load_session_timeline(recorder.path)
    types = [event["event_type"] for event in events]
    assert types.count("cookie_added") == 3  # a,b then c
    assert types.count("cookie_removed") == 1  # a
    blob = recorder.path.read_text(encoding="utf-8")
    assert "secret-a" not in blob
    assert "secret-b" not in blob
    assert "secret-c" not in blob


def test_note_auth_transition_emits_logout_sequence(tmp_path: Path):
    recorder = SessionTimelineRecorder(
        path=tmp_path / "t.jsonl",
        session_id="s",
        provider="amex",
        started_at="2026-07-19T12:00:00+00:00",
        clock=lambda: "2026-07-19T12:00:00+00:00",
    )
    recorder.note_auth_transition("SIGNED_IN", source="test", reason="ok")
    recorder.note_auth_transition("SIGNED_OUT", source="test", reason="idle")
    events = load_session_timeline(recorder.path)
    types = [event["event_type"] for event in events]
    assert "authentication_verified" in types
    assert "auth_state_changed" in types
    assert "logout_detected" in types
    assert "authentication_required" not in types


def test_analyzer_generates_markdown_with_required_sections(tmp_path: Path):
    path = tmp_path / SESSION_TIMELINE_JSONL
    recorder = SessionTimelineRecorder(
        path=path,
        session_id="sess-analyze",
        provider="amex",
        started_at="2026-07-19T12:00:00+00:00",
        clock=lambda: "2026-07-19T12:00:00+00:00",
    )
    recorder.record("runtime_started", timestamp="2026-07-19T12:00:00+00:00")
    recorder.record(
        "keepalive_completed",
        timestamp="2026-07-19T12:05:00+00:00",
        payload={"strategy": "SESSION_API", "result": "success"},
    )
    recorder.note_auth_transition(
        "SIGNED_IN",
        reason="bootstrap",
    )
    # Force deterministic timestamps for logout window.
    recorder.clock = lambda: "2026-07-19T12:20:00+00:00"
    recorder.record(
        "http_401",
        timestamp="2026-07-19T12:19:50+00:00",
        payload={"status": 401, "url": "https://example.com/session"},
    )
    recorder.note_auth_transition(
        "SIGNED_OUT",
        reason="denied",
    )

    analysis = analyze_session_timeline(path)
    assert analysis["session_id"] == "sess-analyze"
    assert analysis["event_count"] >= 4
    assert analysis["session_lifetime_seconds"] is not None
    assert analysis["last_successful_keepalive"] is not None
    assert analysis["logout_detection_sequence"]
    assert analysis["inferred_expiration_mechanism"] == "auth_denied"
    assert analysis["confidence"] == "high"

    outputs = write_session_timeline_analysis(tmp_path, analysis)
    md_path = Path(outputs["markdown_path"])
    assert md_path.name == SESSION_TIMELINE_MD
    markdown = md_path.read_text(encoding="utf-8")
    assert "# Session Timeline" in markdown
    assert "## Chronological events" in markdown
    assert "## Logout detection sequence" in markdown
    assert "Session lifetime" in markdown
    assert "Last successful keepalive" in markdown
    assert "Inferred expiration mechanism" in markdown
    assert "Confidence" in markdown
    assert "auth_denied" in markdown


def test_analyzer_infers_idle_timeout_without_auth_denial(tmp_path: Path):
    path = tmp_path / "timeline.jsonl"
    recorder = SessionTimelineRecorder(
        path=path,
        session_id="s",
        provider="amex",
        started_at="2026-07-19T12:00:00+00:00",
        clock=lambda: "2026-07-19T12:00:00+00:00",
    )
    recorder.note_auth_transition("SIGNED_IN", reason="ok")
    recorder.record(
        "keepalive_completed",
        timestamp="2026-07-19T12:10:00+00:00",
        payload={"strategy": "NONE", "result": "success"},
    )
    recorder.record(
        "logout_detected",
        timestamp="2026-07-19T12:30:00+00:00",
        payload={"authentication_state": "SIGNED_OUT", "reason": "idle"},
    )
    analysis = analyze_session_timeline(path)
    assert analysis["inferred_expiration_mechanism"] in {
        "idle_timeout",
        "idle_timeout_after_keepalive",
    }
    assert analysis["confidence"] in {"medium", "high"}
    markdown = render_session_timeline_markdown(analysis)
    assert "idle_timeout" in markdown


def test_runtime_start_records_browser_reused(tmp_path: Path):
    runtime = _runtime(tmp_path)
    with patch(
        "mighty.provider_runtime.cdp_endpoint_available",
        return_value="http://127.0.0.1:9333",
    ):
        runtime.start()
    events = load_session_timeline(runtime.session_timeline.path)
    types = [event["event_type"] for event in events]
    assert "runtime_started" in types
    assert "browser_reused" in types
    assert "browser_started" not in types


def test_runtime_start_records_browser_started(tmp_path: Path):
    runtime = _runtime(tmp_path)
    process = MagicMock()
    process.pid = 4242
    with patch(
        "mighty.provider_runtime.cdp_endpoint_available",
        return_value=None,
    ), patch(
        "mighty.provider_runtime.terminate_profile_processes",
    ), patch(
        "mighty.provider_runtime.wait_for_profile_release",
        return_value=True,
    ), patch(
        "mighty.provider_runtime.launch_native_chrome",
        return_value=process,
    ), patch(
        "mighty.provider_runtime.wait_for_cdp",
        return_value="http://127.0.0.1:9333",
    ):
        runtime.start()
    events = load_session_timeline(runtime.session_timeline.path)
    types = [event["event_type"] for event in events]
    assert "runtime_started" in types
    assert "browser_started" in types


def test_runtime_verify_records_auth_and_strips_url_secrets(tmp_path: Path):
    runtime = _runtime(tmp_path)
    with patch(
        "mighty.provider_runtime.verify_amex_over_cdp",
        return_value=_signed_in_result(),
    ):
        runtime.verify("amex")
    with patch(
        "mighty.provider_runtime.verify_amex_over_cdp",
        return_value=_signed_out_result(),
    ):
        runtime.verify("amex")

    blob = runtime.session_timeline.path.read_text(encoding="utf-8")
    assert "token=secret" not in blob
    assert "Bearer" not in blob

    events = load_session_timeline(runtime.session_timeline.path)
    types = [event["event_type"] for event in events]
    assert "verification_started" in types
    assert "verification_completed" in types
    assert "authentication_verified" in types
    assert "auth_state_changed" in types
    assert "logout_detected" in types

    for event in events:
        payload = event.get("payload") or {}
        url = payload.get("final_url")
        if url:
            assert "?" not in url


def test_runtime_stop_writes_session_timeline_markdown(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime._timeline("runtime_started", payload={})
    with patch(
        "mighty.provider_runtime.terminate_profile_processes",
    ), patch.object(runtime, "stop_maintenance_watcher"), patch.object(
        runtime, "stop_keepalive_trial"
    ):
        runtime.stop()
    assert (tmp_path / SESSION_TIMELINE_MD).is_file()
    markdown = (tmp_path / SESSION_TIMELINE_MD).read_text(encoding="utf-8")
    assert "Session Timeline" in markdown
