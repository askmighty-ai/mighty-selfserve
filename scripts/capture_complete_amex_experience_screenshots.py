#!/usr/bin/env python3
"""Capture complete-amex-experience PR screenshots (required trio)."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "pr-screenshots" / "complete-amex-experience"
DB = ROOT / ".tmp-complete-amex-experience-screenshots.db"
PORT = 8886


def main() -> int:
    sys.path.insert(0, str(ROOT))
    os.environ["SECRET_KEY"] = "complete-amex-experience-screenshot-secret"
    os.environ.pop("DEMO_MODE", None)
    os.environ.pop("HOME_OS_ENABLED", None)
    os.environ["MIGHTY_ENV"] = "production"
    os.environ["DATABASE_PATH"] = str(DB)
    if DB.exists():
        DB.unlink()

    import app as mighty
    from mighty.connection_state import (
        advance_amex_to_waiting,
        amex_extension_connected,
        start_amex_connect,
    )
    from mighty.journey_narrative import (
        ACTION_PROVIDER_VISIT,
        OBS_STILL_NEEDS_LOGIN,
        record_system_observation,
        record_user_action,
    )
    from mighty.provider_access_manager import record_amex_extension_connected
    from mighty.provider_account import EXTRACTION_NO_ACCOUNT_DATA
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
                "u-cae",
                "cae@test.local",
                generate_password_hash("pass12345"),
                "mk_cae",
                "Alex",
            ),
        )
        ctx = dict(
            iso_fn=mighty.iso,
            encrypt_fn=mighty.encrypt_account_data,
            decrypt_fn=mighty.decrypt_account_data,
        )
        start_amex_connect(db, "u-cae", **ctx)
        advance_amex_to_waiting(db, "u-cae", **ctx)
        amex_extension_connected(
            db, "u-cae", session_verified=True, **ctx
        )
        record_amex_extension_connected(
            db, "u-cae", observed_at=mighty.iso()
        )
        db.execute(
            "UPDATE account_data SET extraction_status=? WHERE user_id=? AND source=?",
            (EXTRACTION_NO_ACCOUNT_DATA, "u-cae", "amex"),
        )
        db.commit()
        record_user_action(
            db, "u-cae", event_type=ACTION_PROVIDER_VISIT, provider="amex"
        )
        record_system_observation(
            db, "u-cae", event_type=OBS_STILL_NEEDS_LOGIN, provider="amex"
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
            sess["user_id"] = "u-cae"
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
            ctx_b = browser.new_context(
                viewport={"width": 1440, "height": 1100}, device_scale_factor=2
            )
            ctx_b.add_cookies(
                [{"name": "session", "value": session_value, "url": base}]
            )
            page = ctx_b.new_page()

            page.goto(f"{base}/dashboard", wait_until="networkidle")
            page.screenshot(path=str(OUT / "all-clear.png"), full_page=False)

            page.goto(f"{base}/credentials", wait_until="networkidle")
            page.screenshot(path=str(OUT / "attention.png"), full_page=False)

            page.goto(f"{base}/settings", wait_until="networkidle")
            page.screenshot(path=str(OUT / "opportunity.png"), full_page=False)
            browser.close()
    finally:
        server.shutdown()

    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
