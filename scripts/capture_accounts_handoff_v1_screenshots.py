#!/usr/bin/env python3
"""Capture Accounts + First-Data Handoff V1 product-review screenshots (~1440px)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "pr-screenshots" / "accounts-handoff-v1"
PORT = 5018
BASE = f"http://127.0.0.1:{PORT}"
DB = ROOT / ".tmp-accounts-handoff-v1-screenshots.db"
EMAIL = "accounts-handoff-v1-screenshots@test.local"
PASSWORD = "pass12345"
NOW = datetime(2026, 7, 23, 15, 30, tzinfo=timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or NOW).replace(microsecond=0).isoformat()


def main() -> int:
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    OUT.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()

    env = os.environ.copy()
    env["DATABASE_PATH"] = str(DB)
    env["SECRET_KEY"] = "accounts-handoff-v1-screenshot-secret"
    env["PORT"] = str(PORT)
    proc = subprocess.Popen(
        [str(ROOT / ".venv" / "bin" / "python"), "app.py"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_health()
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            _signup(page)
            uid = _user_id()

            scenarios = [
                ("discovered.png", "/email-scan", _seed_discovered),
                ("enrolled-auto-verifying.png", "/credentials?filter=waiting", _seed_enrolled_waiting),
                ("needs-mighty-in-chrome.png", "/dashboard", _seed_needs_chrome),
                ("needs-provider-sign-in.png", "/credentials", _seed_needs_sign_in),
                ("verifying.png", "/dashboard", _seed_verifying),
                ("ready.png", "/credentials", _seed_ready),
                ("verification-failed.png", "/credentials", _seed_failed),
                ("home-newly-enrolled.png", "/dashboard", _seed_enrolled_waiting),
                ("home-user-step-required.png", "/dashboard", _seed_needs_sign_in),
                ("home-first-data-ready.png", "/dashboard", _seed_ready),
            ]
            for filename, path, seeder in scenarios:
                _reset_user(uid)
                seeder(uid)
                page.goto(f"{BASE}{path}", wait_until="networkidle")
                page.wait_for_timeout(400)
                out = OUT / filename
                page.screenshot(path=str(out), full_page=False)
                print(f"wrote {out}")

            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    _write_readme()
    return 0


def _wait_health() -> None:
    import urllib.request

    for _ in range(80):
        try:
            with urllib.request.urlopen(f"{BASE}/health", timeout=1) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("server did not become healthy")


def _signup(page) -> None:
    page.goto(f"{BASE}/signup")
    page.fill('input[name="email"]', EMAIL)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_url("**/dashboard**", timeout=15000)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    return conn


def _user_id() -> str:
    conn = _connect()
    try:
        row = conn.execute("SELECT id FROM users WHERE email=?", (EMAIL,)).fetchone()
        assert row is not None
        return row["id"]
    finally:
        conn.close()


def _reset_user(uid: str) -> None:
    conn = _connect()
    try:
        for table in (
            "account_data",
            "account_credentials",
            "account_discovery",
            "email_suggestions",
            "email_connections",
            "provider_session_state",
            "attention_overlay",
        ):
            try:
                conn.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
            except sqlite3.OperationalError:
                pass
        conn.execute(
            "UPDATE users SET extension_version=NULL, extension_last_seen_at=NULL, onboarded=1 WHERE id=?",
            (uid,),
        )
        conn.commit()
    finally:
        conn.close()


def _encrypt(uid: str, payload: dict) -> str:
    sys.path.insert(0, str(ROOT))
    os.environ["DATABASE_PATH"] = str(DB)
    import app as mighty

    with mighty.app.app_context():
        return mighty.encrypt_account_data(uid, payload)


def _insert_account(
    uid: str,
    *,
    source: str,
    display: str,
    sync_status: str,
    connection_status: str = "",
    items: list | None = None,
    mark_extension: bool = True,
    session_connected: bool = False,
) -> None:
    from mighty.account_state import ensure_account_state_tables, recompute_account_state
    from mighty.provider_session_state import SessionEvidence, upsert_provider_session_state

    conn = _connect()
    try:
        ensure_account_state_tables(conn)
        now_dt = NOW.replace(microsecond=0)
        now = iso(now_dt)
        payload_items = list(items or [])
        for it in payload_items:
            it.setdefault("private", True)
        stub = _encrypt(
            uid,
            {
                "items": payload_items,
                "sync_status": sync_status,
                "data_source": "extension",
                "sync_source": "extension",
            },
        )
        conn.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, source, "", "", "", now, now),
        )
        conn.execute(
            "INSERT INTO account_data "
            "(user_id, source, display_name, icon, color, data_enc, synced_at, connection_status, sync_status, extraction_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                uid,
                source,
                display,
                "💳",
                "#e8f0fe",
                stub,
                now if payload_items else "",
                connection_status,
                sync_status,
                "complete" if payload_items else "",
            ),
        )
        if session_connected:
            upsert_provider_session_state(
                conn,
                uid,
                SessionEvidence(
                    provider=source,
                    state="connected",
                    evidence_type="session_verified",
                    evidence_summary="screenshot seed",
                    observed_at=now_dt,
                    source="extension",
                    confidence="high",
                ),
            )
        recompute_account_state(conn, uid, source)
        if mark_extension:
            conn.execute(
                "UPDATE users SET extension_version=?, extension_last_seen_at=? WHERE id=?",
                ("1.0.0-screenshots", now, uid),
            )
        else:
            conn.execute(
                "UPDATE users SET extension_version=NULL, extension_last_seen_at=NULL WHERE id=?",
                (uid,),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_discovered(uid: str) -> None:
    conn = _connect()
    try:
        now = iso()
        conn.execute(
            "INSERT INTO email_connections (user_id, provider, access_token_enc, refresh_token_enc, email_address, scanned_at, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, "gmail", "x", "", EMAIL, now, now),
        )
        conn.execute(
            "INSERT INTO email_suggestions "
            "(user_id, site_key, display_name, category, email_count, sender_domain, created_at, added, dismissed) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (uid, "delta", "Delta", "airline", 4, "email.delta.com", now, 0, 0),
        )
        try:
            conn.execute(
                "INSERT INTO account_discovery "
                "(user_id, provider, source_type, source_ref, matched_domain, match_method, "
                "confidence, email_count, disposition, first_seen_at, last_seen_at, "
                "evidence_summary, display_name, category) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uid,
                    "delta",
                    "gmail_sender",
                    "gmail",
                    "email.delta.com",
                    "exact",
                    0.8,
                    4,
                    "ambiguous",
                    now,
                    now,
                    "Found from Gmail",
                    "Delta",
                    "airline",
                ),
            )
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()


def _seed_enrolled_waiting(uid: str) -> None:
    _insert_account(
        uid,
        source="amex",
        display="American Express",
        sync_status="needs_first_visit",
        connection_status="waiting_for_extension",
        mark_extension=True,
    )


def _seed_needs_chrome(uid: str) -> None:
    _insert_account(
        uid,
        source="amex",
        display="American Express",
        sync_status="needs_first_visit",
        connection_status="waiting_for_extension",
        mark_extension=False,
    )


def _seed_needs_sign_in(uid: str) -> None:
    _insert_account(
        uid,
        source="amex",
        display="American Express",
        sync_status="login_required",
        connection_status="needs_login",
        mark_extension=True,
    )
def _seed_verifying(uid: str) -> None:
    _insert_account(
        uid,
        source="amex",
        display="American Express",
        sync_status="checking",
        connection_status="waiting_for_extension",
        mark_extension=True,
    )


def _seed_ready(uid: str) -> None:
    _insert_account(
        uid,
        source="amex",
        display="American Express",
        sync_status="ok",
        connection_status="connected",
        items=[{"key": "balance", "label": "Balance", "value": "$120", "private": True}],
        mark_extension=True,
        session_connected=True,
    )


def _seed_failed(uid: str) -> None:
    _insert_account(
        uid,
        source="amex",
        display="American Express",
        sync_status="error",
        connection_status="",
        mark_extension=True,
    )


def _write_readme() -> None:
    (OUT / "README.md").write_text(
        """# Accounts + First-Data Handoff V1 — PR screenshots

Logged-in captures at approximately 1440×900 viewport. Review these files locally or from the repo — they are not embedded in chat.

| File | What it shows |
|------|----------------|
| `discovered.png` | Find accounts after mailbox discovery (ambiguous / ready-to-add suggestions) |
| `enrolled-auto-verifying.png` | Accounts — enrolled account still setting up |
| `needs-mighty-in-chrome.png` | Home when Mighty in Chrome is required for first data |
| `needs-provider-sign-in.png` | Accounts — sign in required |
| `verifying.png` | Home while verification is in progress |
| `ready.png` | Accounts — connected / ready |
| `verification-failed.png` | Accounts — could not verify / needs attention |
| `home-newly-enrolled.png` | Home lightweight enrollment confirmation (handoff) |
| `home-user-step-required.png` | Home when a user sign-in step is required |
| `home-first-data-ready.png` | Home all-clear after first usable data |

Generated by `scripts/capture_accounts_handoff_v1_screenshots.py`.
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
