#!/usr/bin/env python3
"""Capture Amex extension-first onboarding PR screenshots."""

from __future__ import annotations

import html as html_lib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "pr-screenshots" / "amex-extension-first-onboarding"
DS = ROOT / "static" / "design-system"

README = """# Amex first-insight onboarding — PR screenshots

Anticipation path: Add Mighty → Visit Amex → checking → first insight. Infrastructure stays quiet. Gmail is optional after first intelligence.

**Do not embed these images in chat.** Open files from the repository.

| File | What it shows |
|------|----------------|
| `attention.png` | Add Mighty to Chrome (`/extension-setup`) — install means, Visit Amex next |
| `all-clear.png` | Home — Visit American Express (no insight claimed yet) |
| `opportunity.png` | First insight on Home plus optional Find more accounts from Gmail |

## Notes

- Capture script: `scripts/capture_amex_extension_first_onboarding_screenshots.py`
- Signup redirects to `/extension-setup` and auto-enrolls Amex
- No heartbeat / diagnostics on the customer path (`?debug=1` admin only)
- CTA language stays action-true: Visit American Express — never “continue to your first insight”
"""


def main() -> int:
    sys.path.insert(0, str(ROOT))
    OUT.mkdir(parents=True, exist_ok=True)

    from mighty.account_status import AccountStatus
    from mighty.extension_setup_ui import render_extension_setup_page
    from mighty.home_state import resolve_home_state
    from mighty.home_ui import render_home_page

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

    attention_html = render_extension_setup_page(
        api_key="mk_demo",
        home_href="/dashboard",
        diagnostics=False,
    )

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
    )

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

    (OUT / "README.md").write_text(README, encoding="utf-8")
    print(f"wrote {OUT / 'README.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
