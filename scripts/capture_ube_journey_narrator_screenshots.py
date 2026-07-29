#!/usr/bin/env python3
"""Capture ube-journey-narrator PR screenshots (required trio)."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "pr-screenshots" / "ube-journey-narrator"
DB = ROOT / ".tmp-ube-journey-narrator-screenshots.db"
PORT = 8884


def main() -> int:
    sys.path.insert(0, str(ROOT))
    os.environ["SECRET_KEY"] = "ube-journey-narrator-screenshot-secret"
    os.environ.pop("DEMO_MODE", None)
    os.environ.pop("HOME_OS_ENABLED", None)
    os.environ["MIGHTY_ENV"] = "production"
    os.environ["DATABASE_PATH"] = str(DB)
    if DB.exists():
        DB.unlink()

    import app as mighty
    from mighty.journey_narrative import (
        ACTION_PROVIDER_VISIT,
        OBS_STILL_NEEDS_LOGIN,
        record_system_observation,
        record_user_action,
    )
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
                "u-jn",
                "jn@test.local",
                generate_password_hash("pass12345"),
                "mk_jn",
                "Alex",
            ),
        )
        db.execute(
            "INSERT INTO account_credentials "
            "(user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,NULL,datetime('now'),datetime('now'))",
            ("u-jn", "amex", "enc", "enc"),
        )
        db.commit()
        # Seed narrative: Visit then still-needs-login (R1 / I5)
        record_user_action(
            db, "u-jn", event_type=ACTION_PROVIDER_VISIT, provider="amex"
        )
        record_system_observation(
            db, "u-jn", event_type=OBS_STILL_NEEDS_LOGIN, provider="amex"
        )

    OUT.mkdir(parents=True, exist_ok=True)
    server = make_server("127.0.0.1", PORT, mighty.app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.4)

    from playwright.sync_api import sync_playwright

    base = f"http://127.0.0.1:{PORT}"
    with mighty.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = "u-jn"
        resp = client.get("/dashboard")
        session_value = None
        get_cookie = getattr(client, "get_cookie", None)
        if callable(get_cookie):
            cobj = get_cookie("session")
            if cobj is not None:
                session_value = getattr(cobj, "value", None)
        if not session_value:
            set_cookie = resp.headers.get("Set-Cookie", "")
            if "session=" in set_cookie:
                session_value = set_cookie.split("session=", 1)[1].split(";", 1)[0]
    if not session_value:
        raise RuntimeError("no session")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 1100}, device_scale_factor=2
            )
            ctx.add_cookies(
                [{"name": "session", "value": session_value, "url": base}]
            )
            page = ctx.new_page()

            # all-clear / continuity: home after Visit with narrative binding
            page.goto(f"{base}/dashboard", wait_until="networkidle")
            page.screenshot(path=str(OUT / "all-clear.png"), full_page=False)

            # attention: credentials
            page.goto(f"{base}/credentials", wait_until="networkidle")
            page.screenshot(path=str(OUT / "attention.png"), full_page=False)

            # opportunity: settings
            page.goto(f"{base}/settings", wait_until="networkidle")
            page.screenshot(path=str(OUT / "opportunity.png"), full_page=False)
            browser.close()
    finally:
        server.shutdown()

    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
