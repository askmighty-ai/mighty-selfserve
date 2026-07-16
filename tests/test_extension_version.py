"""Tests for extension version reporting, storage, and comparison."""

from __future__ import annotations

import html
import json
import os
import re
import sys
from pathlib import Path

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.extension_version import (
    compare_chrome_versions,
    extension_update_required,
    get_extension_version_status,
    read_expected_extension_version,
    record_extension_version,
    should_accept_extension_report,
)
from mighty.home_ui import render_capability_panel, _render_extension_diagnostics
from mighty.capability_state import build_capability_view

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "extension" / "manifest.json"
BACKGROUND = ROOT / "extension" / "background.js"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_extension_version.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()
    mighty.app.config["TESTING"] = True
    c = mighty.app.test_client()
    c.get("/signup")
    with c.session_transaction() as sess:
        csrf = sess["_csrf"]
        email = f"extver_{os.urandom(4).hex()}@test.local"
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    return c, mighty


def _uid(client):
    with client.session_transaction() as sess:
        return sess["user_id"]


def _api_key(mighty, uid):
    with mighty.app.app_context():
        row = mighty.get_db().execute(
            "SELECT api_key FROM users WHERE id=?", (uid,)
        ).fetchone()
        return row["api_key"]


def test_manifest_version_incremented_in_this_pr():
    version = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]
    assert version == "1.3.20"
    assert re.fullmatch(r"\d+(?:\.\d+){1,3}", version)


def test_extension_reports_getManifest_version():
    src = BACKGROUND.read_text(encoding="utf-8")
    assert "chrome.runtime.getManifest().version" in src
    assert "X-Mighty-Extension-Version" in src
    assert "_mightyAuthHeaders" in src
    # Hard-coded deploy stamp must not be the source of truth.
    assert "1.4.11-passive-admin-session-verify" not in src


def test_compare_and_update_required_matrix():
    assert compare_chrome_versions("1.3.14", "1.3.15") == -1
    assert compare_chrome_versions("1.3.15", "1.3.15") == 0
    assert compare_chrome_versions("1.3.16", "1.3.15") == 1
    assert extension_update_required("1.3.14", "1.3.15") is True
    assert extension_update_required("1.3.15", "1.3.15") is False
    assert extension_update_required("1.4.0", "1.3.15") is False
    assert extension_update_required(None, "1.3.15") is False
    assert extension_update_required("bogus", "1.3.15") is False


def test_out_of_order_older_heartbeat_rejected():
    assert should_accept_extension_report(
        existing_version="1.3.15",
        existing_last_seen_at="2026-07-13T20:00:00Z",
        reported_version="1.3.14",
        reported_last_seen_at="2026-07-13T19:00:00Z",
    ) is False
    assert should_accept_extension_report(
        existing_version="1.3.14",
        existing_last_seen_at="2026-07-13T19:00:00Z",
        reported_version="1.3.15",
        reported_last_seen_at="2026-07-13T20:00:00Z",
    ) is True


def test_server_payload_exposes_reported_version(client):
    c, mighty = client
    uid = _uid(c)
    key = _api_key(mighty, uid)
    expected = read_expected_extension_version()

    # Missing reported version => Unknown / null, not expected.
    r0 = c.get("/api/debug/extension-version")
    assert r0.status_code == 200
    body0 = r0.get_json()
    assert body0["extension_version"] is None
    assert body0["extension_expected_version"] == expected
    assert body0["extension_update_required"] is False
    assert body0["extension_last_seen_at"] is None

    # Report an older version via accounts heartbeat.
    r1 = c.get(
        "/api/extension/accounts",
        headers={
            "X-Mighty-Key": key,
            "X-Mighty-Extension-Version": "1.3.14",
        },
    )
    assert r1.status_code == 200
    body1 = c.get("/api/debug/extension-version").get_json()
    assert body1["extension_version"] == "1.3.14"
    assert body1["extension_expected_version"] == expected
    assert body1["extension_update_required"] is True
    assert body1["extension_last_seen_at"]

    # Newer report updates (must be after the live heartbeat last-seen).
    with mighty.app.app_context():
        status_mid = get_extension_version_status(mighty.get_db(), uid)
        from datetime import datetime, timedelta, timezone
        newer_seen = (
            datetime.fromisoformat(status_mid["extension_last_seen_at"].replace("Z", "+00:00"))
            + timedelta(minutes=5)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        record_extension_version(
            mighty.get_db(),
            uid,
            expected,
            seen_at=newer_seen,
        )
    body2 = c.get("/api/debug/extension-version").get_json()
    assert body2["extension_version"] == expected
    assert body2["extension_update_required"] is False

    # Out-of-order older heartbeat cannot replace newer state.
    with mighty.app.app_context():
        updated = record_extension_version(
            mighty.get_db(),
            uid,
            "1.3.14",
            seen_at="2026-07-13T20:00:00Z",
        )
        assert updated is False
        status = get_extension_version_status(mighty.get_db(), uid)
    assert status["extension_version"] == expected
    assert status["extension_last_seen_at"] == body2["extension_last_seen_at"]


def test_missing_reported_version_ui_shows_unknown():
    info = {
        "extension_version": None,
        "extension_expected_version": "1.3.15",
        "extension_last_seen_at": None,
        "extension_update_required": False,
    }
    html_out = _render_extension_diagnostics(info, html.escape)
    assert "Extension version: Unknown" in html_out
    assert "1.3.15" in html_out
    # Must not claim the expected version is the running one.
    assert "Extension version: 1.3.15" not in html_out


def test_update_required_and_current_copy_in_technical_details():
    older = {
        "extension_version": "1.3.14",
        "extension_expected_version": "1.3.15",
        "extension_last_seen_at": "2026-07-13T19:58:00Z",
        "extension_update_required": True,
    }
    html_older = _render_extension_diagnostics(older, html.escape)
    assert "Extension update required" in html_older
    assert "running 1.3.14" in html_older
    assert "current build is 1.3.15" in html_older
    assert 'datetime="2026-07-13T19:58:00Z"' in html_older

    current = {
        "extension_version": "1.3.15",
        "extension_expected_version": "1.3.15",
        "extension_last_seen_at": "2026-07-13T19:58:00Z",
        "extension_update_required": False,
    }
    html_current = _render_extension_diagnostics(current, html.escape)
    assert "Extension update required" not in html_current
    assert "Extension version: 1.3.15" in html_current

    newer = {
        "extension_version": "1.4.0",
        "extension_expected_version": "1.3.15",
        "extension_last_seen_at": "2026-07-13T19:58:00Z",
        "extension_update_required": False,
    }
    html_newer = _render_extension_diagnostics(newer, html.escape)
    assert "Extension update required" not in html_newer


def test_capability_panel_includes_extension_block():
    capability = build_capability_view(None)
    rendered = render_capability_panel(
        capability,
        escape=html.escape,
        extension_info={
            "extension_version": "1.3.15",
            "extension_expected_version": "1.3.15",
            "extension_last_seen_at": "2026-07-14T02:58:00Z",
            "extension_update_required": False,
        },
    )
    assert "Technical Details" in rendered
    assert "Extension version: 1.3.15" in rendered
    assert 'datetime="2026-07-14T02:58:00Z"' in rendered
