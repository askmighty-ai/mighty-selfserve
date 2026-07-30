"""CP-002A — Signup presentation (identity bridge into Discover).

Presentation only. Auth, validation, and routing remain owned by app.py.
"""

from __future__ import annotations

from html import escape

from mighty.design_system import render_brand, render_button
from mighty import user_copy


def render_signup_page(
    *,
    csrf_token: str,
    error_html: str = "",
    email_value: str = "",
    info_html: str = "",
) -> str:
    """Render the first-run signup stage."""
    error_block = error_html or ""
    if error_block and 'role="alert"' not in error_block:
        # Preserve caller markup; ensure assistive tech can hear failures.
        error_block = error_block.replace("<div ", '<div role="alert" ', 1)

    info_block = info_html or ""
    if info_block and 'role="status"' not in info_block:
        info_block = info_block.replace("<div ", '<div role="status" ', 1)

    email_field = (
        '<div class="mds-field">'
        '<label class="mds-field__label" for="signup-email">Email</label>'
        '<input class="mds-field__control" id="signup-email" type="email" name="email" '
        f'value="{escape(email_value)}" '
        'placeholder="you@example.com" required autocomplete="email"/>'
        "</div>"
    )
    password_field = (
        '<div class="mds-field">'
        '<label class="mds-field__label" for="signup-password">Password</label>'
        '<input class="mds-field__control" id="signup-password" type="password" name="password" '
        'placeholder="Choose a password" required autocomplete="new-password" '
        'minlength="6" maxlength="128"/>'
        f'<p class="mds-field__helper" id="signup-password-helper">'
        f"{escape(user_copy.SIGNUP_PASSWORD_HELPER, quote=False)}</p>"
        "</div>"
    )
    primary = render_button(
        user_copy.SIGNUP_CTA,
        variant="primary",
        size="lg",
        block=True,
        type="submit",
    )
    stage = f"""
<div class="signup-stage">
  <div class="signup-intro">
    <h1 class="mds-display mds-display-md">{escape(user_copy.SIGNUP_HEADLINE, quote=False)}</h1>
    <p class="signup-lede">{escape(user_copy.SIGNUP_SUB, quote=False)}</p>
  </div>
  {info_block}
  {error_block}
  <form class="signup-form" method="POST" action="/signup">
    <input type="hidden" name="_csrf" value="{escape(csrf_token)}">
    {email_field}
    {password_field}
    <div class="signup-actions">
      {primary}
      <p class="signup-reassure">{escape(user_copy.SIGNUP_REASSURE, quote=False)}</p>
    </div>
  </form>
  <p class="signup-secondary">
    Already have an account? <a href="/login">Sign in</a>
  </p>
  <p class="signup-legal">
    By continuing you agree to our
    <a href="/tos">Terms</a> and <a href="/privacy">Privacy Policy</a>.
  </p>
</div>
"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<title>Create account — Mighty</title>
<link rel="stylesheet" href="/static/design-system/mighty-ds.css">
<style>
  body.signup-page {{
    margin: 0;
    min-height: 100vh;
    background:
      radial-gradient(1200px 600px at 50% -10%, rgba(47, 107, 89, 0.08), transparent 55%),
      linear-gradient(180deg, #f7f4ee 0%, #f3efe6 48%, #efe9df 100%);
    color: var(--mds-ink);
  }}
  .signup-page .signup-header {{
    padding: 1rem 0 0.25rem;
  }}
  .signup-page .signup-main {{
    padding: 1rem 0 2.5rem;
  }}
  .signup-page .signup-stage {{
    background: var(--mds-surface);
    border: 1px solid var(--mds-line);
    border-radius: var(--mds-radius-md);
    padding: 2rem 1.75rem 1.75rem;
    box-shadow: var(--mds-shadow-sm);
    display: grid;
    gap: 1.35rem;
  }}
  .signup-page .signup-intro {{
    display: grid;
    gap: 0.55rem;
  }}
  .signup-page .signup-lede {{
    margin: 0;
    color: var(--mds-muted);
    font-size: 1.02rem;
    line-height: 1.5;
  }}
  .signup-page .signup-form {{
    display: grid;
    gap: 1rem;
  }}
  .signup-page .signup-actions {{
    display: grid;
    gap: 0.55rem;
    margin-top: 0.25rem;
  }}
  .signup-page .signup-reassure {{
    margin: 0.1rem 0 0;
    text-align: center;
    font-size: 0.88rem;
    line-height: 1.4;
    color: var(--mds-muted);
  }}
  .signup-page .signup-secondary {{
    margin: 0;
    text-align: center;
    font-size: 0.94rem;
    color: var(--mds-muted);
  }}
  .signup-page .signup-secondary a {{
    color: var(--mds-pine-ink);
    font-weight: var(--mds-weight-semibold);
    text-decoration: none;
  }}
  .signup-page .signup-secondary a:hover {{
    text-decoration: underline;
  }}
  .signup-page .signup-legal {{
    margin: 0;
    text-align: center;
    font-size: 0.8rem;
    line-height: 1.45;
    color: var(--mds-muted);
  }}
  .signup-page .signup-legal a {{
    color: inherit;
  }}
  .signup-page .err {{
    font-size: 0.92rem;
    color: var(--mds-danger);
    background: var(--mds-danger-soft);
    border: 1px solid #e8c4c4;
    border-radius: var(--mds-radius-sm);
    padding: 0.7rem 0.9rem;
    margin: 0;
    line-height: 1.45;
  }}
  .signup-page .err a {{
    color: var(--mds-pine-ink);
  }}
  .signup-page .err-hint {{
    margin-top: 0.45rem;
    font-size: 0.84rem;
    line-height: 1.4;
    color: var(--mds-ink);
    opacity: 0.85;
  }}
  .signup-page .info {{
    font-size: 0.92rem;
    color: var(--mds-pine-ink);
    background: #eef6f2;
    border: 1px solid #cfe3da;
    border-radius: var(--mds-radius-sm);
    padding: 0.7rem 0.9rem;
    margin: 0;
    line-height: 1.45;
  }}
  .signup-page .mds-field__control:focus {{
    outline: none;
    border-color: var(--mds-pine);
    box-shadow: 0 0 0 3px var(--mds-focus);
  }}
  @media (max-width: 640px) {{
    .signup-page .signup-stage {{
      padding: 1.5rem 1.25rem 1.4rem;
    }}
  }}
</style>
</head>
<body class="mds signup-page">
  <header class="signup-header">
    <div class="mds-container-narrow">
      {render_brand(href="/")}
    </div>
  </header>
  <main class="signup-main">
    <div class="mds-container-narrow">
      {stage}
    </div>
  </main>
</body>
</html>"""
