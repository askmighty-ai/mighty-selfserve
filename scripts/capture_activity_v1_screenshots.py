#!/usr/bin/env python3
"""Capture Activity V1 product-review screenshots at ~1440px width."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "pr-screenshots" / "activity-v1"
PORT = 5017
BASE = f"http://127.0.0.1:{PORT}"
DB = ROOT / ".tmp-activity-v1-screenshots.db"
EMAIL = "activity-v1-screenshots@test.local"
PASSWORD = "pass12345"
NOW = datetime(2026, 7, 22, 15, 30, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def main() -> int:
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    OUT.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()

    env = os.environ.copy()
    env["DATABASE_PATH"] = str(DB)
    env["SECRET_KEY"] = "activity-v1-screenshot-secret"
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
                ("empty.png", _seed_empty),
                ("needs-approval.png", _seed_needs_approval),
                ("in-progress.png", _seed_in_progress),
                ("completed.png", _seed_completed),
                ("could-not-complete.png", _seed_could_not_complete),
                ("mixed-timeline.png", _seed_mixed),
            ]
            for filename, seeder in scenarios:
                _clear_actions(uid)
                seeder(uid)
                page.goto(f"{BASE}/activity", wait_until="networkidle")
                page.wait_for_selector(".activity-shell", timeout=10000)
                path = OUT / filename
                page.screenshot(path=str(path), full_page=False)
                print(f"wrote {path}")

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

    for _ in range(60):
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


def _clear_actions(uid: str) -> None:
    from mighty.activity_projection import delete_activity_data
    from mighty.agent_action_store import ensure_agent_action_tables
    from mighty.execution_receipt import ensure_receipt_tables

    conn = _connect()
    try:
        ensure_agent_action_tables(conn)
        ensure_receipt_tables(conn)
        delete_activity_data(conn, uid, commit=True)
    finally:
        conn.close()


def _insert(uid: str, **kwargs):
    from mighty.agent_action_store import insert_action
    from mighty.execution_receipt import persist_receipt

    with_receipt = bool(kwargs.pop("with_receipt", False))
    receipt_result = kwargs.pop("receipt_result", "completed")
    receipt_at = kwargs.pop("receipt_at", None)
    conn = _connect()
    try:
        action = insert_action(conn, user_id=uid, commit=True, **kwargs)
        if with_receipt:
            persist_receipt(
                conn,
                action_id=action.action_id,
                user_id=uid,
                agent_id=action.agent_id,
                authorization_decision="authorized",
                authorization_at=action.decided_at or action.created_at,
                auth_channel="activity",
                execution_result=receipt_result,
                execution_attempt=1,
                proposal_hash=action.proposal_hash,
                detail={
                    "ok": receipt_result == "completed",
                    "policy_explanation": action.decision_explanation
                    or "Allowed by your settings.",
                },
                provider=action.provider,
                created_at=receipt_at or action.decided_at or action.created_at,
                commit=True,
            )
        return action
    finally:
        conn.close()


def _seed_empty(uid: str) -> None:
    return


def _seed_needs_approval(uid: str) -> None:
    from mighty.agent_action_store import STATE_AWAITING_AUTHORIZATION

    _insert(
        uid,
        action_type="redeem",
        label="Redeem $50 Amex dining credit",
        fields={"amount": "$50", "merchant": "Any dining"},
        lifecycle_state=STATE_AWAITING_AUTHORIZATION,
        provider="amex",
        created_at=iso(NOW - timedelta(minutes=12)),
        decision_explanation=None,
    )


def _seed_in_progress(uid: str) -> None:
    from mighty.agent_action_store import STATE_EXECUTING

    _insert(
        uid,
        action_type="book",
        label="Hold Hilton free night award",
        fields={"nights": "1", "property": "Hilton Honors"},
        lifecycle_state=STATE_EXECUTING,
        provider="hilton",
        created_at=iso(NOW - timedelta(hours=1)),
        decided_at=iso(NOW - timedelta(minutes=20)),
        decision_explanation="You approved this request.",
    )


def _seed_completed(uid: str) -> None:
    from mighty.agent_action_store import STATE_COMPLETED

    _insert(
        uid,
        action_type="redeem",
        label="Apply Southwest companion pass credit",
        fields={"credit": "$50"},
        lifecycle_state=STATE_COMPLETED,
        provider="southwest",
        created_at=iso(NOW - timedelta(hours=3)),
        decided_at=iso(NOW - timedelta(hours=2)),
        outcome="completed",
        decision_explanation="Allowed by your routine approval settings.",
        with_receipt=True,
        receipt_result="completed",
        receipt_at=iso(NOW - timedelta(hours=2) + timedelta(minutes=2)),
    )


def _seed_could_not_complete(uid: str) -> None:
    from mighty.agent_action_store import STATE_DENIED, STATE_EXPIRED, STATE_FAILED

    _insert(
        uid,
        action_type="transfer",
        label="Transfer 20,000 Marriott points",
        fields={"points": "20000"},
        lifecycle_state=STATE_DENIED,
        provider="marriott",
        created_at=iso(NOW - timedelta(hours=5)),
        decided_at=iso(NOW - timedelta(hours=4)),
    )
    _insert(
        uid,
        action_type="book",
        label="Book United award seat",
        fields={"route": "SFO–JFK"},
        lifecycle_state=STATE_EXPIRED,
        provider="united",
        created_at=iso(NOW - timedelta(hours=8)),
        decided_at=iso(NOW - timedelta(hours=7)),
    )
    _insert(
        uid,
        action_type="redeem",
        label="Use Amex hotel credit",
        fields={"amount": "$200"},
        lifecycle_state=STATE_FAILED,
        provider="amex",
        created_at=iso(NOW - timedelta(days=1)),
        decided_at=iso(NOW - timedelta(days=1) + timedelta(minutes=10)),
        outcome="provider_unavailable",
        decision_explanation="You approved this request.",
        with_receipt=True,
        receipt_result="failed",
        receipt_at=iso(NOW - timedelta(days=1) + timedelta(minutes=12)),
    )


def _seed_mixed(uid: str) -> None:
    _seed_needs_approval(uid)
    _seed_in_progress(uid)
    _seed_completed(uid)
    _seed_could_not_complete(uid)


def _write_readme() -> None:
    (OUT / "README.md").write_text(
        """# Activity V1 screenshots

Logged-in `/activity` captures at approximately 1440×900 viewport. Review these files locally or from the repo — they are not embedded in chat.

| File | State | What it shows |
|------|--------|----------------|
| `empty.png` | Empty | Calm “All quiet” empty state when the user has no Activity items. |
| `needs-approval.png` | Needs approval | Pending agent action with Approve / Deny controls. |
| `in-progress.png` | In progress | Authorized / executing work in the timeline. |
| `completed.png` | Completed | Finished work with readable details / receipt provenance. |
| `could-not-complete.png` | Could not complete | Denied, expired, and failed outcomes with accurate wording. |
| `mixed-timeline.png` | Mixed | Chronological mix of the four customer-facing categories. |
""",
        encoding="utf-8",
    )
    print(f"wrote {OUT / 'README.md'}")


if __name__ == "__main__":
    raise SystemExit(main())
