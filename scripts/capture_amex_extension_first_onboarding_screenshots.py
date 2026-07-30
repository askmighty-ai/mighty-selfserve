#!/usr/bin/env python3
"""Capture Amex extension-first onboarding PR screenshots."""

from __future__ import annotations

import html as html_lib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "pr-screenshots" / "amex-extension-first-onboarding"
DS = ROOT / "static" / "design-system"
FIXTURES = ROOT / "tests" / "fixtures" / "amex" / "rental-status"


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from mighty.intelligence.schema import ensure_intelligence_tables

    ensure_intelligence_tables(conn, commit=True)
    return conn


def _ingest(db: sqlite3.Connection) -> None:
    from mighty.intelligence.refresh import ingest_amex_rental_observations

    cards = json.loads((FIXTURES / "cards_overview.json").read_text(encoding="utf-8"))
    rental = json.loads((FIXTURES / "car_rental_privileges.json").read_text(encoding="utf-8"))
    ingest_amex_rental_observations(
        db,
        user_id="demo",
        observations=[
            {
                "observation_type": "amex_cards",
                "source_url": cards["source_url"],
                "observed_at": cards["observed_at"],
                "payload": cards,
            },
            {
                "observation_type": "amex_car_rental_privileges",
                "source_url": rental["source_url"],
                "account_identity": rental["account_id"],
                "observed_at": rental["observed_at"],
                "payload": rental,
            },
        ],
    )


def main() -> int:
    sys.path.insert(0, str(ROOT))
    OUT.mkdir(parents=True, exist_ok=True)

    from mighty.account_status import AccountStatus
    from mighty.extension_setup_ui import render_extension_setup_page
    from mighty.home_state import resolve_home_state
    from mighty.home_ui import render_home_page
    from mighty.intelligence.ui import INTEL_INSIGHT_CSS

    css = "\n".join(
        (DS / name).read_text(encoding="utf-8")
        for name in (
            "tokens.css",
            "base.css",
            "components.css",
            "motion.css",
            "mighty-ds.css",
        )
    )
    css = f"{css}\n{INTEL_INSIGHT_CSS}"

    def _acct(source: str, display_name: str, status: str) -> AccountStatus:
        presentation_key = {
            "needs_login": "needs_sign_in",
            "up_to_date": "ready",
            "waiting_for_extension": "updating",
        }.get(status, "ready")
        return AccountStatus(
            source=source,
            display_name=display_name,
            status=status,
            presentation_key=presentation_key,
            presentation_label=presentation_key.replace("_", " ").title(),
            last_successful_sync_at=None,
            current_attempt_at=None,
            last_error=None,
            user_action_label=None,
            user_action_url="https://global.americanexpress.com/overview",
        )

    db = _db()

    # attention: Chrome setup (primary onboarding step)
    attention_html = render_extension_setup_page(
        api_key="mk_demo",
        home_href="/dashboard",
        diagnostics=False,
    )

    # all-clear: Visit Amex handoff after Chrome is ready
    all_clear_html = render_home_page(
        resolve_home_state(
            accounts=[_acct("amex", "American Express", "waiting_for_extension")],
            provider_open_urls={
                "amex": "https://global.americanexpress.com/overview",
            },
            worker_setup_needed=False,
        ),
        first_name="Jordan",
        today_label="Thursday, July 30",
        escape=html_lib.escape,
        gmail_connected=False,
        chrome_active=True,
        db=db,
        user_id="demo",
    )

    # opportunity: first insight + optional Gmail enhancement
    _ingest(db)
    opportunity_html = render_home_page(
        resolve_home_state(
            accounts=[_acct("amex", "American Express", "up_to_date")],
        ),
        first_name="Jordan",
        today_label="Thursday, July 30",
        last_checked="just now",
        escape=html_lib.escape,
        gmail_connected=False,
        chrome_active=True,
        first_success_provider="American Express",
        first_success_partial=False,
        db=db,
        user_id="demo",
    )

    frames = {
        "attention.png": attention_html,
        "all-clear.png": all_clear_html,
        "opportunity.png": opportunity_html,
    }

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright required", file=sys.stderr)
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        for name, body in frames.items():
            doc = (
                "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"<style>{css}</style></head><body>{body}</body></html>"
            )
            page.set_content(doc, wait_until="load")
            page.screenshot(path=str(OUT / name), full_page=True)
            print(f"wrote {OUT / name}")
        browser.close()

    (OUT / "README.md").write_text(
        """# Amex extension-first onboarding — PR screenshots

Chrome extension is the primary Amex beta discovery path. Gmail is optional after first intelligence.

**Do not embed these images in chat.** Open files from the repository.

| File | What it shows |
|------|----------------|
| `attention.png` | Install Mighty in Chrome (`/extension-setup`) — first onboarding step |
| `all-clear.png` | Home handoff after Chrome is ready — Visit American Express |
| `opportunity.png` | First insight on Home plus optional Find more accounts from Gmail |

## Notes

- Capture script: `scripts/capture_amex_extension_first_onboarding_screenshots.py`
- Signup redirects to `/extension-setup` and auto-enrolls Amex
- Gmail `/email-scan` remains available as an optional enhancement only after first intelligence
""",
        encoding="utf-8",
    )
    print(f"wrote {OUT / 'README.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
