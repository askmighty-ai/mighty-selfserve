"""Discover My Accounts (CP-003) — preface → review UI.

Slice 1: informed-consent preface, Gmail-only connect, candidate review.
Enrollment / watching persistence is intentionally out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any, Sequence

from mighty.capability_state import CUSTOMER_VISIBLE_PROVIDERS
from mighty.design_system import (
    render_account_row,
    render_brand,
    render_button,
    render_empty_state,
)
from mighty.discovery_policy import (
    AUTO_ENROLL_MIN_CONFIDENCE,
    DISPOSITION_ALREADY_ENROLLED,
    DISPOSITION_DISMISSED,
    DISPOSITION_ELIGIBLE,
    DISPOSITION_IGNORED,
)
from mighty.discovery_store import DiscoveryFact, list_discovery_facts

PHASE_PREFACE = "preface"
PHASE_REVIEW = "review"
PHASE_EMPTY = "empty"
PHASE_CONFIRM_DEFERRED = "confirm_deferred"

_SKIP_DISPOSITIONS = frozenset(
    {
        DISPOSITION_ALREADY_ENROLLED,
        DISPOSITION_DISMISSED,
        DISPOSITION_IGNORED,
        "enrolled",
    }
)


@dataclass(frozen=True)
class ReviewCandidate:
    provider: str
    display_name: str
    evidence: str
    confidence: float
    disposition: str
    preselected: bool
    tier: str  # "confident" | "uncertain"


def gmail_is_connected(db: Any, user_id: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM email_connections WHERE user_id=? AND provider='gmail'",
        (str(user_id),),
    ).fetchone()
    return row is not None


def _evidence_label(fact: DiscoveryFact) -> str:
    name = (fact.display_name or "").strip()
    domain = (fact.matched_domain or "").strip()
    if name:
        return f"Found from {name} mail"
    if domain:
        return f"Found from mail · {domain}"
    return "Found from your mail"


def _is_confident(fact: DiscoveryFact) -> bool:
    if fact.disposition == DISPOSITION_ELIGIBLE:
        return True
    return float(fact.confidence or 0) >= AUTO_ENROLL_MIN_CONFIDENCE


def load_review_candidates(db: Any, user_id: str) -> list[ReviewCandidate]:
    """Customer-visible discovery candidates awaiting review (not enrolled)."""
    facts = list_discovery_facts(db, user_id)
    out: list[ReviewCandidate] = []
    for fact in facts:
        if fact.provider not in CUSTOMER_VISIBLE_PROVIDERS:
            continue
        if fact.disposition in _SKIP_DISPOSITIONS:
            continue
        confident = _is_confident(fact)
        out.append(
            ReviewCandidate(
                provider=fact.provider,
                display_name=fact.display_name or fact.provider.title(),
                evidence=_evidence_label(fact),
                confidence=float(fact.confidence or 0),
                disposition=fact.disposition,
                preselected=confident,
                tier="confident" if confident else "uncertain",
            )
        )
    out.sort(key=lambda c: (0 if c.tier == "confident" else 1, -c.confidence, c.display_name))
    return out


def _oauth_error_banner(oauth_error: str | None) -> str:
    if not oauth_error:
        return ""
    return (
        '<div class="mds-banner mds-banner--attention" role="alert" '
        'style="margin-bottom:1.25rem">'
        "<strong>Could not connect Gmail.</strong> "
        "You can try again, add an account manually, or choose Not now."
        "</div>"
    )


def _page_shell(
    *, title: str, body: str, wide: bool = False, home_href: str = "/dashboard"
) -> str:
    width_cls = "mds-container-onboard" if wide else "mds-container-narrow"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{escape(title)} — Mighty</title>
<link rel="stylesheet" href="/static/design-system/mighty-ds.css">
<style>
  body.discover-page {{
    margin: 0;
    min-height: 100vh;
    background:
      radial-gradient(1200px 600px at 50% -10%, rgba(47, 107, 89, 0.08), transparent 55%),
      linear-gradient(180deg, #f7f4ee 0%, #f3efe6 48%, #efe9df 100%);
    color: var(--mds-ink);
  }}
  .discover-page .discover-header {{
    padding: 1rem 0 0.25rem;
  }}
  .discover-page .discover-main {{
    padding: 1rem 0 2.5rem;
  }}
  .discover-page .discover-stage {{
    background: var(--mds-surface);
    border: 1px solid var(--mds-line);
    border-radius: var(--mds-radius-md);
    padding: 1.75rem 1.5rem 1.6rem;
    box-shadow: var(--mds-shadow-soft);
  }}
  .discover-page .discover-stage--consent {{
    padding: 2rem 1.75rem 1.75rem;
  }}
  .discover-page .discover-consent {{
    display: grid;
    gap: 1.5rem;
  }}
  .discover-page .discover-consent__lede {{
    margin: 0.55rem 0 0;
    color: var(--mds-muted);
    font-size: 1.02rem;
    line-height: 1.5;
  }}
  .discover-page .discover-consent__sections {{
    display: grid;
    gap: 1rem;
  }}
  .discover-page .discover-consent__section {{
    padding: 1rem 1.1rem;
    border-radius: var(--mds-radius-sm);
    border: 1px solid var(--mds-line);
    background: var(--mds-surface-soft);
  }}
  .discover-page .discover-consent__section--limits {{
    background: var(--mds-waiting-soft);
    border-color: #e5d3a8;
  }}
  .discover-page .discover-consent__label {{
    margin: 0 0 0.45rem;
    font-size: 0.94rem;
    font-weight: var(--mds-weight-bold);
    color: var(--mds-ink);
  }}
  .discover-page .discover-consent__list {{
    margin: 0;
    padding-left: 1.15rem;
    color: var(--mds-muted);
    font-size: 0.94rem;
    line-height: 1.45;
  }}
  .discover-page .discover-consent__list li + li {{
    margin-top: 0.25rem;
  }}
  .discover-page .discover-consent__actions {{
    display: grid;
    gap: 0.55rem;
  }}
  .discover-page .discover-consent__reassure {{
    margin: 0.15rem 0 0.35rem;
    text-align: center;
    font-size: 0.88rem;
    line-height: 1.4;
    color: var(--mds-muted);
  }}
  .discover-page .discover-tier {{
    margin: 1.35rem 0 0.55rem;
    font-size: var(--mds-text-eyebrow);
    font-weight: var(--mds-weight-bold);
    letter-spacing: var(--mds-tracking-eyebrow);
    text-transform: uppercase;
    color: var(--mds-muted);
  }}
  .discover-page .discover-list {{
    display: grid;
    gap: 0.65rem;
  }}
  .discover-page .discover-consequence {{
    margin: 1.25rem 0 0;
    font-size: 0.92rem;
    color: var(--mds-muted);
    line-height: 1.45;
  }}
  .discover-page .discover-actions {{
    display: grid;
    gap: 0.65rem;
    margin-top: 1.35rem;
  }}
  .discover-page .discover-manual {{
    margin: 0;
    text-align: center;
    font-size: 0.9rem;
    color: var(--mds-muted);
  }}
  .discover-page .mds-account--selected {{
    border-color: #c5ddd6;
    background: var(--mds-pine-soft);
  }}
  .discover-page .mds-account:not(.mds-account--selected) {{
    opacity: 0.92;
  }}
</style>
</head>
<body class="mds discover-page">
  <header class="discover-header">
    <div class="{width_cls}">
      {render_brand(href=home_href)}
    </div>
  </header>
  <main class="discover-main">
    <div class="{width_cls}">
      {body}
    </div>
  </main>
</body>
</html>"""


def render_preface_page(
    *,
    csrf_token: str,
    home_href: str,
    manual_href: str = "",
    oauth_error: str | None = None,
    gmail_configured: bool = True,
) -> str:
    # Call-site compat only — never surface server config; manual add deferred past first-run.
    _ = (gmail_configured, manual_href)
    primary = (
        f'<form method="POST" action="/email-scan/continue">'
        f'<input type="hidden" name="_csrf" value="{escape(csrf_token)}">'
        f'{render_button("Continue to Google", variant="primary", size="lg", block=True, type="submit")}'
        f"</form>"
    )
    secondary = render_button("Not now", variant="ghost", block=True, href=home_href)
    consent = f"""
<div class="discover-consent">
  <div>
    <h1 class="mds-display mds-display-md">Find more accounts from Gmail</h1>
    <p class="discover-consent__lede">
      Optional: connect Gmail so Mighty can suggest other programs you already use —
      for example airline, hotel, and additional card accounts — from mail you already
      receive. Your Amex insight path does not require this step.
    </p>
  </div>
  <div class="discover-consent__sections">
    <div class="discover-consent__section">
      <p class="discover-consent__label">What Mighty accesses</p>
      <ul class="discover-consent__list">
        <li>Sender addresses and message headers used to recognize known programs
            (for example mail from American Express).</li>
        <li>Google's permission screen will show Gmail read access — Mighty uses it
            only to find programs you already use, not to change your inbox.</li>
      </ul>
    </div>
    <div class="discover-consent__section">
      <p class="discover-consent__label">What Mighty does not do</p>
      <ul class="discover-consent__list">
        <li>Never sends email, deletes mail, or changes your inbox.</li>
        <li>Never asks for American Express or other account passwords in Mighty.</li>
      </ul>
    </div>
  </div>
  <div class="discover-consent__actions">
    {primary}
    <p class="discover-consent__reassure">You'll review everything before Mighty watches anything.</p>
    {secondary}
  </div>
</div>
"""
    body = (
        f"{_oauth_error_banner(oauth_error)}"
        f'<div class="discover-stage discover-stage--consent">{consent}</div>'
    )
    return _page_shell(title="Find your accounts", body=body, home_href=home_href)


_MONOGRAMS = {
    "amex": "AX",
}


def _monogram_for(provider: str, display_name: str) -> str:
    if provider in _MONOGRAMS:
        return _MONOGRAMS[provider]
    letters = "".join(ch for ch in display_name if ch.isalnum())
    return (letters[:2] or provider[:2]).upper()


def _render_candidate_rows(candidates: Sequence[ReviewCandidate], *, tier: str) -> str:
    rows = []
    for c in candidates:
        if c.tier != tier:
            continue
        rows.append(
            render_account_row(
                name=c.display_name,
                monogram=_monogram_for(c.provider, c.display_name),
                meta=c.evidence,
                variant="selectable",
                selectable=True,
                selected=c.preselected,
                checkbox_name="watch",
                checkbox_value=c.provider,
                class_name="mds-account--selected" if c.preselected else "",
            )
        )
    return "\n".join(rows)


def render_review_page(
    *,
    candidates: Sequence[ReviewCandidate],
    csrf_token: str,
    home_href: str,
) -> str:
    confident = _render_candidate_rows(candidates, tier="confident")
    uncertain = _render_candidate_rows(candidates, tier="uncertain")

    sections = []
    if confident:
        sections.append(
            f'<p class="discover-tier">Looks like yours</p>'
            f'<div class="discover-list">{confident}</div>'
        )
    if uncertain:
        sections.append(
            f'<p class="discover-tier">Possible matches — add only if this is yours</p>'
            f'<div class="discover-list">{uncertain}</div>'
        )

    actions = (
        f'<div class="discover-actions">'
        f'{render_button("Start watching these accounts", variant="primary", size="lg", block=True, type="submit")}'
        f'{render_button("I’ll add accounts later", variant="ghost", block=True, href=home_href)}'
        f"</div>"
    )
    body = f"""
<div class="discover-stage">
  <p class="mds-eyebrow">Discovery complete · Your choice</p>
  <h1 class="mds-display mds-display-md">Confirm what Mighty should watch</h1>
  <p class="mds-body" style="margin-top:0.65rem">
    Here are accounts found from your mail. Found does not mean logged in —
    you choose what Mighty watches.
  </p>
  <form method="POST" action="/email-scan/confirm">
    <input type="hidden" name="_csrf" value="{escape(csrf_token)}">
    {"".join(sections)}
    <p class="discover-consequence">
      Only the accounts you select are included. Mighty won't watch anything
      you leave unchecked.
    </p>
    {actions}
  </form>
</div>
<script>
(function () {{
  function syncRow(input) {{
    var row = input.closest('.mds-account');
    if (!row) return;
    if (input.checked) row.classList.add('mds-account--selected');
    else row.classList.remove('mds-account--selected');
  }}
  document.querySelectorAll('input[name="watch"]').forEach(function (input) {{
    syncRow(input);
    input.addEventListener('change', function () {{ syncRow(input); }});
  }});
}})();
</script>
"""
    return _page_shell(
        title="Review accounts", body=body, wide=True, home_href=home_href
    )


def render_empty_discovery_page(*, home_href: str, manual_href: str) -> str:
    # Home is the primary exit; manual add is secondary (not the beta story).
    actions = (
        f'{render_button("Go to Home", variant="primary", size="lg", block=True, href=home_href)}'
        f'<div style="margin-top:0.65rem">'
        f'{render_button("Add an account manually", variant="ghost", block=True, href=manual_href)}'
        f"</div>"
    )
    empty = render_empty_state(
        title="No accounts found",
        body=(
            "Mighty couldn't identify any supported accounts from this scan. "
            "That can happen — Mighty only recognizes programs it knows."
        ),
        future="You can return to Home, or add an account manually if you prefer.",
        action_html=actions,
        variant="no-results",
    )
    body = f'<div class="discover-stage">{empty}</div>'
    return _page_shell(title="No accounts found", body=body, home_href=home_href)


def render_confirm_deferred_page(*, home_href: str, review_href: str = "/email-scan") -> str:
    actions = (
        f'{render_button("Go to Home", variant="primary", size="lg", block=True, href=home_href)}'
        f'<div style="margin-top:0.65rem">'
        f'{render_button("Back to review", variant="ghost", block=True, href=review_href)}'
        f"</div>"
    )
    empty = render_empty_state(
        title="You're all set for now",
        body=(
            "Mighty isn't watching any accounts yet. When you're ready, "
            "you can continue from Home."
        ),
        future="Nothing is being watched right now.",
        action_html=actions,
        variant="first-use",
    )
    body = f'<div class="discover-stage">{empty}</div>'
    return _page_shell(
        title="Confirmation pending", body=body, home_href=home_href
    )
