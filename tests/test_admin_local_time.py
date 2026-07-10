"""Tests for shared admin local-time presentation helper."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.admin_local_time import (
    LOCAL_TIME_CLASS,
    format_admin_local_time,
    parse_admin_timestamp,
    to_utc_iso_z,
    timezone_note_html,
)

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "static" / "admin_local_time.js"

REQUIRED_TIMESTAMP_PAGES = [
    "/admin/login-truth",
    "/admin/session-evidence",
    "/admin/pipeline-runs",
    "/admin/sync-history",
    "/admin/sync-timeline",
    "/admin/provider-access-probe",
    "/admin/coverage",
]


@pytest.fixture()
def admin_client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_admin_local_time.db")
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
        email = f"admin_lt_{os.urandom(4).hex()}@test.local"
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    monkeypatch.setenv("ADMIN_EMAIL", email)
    return c


def _uid(client):
    with client.session_transaction() as sess:
        return sess["user_id"]


def test_format_null_and_empty():
    assert format_admin_local_time(None) == "—"
    assert format_admin_local_time("") == "—"


def test_format_emits_shared_time_element_with_utc_metadata():
    html = format_admin_local_time("2026-07-10T16:08:38Z")
    assert f'class="{LOCAL_TIME_CLASS}"' in html
    assert 'datetime="2026-07-10T16:08:38Z"' in html
    assert 'title="UTC: 2026-07-10T16:08:38Z"' in html
    assert ">2026-07-10T16:08:38Z</time>" in html


def test_format_handles_offset_and_naive_iso():
    offset = format_admin_local_time("2026-07-10T16:08:38+00:00")
    assert 'datetime="2026-07-10T16:08:38Z"' in offset

    naive = format_admin_local_time("2026-07-10 16:08:38")
    assert 'datetime="2026-07-10T16:08:38Z"' in naive
    assert LOCAL_TIME_CLASS in naive


def test_format_handles_epoch_and_datetime():
    dt = datetime(2026, 7, 10, 16, 8, 38, tzinfo=timezone.utc)
    html = format_admin_local_time(dt)
    assert 'datetime="2026-07-10T16:08:38Z"' in html

    epoch_html = format_admin_local_time(dt.timestamp())
    assert LOCAL_TIME_CLASS in epoch_html
    assert 'datetime="2026-07-10T16:08:38Z"' in epoch_html


def test_invalid_timestamp_shows_original_without_crashing():
    html = format_admin_local_time("not-a-real-timestamp")
    assert html == "not-a-real-timestamp"
    assert "<time" not in html


def test_parse_admin_timestamp_variants():
    assert parse_admin_timestamp(None) is None
    assert parse_admin_timestamp("bogus") is None
    z = parse_admin_timestamp("2026-07-10T16:08:38Z")
    assert z is not None and z.tzinfo is not None
    assert to_utc_iso_z(z) == "2026-07-10T16:08:38Z"


def test_timezone_note_present():
    note = timezone_note_html()
    assert "local timezone" in note
    assert "mighty-tz-note" in note


def test_js_syntax_check():
    assert JS_PATH.is_file()
    # node --check validates syntax without executing.
    result = subprocess.run(
        ["node", "--check", str(JS_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_js_relative_rules_present():
    src = JS_PATH.read_text(encoding="utf-8")
    assert "Just now" in src
    assert "Yesterday at" in src
    assert "mighty-rel" in src
    assert "mighty-exact" in src
    assert "initAdminLocalTimes" in src


@pytest.mark.parametrize("path", REQUIRED_TIMESTAMP_PAGES)
def test_required_admin_pages_load_shared_formatter(admin_client, path):
    r = admin_client.get(path)
    assert r.status_code == 200
    assert b"/static/admin_local_time.js" in r.data
    assert b"mighty-local-time" in r.data or b"mighty-tz-note" in r.data
    assert b"Times are shown in your browser" in r.data


def test_login_truth_last_verified_uses_shared_time_element(admin_client):
    import app as mighty
    from mighty.provider_session_state import SessionEvidence, upsert_provider_session_state

    uid = _uid(admin_client)
    with mighty.app.app_context():
        db = mighty.get_db()
        when = datetime(2026, 7, 10, 16, 8, 38, tzinfo=timezone.utc)
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="connected",
                evidence_type="session_verified",
                evidence_summary="verified",
                observed_at=when,
                source="extension_amex_connected",
                confidence="high",
            ),
        )
        db.commit()

    r = admin_client.get("/admin/login-truth")
    assert r.status_code == 200
    assert b'class="mighty-local-time"' in r.data
    assert b'datetime="2026-07-10T16:08:38Z"' in r.data
    assert b'title="UTC: 2026-07-10T16:08:38Z"' in r.data
    # Server fallback is UTC ISO inside <time>, not the old truncated "YYYY-MM-DD HH:MM:SS"
    assert b">2026-07-10T16:08:38Z</time>" in r.data
    assert not re.search(rb">2026-07-10 16:08:38</", r.data)


def test_session_evidence_uses_shared_time_element(admin_client):
    import app as mighty
    from mighty.provider_session_state import SessionEvidence, upsert_provider_session_state

    uid = _uid(admin_client)
    with mighty.app.app_context():
        db = mighty.get_db()
        when = datetime(2026, 7, 10, 16, 8, 38, tzinfo=timezone.utc)
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="connected",
                evidence_type="session_verified",
                evidence_summary="verified",
                observed_at=when,
                source="extension_amex_connected",
                confidence="high",
            ),
        )
        db.commit()

    r = admin_client.get("/admin/session-evidence")
    assert r.status_code == 200
    assert b'class="mighty-local-time"' in r.data
    assert b'datetime="2026-07-10T16:08:38Z"' in r.data
    assert b'title="UTC: 2026-07-10T16:08:38Z"' in r.data


def test_js_initialization_replaces_visible_raw_utc():
    """After init, visible text is relative/local — not the raw UTC primary format."""
    script = r"""
const fs = require('fs');
const path = require('path');

class FakeTimeEl {
  constructor(iso) {
    this.attrs = { datetime: iso, title: 'UTC: ' + iso };
    this.textContent = iso;
    this.innerHTML = iso;
  }
  getAttribute(name) { return this.attrs[name] || null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
}

const iso = '2026-07-10T16:08:38Z';
const el = new FakeTimeEl(iso);
const nodes = [el];

global.document = {
  readyState: 'complete',
  querySelectorAll: function(sel) {
    if (String(sel).indexOf('mighty-local-time') >= 0) return nodes;
    return [];
  },
  addEventListener: function() {},
};
global.window = global;

const src = fs.readFileSync(process.argv[1], 'utf8');
eval(src);
if (typeof global.initAdminLocalTimes !== 'function') {
  console.error('initAdminLocalTimes missing');
  process.exit(1);
}
global.initAdminLocalTimes(global.document);

if (el.getAttribute('data-mighty-local-ready') !== '1') {
  console.error('not marked ready');
  process.exit(1);
}
if (el.getAttribute('datetime') !== iso) {
  console.error('datetime metadata lost');
  process.exit(1);
}
if (el.innerHTML.indexOf('mighty-rel') < 0 || el.innerHTML.indexOf('mighty-exact') < 0) {
  console.error('missing relative/exact spans: ' + el.innerHTML);
  process.exit(1);
}
// Primary visible content should not be the raw ISO string alone.
if (el.innerHTML.trim() === iso) {
  console.error('still showing raw ISO as primary');
  process.exit(1);
}
console.log('ok');
"""
    result = subprocess.run(
        ["node", "-e", script, str(JS_PATH)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "ok" in result.stdout


def test_required_admin_pages_are_get_only_read_paths(admin_client):
    """Smoke: listed diagnostic pages respond to GET and do not accept writes via POST."""
    for path in REQUIRED_TIMESTAMP_PAGES:
        assert admin_client.get(path).status_code == 200
        # These pages are render-only; POST should not mutate via a form handler.
        post = admin_client.post(path, data={})
        assert post.status_code in {404, 405, 400, 403}
