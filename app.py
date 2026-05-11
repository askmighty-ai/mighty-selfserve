"""
Mighty Self-Serve
=================
Personal authorization layer for AI agents.
Self-contained Flask app — SQLite, no external dependencies.

Local:   python3 app.py  →  http://localhost:5004
Railway: set start command to  python3 app.py
         PORT env var is picked up automatically.

Env vars (all optional):
  SECRET_KEY    — Flask session secret (generated randomly if not set)
  DATABASE_PATH — SQLite file path (default: mighty.db)
  BASE_URL      — Public URL override (e.g. https://mighty-selfserve.up.railway.app)
  PORT          — Port to listen on (default: 5004)
"""

import os, json, secrets, hashlib, sqlite3, threading, smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify, session, redirect, g, make_response

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

DATABASE        = os.environ.get("DATABASE_PATH", "mighty.db")
PORT            = int(os.environ.get("PORT", 5004))
TIMEOUT_SEC     = 300  # pending authorization expires after 5 minutes
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_PASS = os.environ.get("GMAIL_PASS", "")


# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()

def init_db():
    with sqlite3.connect(DATABASE) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            TEXT PRIMARY KEY,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                api_key       TEXT UNIQUE NOT NULL,
                created_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS actions (
                id             TEXT PRIMARY KEY,
                user_id        TEXT NOT NULL,
                action_type    TEXT NOT NULL,
                label          TEXT NOT NULL,
                fields         TEXT,
                status         TEXT NOT NULL,
                outcome        TEXT,
                approval_token TEXT UNIQUE,
                created_at     TEXT NOT NULL,
                decided_at     TEXT,
                expires_at     TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_actions_user  ON actions(user_id);
            CREATE INDEX IF NOT EXISTS idx_actions_token ON actions(approval_token);
            CREATE INDEX IF NOT EXISTS idx_users_key     ON users(api_key);
        """)

init_db()
print(f"[Mighty] GMAIL_USER={'set' if GMAIL_USER else 'NOT SET'}, GMAIL_PASS={'set' if GMAIL_PASS else 'NOT SET'}", flush=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def hash_pw(pw):
    salt = secrets.token_hex(16)
    h    = hashlib.sha256(f"{salt}{pw}".encode()).hexdigest()
    return f"{salt}:{h}"

def check_pw(stored, provided):
    salt, h = stored.split(":", 1)
    return hashlib.sha256(f"{salt}{provided}".encode()).hexdigest() == h

def utcnow():
    return datetime.now(timezone.utc)

def iso():
    return utcnow().isoformat()

def base_url():
    b = os.environ.get("BASE_URL", "").rstrip("/")
    return b if b else request.url_root.rstrip("/")

def require_login(f):
    @wraps(f)
    def inner(*a, **kw):
        if "user_id" not in session:
            return redirect("/login")
        return f(*a, **kw)
    return inner

def api_user():
    """Return user row from API key in request body or X-Mighty-Key header."""
    data = request.get_json(force=True, silent=True) or {}
    key  = data.get("api_key") or request.headers.get("X-Mighty-Key", "")
    if not key:
        return None, data
    row = get_db().execute("SELECT * FROM users WHERE api_key=?", (key,)).fetchone()
    return row, data

def expire_pending():
    """Mark timed-out pending authorizations as expired."""
    get_db().execute(
        "UPDATE actions SET status='timeout', decided_at=? "
        "WHERE status='pending' AND expires_at < ?",
        (iso(), iso()),
    )
    get_db().commit()

def fmt_time(iso_str):
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%-I:%M %p").lstrip("0") + " · " + dt.strftime("%b %-d")
    except Exception:
        return iso_str[:16]

STATUS_BADGE = {
    "logged":   ('<span class="badge badge-logged">Logged</span>', "#6b7280"),
    "pending":  ('<span class="badge badge-pending">Pending ⏳</span>', "#d97706"),
    "approved": ('<span class="badge badge-approved">Approved ✓</span>', "#16a34a"),
    "denied":   ('<span class="badge badge-denied">Denied ✗</span>', "#dc2626"),
    "timeout":  ('<span class="badge badge-timeout">Timed out</span>', "#9ca3af"),
}


# ── Email notifications ───────────────────────────────────────────────────────

def send_authorization_email(to_email, label, action_type, fields, approval_url):
    """Send an authorization request email via Gmail SMTP. Runs in a background thread."""
    if not GMAIL_USER or not GMAIL_PASS:
        print("[Mighty] Email skipped — GMAIL_USER or GMAIL_PASS not set", flush=True)
        return

    # Build fields rows
    fields_html = ""
    if fields:
        try:
            for k, v in (fields if isinstance(fields, list) else json.loads(fields)):
                fields_html += f'<tr><td style="padding:6px 0;color:#888;font-size:13px;width:120px;vertical-align:top">{k}</td><td style="padding:6px 0;color:#1a1a1a;font-size:13px">{v}</td></tr>'
        except Exception:
            pass

    fields_section = f'<table style="width:100%;border-collapse:collapse;margin:16px 0">{fields_html}</table>' if fields_html else ""

    html = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f8f7f5;font-family:Arial,sans-serif">
  <div style="max-width:480px;margin:40px auto;padding:0 16px">
    <div style="margin-bottom:20px">
      <span style="font-size:18px;font-weight:700;color:#1a1a1a">⚡ Mighty</span>
    </div>
    <div style="background:#fff;border:1px solid #e5e3df;border-radius:16px;overflow:hidden">
      <div style="background:#fffbeb;border-bottom:1px solid #fde68a;padding:12px 20px">
        <span style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#92400e">Authorization Required</span>
      </div>
      <div style="padding:20px">
        <div style="font-size:18px;font-weight:700;color:#1a1a1a;line-height:1.4;margin-bottom:4px">{label}</div>
        <div style="font-size:12px;color:#aaa;font-family:monospace;margin-bottom:8px">{action_type}</div>
        {fields_section}
        <div style="font-size:12px;color:#888;margin-bottom:20px">Your AI agent is waiting. This request expires in 5 minutes.</div>
        <a href="{approval_url}" style="display:block;padding:14px;background:#7c3aed;color:#fff;text-decoration:none;border-radius:10px;font-size:15px;font-weight:700;text-align:center">
          Review &amp; Decide →
        </a>
        <div style="text-align:center;margin-top:10px;font-size:11px;color:#bbb">Opens a page where you can approve or deny.</div>
      </div>
    </div>
  </div>
</body>
</html>"""

    def _send():
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"Action needed: {label}"
            msg["From"]    = f"Mighty <{GMAIL_USER}>"
            msg["To"]      = to_email
            msg.attach(MIMEText(html, "html"))
            with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(GMAIL_USER, GMAIL_PASS)
                smtp.sendmail(GMAIL_USER, to_email, msg.as_string())
        except Exception as e:
            print(f"[Mighty] Email send failed: {e}", flush=True)

    threading.Thread(target=_send, daemon=True).start()


# ── HTML ──────────────────────────────────────────────────────────────────────

BASE_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',sans-serif;background:#f8f7f5;color:#1a1a1a;min-height:100vh}
a{color:#7c3aed;text-decoration:none}
a:hover{text-decoration:underline}
input{font-family:inherit}
button{font-family:inherit;cursor:pointer}
.badge{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:20px;letter-spacing:0.3px}
.badge-logged{background:#f3f4f6;color:#6b7280}
.badge-pending{background:#fef3c7;color:#d97706}
.badge-approved{background:#f0fdf4;color:#16a34a}
.badge-denied{background:#fef2f2;color:#dc2626}
.badge-timeout{background:#f3f4f6;color:#9ca3af}
"""

LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Mighty — Personal Authorization for AI Agents</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;align-items:center;justify-content:center;padding:24px}
.card{background:#fff;border:1px solid #e5e3df;border-radius:16px;padding:40px;width:100%;max-width:440px;box-shadow:0 4px 24px rgba(0,0,0,0.06)}
.logo{display:flex;align-items:center;gap:10px;margin-bottom:28px}
.logo-mark{width:32px;height:32px;background:linear-gradient(135deg,#7c3aed,#4f46e5);border-radius:8px;display:flex;align-items:center;justify-content:center}
.logo-mark svg{width:18px;height:18px}
.logo-name{font-size:18px;font-weight:700;color:#1a1a1a}
.logo-tag{font-size:11px;color:#aaa;font-weight:500}
h1{font-size:22px;font-weight:700;margin-bottom:8px;color:#1a1a1a}
.sub{font-size:14px;color:#666;line-height:1.6;margin-bottom:24px}
.bullets{display:flex;flex-direction:column;gap:8px;margin-bottom:28px}
.bullet{display:flex;align-items:flex-start;gap:10px;font-size:13px;color:#444;line-height:1.5}
.bullet-dot{width:20px;height:20px;border-radius:50%;background:#f3f0ff;color:#7c3aed;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px}
.divider{border:none;border-top:1px solid #f0ede8;margin:20px 0}
label{display:block;font-size:12px;font-weight:600;color:#555;margin-bottom:5px;letter-spacing:0.3px}
input[type=email],input[type=password]{width:100%;padding:10px 12px;border:1.5px solid #e5e3df;border-radius:8px;font-size:14px;color:#1a1a1a;background:#fff;transition:border-color 0.12s;margin-bottom:14px}
input:focus{outline:none;border-color:#7c3aed}
.btn-primary{width:100%;padding:12px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;transition:background 0.12s;margin-top:4px}
.btn-primary:hover{background:#6d28d9}
.err{font-size:13px;color:#dc2626;background:#fef2f2;border:1px solid #fecaca;border-radius:7px;padding:9px 12px;margin-bottom:14px}
.footer{text-align:center;margin-top:20px;font-size:13px;color:#888}
</style>
</head>
<body>
<div class="card">
  <div class="logo">
    <div class="logo-mark">
      <svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M10 2L3 6v8l7 4 7-4V6l-7-4z" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/>
        <path d="M10 2v12M3 6l7 4 7-4" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/>
      </svg>
    </div>
    <div><div class="logo-name">Mighty</div><div class="logo-tag">Self-Serve</div></div>
  </div>
  <h1>Your AI agents, accountable.</h1>
  <p class="sub">A personal authorization layer for AI agents. Know what they do, approve what matters.</p>
  <div class="bullets">
    <div class="bullet"><div class="bullet-dot">1</div>Sign up and get a personal API key</div>
    <div class="bullet"><div class="bullet-dot">2</div>Paste one system prompt into your Claude project</div>
    <div class="bullet"><div class="bullet-dot">3</div>Every agent action is logged — and you approve the important ones</div>
  </div>
  <hr class="divider">
  {error}
  <form method="POST" action="/signup">
    <label>Email</label>
    <input type="email" name="email" placeholder="you@example.com" required autocomplete="email">
    <label>Password</label>
    <input type="password" name="password" placeholder="Choose a password" required autocomplete="new-password">
    <button class="btn-primary" type="submit">Create free account →</button>
  </form>
  <div class="footer">Already have an account? <a href="/login">Sign in</a></div>
</div>
</body>
</html>"""

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Sign in — Mighty</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;align-items:center;justify-content:center;padding:24px}
.card{background:#fff;border:1px solid #e5e3df;border-radius:16px;padding:40px;width:100%;max-width:400px;box-shadow:0 4px 24px rgba(0,0,0,0.06)}
.logo{display:flex;align-items:center;gap:10px;margin-bottom:28px}
.logo-mark{width:32px;height:32px;background:linear-gradient(135deg,#7c3aed,#4f46e5);border-radius:8px;display:flex;align-items:center;justify-content:center}
.logo-mark svg{width:18px;height:18px}
.logo-name{font-size:18px;font-weight:700;color:#1a1a1a}
h1{font-size:20px;font-weight:700;margin-bottom:20px}
label{display:block;font-size:12px;font-weight:600;color:#555;margin-bottom:5px;letter-spacing:0.3px}
input[type=email],input[type=password]{width:100%;padding:10px 12px;border:1.5px solid #e5e3df;border-radius:8px;font-size:14px;color:#1a1a1a;background:#fff;transition:border-color 0.12s;margin-bottom:14px}
input:focus{outline:none;border-color:#7c3aed}
.btn-primary{width:100%;padding:12px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;transition:background 0.12s}
.btn-primary:hover{background:#6d28d9}
.err{font-size:13px;color:#dc2626;background:#fef2f2;border:1px solid #fecaca;border-radius:7px;padding:9px 12px;margin-bottom:14px}
.footer{text-align:center;margin-top:20px;font-size:13px;color:#888}
</style>
</head>
<body>
<div class="card">
  <div class="logo">
    <div class="logo-mark">
      <svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M10 2L3 6v8l7 4 7-4V6l-7-4z" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/>
        <path d="M10 2v12M3 6l7 4 7-4" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/>
      </svg>
    </div>
    <div class="logo-name">Mighty</div>
  </div>
  <h1>Welcome back</h1>
  {error}
  <form method="POST" action="/login">
    <label>Email</label>
    <input type="email" name="email" placeholder="you@example.com" required autocomplete="email">
    <label>Password</label>
    <input type="password" name="password" placeholder="Your password" required autocomplete="current-password">
    <button class="btn-primary" type="submit">Sign in →</button>
  </form>
  <div class="footer">No account? <a href="/">Sign up free</a></div>
</div>
</body>
</html>"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Dashboard — Mighty</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;flex-direction:column;min-height:100vh}
.topbar{background:#1a1a2e;color:#fff;padding:0 24px;height:52px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
.topbar-logo{display:flex;align-items:center;gap:10px}
.topbar-logo-mark{width:24px;height:24px;background:linear-gradient(135deg,#7c3aed,#4f46e5);border-radius:6px;display:flex;align-items:center;justify-content:center}
.topbar-logo-mark svg{width:14px;height:14px}
.topbar-name{font-size:15px;font-weight:700;color:#fff}
.topbar-email{font-size:12px;color:rgba(255,255,255,0.5)}
.topbar-right{display:flex;align-items:center;gap:16px}
.btn-logout{font-size:12px;color:rgba(255,255,255,0.5);background:none;border:none;cursor:pointer;padding:4px 8px;border-radius:5px;transition:background 0.12s}
.btn-logout:hover{background:rgba(255,255,255,0.1);color:#fff}
.main{flex:1;display:grid;grid-template-columns:340px 1fr;gap:0;max-width:1200px;width:100%;margin:0 auto;padding:28px 24px;gap:24px}
@media(max-width:768px){.main{grid-template-columns:1fr}}
.sidebar{display:flex;flex-direction:column;gap:16px}
.card{background:#fff;border:1px solid #e5e3df;border-radius:12px;padding:20px}
.card-title{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#aaa;margin-bottom:14px}
.api-key-row{display:flex;align-items:center;gap:8px}
.api-key-val{flex:1;font-family:ui-monospace,monospace;font-size:12px;color:#1a1a1a;background:#f8f7f5;border:1px solid #e5e3df;border-radius:6px;padding:8px 10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.btn-copy{font-size:11px;font-weight:600;padding:6px 12px;background:#f3f0ff;color:#7c3aed;border:1px solid #e9d5ff;border-radius:6px;white-space:nowrap;transition:background 0.12s}
.btn-copy:hover{background:#ede9fe}
.prompt-box{font-family:ui-monospace,monospace;font-size:11px;color:#444;background:#f8f7f5;border:1px solid #e5e3df;border-radius:8px;padding:12px;white-space:pre-wrap;line-height:1.6;max-height:220px;overflow-y:auto;margin-bottom:10px}
.btn-copy-prompt{font-size:12px;font-weight:600;padding:8px 14px;background:#7c3aed;color:#fff;border:none;border-radius:7px;width:100%;transition:background 0.12s}
.btn-copy-prompt:hover{background:#6d28d9}
.feed{display:flex;flex-direction:column;gap:12px}
.pending-section{margin-bottom:8px}
.pending-title{font-size:12px;font-weight:700;color:#d97706;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:10px;display:flex;align-items:center;gap:6px}
.pending-dot{width:7px;height:7px;border-radius:50%;background:#f59e0b;animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
.action-card{background:#fff;border:1px solid #e5e3df;border-radius:10px;overflow:hidden;transition:box-shadow 0.12s}
.action-card:hover{box-shadow:0 2px 12px rgba(0,0,0,0.08)}
.action-card.is-pending{border-color:#fbbf24;box-shadow:0 0 0 1px #fbbf24}
.action-header{padding:14px 16px 10px;display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.action-label{font-size:14px;font-weight:600;color:#1a1a1a;line-height:1.4}
.action-type{font-size:11px;color:#aaa;margin-top:2px;font-family:ui-monospace,monospace}
.action-meta{text-align:right;flex-shrink:0}
.action-time{font-size:11px;color:#bbb;margin-top:4px}
.action-fields{padding:0 16px 12px;display:flex;flex-direction:column;gap:6px}
.field-row{display:flex;gap:8px;font-size:12px}
.field-key{color:#aaa;font-weight:600;min-width:80px;flex-shrink:0}
.field-val{color:#555;line-height:1.4;word-break:break-word}
.action-decision{padding:12px 16px;border-top:1px solid #f0ede8;display:flex;gap:8px}
.btn-approve{flex:1;padding:9px;background:#16a34a;color:#fff;border:none;border-radius:7px;font-size:13px;font-weight:600;transition:background 0.12s}
.btn-approve:hover{background:#15803d}
.btn-deny{flex:1;padding:9px;background:#fff;color:#dc2626;border:1.5px solid #fecaca;border-radius:7px;font-size:13px;font-weight:600;transition:all 0.12s}
.btn-deny:hover{background:#fef2f2}
.empty-feed{text-align:center;padding:60px 20px;color:#bbb;font-size:14px}
.empty-feed-icon{font-size:32px;margin-bottom:12px;opacity:0.4}
.content-title{font-size:16px;font-weight:700;margin-bottom:4px}
.content-sub{font-size:13px;color:#888;margin-bottom:20px}
</style>
</head>
<body>
<div class="topbar">
  <div class="topbar-logo">
    <div class="topbar-logo-mark">
      <svg viewBox="0 0 20 20" fill="none"><path d="M10 2L3 6v8l7 4 7-4V6l-7-4z" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/><path d="M10 2v12M3 6l7 4 7-4" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></svg>
    </div>
    <span class="topbar-name">Mighty</span>
  </div>
  <div class="topbar-right">
    <span class="topbar-email">{email}</span>
    <form method="POST" action="/logout" style="margin:0"><button class="btn-logout" type="submit">Sign out</button></form>
  </div>
</div>

<div class="main">
  <!-- Sidebar -->
  <div class="sidebar">
    <div class="card">
      <div class="card-title">Your API Key</div>
      <div class="api-key-row">
        <div class="api-key-val" id="apiKeyVal">{api_key}</div>
        <button class="btn-copy" onclick="copyKey()">Copy</button>
      </div>
    </div>
    <div class="card">
      <div class="card-title">System Prompt</div>
      <div class="prompt-box" id="promptBox">{prompt}</div>
      <button class="btn-copy-prompt" onclick="copyPrompt()">Copy system prompt</button>
      <div style="font-size:11px;color:#bbb;margin-top:10px;line-height:1.6">Paste this into your Claude Project's custom instructions. Your agent will log actions and request your approval before taking consequential steps.</div>
    </div>
    <div class="card" style="background:#faf5ff;border-color:#e9d5ff">
      <div class="card-title" style="color:#7c3aed">How it works</div>
      <div style="font-size:12px;color:#555;line-height:1.7">
        1. Agent logs routine actions silently<br>
        2. Before anything consequential, it requests your approval<br>
        3. A card appears here — approve or deny from your phone or desktop<br>
        4. Agent waits up to 5 min for your decision
      </div>
    </div>
  </div>

  <!-- Main feed -->
  <div>
    <div class="content-title">Authorization Log</div>
    <div class="content-sub">All actions your agents have logged or requested approval for</div>
    <div class="feed" id="feed">
      {feed_html}
    </div>
  </div>
</div>

<script>
function copyKey() {
  navigator.clipboard.writeText(document.getElementById('apiKeyVal').textContent.trim());
  event.target.textContent = 'Copied!';
  setTimeout(() => event.target.textContent = 'Copy', 1500);
}
function copyPrompt() {
  navigator.clipboard.writeText(document.getElementById('promptBox').textContent);
  event.target.textContent = 'Copied!';
  setTimeout(() => event.target.textContent = 'Copy system prompt', 1500);
}
function decide(actionId, decision) {
  fetch('/dashboard/decide/' + actionId, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({decision})
  }).then(() => location.reload());
}
// Auto-refresh every 4s so pending cards appear without manual reload
var hasPending = document.querySelectorAll('.is-pending').length > 0;
if (hasPending) {
  setInterval(() => location.reload(), 4000);
} else {
  setInterval(() => {
    fetch('/dashboard/has-pending').then(r => r.json()).then(d => {
      if (d.pending) location.reload();
    });
  }, 4000);
}
</script>
</body>
</html>"""

APPROVE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Authorize action — Mighty</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px;background:#f8f7f5}
.wrap{width:100%;max-width:440px}
.brand{display:flex;align-items:center;gap:8px;margin-bottom:20px;justify-content:center}
.brand-mark{width:28px;height:28px;background:linear-gradient(135deg,#7c3aed,#4f46e5);border-radius:7px;display:flex;align-items:center;justify-content:center}
.brand-mark svg{width:16px;height:16px}
.brand-name{font-size:16px;font-weight:700;color:#1a1a1a}
.card{background:#fff;border:1px solid #e5e3df;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08)}
.card-header{background:#fffbeb;border-bottom:1px solid #fde68a;padding:16px 20px;display:flex;align-items:center;gap:10px}
.card-header-dot{width:8px;height:8px;border-radius:50%;background:#f59e0b;animation:pulse 1.5s infinite;flex-shrink:0}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
.card-header-text{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#92400e}
.card-headline{font-size:18px;font-weight:700;color:#1a1a1a;padding:18px 20px 4px;line-height:1.4}
.card-type{font-size:12px;color:#aaa;padding:0 20px 16px;font-family:ui-monospace,monospace}
.card-fields{padding:0 20px 16px;display:flex;flex-direction:column;gap:10px;border-bottom:1px solid #f0ede8}
.field-label{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:#bbb;margin-bottom:2px}
.field-value{font-size:13px;color:#1a1a1a;line-height:1.5;word-break:break-word}
.card-actions{padding:16px 20px;display:flex;gap:10px}
.btn-approve{flex:1;padding:13px;background:#16a34a;color:#fff;border:none;border-radius:10px;font-size:15px;font-weight:700;transition:background 0.12s}
.btn-approve:hover{background:#15803d}
.btn-approve:active{transform:scale(0.98)}
.btn-deny{flex:1;padding:13px;background:#fff;color:#dc2626;border:2px solid #fecaca;border-radius:10px;font-size:15px;font-weight:700;transition:all 0.12s}
.btn-deny:hover{background:#fef2f2}
.btn-deny:active{transform:scale(0.98)}
.outcome{text-align:center;padding:28px 20px;font-size:15px;font-weight:600}
.outcome.approved{color:#16a34a;background:#f0fdf4}
.outcome.denied{color:#dc2626;background:#fef2f2}
.outcome.timeout{color:#9ca3af;background:#f9fafb}
.timeout-note{font-size:12px;color:#bbb;text-align:center;padding:10px 20px 16px;line-height:1.5}
</style>
</head>
<body>
<div class="wrap">
  <div class="brand">
    <div class="brand-mark">
      <svg viewBox="0 0 20 20" fill="none"><path d="M10 2L3 6v8l7 4 7-4V6l-7-4z" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/><path d="M10 2v12M3 6l7 4 7-4" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></svg>
    </div>
    <span class="brand-name">Mighty</span>
  </div>
  <div class="card">
    {body}
  </div>
  <div style="text-align:center;margin-top:16px;font-size:12px;color:#bbb">Your AI agent is waiting for this decision.</div>
</div>
</body>
</html>"""


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/")
def landing():
    if "user_id" in session:
        return redirect("/dashboard")
    return LANDING_HTML.replace("{error}", "")

@app.route("/signup", methods=["POST"])
def signup():
    email    = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    if not email or not password or len(password) < 6:
        err = '<div class="err">Please enter a valid email and a password (6+ characters).</div>'
        return LANDING_HTML.replace("{error}", err)
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        err = '<div class="err">An account with that email already exists. <a href="/login">Sign in</a></div>'
        return LANDING_HTML.replace("{error}", err)
    uid = secrets.token_hex(16)
    key = "mk_" + secrets.token_hex(20)
    db.execute(
        "INSERT INTO users (id,email,password_hash,api_key,created_at) VALUES (?,?,?,?,?)",
        (uid, email, hash_pw(password), key, iso()),
    )
    db.commit()
    session["user_id"] = uid
    session["email"]   = email
    return redirect("/dashboard")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return LOGIN_HTML.replace("{error}", "")
    email    = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    row = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not row or not check_pw(row["password_hash"], password):
        err = '<div class="err">Incorrect email or password.</div>'
        return LOGIN_HTML.replace("{error}", err)
    session["user_id"] = row["id"]
    session["email"]   = row["email"]
    return redirect("/dashboard")

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect("/login")


# ── Dashboard ─────────────────────────────────────────────────────────────────

def build_prompt(api_key, url):
    return (
        f"MIGHTY AUTHORIZATION — read and follow these rules every session.\n\n"
        f"Before any consequential action (sending messages or emails, making purchases,\n"
        f"modifying important files, calling external APIs, sharing data with third parties),\n"
        f"you MUST request authorization:\n\n"
        f"  POST {url}/api/authorize\n"
        f"  {{\"api_key\":\"{api_key}\",\"action_type\":\"<category>\",\n"
        f"   \"label\":\"<what you are about to do>\",\n"
        f"   \"fields\":[[\"Key\",\"Value\"],...]  }}\n\n"
        f"Response handling:\n"
        f"  approved → proceed\n"
        f"  denied   → stop, tell the user it was denied\n"
        f"  pending  → poll GET {url}/api/status/<request_id> every 3s\n"
        f"             until approved / denied / timeout (5 min)\n\n"
        f"For routine actions that don't need approval, log them:\n\n"
        f"  POST {url}/api/record\n"
        f"  {{\"api_key\":\"{api_key}\",\"action_type\":\"<category>\",\n"
        f"   \"label\":\"<what happened>\",\"outcome\":\"completed\",\n"
        f"   \"fields\":[[\"Key\",\"Value\"],...]  }}"
    )

def build_feed_html(actions, base):
    if not actions:
        return '<div class="empty-feed"><div class="empty-feed-icon">✦</div>No actions yet.<br>Paste the system prompt into Claude to get started.</div>'
    html = []
    pending = [a for a in actions if a["status"] == "pending"]
    rest    = [a for a in actions if a["status"] != "pending"]
    if pending:
        html.append('<div class="pending-section">')
        html.append('<div class="pending-title"><div class="pending-dot"></div>Awaiting your decision</div>')
        for a in pending:
            html.append(action_card_html(a, base, show_buttons=True))
        html.append('</div>')
    for a in rest:
        html.append(action_card_html(a, base, show_buttons=False))
    return "\n".join(html)

def action_card_html(a, base, show_buttons):
    badge, _ = STATUS_BADGE.get(a["status"], ('', ''))
    fields_html = ""
    if a["fields"]:
        try:
            flist = json.loads(a["fields"])
            for k, v in flist:
                val = v if isinstance(v, str) else json.dumps(v)
                fields_html += f'<div class="field-row"><span class="field-key">{k}</span><span class="field-val">{val}</span></div>'
        except Exception:
            pass
    pending_cls = " is-pending" if a["status"] == "pending" else ""
    approve_url = f'{base}/approve/{a["approval_token"]}' if a["approval_token"] else ""
    btns = ""
    if show_buttons:
        btns = f'''<div class="action-decision">
          <button class="btn-approve" onclick="decide('{a["id"]}','approve')">Approve</button>
          <button class="btn-deny"    onclick="decide('{a["id"]}','deny')">Deny</button>
        </div>'''
        if approve_url:
            btns += f'<div style="padding:0 16px 12px;font-size:11px;color:#bbb">Or open <a href="{approve_url}" target="_blank" style="color:#7c3aed">approval link</a> on your phone</div>'
    return f'''<div class="action-card{pending_cls}">
      <div class="action-header">
        <div><div class="action-label">{a["label"]}</div><div class="action-type">{a["action_type"]}</div></div>
        <div class="action-meta">{badge}<div class="action-time">{fmt_time(a["created_at"])}</div></div>
      </div>
      {'<div class="action-fields">' + fields_html + '</div>' if fields_html else ''}
      {btns}
    </div>'''

@app.route("/dashboard")
@require_login
def dashboard():
    expire_pending()
    db    = get_db()
    user  = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    acts  = db.execute(
        "SELECT * FROM actions WHERE user_id=? ORDER BY created_at DESC LIMIT 100",
        (session["user_id"],),
    ).fetchall()
    url   = base_url()
    prompt = build_prompt(user["api_key"], url)
    feed   = build_feed_html(acts, url)
    return (DASHBOARD_HTML
            .replace("{email}",   user["email"])
            .replace("{api_key}", user["api_key"])
            .replace("{prompt}",  prompt)
            .replace("{feed_html}", feed))

@app.route("/dashboard/decide/<action_id>", methods=["POST"])
@require_login
def decide(action_id):
    data     = request.get_json(force=True)
    decision = data.get("decision")
    if decision not in ("approve", "deny"):
        return jsonify({"error": "invalid"}), 400
    status = "approved" if decision == "approve" else "denied"
    db = get_db()
    db.execute(
        "UPDATE actions SET status=?, decided_at=? WHERE id=? AND user_id=? AND status='pending'",
        (status, iso(), action_id, session["user_id"]),
    )
    db.commit()
    return jsonify({"status": status})

@app.route("/dashboard/has-pending")
@require_login
def has_pending():
    expire_pending()
    row = get_db().execute(
        "SELECT 1 FROM actions WHERE user_id=? AND status='pending' LIMIT 1",
        (session["user_id"],),
    ).fetchone()
    return jsonify({"pending": bool(row)})


# ── Token-based approval page (no login required) ─────────────────────────────

@app.route("/approve/<token>", methods=["GET"])
def approve_page(token):
    expire_pending()
    db  = get_db()
    row = db.execute("SELECT * FROM actions WHERE approval_token=?", (token,)).fetchone()
    if not row:
        body = '<div class="outcome timeout">Authorization request not found.</div>'
        return APPROVE_HTML.replace("{body}", body)
    if row["status"] != "pending":
        labels = {"approved": "✓ Approved", "denied": "✗ Denied", "timeout": "⏰ Timed out"}
        label  = labels.get(row["status"], row["status"].title())
        body   = f'<div class="outcome {row["status"]}">{label}</div>'
        return APPROVE_HTML.replace("{body}", body)
    # Build fields HTML
    fields_html = ""
    if row["fields"]:
        try:
            for k, v in json.loads(row["fields"]):
                val = v if isinstance(v, str) else json.dumps(v)
                fields_html += f'<div style="margin-bottom:12px"><div class="field-label">{k}</div><div class="field-value">{val}</div></div>'
        except Exception:
            pass
    body = f"""
      <div class="card-header"><div class="card-header-dot"></div><span class="card-header-text">Authorization Required</span></div>
      <div class="card-headline">{row["label"]}</div>
      <div class="card-type">{row["action_type"]}</div>
      {'<div class="card-fields">' + fields_html + '</div>' if fields_html else ''}
      <div class="card-actions">
        <button class="btn-approve" onclick="submit('approve')">Approve</button>
        <button class="btn-deny"    onclick="submit('deny')">Deny</button>
      </div>
      <div class="timeout-note">This request will time out in 5 minutes if not decided.</div>
      <script>
      function submit(dec) {{
        fetch('/approve/{token}', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{decision:dec}})}})
          .then(r=>r.json()).then(d=>{{
            document.querySelector('.card').innerHTML =
              '<div class="outcome ' + d.status + '">' + (d.status==='approved'?'✓ Approved':'✗ Denied') + '</div>';
          }});
      }}
      </script>"""
    return APPROVE_HTML.replace("{body}", body)

@app.route("/approve/<token>", methods=["POST"])
def approve_submit(token):
    data     = request.get_json(force=True)
    decision = data.get("decision")
    if decision not in ("approve", "deny"):
        return jsonify({"error": "invalid"}), 400
    status = "approved" if decision == "approve" else "denied"
    db = get_db()
    res = db.execute(
        "UPDATE actions SET status=?, decided_at=? WHERE approval_token=? AND status='pending'",
        (status, iso(), token),
    )
    db.commit()
    if res.rowcount == 0:
        return jsonify({"error": "not found or already decided"}), 404
    return jsonify({"status": status})


# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/api/record", methods=["POST"])
def api_record():
    """Log a completed action — no approval needed."""
    user, data = api_user()
    if not user:
        return jsonify({"error": "Invalid or missing api_key"}), 401
    action_type = data.get("action_type", "other")
    label       = data.get("label", "Action")
    fields      = data.get("fields")
    outcome     = data.get("outcome", "completed")
    action_id   = secrets.token_hex(16)
    get_db().execute(
        "INSERT INTO actions (id,user_id,action_type,label,fields,status,outcome,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (action_id, user["id"], action_type, label,
         json.dumps(fields) if fields else None, "logged", outcome, iso()),
    )
    get_db().commit()
    return jsonify({"status": "logged", "record_id": action_id})

@app.route("/api/authorize", methods=["POST"])
def api_authorize():
    """Request authorization for a consequential action. Returns pending + approval URL."""
    user, data = api_user()
    if not user:
        return jsonify({"error": "Invalid or missing api_key"}), 401
    action_type    = data.get("action_type", "other")
    label          = data.get("label", "Action")
    fields         = data.get("fields")
    action_id      = secrets.token_hex(16)
    approval_token = secrets.token_urlsafe(24)
    expires_at     = (utcnow() + timedelta(seconds=TIMEOUT_SEC)).isoformat()
    get_db().execute(
        "INSERT INTO actions "
        "(id,user_id,action_type,label,fields,status,approval_token,created_at,expires_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (action_id, user["id"], action_type, label,
         json.dumps(fields) if fields else None,
         "pending", approval_token, iso(), expires_at),
    )
    get_db().commit()
    url          = base_url()
    approval_url = f"{url}/approve/{approval_token}"
    # Fire-and-forget email notification
    send_authorization_email(
        to_email=user["email"],
        label=label,
        action_type=action_type,
        fields=fields,
        approval_url=approval_url,
    )
    return jsonify({
        "status":       "pending",
        "request_id":   action_id,
        "approval_url": approval_url,
        "poll_url":     f"{url}/api/status/{action_id}",
        "expires_in":   TIMEOUT_SEC,
    })

@app.route("/api/status/<action_id>", methods=["GET"])
def api_status(action_id):
    """Poll for the status of a pending authorization."""
    key = request.headers.get("X-Mighty-Key") or request.args.get("api_key", "")
    user = get_db().execute("SELECT * FROM users WHERE api_key=?", (key,)).fetchone()
    if not user:
        return jsonify({"error": "Invalid or missing api_key"}), 401
    expire_pending()
    row = get_db().execute(
        "SELECT status, decided_at FROM actions WHERE id=? AND user_id=?",
        (action_id, user["id"]),
    ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify({"status": row["status"], "decided_at": row["decided_at"]})


# ── Health check ──────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"ok": True})


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
