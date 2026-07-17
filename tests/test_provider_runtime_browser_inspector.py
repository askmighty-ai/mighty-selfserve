"""Tests for the CDP-backed Browser Inspector and Amex classifier."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from mighty.provider_runtime import (
    AUTH_STATE_SOURCE_LATEST_CANONICAL,
    AUTH_STATE_SOURCE_NONE,
    BROWSER_INSPECTOR_JS,
    CDP_CONTINUE_TOKEN_PREFIX,
    BrowserInspection,
    EXPIRATION_DIALOG_CONTAINER_SELECTORS,
    InspectionCandidate,
    ProviderRuntime,
    classify_amex_expiration_candidate,
    classify_amex_expiration_from_inspection,
    debug_inspect_browser_context,
    dismiss_amex_expiration_dialog,
    format_browser_inspect_debug_report,
    inspect_amex_page_signals,
    inspect_browser_context,
    inspect_page_browser,
    probe_page_cdp_capabilities,
    redact_long_digit_sequences,
    sanitize_inspection_snippet,
    select_provider_page,
)


LIVE_AMEX_DIALOG_TEXT = (
    "Your session is about to expire. "
    "You will be signed out due to inactivity. "
    "Select Continue to stay signed in."
)

EQUIVALENT_AMEX_DIALOG_TEXT = (
    "Your session will expire soon. "
    "Due to inactivity you may be logged out. "
    "Choose Continue to remain signed in."
)

PAGE_URL = "https://global.americanexpress.com/overview"


def _candidate(**overrides) -> InspectionCandidate:
    base = dict(
        source_type="DOM",
        page_url=PAGE_URL,
        frame_url=PAGE_URL,
        tag_name="div",
        role=None,
        class_summary="sessionTimeoutPanel",
        text_snippet=LIVE_AMEX_DIALOG_TEXT.lower(),
        visible_button_labels=["continue", "log out"],
        visible_link_labels=[],
        detector_tags=["modal_text", "substantial_coverage"],
        fixed_or_absolute=True,
        continue_token=f"{CDP_CONTINUE_TOKEN_PREFIX}55",
    )
    base.update(overrides)
    return InspectionCandidate(**base)


def _text_node(node_id: int, backend: int, value: str) -> dict:
    return {
        "nodeId": node_id,
        "backendNodeId": backend,
        "nodeName": "#text",
        "nodeType": 3,
        "nodeValue": value,
        "children": [],
    }


def _element(
    node_id: int,
    backend: int,
    name: str,
    *,
    attrs: list[str] | None = None,
    children: list[dict] | None = None,
    shadow_roots: list[dict] | None = None,
    content_document: dict | None = None,
    frame_id: str | None = None,
) -> dict:
    node = {
        "nodeId": node_id,
        "backendNodeId": backend,
        "nodeName": name,
        "nodeType": 1,
        "attributes": attrs or [],
        "children": children or [],
    }
    if shadow_roots is not None:
        node["shadowRoots"] = shadow_roots
    if content_document is not None:
        node["contentDocument"] = content_document
    if frame_id is not None:
        node["frameId"] = frame_id
    return node


def _document(root_child: dict, *, node_id: int = 1, backend: int = 1, frame_id: str = "main") -> dict:
    return {
        "nodeId": node_id,
        "backendNodeId": backend,
        "nodeName": "#document",
        "nodeType": 9,
        "frameId": frame_id,
        "children": [root_child],
    }


def _dialog_tree(
    *,
    text: str = LIVE_AMEX_DIALOG_TEXT,
    role: str | None = None,
    aria_modal: str | None = None,
    class_name: str = "axp-session-timeout-panel",
    source: str = "dom",
) -> dict:
    attrs = ["class", class_name]
    if role:
        attrs.extend(["role", role])
    if aria_modal is not None:
        attrs.extend(["aria-modal", aria_modal])

    continue_btn = _element(
        50,
        50,
        "BUTTON",
        attrs=["type", "button"],
        children=[_text_node(51, 51, "Continue")],
    )
    logout_btn = _element(
        60,
        60,
        "BUTTON",
        attrs=["type", "button"],
        children=[_text_node(61, 61, "Log Out")],
    )
    panel = _element(
        40,
        40,
        "DIV",
        attrs=attrs,
        children=[
            _text_node(41, 41, text),
            continue_btn,
            logout_btn,
        ],
    )

    if source == "shadow":
        host = _element(
            30,
            30,
            "SESSION-TIMEOUT-HOST",
            attrs=["class", "timeout"],
            shadow_roots=[
                {
                    "nodeId": 31,
                    "backendNodeId": 31,
                    "nodeName": "#document-fragment",
                    "nodeType": 11,
                    "children": [panel],
                }
            ],
        )
        body = _element(20, 20, "BODY", children=[host])
    elif source == "iframe":
        frame_doc = _document(
            _element(20, 20, "HTML", children=[_element(21, 21, "BODY", children=[panel])]),
            node_id=19,
            backend=19,
            frame_id="child",
        )
        iframe = _element(
            30,
            30,
            "IFRAME",
            content_document=frame_doc,
            frame_id="child",
        )
        body = _element(20, 20, "BODY", children=[iframe])
    else:
        body = _element(20, 20, "BODY", children=[panel])

    html = _element(10, 10, "HTML", children=[body])
    return _document(html)


def _style_entries(
    *,
    position: str = "fixed",
    z_index: str = "1000",
    display: str = "block",
    visibility: str = "visible",
    opacity: str = "1",
) -> list[dict[str, str]]:
    return [
        {"name": "display", "value": display},
        {"name": "visibility", "value": visibility},
        {"name": "opacity", "value": opacity},
        {"name": "position", "value": position},
        {"name": "z-index", "value": z_index},
    ]


def _box_model(x: float = 10, y: float = 10, w: float = 400, h: float = 200) -> dict:
    return {
        "model": {
            "content": [x, y, x + w, y, x + w, y + h, x, y + h],
        }
    }


class CdpSessionMock:
    """Configurable CDP session.send mock for Browser Inspector tests."""

    def __init__(
        self,
        *,
        document: dict,
        ax_nodes: list[dict] | None = None,
        frame_tree: dict | None = None,
        style_by_node: dict[int, list[dict[str, str]]] | None = None,
        box_by_backend: dict[int, dict] | None = None,
        fail_methods: dict[str, Exception] | None = None,
        container_node_ids: list[int] | None = None,
        action_node_ids_by_parent: dict[int, list[int]] | None = None,
    ) -> None:
        self.document = document
        self.ax_nodes = ax_nodes or []
        self.frame_tree = frame_tree or {
            "frameTree": {
                "frame": {"id": "main", "url": PAGE_URL},
                "childFrames": [],
            }
        }
        self.style_by_node = style_by_node or {}
        self.box_by_backend = box_by_backend or {}
        self.fail_methods = fail_methods or {}
        self.container_node_ids = container_node_ids
        self.action_node_ids_by_parent = action_node_ids_by_parent or {}
        self.calls: list[tuple[str, dict | None]] = []
        self._node_index = {}
        self._index_tree(document)

    def _index_tree(self, node: dict) -> None:
        node_id = node.get("nodeId")
        if node_id is not None:
            self._node_index[int(node_id)] = node
        for child in node.get("children") or []:
            self._index_tree(child)
        for shadow in node.get("shadowRoots") or []:
            self._index_tree(shadow)
        content = node.get("contentDocument")
        if isinstance(content, dict):
            self._index_tree(content)

    def _descendant_ids(self, root_id: int, *, cross_shadow: bool = False, cross_iframe: bool = False) -> set[int]:
        root = self._node_index.get(root_id)
        if root is None:
            return set()
        found: set[int] = set()

        def walk(node: dict) -> None:
            node_id = node.get("nodeId")
            if node_id is not None:
                found.add(int(node_id))
            for child in node.get("children") or []:
                walk(child)
            if cross_shadow:
                for shadow in node.get("shadowRoots") or []:
                    walk(shadow)
            if cross_iframe:
                content = node.get("contentDocument")
                if isinstance(content, dict):
                    walk(content)

        walk(root)
        return found

    def _default_container_ids(self, *, within: set[int] | None = None) -> list[int]:
        ids: list[int] = []
        for node in self._node_index.values():
            node_id = int(node["nodeId"])
            if within is not None and node_id not in within:
                continue
            attrs = node.get("attributes") or []
            attr_map = {attrs[i]: attrs[i + 1] for i in range(0, len(attrs) - 1, 2)}
            class_name = attr_map.get("class", "")
            role = attr_map.get("role")
            aria_modal = attr_map.get("aria-modal")
            name = str(node.get("nodeName") or "").lower()
            if (
                role == "dialog"
                or aria_modal == "true"
                or name == "dialog"
                or any(
                    token in class_name.lower()
                    for token in (
                        "modal",
                        "dialog",
                        "overlay",
                        "drawer",
                        "popover",
                        "popup",
                        "timeout",
                        "session",
                        "expire",
                    )
                )
            ):
                ids.append(node_id)
        return ids

    def send(self, method: str, params: dict | None = None):
        params = params or {}
        self.calls.append((method, params))
        if method in self.fail_methods:
            raise self.fail_methods[method]
        if method in {"DOM.enable", "CSS.enable", "Accessibility.enable"}:
            return {}
        if method == "Page.getFrameTree":
            return self.frame_tree
        if method == "DOM.getDocument":
            return {"root": self.document}
        if method == "Accessibility.getFullAXTree":
            return {"nodes": self.ax_nodes}
        if method == "Page.getLayoutMetrics":
            return {"cssVisualViewport": {"clientWidth": 1200, "clientHeight": 800}}
        if method == "DOM.querySelectorAll":
            selector = params.get("selector") or ""
            parent_id = int(params.get("nodeId"))
            # Mirror browser semantics: querySelectorAll does not cross shadow/iframe.
            within = self._descendant_ids(parent_id, cross_shadow=False, cross_iframe=False)
            if "button" in selector or 'role="button"' in selector:
                if parent_id in self.action_node_ids_by_parent:
                    return {"nodeIds": self.action_node_ids_by_parent[parent_id]}
                ids = [
                    node_id
                    for node_id in within
                    if str((self._node_index.get(node_id) or {}).get("nodeName") or "").lower()
                    in {"button", "a"}
                ]
                return {"nodeIds": ids}
            if selector.startswith("div, section"):
                ids = [
                    node_id
                    for node_id in within
                    if str((self._node_index.get(node_id) or {}).get("nodeName") or "").lower()
                    in {"div", "section", "aside", "article"}
                ]
                return {"nodeIds": ids[:120]}
            if self.container_node_ids is not None:
                return {
                    "nodeIds": [node_id for node_id in self.container_node_ids if node_id in within]
                }
            return {"nodeIds": self._default_container_ids(within=within)}
        if method == "DOM.getAttributes":
            node = self._node_index.get(int(params.get("nodeId")))
            return {"attributes": list((node or {}).get("attributes") or [])}
        if method == "CSS.getComputedStyleForNode":
            node_id = int(params.get("nodeId"))
            return {
                "computedStyle": self.style_by_node.get(node_id, _style_entries())
            }
        if method == "DOM.getBoxModel":
            backend = int(params.get("backendNodeId"))
            if backend in self.box_by_backend:
                return self.box_by_backend[backend]
            return _box_model()
        if method == "DOM.describeNode":
            if "nodeId" in params:
                node = self._node_index.get(int(params["nodeId"]))
                return {"node": node or {}}
            backend = int(params.get("backendNodeId"))
            for node in self._node_index.values():
                if int(node.get("backendNodeId") or -1) == backend:
                    return {"node": node}
            return {"node": {}}
        if method == "DOM.scrollIntoViewIfNeeded":
            return {}
        if method == "Input.dispatchMouseEvent":
            return {}
        if method == "DOM.resolveNode":
            return {"object": {"objectId": "obj-1"}}
        if method == "DOM.requestNode":
            return {"nodeId": 40}
        return {}

    def detach(self) -> None:
        return None


def _bind_cdp(page: MagicMock, session: CdpSessionMock) -> None:
    context = MagicMock()
    context.new_cdp_session.return_value = session
    page.context = context
    page.viewport_size = {"width": 1200, "height": 800}


def _amex_page(*, frames=None, url: str = PAGE_URL) -> MagicMock:
    page = MagicMock()
    page.url = url
    page.is_closed.return_value = False
    page.main_frame = page
    page.frames = frames if frames is not None else [page]
    page.parent_frame = None
    page.is_detached.return_value = False
    return page


def test_cdp_capability_probe_reports_each_operation():
    page = _amex_page()
    session = CdpSessionMock(document=_dialog_tree())
    _bind_cdp(page, session)

    result = probe_page_cdp_capabilities(page, stop_on_first_failure=False)
    assert result["ok"] is True
    names = [item["probe"] for item in result["probes"]]
    assert names == [
        "Page.getFrameTree",
        "DOM.enable",
        "CSS.enable",
        "Accessibility.enable",
        "DOM.getDocument",
        "Accessibility.getFullAXTree",
    ]
    assert all(item["ok"] for item in result["probes"])
    assert result["probes"][4]["summary"]["root_node_id"] == 1


def test_cdp_capability_probe_stops_on_failure():
    page = _amex_page()
    session = CdpSessionMock(
        document=_dialog_tree(),
        fail_methods={"DOM.getDocument": RuntimeError("pierced document blocked")},
    )
    _bind_cdp(page, session)
    result = probe_page_cdp_capabilities(page, stop_on_first_failure=True)
    assert result["ok"] is False
    assert result["stopped_early"] is True
    assert result["first_failure"]["cdp_method"] == "DOM.getDocument"
    assert result["first_failure"]["exception_class"] == "RuntimeError"


def test_modal_without_role_dialog_is_detected():
    joined = ",".join(EXPIRATION_DIALOG_CONTAINER_SELECTORS)
    assert '[role="dialog"]' in joined
    assert '[class*="modal"]' in joined or ".modal" in joined
    assert "retired" in BROWSER_INSPECTOR_JS

    page = _amex_page()
    session = CdpSessionMock(document=_dialog_tree(role=None))
    _bind_cdp(page, session)

    info_candidates, _, _, _ = inspect_page_browser(page, mark_continue=True)
    inspection = BrowserInspection(
        inspected_at="t",
        selected_page_url=page.url,
        page_count=1,
        frame_count=1,
        candidate_count=len(info_candidates),
        candidates=info_candidates,
    )
    classified = classify_amex_expiration_from_inspection(inspection)
    assert classified["detected"] is True
    assert classified["candidate"].role is None
    assert page.evaluate.call_count == 0


def test_ax_dialog_candidate():
    page = _amex_page()
    tree = _dialog_tree(class_name="plain-panel")
    # Remove class tokens that would match CSS selectors.
    panel = tree["children"][0]["children"][0]["children"][0]
    panel["attributes"] = []
    session = CdpSessionMock(
        document=tree,
        container_node_ids=[],
        ax_nodes=[
            {
                "backendDOMNodeId": 40,
                "role": {"value": "dialog"},
                "name": {"value": "session timeout"},
                "ignored": False,
            }
        ],
        action_node_ids_by_parent={40: [50, 60]},
    )
    # Ensure describe/index can still find panel text/buttons.
    _bind_cdp(page, session)
    candidates, _, _, _ = inspect_page_browser(page, mark_continue=True)
    assert candidates
    assert (
        "ax_dialog" in candidates[0].detector_tags
        or "role_dialog" in candidates[0].detector_tags
    )
    classified = classify_amex_expiration_from_inspection(
        BrowserInspection(
            inspected_at="t",
            selected_page_url=page.url,
            page_count=1,
            frame_count=1,
            candidate_count=len(candidates),
            candidates=candidates,
        )
    )
    assert classified["detected"] is True


def test_role_dialog_candidate():
    conditions = classify_amex_expiration_candidate(
        _candidate(role="dialog", detector_tags=["role_dialog", "modal_text"])
    )
    assert conditions["classified_as_expiration_dialog"] is True


def test_aria_modal_candidate():
    page = _amex_page()
    session = CdpSessionMock(document=_dialog_tree(aria_modal="true", class_name="panel"))
    _bind_cdp(page, session)
    candidates, _, _, _ = inspect_page_browser(page)
    assert candidates
    assert candidates[0].aria_modal is True
    conditions = classify_amex_expiration_candidate(candidates[0])
    assert conditions["classified_as_expiration_dialog"] is True


def test_modal_in_iframe_and_nested_iframe():
    main = MagicMock(name="main")
    main.url = PAGE_URL
    main.is_detached.return_value = False
    main.parent_frame = None

    nested = MagicMock(name="nested")
    nested.url = "https://functions.americanexpress.com/session-timeout"
    nested.is_detached.return_value = False
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

    candidates, frame_count, errors, frame_diagnostics = inspect_page_browser(
        page, mark_continue=True
    )
    assert frame_count >= 2
    assert not any("inaccessible" in err for err in errors)
    assert any(item.source_type == "IFRAME" for item in candidates)
    classified = classify_amex_expiration_from_inspection(
        BrowserInspection(
            inspected_at="t",
            selected_page_url=page.url,
            page_count=1,
            frame_count=frame_count,
            candidate_count=len(candidates),
            candidates=candidates,
        )
    )
    assert classified["detected"] is True


def test_modal_in_open_shadow_root():
    page = _amex_page()
    session = CdpSessionMock(document=_dialog_tree(source="shadow"))
    _bind_cdp(page, session)
    candidates, _, _, _ = inspect_page_browser(page)
    assert candidates[0].source_type == "SHADOW_DOM"
    assert candidates[0].host_tag_class_summary is not None


def test_inaccessible_cross_origin_frame_is_sanitized():
    main = MagicMock(name="main")
    main.url = PAGE_URL
    main.is_detached.return_value = False
    main.parent_frame = None

    blocked = MagicMock(name="blocked")
    blocked.url = "https://other-bank.example/challenge"
    blocked.is_detached.return_value = False
    blocked.parent_frame = main

    page = _amex_page(frames=[main, blocked])
    page.main_frame = main
    # Pierced DOM has no contentDocument for the foreign frame.
    session = CdpSessionMock(
        document=_document(_element(10, 10, "HTML", children=[_element(20, 20, "BODY")])),
        container_node_ids=[],
    )
    _bind_cdp(page, session)

    candidates, _, errors, frame_diagnostics = inspect_page_browser(page)
    assert any("inaccessible_frame" in err for err in errors)
    assert any("frame_inaccessible" in (item.errors or []) for item in candidates)
    assert any("inaccessible_frame" in (item.detector_tags or []) for item in candidates)
    assert len(frame_diagnostics) >= 1
    diag = next(
        item
        for item in frame_diagnostics
        if item.get("frame_url") == "https://other-bank.example/challenge"
    )
    assert diag["is_main_frame"] is False
    assert diag["parent_frame_url"] == PAGE_URL
    assert diag["cdp_method"] == "DOM.getDocument"
    assert diag["failure_phase"] == "frame_document"
    assert diag["failure_scope"] == "entire_frame"
    assert diag["appears_cross_origin"] is True
    assert diag["exception_class"] == "RuntimeError"

    inspection = inspect_browser_context(
        MagicMock(pages=[page]),
        provider="amex",
        select_page_fn=lambda context, create_if_missing=False: page,
    )
    sanitized = inspection.to_sanitized_dict()
    assert sanitized["candidates"][0]["errors"] == ["frame_inaccessible"]
    assert "developer_diagnostics" in sanitized
    assert sanitized["developer_diagnostics"]["inaccessible_frame_count"] >= 1


def test_style_retrieval_failure_does_not_fail_inspection():
    page = _amex_page()
    session = CdpSessionMock(
        document=_dialog_tree(),
        fail_methods={"CSS.getComputedStyleForNode": RuntimeError("style blocked")},
    )
    _bind_cdp(page, session)
    candidates, _, errors, diagnostics = inspect_page_browser(page)
    # Without style/geometry the dialog may not qualify; inspection must not raise.
    assert isinstance(candidates, list)
    assert any(item.get("cdp_method") == "CSS.getComputedStyleForNode" for item in diagnostics)
    assert page.evaluate.call_count == 0


def test_geometry_retrieval_failure_does_not_fail_inspection():
    page = _amex_page()
    session = CdpSessionMock(
        document=_dialog_tree(),
        fail_methods={"DOM.getBoxModel": RuntimeError("box blocked")},
    )
    _bind_cdp(page, session)
    candidates, _, _, diagnostics = inspect_page_browser(page)
    assert isinstance(candidates, list)
    assert any(item.get("cdp_method") == "DOM.getBoxModel" for item in diagnostics)


def test_nested_candidate_deduplication_prefers_outer():
    outer = _element(
        40,
        40,
        "DIV",
        attrs=["class", "sessionTimeoutPanel", "aria-modal", "true"],
        children=[
            _text_node(41, 41, LIVE_AMEX_DIALOG_TEXT),
            _element(
                42,
                42,
                "DIV",
                attrs=["class", "modal-inner session"],
                children=[
                    _text_node(43, 43, LIVE_AMEX_DIALOG_TEXT),
                    _element(
                        50,
                        50,
                        "BUTTON",
                        children=[_text_node(51, 51, "Continue")],
                    ),
                    _element(
                        60,
                        60,
                        "BUTTON",
                        children=[_text_node(61, 61, "Log Out")],
                    ),
                ],
            ),
        ],
    )
    tree = _document(_element(10, 10, "HTML", children=[_element(20, 20, "BODY", children=[outer])]))
    page = _amex_page()
    session = CdpSessionMock(
        document=tree,
        container_node_ids=[40, 42],
        action_node_ids_by_parent={40: [50, 60], 42: [50, 60]},
    )
    _bind_cdp(page, session)
    candidates, _, _, _ = inspect_page_browser(page)
    dialog_candidates = [item for item in candidates if "inaccessible" not in item.detector_tags]
    assert len(dialog_candidates) == 1
    assert dialog_candidates[0].class_summary == "sessionTimeoutPanel"


def test_exact_and_equivalent_live_amex_wording():
    exact = classify_amex_expiration_candidate(_candidate())
    assert exact["headline_match"] is True
    assert exact["expiration_language_match"] is True
    assert exact["continue_action_match"] is True
    assert exact["logout_action_match"] is True
    assert exact["classified_as_expiration_dialog"] is True

    equivalent = classify_amex_expiration_candidate(
        _candidate(
            text_snippet=EQUIVALENT_AMEX_DIALOG_TEXT.lower(),
            visible_button_labels=["continue"],
        )
    )
    assert equivalent["classified_as_expiration_dialog"] is True


def test_unrelated_continue_button_ignored():
    conditions = classify_amex_expiration_candidate(
        _candidate(
            text_snippet="continue to view your card benefits and offers.",
            visible_button_labels=["continue"],
        )
    )
    assert conditions["headline_match"] is False
    assert conditions["classified_as_expiration_dialog"] is False


def test_text_length_bound_and_number_redaction():
    long_text = ("session expire continue log out " + ("x" * 500))
    snippet = sanitize_inspection_snippet(long_text)
    assert snippet is not None
    assert len(snippet) <= 300

    redacted = redact_long_digit_sequences("card 1234567890123456 session expire")
    assert "[REDACTED_NUMBER]" in redacted
    assert "1234567890123456" not in redacted
    assert sanitize_inspection_snippet("card 999999 session expire continue") is not None
    assert "[REDACTED_NUMBER]" in (
        sanitize_inspection_snippet("session 123456789 expire continue log out") or ""
    )


def test_screenshot_disabled_by_default(tmp_path: Path):
    context = MagicMock()
    page = _amex_page()
    session = CdpSessionMock(
        document=_document(_element(10, 10, "HTML", children=[_element(20, 20, "BODY")])),
        container_node_ids=[],
    )
    _bind_cdp(page, session)
    context.pages = [page]

    inspection = inspect_browser_context(
        context,
        provider="amex",
        capture_screenshot=False,
        diagnostics_dir=tmp_path / "diagnostics",
    )
    assert inspection.screenshot_path is None
    page.screenshot.assert_not_called()


def test_screenshot_path_only_when_enabled(tmp_path: Path):
    context = MagicMock()
    page = _amex_page()
    session = CdpSessionMock(
        document=_document(_element(10, 10, "HTML", children=[_element(20, 20, "BODY")])),
        container_node_ids=[],
    )
    _bind_cdp(page, session)
    context.pages = [page]
    diagnostics_dir = tmp_path / "diagnostics"

    def fake_screenshot(*, path: str, full_page: bool = False) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"fake")

    page.screenshot.side_effect = fake_screenshot

    inspection = inspect_browser_context(
        context,
        provider="amex",
        capture_screenshot=True,
        diagnostics_dir=diagnostics_dir,
    )
    assert inspection.screenshot_path is not None
    assert inspection.screenshot_path.startswith(str(diagnostics_dir))
    assert "amex_browser_inspection_" in inspection.screenshot_path
    serialized = json.dumps(inspection.to_sanitized_dict())
    assert "fake" not in serialized


def test_keepalive_uses_latest_canonical_not_login_unknown():
    page = _amex_page()
    session = CdpSessionMock(
        document=_document(_element(10, 10, "HTML", children=[_element(20, 20, "BODY")])),
        container_node_ids=[],
    )
    _bind_cdp(page, session)
    page.locator.return_value.inner_text.return_value = "Account Home"

    signals = inspect_amex_page_signals(
        page,
        latest_canonical_state="SIGNED_IN",
    )
    assert signals["authentication_state"] == "SIGNED_IN"
    assert (
        signals["inspection_authentication_state_source"]
        == AUTH_STATE_SOURCE_LATEST_CANONICAL
    )
    assert signals["authentication_state"] != "LOGIN_UNKNOWN"

    none_signals = inspect_amex_page_signals(page)
    assert none_signals["authentication_state"] is None
    assert none_signals["inspection_authentication_state_source"] == AUTH_STATE_SOURCE_NONE


def test_maintenance_clicks_continue_inside_classified_candidate_only():
    page = _amex_page()
    session = CdpSessionMock(document=_dialog_tree())
    empty = CdpSessionMock(
        document=_document(_element(10, 10, "HTML", children=[_element(20, 20, "BODY")])),
        container_node_ids=[],
    )
    phase = {"n": 0}

    def next_session(_page):
        phase["n"] += 1
        # inspect + CDP click share the dialog session; later polls see empty DOM.
        return session if phase["n"] <= 2 else empty

    page.context = MagicMock()
    page.context.new_cdp_session.side_effect = next_session
    page.viewport_size = {"width": 1200, "height": 800}
    page.wait_for_timeout.return_value = None

    early = dismiss_amex_expiration_dialog(page)
    assert early is None
    click_calls = [call for call in session.calls if call[0] == "Input.dispatchMouseEvent"]
    assert len(click_calls) == 2
    assert page.evaluate.call_count == 0
    page.locator.assert_not_called()


def test_no_production_inspector_path_uses_evaluate():
    page = _amex_page()
    session = CdpSessionMock(document=_dialog_tree())
    _bind_cdp(page, session)
    inspect_page_browser(page, mark_continue=True)
    inspect_browser_context(
        MagicMock(pages=[page]),
        provider="amex",
        select_page_fn=lambda context, create_if_missing=False: page,
    )
    debug_inspect_browser_context(
        MagicMock(pages=[page]),
        provider="amex",
        select_page_fn=lambda context, create_if_missing=False: page,
    )
    assert page.evaluate.call_count == 0


def test_no_sensitive_data_in_serialized_inspection():
    inspection = BrowserInspection(
        inspected_at="2026-01-01T00:00:00+00:00",
        selected_page_url=PAGE_URL,
        page_count=1,
        frame_count=1,
        candidate_count=1,
        candidates=[
            _candidate(
                text_snippet=sanitize_inspection_snippet(
                    "your session is about to expire card 4111111111111111 "
                    "continue log out"
                ),
                page_url="https://global.americanexpress.com/overview?account=secret",
                frame_url="https://global.americanexpress.com/overview?token=abc",
            )
        ],
        errors=[],
        screenshot_path="/tmp/amex_browser_inspection.png",
    )
    payload = inspection.to_sanitized_dict()
    payload["candidates"][0]["page_url"] = PAGE_URL
    payload["candidates"][0]["frame_url"] = PAGE_URL
    serialized = json.dumps(payload)
    assert "4111111111111111" not in serialized
    assert "account=secret" not in serialized
    assert "token=abc" not in serialized
    assert "<html" not in serialized
    assert "password" not in serialized
    assert len(payload["candidates"][0]["text_snippet"] or "") <= 300


def test_select_provider_page_prefers_global_and_ignores_noise():
    context = MagicMock()
    ignored = MagicMock()
    ignored.url = "chrome-extension://abc/popup.html"
    ignored.is_closed.return_value = False
    blank = MagicMock()
    blank.url = "about:blank"
    blank.is_closed.return_value = False
    login = MagicMock()
    login.url = "https://www.americanexpress.com/en-us/account/login"
    login.is_closed.return_value = False
    login.viewport_size = {"width": 800, "height": 600}
    global_page = MagicMock()
    global_page.url = PAGE_URL
    global_page.is_closed.return_value = False
    global_page.viewport_size = {"width": 1200, "height": 900}
    context.pages = [ignored, blank, login, global_page]

    selected = select_provider_page(
        context,
        hostname_suffixes=("americanexpress.com",),
        preferred_hostnames=("global.americanexpress.com",),
        deprioritize_login=True,
    )
    assert selected is global_page


def test_runtime_persists_latest_inspection_without_bytes(tmp_path: Path):
    runtime = ProviderRuntime(
        root=tmp_path,
        cdp_port=9333,
        state_path=tmp_path / "state.json",
        result_path=tmp_path / "result.json",
    )
    runtime.cdp_url = "http://127.0.0.1:9333"
    page = _amex_page()
    session = CdpSessionMock(document=_dialog_tree())
    _bind_cdp(page, session)
    browser = MagicMock()
    browser.contexts = [MagicMock(pages=[page])]
    cm = MagicMock()
    cm.__enter__.return_value = MagicMock(
        chromium=MagicMock(connect_over_cdp=MagicMock(return_value=browser))
    )
    cm.__exit__.return_value = None

    with patch("mighty.provider_runtime.sync_playwright", return_value=cm):
        payload = runtime.inspect_browser("amex", capture_screenshot=False)

    assert payload["ok"] is True
    assert payload["screenshot_path"] is None
    assert "developer_diagnostics" in payload
    latest = runtime.latest_browser_inspection("amex")
    assert latest["ok"] is True
    assert "candidates" in latest
    assert latest.get("screenshot_path") is None


def test_browser_inspect_debug_stops_on_first_probe_failure():
    page = _amex_page()
    session = CdpSessionMock(
        document=_dialog_tree(),
        fail_methods={"Accessibility.enable": RuntimeError("ax enable blocked")},
    )
    _bind_cdp(page, session)
    context = MagicMock(pages=[page])

    payload = debug_inspect_browser_context(
        context,
        provider="amex",
        select_page_fn=lambda ctx, create_if_missing=False: page,
    )
    assert payload["ok"] is False
    assert payload["stopped_early"] is True
    probes = payload["cdp_probes"]
    assert [item["probe"] for item in probes] == [
        "Page.getFrameTree",
        "DOM.enable",
        "CSS.enable",
        "Accessibility.enable",
    ]
    assert probes[-1]["ok"] is False
    assert probes[-1]["exception_class"] == "RuntimeError"
    assert probes[-1]["traceback"]
    failure = payload["first_failure"]
    assert failure["probe"] == "Accessibility.enable"
    assert failure["cdp_method"] == "Accessibility.enable"

    report = format_browser_inspect_debug_report(payload)
    assert "=== Pages (1) ===" in report
    assert "PROBE Page.getFrameTree: OK" in report
    assert "PROBE Accessibility.enable: FAIL" in report
    assert "FIRST FAILURE (stopped)" in report
    assert "RuntimeError" in report
    assert page.evaluate.call_count == 0


def test_browser_inspect_debug_all_probes_succeed():
    page = _amex_page()
    session = CdpSessionMock(document=_dialog_tree())
    _bind_cdp(page, session)
    context = MagicMock(pages=[page])
    payload = debug_inspect_browser_context(
        context,
        provider="amex",
        select_page_fn=lambda ctx, create_if_missing=False: page,
    )
    assert payload["ok"] is True
    assert payload["first_failure"] is None
    assert payload["stopped_early"] is False
    assert all(probe["ok"] for probe in payload["cdp_probes"])
    assert page.evaluate.call_count == 0
