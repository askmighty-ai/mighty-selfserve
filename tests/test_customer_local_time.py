"""Tests for customer Truth Dashboard local-time presentation."""

from __future__ import annotations

import html
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_readiness import AccountReadiness, READY
from mighty.account_status import AccountStatus
from mighty.capability_state import CapabilityState, build_capability_view, _fmt_ts
from mighty.customer_account_access import (
    DISCOVERED_MANUAL,
    build_customer_account_access_view,
)
from mighty.customer_local_time import (
    CUSTOMER_LOCAL_TIME_CLASS,
    format_customer_local_time,
)
from mighty.home_state import resolve_home_state
from mighty.home_ui import render_home_page
from mighty import user_copy

ROOT = Path(__file__).resolve().parents[1]
JS_PATH = ROOT / "static" / "customer_local_time.js"


def _escape(value):
    return html.escape(str(value)) if value is not None else ""


def _readiness(**kwargs) -> AccountReadiness:
    defaults = dict(
        provider="amex",
        state=READY,
        status_label="Connected",
        status_copy=user_copy.READINESS_COPY_READY,
        presentation_key="ready",
        canonical_status="up_to_date",
        login_required=False,
        session_state="connected",
        access_cycle_id=None,
        session_evidence_at=None,
        extraction_at="2026-07-13T15:00:00+00:00",
        extraction_ok=True,
        extraction_correlated=True,
        verification_id=None,
        cached_data_label=None,
        last_confirmed_ready_at="2026-07-14T00:41:10+00:00",
        last_confirmed_access_cycle_id="cycle-1",
        background_verification=False,
        secondary_label=None,
    )
    defaults.update(kwargs)
    return AccountReadiness(**defaults)  # type: ignore[arg-type]


def _status(view) -> AccountStatus:
    return AccountStatus(
        source=view.provider,
        display_name=view.display_name,
        status="up_to_date",
        presentation_key="ready",
        presentation_label=view.status_label,
        last_successful_sync_at=view.last_confirmed_at,
        current_attempt_at=None,
        last_error=None,
        user_action_label=view.user_action_text,
        user_action_url=view.user_action_url,
        customer_access=view,
    )


def test_format_emits_canonical_utc_iso_in_datetime():
    html_out = format_customer_local_time("2026-07-14T00:41:10+00:00")
    assert f'class="{CUSTOMER_LOCAL_TIME_CLASS}"' in html_out
    assert 'datetime="2026-07-14T00:41:10Z"' in html_out
    assert 'title="UTC: 2026-07-14T00:41:10Z"' in html_out
    assert ">2026-07-14T00:41:10Z</time>" in html_out


def test_missing_timestamp_degrades_safely():
    assert format_customer_local_time(None) == "—"
    assert format_customer_local_time("") == "—"
    assert format_customer_local_time("not-a-timestamp") == "not-a-timestamp"


def test_raw_space_separated_utc_lookalike_is_treated_as_utc():
    """Do not treat 'YYYY-MM-DD HH:mm:ss' as already-local."""
    html_out = format_customer_local_time("2026-07-14 00:41:10")
    assert 'datetime="2026-07-14T00:41:10Z"' in html_out


def test_fmt_ts_keeps_canonical_iso_for_sorting_and_api():
    assert _fmt_ts("2026-07-14T00:41:10+00:00") == "2026-07-14T00:41:10Z"
    assert _fmt_ts(None) is None


def test_truth_dashboard_emits_local_time_elements_not_raw_space_ts():
    view = build_customer_account_access_view(
        provider="amex",
        display_name="American Express",
        readiness=_readiness(),
        discovered_from=DISCOVERED_MANUAL,
    )
    result = resolve_home_state(
        accounts=[_status(view)],
        extracted_items=[{"label": "Membership Rewards", "value": "125,000"}],
        session_confidence="high",
    )
    rendered = render_home_page(
        result,
        first_name="Alex",
        today_label="Monday, July 13",
        escape=_escape,
    )
    assert result.capability is not None
    assert result.capability.state == CapabilityState.EXTRACTION_SUCCESS
    assert result.capability.last_verified == "2026-07-14T00:41:10Z"
    assert f'class="{CUSTOMER_LOCAL_TIME_CLASS}"' in rendered
    assert 'datetime="2026-07-14T00:41:10Z"' in rendered
    assert "Latest check completed:" in rendered
    # Raw UTC-looking customer display must not appear as primary text.
    assert "Latest check completed: 2026-07-14 00:41:10" not in rendered
    assert "Truth Timeline" in rendered
    assert rendered.count(CUSTOMER_LOCAL_TIME_CLASS) >= 2


def test_js_syntax_check():
    assert JS_PATH.is_file()
    result = subprocess.run(
        ["node", "--check", str(JS_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_browser_formatter_uses_local_timezone_and_shared_api():
    script = r"""
const fs = require('fs');

class FakeTimeEl {
  constructor(iso) {
    this.attrs = { datetime: iso };
    this.textContent = iso;
    this.innerHTML = iso;
  }
  getAttribute(name) { return this.attrs[name] || null; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
}

const iso = '2026-07-14T00:41:10Z';
const el = new FakeTimeEl(iso);
const nodes = [el];

global.document = {
  readyState: 'complete',
  querySelectorAll: function(sel) {
    if (String(sel).indexOf('mighty-customer-local-time') >= 0) return nodes;
    return [];
  },
  addEventListener: function() {},
};
global.window = global;

const src = fs.readFileSync(process.argv[1], 'utf8');
eval(src);
if (typeof global.initCustomerLocalTimes !== 'function') {
  console.error('initCustomerLocalTimes missing');
  process.exit(1);
}
if (typeof global.parseMightyCustomerTimestamp !== 'function') {
  console.error('parseMightyCustomerTimestamp missing');
  process.exit(1);
}
global.initCustomerLocalTimes(global.document);

if (el.getAttribute('data-mighty-customer-local-ready') !== '1') {
  console.error('not marked ready');
  process.exit(1);
}
if (el.getAttribute('datetime') !== iso) {
  console.error('datetime metadata lost');
  process.exit(1);
}
if (el.textContent === iso || el.textContent === '2026-07-14 00:41:10') {
  console.error('still showing raw UTC-looking timestamp: ' + el.textContent);
  process.exit(1);
}
if (!/[AP]M/.test(el.textContent) && !/\d{1,2}:\d{2}/.test(el.textContent)) {
  console.error('missing local clock fragments: ' + el.textContent);
  process.exit(1);
}
// Shared parser must treat space-separated naive as UTC, not local.
const parsed = global.parseMightyCustomerTimestamp('2026-07-14 00:41:10');
if (!parsed || parsed.toISOString() !== '2026-07-14T00:41:10.000Z') {
  console.error('naive space timestamp not treated as UTC');
  process.exit(1);
}
console.log('ok|' + el.textContent);
"""
    result = subprocess.run(
        ["node", "-e", script, str(JS_PATH)],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "ok|" in result.stdout
