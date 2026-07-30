"""Auth door presentation — login / forgot password (MDS parity with signup).

Presentation only. Auth, validation, and routing remain owned by app.py.
"""

from __future__ import annotations

from html import escape

from mighty.design_system import render_brand, render_button


def _auth_page_shell(*, title: str, stage: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<title>{escape(title)} — Mighty</title>
<link rel="stylesheet" href="/static/design-system/mighty-ds.css">
<style>
  body.auth-door-page {{
    margin: 0;
    min-height: 100vh;
    background:
      radial-gradient(1200px 600px at 50% -10%, rgba(47, 107, 89, 0.08), transparent 55%),
      linear-gradient(180deg, #f7f4ee 0%, #f3efe6 48%, #efe9df 100%);
    color: var(--mds-ink);
  }}
  .auth-door-page .auth-door-header {{
    padding: 1rem 0 0.25rem;
  }}
  .auth-door-page .auth-door-main {{
    padding: 1rem 0 2.5rem;
  }}
  .auth-door-page .auth-door-stage {{
    background: var(--mds-surface);
    border: 1px solid var(--mds-line);
    border-radius: var(--mds-radius-md);
    padding: 2rem 1.75rem 1.75rem;
    box-shadow: var(--mds-shadow-sm);
    display: grid;
    gap: 1.35rem;
  }}
  .auth-door-page .auth-door-intro {{
    display: grid;
    gap: 0.55rem;
  }}
  .auth-door-page .auth-door-lede {{
    margin: 0;
    color: var(--mds-muted);
    font-size: 1.02rem;
    line-height: 1.5;
  }}
  .auth-door-page .auth-door-form {{
    display: grid;
    gap: 1rem;
  }}
  .auth-door-page .auth-door-actions {{
    display: grid;
    gap: 0.55rem;
    margin-top: 0.25rem;
  }}
  .auth-door-page .auth-door-secondary {{
    margin: 0;
    text-align: center;
    font-size: 0.94rem;
    color: var(--mds-muted);
  }}
  .auth-door-page .auth-door-secondary a {{
    color: var(--mds-pine-ink);
    font-weight: var(--mds-weight-semibold);
    text-decoration: none;
  }}
  .auth-door-page .auth-door-secondary a:hover {{
    text-decoration: underline;
  }}
  .auth-door-page .auth-door-back {{
    margin: 0;
    font-size: 0.9rem;
  }}
  .auth-door-page .auth-door-back a {{
    color: var(--mds-muted);
    text-decoration: none;
  }}
  .auth-door-page .auth-door-back a:hover {{
    color: var(--mds-pine-ink);
    text-decoration: underline;
  }}
  .auth-door-page .err {{
    font-size: 0.92rem;
    color: var(--mds-danger);
    background: var(--mds-danger-soft);
    border: 1px solid #e8c4c4;
    border-radius: var(--mds-radius-sm);
    padding: 0.7rem 0.9rem;
    margin: 0;
    line-height: 1.45;
  }}
  .auth-door-page .info {{
    font-size: 0.92rem;
    color: var(--mds-pine-ink);
    background: #eef6f2;
    border: 1px solid #cfe3da;
    border-radius: var(--mds-radius-sm);
    padding: 0.7rem 0.9rem;
    margin: 0;
    line-height: 1.45;
  }}
  .auth-door-page .mds-field__control:focus {{
    outline: none;
    border-color: var(--mds-pine);
    box-shadow: 0 0 0 3px var(--mds-focus);
  }}
  @media (max-width: 640px) {{
    .auth-door-page .auth-door-stage {{
      padding: 1.5rem 1.25rem 1.4rem;
    }}
  }}
</style>
</head>
<body class="mds auth-door-page">
  <header class="auth-door-header">
    <div class="mds-container-narrow">
      {render_brand(href="/")}
    </div>
  </header>
  <main class="auth-door-main">
    <div class="mds-container-narrow">
      {stage}
    </div>
  </main>
</body>
</html>"""


def render_login_page(
    *,
    csrf_token: str,
    error_html: str = "",
    next_path: str = "",
) -> str:
    """Render the MDS sign-in door (signup parity)."""
    error_block = error_html or ""
    if error_block and 'role="alert"' not in error_block and 'role="status"' not in error_block:
        if error_block.startswith("<div "):
            error_block = error_block.replace("<div ", '<div role="alert" ', 1)

    email_field = (
        '<div class="mds-field">'
        '<label class="mds-field__label" for="login-email">Email</label>'
        '<input class="mds-field__control" id="login-email" type="email" name="email" '
        'placeholder="you@example.com" required autocomplete="email"/>'
        "</div>"
    )
    password_field = (
        '<div class="mds-field">'
        '<label class="mds-field__label" for="login-password">Password</label>'
        '<input class="mds-field__control" id="login-password" type="password" name="password" '
        'placeholder="Your password" required autocomplete="current-password" maxlength="128"/>'
        "</div>"
    )
    primary = render_button("Sign in", variant="primary", size="lg", block=True, type="submit")
    stage = f"""
<div class="auth-door-stage">
  <p class="auth-door-back"><a href="/">&larr; Home</a></p>
  <div class="auth-door-intro">
    <h1 class="mds-display mds-display-md">Welcome back</h1>
    <p class="auth-door-lede">Sign in to your Mighty account.</p>
  </div>
  {error_block}
  <form class="auth-door-form" method="POST" action="/login">
    <input type="hidden" name="_csrf" value="{escape(csrf_token)}">
    <input type="hidden" name="next" id="next-field" value="{escape(next_path)}">
    {email_field}
    {password_field}
    <div class="auth-door-actions">{primary}</div>
  </form>
  <p class="auth-door-secondary">
    Forgot your password? <a href="/forgot-password">Reset it here</a>
  </p>
  <p class="auth-door-secondary">
    No account? <a href="/signup">Create account</a>
    · <a href="/beta/restart">Start over</a>
  </p>
</div>
<script>
var nf = document.getElementById('next-field');
if (nf && !nf.value) nf.value = new URLSearchParams(window.location.search).get('next') || '';
</script>
"""
    return _auth_page_shell(title="Sign in", stage=stage)


def render_forgot_page(
    *,
    csrf_token: str,
    message_html: str = "",
) -> str:
    """Render the MDS forgot-password door."""
    message_block = message_html or ""
    email_field = (
        '<div class="mds-field">'
        '<label class="mds-field__label" for="forgot-email">Email</label>'
        '<input class="mds-field__control" id="forgot-email" type="email" name="email" '
        'placeholder="you@example.com" required autocomplete="email"/>'
        "</div>"
    )
    primary = render_button(
        "Send reset link",
        variant="primary",
        size="lg",
        block=True,
        type="submit",
    )
    stage = f"""
<div class="auth-door-stage">
  <p class="auth-door-back"><a href="/login">&larr; Back to sign in</a></p>
  <div class="auth-door-intro">
    <h1 class="mds-display mds-display-md">Reset password</h1>
    <p class="auth-door-lede">
      Enter your email and we’ll send a link to choose a new password.
    </p>
  </div>
  {message_block}
  <form class="auth-door-form" method="POST" action="/forgot-password">
    <input type="hidden" name="_csrf" value="{escape(csrf_token)}">
    {email_field}
    <div class="auth-door-actions">{primary}</div>
  </form>
</div>
"""
    return _auth_page_shell(title="Reset password", stage=stage)


def render_reset_page(
    *,
    csrf_token: str,
    error_html: str = "",
) -> str:
    """Render the MDS choose-new-password door."""
    error_block = error_html or ""
    password_field = (
        '<div class="mds-field">'
        '<label class="mds-field__label" for="reset-password">New password</label>'
        '<input class="mds-field__control" id="reset-password" type="password" name="password" '
        'placeholder="At least 6 characters" required autocomplete="new-password" '
        'minlength="6" maxlength="128"/>'
        "</div>"
    )
    confirm_field = (
        '<div class="mds-field">'
        '<label class="mds-field__label" for="reset-confirm">Confirm password</label>'
        '<input class="mds-field__control" id="reset-confirm" type="password" name="confirm" '
        'placeholder="Repeat password" required autocomplete="new-password" '
        'minlength="6" maxlength="128"/>'
        "</div>"
    )
    primary = render_button(
        "Update password",
        variant="primary",
        size="lg",
        block=True,
        type="submit",
    )
    # action is filled by the route via form action on the token URL — use relative current
    stage = f"""
<div class="auth-door-stage">
  <p class="auth-door-back"><a href="/login">&larr; Back to sign in</a></p>
  <div class="auth-door-intro">
    <h1 class="mds-display mds-display-md">Choose a new password</h1>
    <p class="auth-door-lede">Enter a new password for your Mighty account.</p>
  </div>
  {error_block}
  <form class="auth-door-form" method="POST">
    <input type="hidden" name="_csrf" value="{escape(csrf_token)}">
    {password_field}
    {confirm_field}
    <div class="auth-door-actions">{primary}</div>
  </form>
</div>
"""
    return _auth_page_shell(title="Choose a new password", stage=stage)
