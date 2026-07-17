"""Tests for the developer-only CDP DOM text explorer."""

from __future__ import annotations

from unittest.mock import MagicMock

from mighty.provider_runtime import (
    find_text_in_browser_context,
    find_text_in_page_cdp,
    format_browser_find_text_report,
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


def test_exact_match():
    page = _amex_page()
    session = CdpSessionMock(document=_dialog_tree())
    _bind_cdp(page, session)

    payload = find_text_in_page_cdp(page, LIVE_AMEX_DIALOG_TEXT)
    assert payload["ok"] is True
    assert payload["match_count"] >= 1
    assert any(match.get("exact_match") for match in payload["matches"])
    assert page.evaluate.call_count == 0


def test_substring_match():
    page = _amex_page()
    session = CdpSessionMock(document=_dialog_tree())
    _bind_cdp(page, session)

    payload = find_text_in_page_cdp(page, "expire")
    assert payload["ok"] is True
    assert payload["match_count"] >= 1
    match = payload["matches"][0]
    assert "expire" in (match.get("matched_text") or "")
    assert match.get("backend_node_id") is not None
    assert match.get("tag_name")
    assert isinstance(match.get("parent_chain"), list)


def test_multiple_matches_sorted():
    page = _amex_page()
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
                            30,
                            30,
                            "DIV",
                            attrs=["class", "header"],
                            children=[_text_node(31, 31, "Continue browsing offers")],
                        ),
                        _element(
                            40,
                            40,
                            "BUTTON",
                            attrs=["aria-label", "Continue"],
                            children=[_text_node(41, 41, "Continue")],
                        ),
                        _element(
                            50,
                            50,
                            "SPAN",
                            children=[_text_node(51, 51, "please continue later")],
                        ),
                    ],
                )
            ],
        )
    )
    session = CdpSessionMock(
        document=tree,
        ax_nodes=[
            {
                "backendDOMNodeId": 40,
                "role": {"value": "button"},
                "name": {"value": "Continue"},
                "ignored": False,
            }
        ],
    )
    _bind_cdp(page, session)

    payload = find_text_in_page_cdp(page, "Continue")
    assert payload["match_count"] >= 2
    first = payload["matches"][0]
    # Exact / AX-name matches should rank ahead of longer substring text.
    assert first.get("exact_match") is True or first.get("match_source") == "ax_name"


def test_ax_name_only_match():
    page = _amex_page()
    # Element has no text node; only an AX name carries the needle.
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
                            attrs=["role", "dialog", "class", "timeout-host"],
                            children=[],
                        )
                    ],
                )
            ],
        )
    )
    session = CdpSessionMock(
        document=tree,
        ax_nodes=[
            {
                "backendDOMNodeId": 40,
                "role": {"value": "dialog"},
                "name": {"value": "Your session is about to expire"},
                "ignored": False,
            }
        ],
    )
    _bind_cdp(page, session)

    payload = find_text_in_page_cdp(page, "expire")
    assert payload["ok"] is True
    assert payload["match_count"] >= 1
    assert any(match.get("match_source") == "ax_name" for match in payload["matches"])
    assert any(
        "expire" in (match.get("matched_text") or "") for match in payload["matches"]
    )


def test_iframe_match():
    main = MagicMock(name="main")
    main.url = PAGE_URL
    main.parent_frame = None
    nested = MagicMock(name="nested")
    nested.url = "https://functions.americanexpress.com/session-timeout"
    nested.parent_frame = main

    page = _amex_page(frames=[main, nested])
    page.main_frame = main
    session = CdpSessionMock(
        document=_dialog_tree(source="iframe"),
        frame_tree={
            "frameTree": {
                "frame": {"id": "main", "url": PAGE_URL},
                "childFrames": [
                    {
                        "frame": {
                            "id": "child",
                            "url": "https://functions.americanexpress.com/session-timeout",
                        },
                        "childFrames": [],
                    }
                ],
            }
        },
    )
    _bind_cdp(page, session)

    payload = find_text_in_page_cdp(page, "expire")
    assert payload["ok"] is True
    assert payload["match_count"] >= 1
    assert any(int(match.get("iframe_depth") or 0) >= 1 for match in payload["matches"])


def test_shadow_root_match():
    page = _amex_page()
    session = CdpSessionMock(document=_dialog_tree(source="shadow"))
    _bind_cdp(page, session)

    payload = find_text_in_page_cdp(page, "expire")
    assert payload["ok"] is True
    assert payload["match_count"] >= 1
    assert any(int(match.get("shadow_root_depth") or 0) >= 1 for match in payload["matches"])


def test_no_matches():
    page = _amex_page()
    session = CdpSessionMock(
        document=_document(
            _element(10, 10, "HTML", children=[_element(20, 20, "BODY")])
        ),
        container_node_ids=[],
    )
    _bind_cdp(page, session)

    payload = find_text_in_page_cdp(page, "expire")
    assert payload["ok"] is True
    assert payload["match_count"] == 0
    assert payload["matches"] == []
    report = format_browser_find_text_report(payload)
    assert "NO MATCHES" in report


def test_find_text_via_browser_context_and_report_shape():
    page = _amex_page()
    session = CdpSessionMock(document=_dialog_tree())
    _bind_cdp(page, session)
    context = MagicMock(pages=[page])

    payload = find_text_in_browser_context(
        context,
        "Log Out",
        provider="amex",
        select_page_fn=lambda ctx, create_if_missing=False: page,
    )
    assert payload["ok"] is True
    assert payload["match_count"] >= 1
    match = payload["matches"][0]
    for key in (
        "frame_url",
        "backend_node_id",
        "tag_name",
        "text_snippet",
        "matched_text",
        "parent_chain",
        "shadow_root_depth",
        "iframe_depth",
        "attributes",
        "button_descendants",
        "link_descendants",
    ):
        assert key in match
    report = format_browser_find_text_report(payload)
    assert "MATCH 1" in report
    assert "backend_node_id:" in report
    assert page.evaluate.call_count == 0
