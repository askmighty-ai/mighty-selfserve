"""Tests for the developer-only CDP browser text watcher."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from mighty.provider_runtime import (
    AUTH_STATE_SOURCE_LATEST_CANONICAL,
    find_text_in_page_cdp,
    matched_terms_in_page_cdp,
    parse_browser_watch_terms,
    redact_long_digit_sequences,
    watch_text_in_browser_context,
    watch_text_on_page,
)
from tests.test_provider_runtime_browser_inspector import (
    CdpSessionMock,
    LIVE_AMEX_DIALOG_TEXT,
    PAGE_URL,
    _amex_page,
    _bind_cdp,
    _dialog_tree,
    _document,
    _element,
    _text_node,
)


def _empty_tree() -> dict:
    return _document(_element(10, 10, "HTML", children=[_element(20, 20, "BODY")]))


def _clock(steps: list[float]):
    """Return a monotonic clock that advances through the given values."""
    values = list(steps)
    # Pad so the watcher can always read a final time after the last sleep.
    values.append(values[-1] + 10_000)

    def monotonic() -> float:
        if len(values) == 1:
            return values[0]
        return values.pop(0)

    return monotonic


def test_parse_browser_watch_terms_defaults_and_splits():
    assert parse_browser_watch_terms(None) == [
        "expire",
        "Your session",
        "Continue",
        "Log Out",
    ]
    assert parse_browser_watch_terms("expire, Your session ,Continue") == [
        "expire",
        "Your session",
        "Continue",
    ]


def test_first_poll_match(tmp_path: Path):
    page = _amex_page()
    session = CdpSessionMock(document=_dialog_tree())
    _bind_cdp(page, session)
    output = tmp_path / "watch.json"
    sleeps: list[float] = []

    payload = watch_text_on_page(
        page,
        ["expire"],
        interval_seconds=1,
        timeout_seconds=10,
        stop_after_first_match=True,
        output_file=output,
        canonical_authentication_state="SIGNED_IN",
        canonical_authentication_state_source=AUTH_STATE_SOURCE_LATEST_CANONICAL,
        sleep_fn=lambda seconds: sleeps.append(seconds),
        monotonic_fn=_clock([0.0, 0.0]),
        inspect_fn=lambda _page: {
            "inspected_at": "t",
            "selected_page_url": PAGE_URL,
            "candidates": [],
            "collector": "test",
        },
    )

    assert payload["matched"] is True
    assert payload["matched_terms"] == ["expire"]
    assert payload["poll_count"] == 1
    assert payload["timed_out"] is False
    assert payload["output_file"] == str(output)
    assert output.is_file()
    assert sleeps == []
    assert page.evaluate.call_count == 0


def test_match_after_several_polls(tmp_path: Path):
    page = _amex_page()
    polls = {"n": 0}

    def poll_fn(_page, terms):
        polls["n"] += 1
        if polls["n"] < 3:
            return {"ok": True, "matched_terms": [], "selected_page_url": PAGE_URL}
        return {
            "ok": True,
            "matched_terms": [terms[0]],
            "selected_page_url": PAGE_URL,
        }

    output = tmp_path / "later.json"
    payload = watch_text_on_page(
        page,
        ["expire"],
        interval_seconds=1,
        timeout_seconds=30,
        stop_after_first_match=True,
        output_file=output,
        sleep_fn=lambda _seconds: None,
        monotonic_fn=_clock([0.0, 1.0, 2.0, 3.0]),
        poll_fn=poll_fn,
        find_text_fn=lambda _page, query: {
            "ok": True,
            "query": query,
            "match_count": 1,
            "matches": [{"matched_text": "expire", "text_snippet": "session expire"}],
        },
        inspect_fn=lambda _page: {"candidates": []},
    )

    assert polls["n"] == 3
    assert payload["matched"] is True
    assert payload["poll_count"] == 3
    assert payload["matched_terms"] == ["expire"]


def test_multiple_configured_terms(tmp_path: Path):
    page = _amex_page()
    session = CdpSessionMock(document=_dialog_tree())
    _bind_cdp(page, session)
    output = tmp_path / "multi.json"

    payload = watch_text_on_page(
        page,
        ["expire", "Continue", "Log Out", "missing-term-xyz"],
        interval_seconds=1,
        timeout_seconds=5,
        stop_after_first_match=True,
        output_file=output,
        sleep_fn=lambda _seconds: None,
        monotonic_fn=_clock([0.0]),
        inspect_fn=lambda _page: {"candidates": []},
    )

    assert payload["matched"] is True
    assert "expire" in payload["matched_terms"]
    assert "Continue" in payload["matched_terms"]
    assert "Log Out" in payload["matched_terms"]
    assert "missing-term-xyz" not in payload["matched_terms"]
    assert set(payload["find_text_results_by_term"]) == {
        "expire",
        "Continue",
        "Log Out",
        "missing-term-xyz",
    }


def test_stop_after_first_match(tmp_path: Path):
    page = _amex_page()
    polls = {"n": 0}

    def poll_fn(_page, _terms):
        polls["n"] += 1
        return {
            "ok": True,
            "matched_terms": ["expire"],
            "selected_page_url": PAGE_URL,
        }

    output = tmp_path / "once.json"
    payload = watch_text_on_page(
        page,
        ["expire"],
        interval_seconds=1,
        timeout_seconds=100,
        stop_after_first_match=True,
        output_file=output,
        sleep_fn=lambda _seconds: None,
        monotonic_fn=_clock([0.0, 1.0, 2.0, 3.0]),
        poll_fn=poll_fn,
        find_text_fn=lambda _page, query: {
            "ok": True,
            "query": query,
            "match_count": 1,
            "matches": [],
        },
        inspect_fn=lambda _page: {"candidates": []},
    )

    assert polls["n"] == 1
    assert payload["poll_count"] == 1
    assert list(tmp_path.glob("*.json")) == [output]


def test_timeout_with_no_match(tmp_path: Path):
    page = _amex_page()
    session = CdpSessionMock(document=_empty_tree(), container_node_ids=[])
    _bind_cdp(page, session)
    output = tmp_path / "timeout.json"
    mono = {"t": 0.0}

    def monotonic() -> float:
        return mono["t"]

    def sleep(seconds: float) -> None:
        mono["t"] += seconds

    payload = watch_text_on_page(
        page,
        ["expire"],
        interval_seconds=1,
        timeout_seconds=3,
        stop_after_first_match=True,
        output_file=output,
        sleep_fn=sleep,
        monotonic_fn=monotonic,
    )

    assert payload["matched"] is False
    assert payload["timed_out"] is True
    assert payload["matched_terms"] == []
    assert payload["browser_inspection"] is None
    assert payload["find_text_results_by_term"] == {}
    assert output.is_file()
    assert payload["poll_count"] >= 2


def test_one_saved_diagnostic_bundle(tmp_path: Path):
    page = _amex_page()
    output = tmp_path / "single.json"
    payload = watch_text_on_page(
        page,
        ["expire"],
        interval_seconds=1,
        timeout_seconds=10,
        stop_after_first_match=True,
        output_file=output,
        sleep_fn=lambda _seconds: None,
        monotonic_fn=_clock([0.0]),
        poll_fn=lambda _page, terms: {
            "ok": True,
            "matched_terms": [terms[0]],
            "selected_page_url": PAGE_URL,
        },
        find_text_fn=lambda _page, query: {
            "ok": True,
            "query": query,
            "match_count": 1,
            "matches": [
                {
                    "matched_text": "expire",
                    "text_snippet": "Your session is about to expire",
                }
            ],
        },
        inspect_fn=lambda _page: {"candidates": [], "selected_page_url": PAGE_URL},
    )

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert files[0] == output
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["matched_terms"] == ["expire"]
    assert saved["output_file"] == str(output)
    assert payload["output_file"] == str(output)


def test_sanitized_output(tmp_path: Path):
    page = _amex_page()
    sensitive = (
        f"{LIVE_AMEX_DIALOG_TEXT} card 4111111111111111 balance 1234567890"
    )
    tree = _document(
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
                        _element(
                            40,
                            40,
                            "DIV",
                            children=[_text_node(41, 41, sensitive)],
                        )
                    ],
                )
            ],
        )
    )
    session = CdpSessionMock(document=tree)
    _bind_cdp(page, session)
    output = tmp_path / "sanitized.json"

    payload = watch_text_on_page(
        page,
        ["expire"],
        interval_seconds=1,
        timeout_seconds=5,
        stop_after_first_match=True,
        output_file=output,
        sleep_fn=lambda _seconds: None,
        monotonic_fn=_clock([0.0]),
        inspect_fn=lambda _page: {
            "candidates": [
                {
                    "text_snippet": redact_long_digit_sequences(sensitive.lower())[:300],
                    "page_url": PAGE_URL,
                }
            ]
        },
    )

    raw = output.read_text(encoding="utf-8")
    assert "4111111111111111" not in raw
    assert "1234567890" not in raw
    assert "password" not in raw.lower()
    assert "cookie" not in raw.lower()
    blob = json.loads(raw)
    for term_payload in blob["find_text_results_by_term"].values():
        for match in term_payload.get("matches") or []:
            snippet = match.get("text_snippet") or ""
            assert len(snippet) <= 300
            assert "4111111111111111" not in snippet
    assert payload["browser_inspection"] is not None


def test_no_evaluate_calls(tmp_path: Path):
    page = _amex_page()
    session = CdpSessionMock(document=_dialog_tree())
    _bind_cdp(page, session)
    page.evaluate = MagicMock(name="evaluate")

    watch_text_on_page(
        page,
        ["expire"],
        interval_seconds=1,
        timeout_seconds=5,
        stop_after_first_match=True,
        output_file=tmp_path / "noeval.json",
        sleep_fn=lambda _seconds: None,
        monotonic_fn=_clock([0.0]),
        inspect_fn=lambda _page: {"candidates": []},
    )

    assert page.evaluate.call_count == 0
    poll = matched_terms_in_page_cdp(page, ["expire"])
    assert poll["ok"] is True
    assert page.evaluate.call_count == 0
    find_text_in_page_cdp(page, "expire")
    assert page.evaluate.call_count == 0


def test_no_page_mutation(tmp_path: Path):
    page = _amex_page()
    page.click = MagicMock(name="click")
    page.mouse = MagicMock(name="mouse")
    page.keyboard = MagicMock(name="keyboard")
    page.fill = MagicMock(name="fill")
    page.type = MagicMock(name="type")
    page.goto = MagicMock(name="goto")
    page.reload = MagicMock(name="reload")

    watch_text_on_page(
        page,
        ["expire"],
        interval_seconds=1,
        timeout_seconds=5,
        stop_after_first_match=True,
        output_file=tmp_path / "nomutate.json",
        sleep_fn=lambda _seconds: None,
        monotonic_fn=_clock([0.0]),
        poll_fn=lambda _page, terms: {
            "ok": True,
            "matched_terms": [terms[0]],
            "selected_page_url": PAGE_URL,
        },
        find_text_fn=lambda _page, query: {
            "ok": True,
            "query": query,
            "match_count": 0,
            "matches": [],
        },
        inspect_fn=lambda _page: {"candidates": []},
    )

    page.click.assert_not_called()
    page.fill.assert_not_called()
    page.type.assert_not_called()
    page.goto.assert_not_called()
    page.reload.assert_not_called()
    page.mouse.click.assert_not_called()
    page.keyboard.press.assert_not_called()


def test_browser_inspection_and_auth_state_included(tmp_path: Path):
    page = _amex_page()
    inspection = {
        "inspected_at": "2026-01-01T00:00:00+00:00",
        "selected_page_url": PAGE_URL,
        "candidate_count": 1,
        "candidates": [{"text_snippet": "your session is about to expire"}],
    }
    output = tmp_path / "inspect.json"
    payload = watch_text_on_page(
        page,
        ["expire"],
        interval_seconds=1,
        timeout_seconds=5,
        stop_after_first_match=True,
        output_file=output,
        canonical_authentication_state="SIGNED_IN",
        canonical_authentication_state_source=AUTH_STATE_SOURCE_LATEST_CANONICAL,
        sleep_fn=lambda _seconds: None,
        monotonic_fn=_clock([0.0]),
        poll_fn=lambda _page, terms: {
            "ok": True,
            "matched_terms": ["expire"],
            "selected_page_url": PAGE_URL,
        },
        find_text_fn=lambda _page, query: {
            "ok": True,
            "query": query,
            "match_count": 1,
            "matches": [],
        },
        inspect_fn=lambda _page: inspection,
    )

    assert payload["browser_inspection"] == inspection
    assert payload["canonical_authentication_state"] == "SIGNED_IN"
    assert (
        payload["canonical_authentication_state_source"]
        == AUTH_STATE_SOURCE_LATEST_CANONICAL
    )
    assert payload["selected_page_url"] == PAGE_URL
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["browser_inspection"] == inspection
    assert saved["canonical_authentication_state"] == "SIGNED_IN"


def test_cdp_errors_do_not_crash_watcher(tmp_path: Path):
    page = _amex_page()
    polls = {"n": 0}

    def poll_fn(_page, terms):
        polls["n"] += 1
        if polls["n"] == 1:
            raise RuntimeError("cdp_boom")
        if polls["n"] == 2:
            return {
                "ok": False,
                "matched_terms": [],
                "error": "DOM.getDocument failed",
                "selected_page_url": PAGE_URL,
            }
        return {
            "ok": True,
            "matched_terms": [terms[0]],
            "selected_page_url": PAGE_URL,
        }

    output = tmp_path / "errors.json"
    payload = watch_text_on_page(
        page,
        ["expire"],
        interval_seconds=1,
        timeout_seconds=30,
        stop_after_first_match=True,
        output_file=output,
        sleep_fn=lambda _seconds: None,
        monotonic_fn=_clock([0.0, 1.0, 2.0, 3.0]),
        poll_fn=poll_fn,
        find_text_fn=lambda _page, query: {
            "ok": True,
            "query": query,
            "match_count": 1,
            "matches": [],
        },
        inspect_fn=lambda _page: {"candidates": []},
    )

    assert payload["matched"] is True
    assert polls["n"] == 3
    assert any("cdp_boom" in err for err in payload["errors"])
    assert any("DOM.getDocument failed" in err for err in payload["errors"])


def test_watch_via_browser_context(tmp_path: Path):
    page = _amex_page()
    context = MagicMock(pages=[page])
    page.context = context
    output = tmp_path / "ctx.json"

    payload = watch_text_in_browser_context(
        context,
        ["expire"],
        provider="amex",
        select_page_fn=lambda _ctx, create_if_missing=False: page,
        interval_seconds=1,
        timeout_seconds=5,
        stop_after_first_match=True,
        output_file=output,
        canonical_authentication_state="SIGNED_IN",
        canonical_authentication_state_source=AUTH_STATE_SOURCE_LATEST_CANONICAL,
        sleep_fn=lambda _seconds: None,
        monotonic_fn=_clock([0.0]),
        poll_fn=lambda _page, terms: {
            "ok": True,
            "matched_terms": ["expire"],
            "selected_page_url": PAGE_URL,
        },
        find_text_fn=lambda _page, query: {
            "ok": True,
            "query": query,
            "match_count": 1,
            "matches": [],
        },
        inspect_fn=lambda _page: {"candidates": []},
    )

    assert payload["matched"] is True
    assert payload["ok"] is True
    assert output.is_file()
