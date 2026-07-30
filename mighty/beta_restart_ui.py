"""Invite-only beta restart — wipe + recreate with the same email."""

from __future__ import annotations

from html import escape
from urllib.parse import quote

from mighty.design_system import render_brand, render_button
from mighty import user_copy


def render_beta_restart_page(
    *,
    csrf_token: str,
    email: str = "",
    error_html: str = "",
) -> str:
    """Password-gated clean restart for invite-only beta testers."""
    error_block = error_html or ""
    if error_block and 'role="alert"' not in error_block:
        error_block = error_block.replace("<div ", '<div role="alert" ', 1)

    email_field = (
        '<div class="mds-field">'
        '<label class="mds-field__label" for="restart-email">Email</label>'
        '<input class="mds-field__control" id="restart-email" type="email" name="email" '
        f'value="{escape(email)}" placeholder="you@example.com" required '
        'autocomplete="email"/>'
        "</div>"
    )
    password_field = (
        '<div class="mds-field">'
        '<label class="mds-field__label" for="restart-password">Current password</label>'
        '<input class="mds-field__control" id="restart-password" type="password" '
        'name="password" placeholder="Password for this Mighty account" required '
        'autocomplete="current-password" minlength="6" maxlength="128"/>'
        f'<p class="mds-field__helper">{escape(user_copy.BETA_RESTART_PASSWORD_HELPER, quote=False)}</p>'
        "</div>"
    )
    confirm = (
        '<label class="restart-confirm">'
        '<input type="checkbox" name="confirm_wipe" value="1" required>'
        f'<span>{escape(user_copy.BETA_RESTART_CONFIRM, quote=False)}</span>'
        "</label>"
    )
    primary = render_button(
        user_copy.BETA_RESTART_CTA,
        variant="primary",
        size="lg",
        block=True,
        type="submit",
    )
    stage = f"""
<div class="restart-stage">
  <div class="restart-intro">
    <h1 class="mds-display mds-display-md">{escape(user_copy.BETA_RESTART_HEADLINE, quote=False)}</h1>
    <p class="restart-lede">{escape(user_copy.BETA_RESTART_LEDE, quote=False)}</p>
  </div>
  {error_block}
  <form class="restart-form" method="POST" action="/beta/restart">
    <input type="hidden" name="_csrf" value="{escape(csrf_token)}">
    {email_field}
    {password_field}
    {confirm}
    <div class="restart-actions">
      {primary}
      <p class="restart-reassure">{escape(user_copy.BETA_RESTART_REASSURE, quote=False)}</p>
    </div>
  </form>
  <p class="restart-secondary">
    {escape(user_copy.BETA_RESTART_SIGN_IN_PROMPT, quote=False)}
    <a href="/login">Sign in</a>
    ·
    <a href="/forgot-password">Forgot password</a>
  </p>
</div>
"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<title>Start over — Mighty</title>
<link rel="stylesheet" href="/static/design-system/mighty-ds.css">
<style>
  body.restart-page {{
    margin: 0;
    min-height: 100vh;
    background:
      radial-gradient(1200px 600px at 50% -10%, rgba(47, 107, 89, 0.08), transparent 55%),
      linear-gradient(180deg, #f7f4ee 0%, #f3efe6 48%, #efe9df 100%);
    color: var(--mds-ink);
  }}
  .restart-page .restart-header {{ padding: 1rem 0 0.25rem; }}
  .restart-page .restart-main {{ padding: 1rem 0 2.5rem; }}
  .restart-page .restart-stage {{
    background: var(--mds-surface);
    border: 1px solid var(--mds-line);
    border-radius: var(--mds-radius-md);
    padding: 2rem 1.75rem 1.75rem;
    box-shadow: var(--mds-shadow-sm);
    display: grid;
    gap: 1.35rem;
  }}
  .restart-page .restart-intro {{ display: grid; gap: 0.55rem; }}
  .restart-page .restart-lede {{
    margin: 0; color: var(--mds-muted); font-size: 1.02rem; line-height: 1.5;
  }}
  .restart-page .restart-form {{ display: grid; gap: 1rem; }}
  .restart-page .restart-actions {{ display: grid; gap: 0.55rem; margin-top: 0.25rem; }}
  .restart-page .restart-reassure {{
    margin: 0.1rem 0 0; text-align: center; font-size: 0.88rem;
    line-height: 1.4; color: var(--mds-muted);
  }}
  .restart-page .restart-confirm {{
    display: flex; gap: 0.65rem; align-items: flex-start;
    font-size: 0.92rem; line-height: 1.45; color: var(--mds-ink);
  }}
  .restart-page .restart-confirm input {{ margin-top: 0.2rem; }}
  .restart-page .restart-secondary {{
    margin: 0; text-align: center; font-size: 0.94rem; color: var(--mds-muted);
  }}
  .restart-page .restart-secondary a {{
    color: var(--mds-pine-ink); font-weight: var(--mds-weight-semibold);
    text-decoration: none;
  }}
  .restart-page .restart-secondary a:hover {{ text-decoration: underline; }}
  .restart-page .err {{
    font-size: 0.92rem; color: var(--mds-danger); background: var(--mds-danger-soft);
    border: 1px solid #e8c4c4; border-radius: var(--mds-radius-sm);
    padding: 0.7rem 0.9rem; margin: 0; line-height: 1.45;
  }}
  .restart-page .err a {{ color: var(--mds-pine-ink); }}
  .restart-page .mds-field__control:focus {{
    outline: none; border-color: var(--mds-pine); box-shadow: 0 0 0 3px var(--mds-focus);
  }}
</style>
</head>
<body class="mds restart-page">
  <header class="restart-header">
    <div class="mds-container-narrow">{render_brand(href="/")}</div>
  </header>
  <main class="restart-main">
    <div class="mds-container-narrow">{stage}</div>
  </main>
</body>
</html>"""


def signup_duplicate_account_error_html(email: str) -> str:
    """Recoverable duplicate-email message for signup."""
    restart = f"/beta/restart?email={quote(email or '', safe='')}"
    return (
        '<div class="err" role="alert">'
        f"{escape(user_copy.SIGNUP_DUPLICATE_TITLE, quote=False)} "
        f'<a href="/login">Sign in</a>'
        f' · <a href="{escape(restart)}">{escape(user_copy.SIGNUP_DUPLICATE_RESTART_LINK, quote=False)}</a>'
        f'<div class="err-hint">{escape(user_copy.SIGNUP_DUPLICATE_HINT, quote=False)}</div>'
        "</div>"
    )
