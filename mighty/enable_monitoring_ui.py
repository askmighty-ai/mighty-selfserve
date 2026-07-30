"""Enable Monitoring (CP-004) — explain how Mighty stays up to date.

Presentation for post-Confirm handoff. Watching enrollment happens on Confirm
(see invite-path decision 2026-07-27-confirm-enrolls-watching). Chrome install
steps live on `/extension-setup`.
"""

from __future__ import annotations

from html import escape

from mighty.design_system import render_brand, render_button
from mighty import user_copy

STATE_DEFAULT = "default"
STATE_READY = "ready"


def render_enable_monitoring_page(
    *,
    home_href: str,
    enable_href: str = "/extension-setup",
    state: str = STATE_DEFAULT,
) -> str:
    """Render the Enable Monitoring teaching surface."""
    if state == STATE_READY:
        stage = _render_ready_stage(home_href=home_href)
        title = user_copy.ENABLE_MONITORING_READY_HEADLINE
    else:
        stage = _render_default_stage(home_href=home_href, enable_href=enable_href)
        title = user_copy.ENABLE_MONITORING_PAGE_TITLE
    return _page_shell(title=title, body=stage, home_href=home_href)


def _render_default_stage(*, home_href: str, enable_href: str) -> str:
    teach_items = (
        (user_copy.ENABLE_MONITORING_TEACH_1_TITLE, user_copy.ENABLE_MONITORING_TEACH_1_BODY),
        (user_copy.ENABLE_MONITORING_TEACH_2_TITLE, user_copy.ENABLE_MONITORING_TEACH_2_BODY),
        (user_copy.ENABLE_MONITORING_TEACH_3_TITLE, user_copy.ENABLE_MONITORING_TEACH_3_BODY),
    )
    teach_html = "".join(
        f'<li class="monitor-teach__item mds-rise-in" style="animation-delay:{i * 70}ms">'
        f'<p class="monitor-teach__title">{escape(title)}</p>'
        f'<p class="monitor-teach__body">{escape(body)}</p>'
        f"</li>"
        for i, (title, body) in enumerate(teach_items)
    )
    primary = render_button(
        user_copy.ENABLE_MONITORING_CTA,
        variant="primary",
        size="lg",
        block=True,
        href=enable_href,
    )
    secondary = render_button(
        user_copy.ENABLE_MONITORING_SECONDARY,
        variant="ghost",
        block=True,
        href=home_href,
    )
    details = f"""
<details class="monitor-details">
  <summary>{escape(user_copy.ENABLE_MONITORING_DETAILS_SUMMARY)}</summary>
  <ul class="monitor-details__list">
    <li><strong>When it runs.</strong> {escape(user_copy.ENABLE_MONITORING_DETAILS_WHEN)}</li>
    <li><strong>What it can see.</strong> {escape(user_copy.ENABLE_MONITORING_DETAILS_SEES)}</li>
    <li><strong>Turning it off.</strong> {escape(user_copy.ENABLE_MONITORING_DETAILS_OFF)}</li>
    <li><strong>If you skip.</strong> {escape(user_copy.ENABLE_MONITORING_DETAILS_SKIP)}</li>
    <li><strong>Home vs updates.</strong> {escape(user_copy.ENABLE_MONITORING_DETAILS_HOME)}</li>
  </ul>
</details>
"""
    return f"""
<div class="monitor-stage monitor-stage--consent">
  <div class="monitor-consent">
    <div class="mds-fade-in">
      <p class="mds-eyebrow">{escape(user_copy.ENABLE_MONITORING_EYEBROW)}</p>
      <h1 class="mds-display mds-display-md">{escape(user_copy.ENABLE_MONITORING_HEADLINE)}</h1>
      <p class="monitor-consent__lede">{escape(user_copy.ENABLE_MONITORING_LEDE)}</p>
    </div>

    <ol class="monitor-teach" aria-label="How Mighty stays up to date">
      {teach_html}
    </ol>

    <div class="monitor-why mds-rise-in" style="animation-delay:220ms">
      <p class="monitor-why__label">{escape(user_copy.ENABLE_MONITORING_WHY_LABEL)}</p>
      <p class="monitor-why__body">{escape(user_copy.ENABLE_MONITORING_WHY_BODY)}</p>
      <div class="monitor-roles" aria-label="Who does what">
        <p class="monitor-roles__item"><span>You</span> {escape(user_copy.ENABLE_MONITORING_ROLE_YOU)}</p>
        <p class="monitor-roles__item"><span>Mighty</span> {escape(user_copy.ENABLE_MONITORING_ROLE_MIGHTY)}</p>
      </div>
    </div>

    <p class="monitor-mobile-note" role="note">{escape(user_copy.ENABLE_MONITORING_MOBILE_NOTE)}</p>

    <div class="monitor-actions mds-fade-in" style="animation-delay:280ms">
      {primary}
      <p class="monitor-reassure">{escape(user_copy.ENABLE_MONITORING_REASSURE)}</p>
      {secondary}
    </div>

    {details}
  </div>
</div>
"""



def _render_ready_stage(*, home_href: str) -> str:
    primary = render_button(
        user_copy.ENABLE_MONITORING_READY_CTA,
        variant="primary",
        size="lg",
        block=True,
        href=home_href,
    )
    return f"""
<div class="monitor-stage monitor-stage--consent">
  <div class="monitor-consent mds-fade-in">
    <div>
      <p class="mds-eyebrow">{escape(user_copy.ENABLE_MONITORING_READY_EYEBROW)}</p>
      <h1 class="mds-display mds-display-md">{escape(user_copy.ENABLE_MONITORING_READY_HEADLINE)}</h1>
      <p class="monitor-consent__lede">{escape(user_copy.ENABLE_MONITORING_READY_LEDE)}</p>
    </div>
    <div class="monitor-actions">
      {primary}
    </div>
  </div>
</div>
"""


def _page_shell(*, title: str, body: str, home_href: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<title>{escape(title)} — Mighty</title>
<link rel="stylesheet" href="/static/design-system/mighty-ds.css">
<style>
  body.monitor-page {{
    margin: 0;
    min-height: 100vh;
    background:
      radial-gradient(1200px 600px at 50% -10%, rgba(47, 107, 89, 0.08), transparent 55%),
      linear-gradient(180deg, #f7f4ee 0%, #f3efe6 48%, #efe9df 100%);
    color: var(--mds-ink);
  }}
  .monitor-page .monitor-header {{
    padding: 1rem 0 0.25rem;
  }}
  .monitor-page .monitor-main {{
    padding: 1rem 0 2.5rem;
  }}
  .monitor-page .monitor-stage {{
    background: var(--mds-surface);
    border: 1px solid var(--mds-line);
    border-radius: var(--mds-radius-md);
    padding: 1.5rem 1.4rem 1.35rem;
    box-shadow: var(--mds-shadow-soft);
  }}
  .monitor-page .monitor-stage--consent {{
    padding: 1.6rem 1.5rem 1.4rem;
  }}
  .monitor-page .monitor-consent {{
    display: grid;
    gap: 1rem;
  }}
  .monitor-page .monitor-consent__lede {{
    margin: 0.45rem 0 0;
    color: var(--mds-muted);
    font-size: 0.98rem;
    line-height: 1.45;
  }}
  .monitor-page .monitor-teach {{
    list-style: none;
    margin: 0;
    padding: 0.15rem 0 0;
    display: grid;
    gap: 0.55rem;
  }}
  .monitor-page .monitor-teach__item {{
    margin: 0;
    padding: 0.7rem 0.85rem;
    border-radius: var(--mds-radius-sm);
    border: 1px solid var(--mds-line);
    background: var(--mds-surface-soft);
  }}
  .monitor-page .monitor-teach__title {{
    margin: 0 0 0.15rem;
    font-size: 0.9rem;
    font-weight: var(--mds-weight-bold);
    color: var(--mds-ink);
  }}
  .monitor-page .monitor-teach__body {{
    margin: 0;
    font-size: 0.88rem;
    line-height: 1.4;
    color: var(--mds-muted);
  }}
  .monitor-page .monitor-why {{
    padding: 0.8rem 0.9rem;
    border-radius: var(--mds-radius-sm);
    border: 1px solid #e5d3a8;
    background: var(--mds-waiting-soft);
  }}
  .monitor-page .monitor-why__label {{
    margin: 0 0 0.3rem;
    font-size: 0.9rem;
    font-weight: var(--mds-weight-bold);
    color: var(--mds-ink);
  }}
  .monitor-page .monitor-why__body {{
    margin: 0;
    font-size: 0.88rem;
    line-height: 1.4;
    color: var(--mds-muted);
  }}
  .monitor-page .monitor-roles {{
    display: grid;
    gap: 0.3rem;
    margin-top: 0.65rem;
  }}
  .monitor-page .monitor-roles__item {{
    margin: 0;
    font-size: 0.86rem;
    line-height: 1.35;
    color: var(--mds-muted);
  }}
  .monitor-page .monitor-roles__item span {{
    display: inline-block;
    min-width: 3.2rem;
    margin-right: 0.3rem;
    font-weight: var(--mds-weight-semibold);
    color: var(--mds-pine-ink);
  }}
  .monitor-page .monitor-mobile-note {{
    display: none;
    margin: 0;
    padding: 0.75rem 0.85rem;
    border-radius: var(--mds-radius-sm);
    border: 1px solid var(--mds-line);
    background: var(--mds-surface-soft);
    font-size: 0.88rem;
    line-height: 1.4;
    color: var(--mds-muted);
  }}
  .monitor-page .monitor-actions {{
    display: grid;
    gap: 0.45rem;
  }}
  .monitor-page .monitor-reassure {{
    margin: 0.05rem 0 0.1rem;
    text-align: center;
    font-size: 0.84rem;
    line-height: 1.35;
    color: var(--mds-muted);
  }}
  .monitor-page .monitor-details {{
    border-top: 1px solid var(--mds-line);
    padding-top: 0.7rem;
  }}
  .monitor-page .monitor-details summary {{
    cursor: pointer;
    font-size: 0.9rem;
    font-weight: var(--mds-weight-semibold);
    color: var(--mds-pine-ink);
    list-style-position: outside;
  }}
  .monitor-page .monitor-details summary:focus-visible {{
    outline: 2px solid var(--mds-pine);
    outline-offset: 3px;
    border-radius: 4px;
  }}
  .monitor-page .monitor-details__list {{
    margin: 0.65rem 0 0;
    padding-left: 1.15rem;
    color: var(--mds-muted);
    font-size: 0.86rem;
    line-height: 1.4;
  }}
  .monitor-page .monitor-details__list li + li {{
    margin-top: 0.4rem;
  }}
  .monitor-page .monitor-details__list strong {{
    color: var(--mds-ink);
  }}
  @media (max-width: 640px) {{
    .monitor-page .monitor-stage--consent {{
      padding: 1.35rem 1.15rem 1.25rem;
    }}
    .monitor-page .monitor-mobile-note {{
      display: block;
    }}
  }}
</style>
</head>
<body class="mds monitor-page">
  <header class="monitor-header">
    <div class="mds-container-narrow">
      {render_brand(href=home_href)}
    </div>
  </header>
  <main class="monitor-main">
    <div class="mds-container-narrow">
      {body}
    </div>
  </main>
</body>
</html>"""
