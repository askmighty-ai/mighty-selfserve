"""Tests for minimal background-only Chrome extension popup."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTENSION_DIR = REPO_ROOT / "extension"
POPUP_HTML = EXTENSION_DIR / "popup.html"
POPUP_JS = EXTENSION_DIR / "popup.js"


def _extension_js_files() -> list[Path]:
    return sorted(EXTENSION_DIR.rglob("*.js"))


def test_extension_popup_does_not_render_per_account_rows():
    js = POPUP_JS.read_text()
    html = POPUP_HTML.read_text()
    assert "needs sign in" not in html.lower()
    assert "needs login" not in html.lower()
    assert "account-row" not in html.lower()
    # Background-only popup must not drive per-account access-loop UI.
    assert "account-row" not in js
    assert "progress-wrap" not in html


def test_extension_popup_background_copy():
    html = POPUP_HTML.read_text()
    js = POPUP_JS.read_text()
    assert "Working in the background" in html
    assert "Open Account Center" in html
    assert "Working in the background" in js
    assert "Open Account Center" in js
    assert "Keeping your accounts up to date" in html
    assert "status_keeping_updated" in js


def test_popup_js_has_single_cta_label_declaration():
    """Regression: duplicate const ctaLabel breaks Chrome popup parse."""
    js = POPUP_JS.read_text()
    decls = re.findall(r"^\s*const\s+ctaLabel\b", js, flags=re.MULTILINE)
    assert len(decls) == 1, f"expected one ctaLabel declaration, found {len(decls)}"
    assert "loop.open_account_center" in js or "loop && loop.open_account_center" in js
    assert "worker.open_account_center" in js
    assert "/account-center" in js


def test_popup_js_parses_successfully():
    """Chrome loads popup.js as a classic script; node --check must pass."""
    result = subprocess.run(
        ["node", "--check", str(POPUP_JS)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_all_extension_javascript_parses():
    """node --check every extension JS file so SyntaxError cannot ship again."""
    files = _extension_js_files()
    assert files, "expected extension JS files"
    failures: list[str] = []
    for path in files:
        result = subprocess.run(
            ["node", "--check", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            failures.append(f"{path.relative_to(REPO_ROOT)}:\n{result.stderr.strip()}")
    assert not failures, "extension JS syntax check failed:\n\n" + "\n\n".join(failures)
