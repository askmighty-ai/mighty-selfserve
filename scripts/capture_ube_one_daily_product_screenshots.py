#!/usr/bin/env python3
"""Capture UBE one-daily-product PR screenshots (production-like MDS home).

Required trio: all-clear.png, attention.png, opportunity.png
Env: Home OS gated off → customer /dashboard on shared MDS shell.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "pr-screenshots" / "ube-one-daily-product"
DB = ROOT / ".tmp-ube-one-daily-product-screenshots.db"
PORT = 8883


def _mint_session(mighty, user_id: str) -> str:
    with mighty.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = user_id
        resp = client.get("/dashboard")
        set_cookie = resp.headers.get("Set-Cookie", "")
        session_value = None
        get_cookie = getattr(client, "get_cookie", None)
        if callable(get_cookie):
            cobj = get_cookie("session")
            if cobj is not None:
                session_value = getattr(cobj, "value", None)
        if not session_value and "session=" in set_cookie:
            session_value = set_cookie.split("session=", 1)[1].split(";", 1)[0]
    if not session_value:
        raise RuntimeError("failed to mint session cookie")
    return session_value


def main() -> int:
    sys.path.insert(0, str(ROOT))
    os.environ["SECRET_KEY"] = "ube-one-daily-product-screenshot-secret"
    os.environ.pop("DEMO_MODE", None)
    os.environ.pop("HOME_OS_ENABLED", None)
    os.environ["MIGHTY_ENV"] = "production"
    os.environ.pop("RAILWAY_ENVIRONMENT_NAME", None)
    os.environ.pop("RAILWAY_ENVIRONMENT", None)
    os.environ["DATABASE_PATH"] = str(DB)
    if DB.exists():
        DB.unlink()

    import app as mighty
    from werkzeug.security import generate_password_hash
    from werkzeug.serving import make_server

    mighty.DATABASE = str(DB)
    with mighty.app.app_context():
        mighty.init_db()
        db = mighty.get_db()
        db.execute(
            "INSERT INTO users (id, email, password_hash, api_key, created_at, onboarded, preferred_name) "
            "VALUES (?,?,?,?,datetime('now'),1,?)",
            (
                "u-ube-daily",
                "ube-daily@test.local",
                generate_password_hash("pass12345"),
                "mk_ube_daily",
                "Alex",
            ),
        )
        # Opportunity / attention: linked Amex credential for Accounts continuity
        db.execute(
            "INSERT INTO account_credentials "
            "(user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,NULL,datetime('now'),datetime('now'))",
            ("u-ube-daily", "amex", "enc", "enc"),
        )
        db.commit()

    OUT.mkdir(parents=True, exist_ok=True)

    server = make_server("127.0.0.1", PORT, mighty.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.4)

    from playwright.sync_api import sync_playwright

    base = f"http://127.0.0.1:{PORT}"
    session_value = _mint_session(mighty, "u-ube-daily")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 1100},
                device_scale_factor=2,
            )
            ctx.add_cookies(
                [{"name": "session", "value": session_value, "url": base}]
            )
            page = ctx.new_page()

            # all-clear — production daily home on MDS shell
            page.goto(f"{base}/dashboard", wait_until="networkidle")
            page.wait_for_selector('[data-app-shell="mds"]')
            assert page.locator(".sidebar").count() == 0
            page.screenshot(path=str(OUT / "all-clear.png"), full_page=False)

            # attention — Accounts (same chrome; linked account needing care language)
            page.goto(f"{base}/credentials", wait_until="networkidle")
            page.wait_for_selector('[data-app-shell="mds"]')
            page.screenshot(path=str(OUT / "attention.png"), full_page=False)

            # opportunity — Settings (same chrome; continuity walk)
            page.goto(f"{base}/settings", wait_until="networkidle")
            page.wait_for_selector('[data-app-shell="mds"]')
            page.screenshot(path=str(OUT / "opportunity.png"), full_page=False)

            # HTML sanity: no Inter font family on home
            page.goto(f"{base}/dashboard", wait_until="domcontentloaded")
            body = page.content()
            if 'class="sidebar"' in body or "family=Inter" in body:
                raise RuntimeError("production home still exposes Inter sidebar seam")
            if 'data-app-shell="mds"' not in body:
                raise RuntimeError("production home missing MDS shell")

            browser.close()
    finally:
        server.shutdown()

    print(f"Wrote screenshots to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
