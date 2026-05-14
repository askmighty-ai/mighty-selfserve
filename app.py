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

import os, io, csv, json, secrets, hashlib, sqlite3, threading, urllib.request, urllib.error, html

from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify, session, redirect, g, make_response

def he(s):
    """HTML-escape a value for safe insertion into HTML."""
    return html.escape(str(s)) if s is not None else ""

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["SESSION_COOKIE_HTTPONLY"]  = True
app.config["SESSION_COOKIE_SAMESITE"]  = "Lax"
app.config["SESSION_COOKIE_SECURE"]    = os.environ.get("RAILWAY_ENVIRONMENT") == "production"

DATABASE        = os.environ.get("DATABASE_PATH", "mighty.db")
PORT            = int(os.environ.get("PORT", 5004))
TIMEOUT_SEC     = 300  # pending authorization expires after 5 minutes
POSTMARK_API_KEY = os.environ.get("POSTMARK_API_KEY", "")
POSTMARK_FROM    = os.environ.get("POSTMARK_FROM", "Mighty <noreply@mighty.ai>")
NOTIFY_EMAIL_OVERRIDE = os.environ.get("NOTIFY_EMAIL", "")  # override recipient for sandbox testing
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

LOGO_ICON_B64 = "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"


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
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                subscription TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_push_user ON push_subscriptions(user_id);
            CREATE TABLE IF NOT EXISTS enterprise_leads (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                email      TEXT NOT NULL,
                company    TEXT,
                message    TEXT,
                created_at TEXT NOT NULL
            );
        """)
        try:
            db.execute("ALTER TABLE actions ADD COLUMN consequence_level TEXT DEFAULT 'routine'")
        except Exception:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE users ADD COLUMN notify_email INTEGER DEFAULT 1")
        except Exception:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE users ADD COLUMN notify_ntfy INTEGER DEFAULT 1")
        except Exception:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE users ADD COLUMN notify_push INTEGER DEFAULT 1")
        except Exception:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE users ADD COLUMN onboarded INTEGER DEFAULT 0")
        except Exception:
            pass

init_db()
print(f"[Mighty] POSTMARK_API_KEY={'set' if POSTMARK_API_KEY else 'NOT SET'}", flush=True)


# ── VAPID key management ──────────────────────────────────────────────────────

def get_vapid_keys():
    """Return (private_key_base64url, public_key_base64url) — generating once and caching in DB."""
    import base64
    from py_vapid import Vapid
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    def _generate(db):
        v = Vapid()
        v.generate_keys()
        # Raw 32-byte EC private scalar → base64url (what pywebpush 2.x expects)
        priv_int   = v.private_key.private_numbers().private_value
        priv       = base64.urlsafe_b64encode(priv_int.to_bytes(32, 'big')).rstrip(b'=').decode()
        # Uncompressed EC point → base64url (what browsers expect as applicationServerKey)
        pub_bytes  = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        pub        = base64.urlsafe_b64encode(pub_bytes).rstrip(b'=').decode()
        db.execute("INSERT OR REPLACE INTO settings VALUES ('vapid_private', ?)", (priv,))
        db.execute("INSERT OR REPLACE INTO settings VALUES ('vapid_public',  ?)", (pub,))
        db.commit()
        return priv, pub

    with sqlite3.connect(DATABASE) as db:
        db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        row = db.execute("SELECT value FROM settings WHERE key='vapid_private'").fetchone()
        if row and not row[0].startswith('-----BEGIN'):
            # Already stored in correct raw base64url format
            priv = row[0]
            pub  = db.execute("SELECT value FROM settings WHERE key='vapid_public'").fetchone()[0]
            return priv, pub
        # First run, or old PEM format — generate fresh keys
        return _generate(db)

VAPID_PRIVATE, VAPID_PUBLIC = get_vapid_keys()
print(f"[Mighty] VAPID public key: {VAPID_PUBLIC[:20]}...", flush=True)


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
    "logged":   '<span class="badge badge-logged">Logged</span>',
    "pending":  '<span class="badge badge-pending">Pending</span>',
    "approved": '<span class="badge badge-approved">Approved</span>',
    "denied":   '<span class="badge badge-denied">Denied</span>',
    "timeout":  '<span class="badge badge-timeout">Timed out</span>',
}


# ── Email notifications ───────────────────────────────────────────────────────

def send_authorization_email(to_email, label, action_type, fields, approval_url):
    """Send an authorization request email via Postmark API. Runs in a background thread."""
    if not POSTMARK_API_KEY:
        print("[Mighty] Email skipped — POSTMARK_API_KEY not set", flush=True)
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
      <div style="background:#f5f3ff;border-bottom:1px solid #e9d5ff;padding:12px 20px">
        <span style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#5b21b6">Authorization Required</span>
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

    payload = json.dumps({
        "From":     POSTMARK_FROM,
        "To":       to_email,
        "Subject":  f"Action needed: {label}",
        "HtmlBody": html,
    }).encode()

    def _send():
        try:
            req = urllib.request.Request(
                "https://api.postmarkapp.com/email",
                data=payload,
                headers={
                    "X-Postmark-Server-Token": POSTMARK_API_KEY,
                    "Content-Type":            "application/json",
                    "Accept":                  "application/json",
                },
            )
            urllib.request.urlopen(req, timeout=10)
            print("[Mighty] Email sent successfully", flush=True)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            print(f"[Mighty] Email send failed: HTTP {e.code} — {body}", flush=True)
        except Exception as e:
            print(f"[Mighty] Email send failed: {e}", flush=True)

    threading.Thread(target=_send, daemon=True).start()


def ntfy_topic(api_key):
    """Derive a stable, user-specific ntfy.sh topic from their API key."""
    return "mighty-" + api_key[:12]


def send_ntfy_notification(api_key, label, action_type, approval_url):
    """Send a push notification via ntfy.sh. No account or API key required."""
    topic = ntfy_topic(api_key)

    def _send():
        try:
            payload = label.encode()
            req = urllib.request.Request(
                f"https://ntfy.sh/{topic}",
                data=payload,
                headers={
                    "Title":    f"Action needed: {action_type}",
                    "Priority": "high",
                    "Tags":     "rotating_light",
                    "Click":    approval_url,
                    "Actions":  f"view, Review & Decide, {approval_url}",
                },
            )
            urllib.request.urlopen(req, timeout=10)
            print(f"[Mighty] ntfy notification sent to topic {topic}", flush=True)
        except Exception as e:
            print(f"[Mighty] ntfy notification failed: {e}", flush=True)

    threading.Thread(target=_send, daemon=True).start()


def send_web_push(user_id, title, body, url, action_id=None):
    """Send a Web Push notification to all subscriptions for a user."""
    rows = get_db().execute(
        "SELECT id, subscription FROM push_subscriptions WHERE user_id=?", (user_id,)
    ).fetchall()
    if not rows:
        print(f"[Mighty] No push subscriptions for user {user_id[:8]}", flush=True)
        return

    actions = []
    if action_id:
        actions = [{"action": "open", "title": "Review"}]

    payload = json.dumps({
        "title":   title,
        "body":    body,
        "url":     url,
        "tag":     f"mighty-{action_id[:8] if action_id else 'notif'}",
        "actions": actions,
    })

    sub_ids  = [r["id"] for r in rows]
    sub_data = [(r["id"], json.loads(r["subscription"])) for r in rows]

    def _send():
        from pywebpush import webpush, WebPushException
        import sqlite3 as _sqlite3
        stale_ids = []
        for sub_id, sub in sub_data:
            try:
                webpush(
                    subscription_info=sub,
                    data=payload,
                    vapid_private_key=VAPID_PRIVATE,
                    vapid_claims={"sub": "mailto:noreply@mighty.ai"},
                    content_encoding="aes128gcm",
                )
                print(f"[Mighty] Web push sent to user {user_id[:8]}", flush=True)
            except WebPushException as e:
                resp = e.response
                status = resp.status_code if resp is not None else 0
                print(f"[Mighty] Web push failed ({status}): {e}", flush=True)
                if status in (404, 410, 403):
                    # Subscription expired or invalid — remove it
                    stale_ids.append(sub_id)
            except Exception as e:
                print(f"[Mighty] Web push error: {e}", flush=True)
        if stale_ids:
            with _sqlite3.connect(DATABASE) as db:
                db.executemany("DELETE FROM push_subscriptions WHERE id=?",
                               [(sid,) for sid in stale_ids])
                db.commit()
            print(f"[Mighty] Removed {len(stale_ids)} stale subscription(s)", flush=True)

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
<title>Mighty — Your AI agents, accountable to you.</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
html{scroll-behavior:smooth}
body{background:#fff;color:#1a1a1a}
/* Nav */
.nav{position:sticky;top:0;z-index:100;background:#fff;border-bottom:1px solid #e5e3df;height:60px;display:flex;align-items:center;padding:0 24px}
.nav-inner{max-width:900px;margin:0 auto;width:100%;display:flex;align-items:center;justify-content:space-between}
.logo{display:flex;align-items:center;gap:10px}
.logo-mark{width:32px;height:32px;display:flex;align-items:center;justify-content:center}
.logo-mark img{height:32px;width:auto}
.logo-name{font-size:18px;font-weight:800;letter-spacing:0.5px;background:linear-gradient(135deg,#7c3aed,#6d28d9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.nav-actions{display:flex;align-items:center;gap:16px}
.nav-signin{font-size:14px;font-weight:500;color:#444;text-decoration:none}
.nav-signin:hover{color:#7c3aed;text-decoration:none}
.btn-nav{padding:8px 18px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;text-decoration:none;transition:background 0.12s}
.btn-nav:hover{background:#6d28d9;text-decoration:none;color:#fff}
/* Hero */
.hero{background:#fff;padding:100px 24px 80px;text-align:center}
.hero-inner{max-width:700px;margin:0 auto}
.hero h1{font-size:50px;font-weight:800;line-height:1.1;letter-spacing:-1px;color:#1a1a1a;margin-bottom:22px}
.hero-sub{font-size:18px;color:#555;line-height:1.6;max-width:560px;margin:0 auto 36px}
.hero-ctas{display:flex;align-items:center;justify-content:center;gap:20px;flex-wrap:wrap;margin-bottom:28px}
.btn-primary-lg{padding:14px 28px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:700;cursor:pointer;text-decoration:none;transition:background 0.12s;display:inline-block}
.btn-primary-lg:hover{background:#6d28d9;text-decoration:none;color:#fff}
.hero-link{font-size:14px;color:#7c3aed;text-decoration:none;font-weight:500}
.hero-link:hover{text-decoration:underline}
.hero-chips{display:flex;align-items:center;justify-content:center;gap:18px;flex-wrap:wrap}
.chip{font-size:13px;color:#555;font-weight:500}
/* How it works */
.hiw{background:#f8f7f5;padding:80px 24px}
.hiw-inner{max-width:900px;margin:0 auto}
.section-label{font-size:12px;font-weight:700;letter-spacing:1.5px;color:#7c3aed;text-transform:uppercase;margin-bottom:12px}
.section-title{font-size:32px;font-weight:800;color:#1a1a1a;margin-bottom:48px}
.steps{display:flex;flex-direction:column;gap:36px}
.step{display:flex;align-items:flex-start;gap:24px}
.step-num{width:40px;height:40px;border-radius:50%;background:#7c3aed;color:#fff;font-size:16px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.step-body h3{font-size:18px;font-weight:700;color:#1a1a1a;margin-bottom:6px}
.step-body p{font-size:15px;color:#555;line-height:1.6}
/* Features */
.features{background:#fff;padding:80px 24px}
.features-inner{max-width:900px;margin:0 auto}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:24px}
@media(max-width:700px){.cards{grid-template-columns:1fr}.hero h1{font-size:34px}.hero-ctas{flex-direction:column;gap:12px}}
.fcard{background:#fff;border:1.5px solid #e5e3df;border-radius:12px;padding:28px 24px}
.fcard h3{font-size:16px;font-weight:700;color:#1a1a1a;margin-bottom:10px}
.fcard p{font-size:14px;color:#555;line-height:1.6}
.fcard-icon{width:36px;height:36px;border-radius:10px;background:#f3f0ff;display:flex;align-items:center;justify-content:center;margin-bottom:16px;font-size:18px}
/* Enterprise */
.enterprise{background:#f3f0ff;padding:80px 24px}
.enterprise-inner{max-width:640px;margin:0 auto;text-align:center}
.enterprise h2{font-size:30px;font-weight:800;color:#1a1a1a;margin-bottom:14px}
.enterprise-sub{font-size:16px;color:#555;line-height:1.6;margin-bottom:40px}
.ent-form{background:#fff;border:1.5px solid #ddd6fe;border-radius:16px;padding:36px;text-align:left}
.ent-form label{display:block;font-size:12px;font-weight:600;color:#555;margin-bottom:5px;letter-spacing:0.3px}
.ent-form input,.ent-form textarea{width:100%;padding:10px 12px;border:1.5px solid #e5e3df;border-radius:8px;font-size:14px;color:#1a1a1a;background:#fff;transition:border-color 0.12s;margin-bottom:16px;font-family:inherit}
.ent-form input:focus,.ent-form textarea:focus{outline:none;border-color:#7c3aed}
.ent-form textarea{height:100px;resize:vertical}
.btn-ent{width:100%;padding:12px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;transition:background 0.12s;cursor:pointer}
.btn-ent:hover{background:#6d28d9}
.ent-thanks{display:none;text-align:center;padding:20px 0;font-size:15px;color:#16a34a;font-weight:600}
/* Footer */
.footer-bar{background:#fff;border-top:1px solid #e5e3df;padding:28px 24px;text-align:center;font-size:13px;color:#9ca3af}
</style>
</head>
<body>

<!-- Nav -->
<nav class="nav">
  <div class="nav-inner">
    <a href="/" class="logo" style="text-decoration:none">
      <div class="logo-mark"><img src="/logo-icon.png" alt="Mighty"></div>
      <div class="logo-name">Mighty</div>
    </a>
    <div class="nav-actions">
      <a href="/login" class="nav-signin">Sign in</a>
      <a href="/signup" class="btn-nav">Get started free</a>
    </div>
  </div>
</nav>

<!-- Hero -->
<section class="hero">
  <div class="hero-inner">
    <h1>Your AI agents, accountable to you.</h1>
    <p class="hero-sub">Mighty adds approval checkpoints and a permanent activity log to any AI agent. Set it up once — your agents pause before consequential actions and wait for your decision.</p>
    <div class="hero-ctas">
      <a href="/signup" class="btn-primary-lg">Get started free &rarr;</a>
      <a href="#enterprise" class="hero-link">Using Mighty across a team? Talk to us &rarr;</a>
    </div>
    <div class="hero-chips">
      <span class="chip">&#10003; Works with Claude, ChatGPT &amp; custom agents</span>
      <span class="chip">&#10003; 5-minute setup</span>
      <span class="chip">&#10003; Free to start</span>
    </div>
  </div>
</section>

<!-- How it works -->
<section class="hiw">
  <div class="hiw-inner">
    <div class="section-label">How it works</div>
    <div class="section-title">Up and running in three steps</div>
    <div class="steps">
      <div class="step">
        <div class="step-num">1</div>
        <div class="step-body">
          <h3>Connect your agent</h3>
          <p>Add the Mighty system prompt or MCP plugin. Takes about 5 minutes.</p>
        </div>
      </div>
      <div class="step">
        <div class="step-num">2</div>
        <div class="step-body">
          <h3>Agent pauses before acting</h3>
          <p>When your agent is about to do something consequential — send an email, make a purchase, edit a file — it stops and asks.</p>
        </div>
      </div>
      <div class="step">
        <div class="step-num">3</div>
        <div class="step-body">
          <h3>You decide. Everything is logged.</h3>
          <p>Approve or deny from any device. Every action your agent takes or requests is recorded permanently.</p>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- Feature cards -->
<section class="features">
  <div class="features-inner">
    <div class="section-label">What you get</div>
    <div class="section-title" style="margin-bottom:36px">Built-in oversight for every agent</div>
    <div class="cards">
      <div class="fcard">
        <div class="fcard-icon">&#9989;</div>
        <h3>Approval checkpoints</h3>
        <p>You define what is consequential. Your agent pauses and waits — approved: proceed, denied: stop.</p>
      </div>
      <div class="fcard">
        <div class="fcard-icon">&#128196;</div>
        <h3>Permanent audit log</h3>
        <p>Every action your agent takes is logged with a timestamp, description, and your decision. Ready when you need it.</p>
      </div>
      <div class="fcard">
        <div class="fcard-icon">&#128279;</div>
        <h3>Any agent, any platform</h3>
        <p>Claude Desktop (MCP), ChatGPT Projects, or your own custom agent via a simple HTTP API.</p>
      </div>
    </div>
  </div>
</section>

<!-- Enterprise -->
<section class="enterprise" id="enterprise">
  <div class="enterprise-inner">
    <h2>Using AI agents across your organization?</h2>
    <p class="enterprise-sub">When teams deploy AI agents at scale, Mighty's authorization layer becomes a governance and compliance tool. Audit trails, approval workflows, and oversight — built in from day one. Tell us about your use case.</p>
    <div class="ent-form" id="ent-form-wrap">
      <form id="ent-form">
        <label>Full name</label>
        <input type="text" id="ent-name" placeholder="Jane Smith" required>
        <label>Work email</label>
        <input type="email" id="ent-email" placeholder="jane@company.com" required>
        <label>Company</label>
        <input type="text" id="ent-company" placeholder="Acme Corp">
        <label>Tell us about your use case <span style="font-weight:400;color:#aaa">(optional)</span></label>
        <textarea id="ent-message" placeholder="We are deploying agents that..."></textarea>
        <button type="submit" class="btn-ent" id="enterprise-submit-btn">Get in touch &rarr;</button>
      </form>
      <div class="ent-thanks" id="ent-thanks">Thanks — we will be in touch within one business day.</div>
    </div>
  </div>
</section>

<!-- Footer -->
<div class="footer-bar">&copy; <span id="cy"></span> Mighty &middot; Your agents, your control</div>
<script>document.getElementById("cy").textContent=new Date().getFullYear();</script>

<script>
document.getElementById("ent-form").addEventListener("submit", function(e) {
  e.preventDefault();
  var name = document.getElementById("ent-name").value.trim();
  var email = document.getElementById("ent-email").value.trim();
  var company = document.getElementById("ent-company").value.trim();
  var message = document.getElementById("ent-message").value.trim();
  var submitBtn = document.getElementById("enterprise-submit-btn");
  if (submitBtn) { submitBtn.disabled = true; submitBtn.textContent = "Sending..."; }
  fetch("/enterprise-interest", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({"name": name, "email": email, "company": company, "message": message})
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) {
      document.getElementById("ent-form").style.display = "none";
      document.getElementById("ent-thanks").style.display = "block";
    }
  }).catch(function() {
    document.getElementById("enterprise-submit-btn").textContent = "Error — please try again";
    document.getElementById("enterprise-submit-btn").disabled = false;
  });
});
</script>

</body>
</html>"""

SIGNUP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Create account — Mighty</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;align-items:center;justify-content:center;padding:24px}
.card{background:#fff;border:1px solid #e5e3df;border-radius:16px;padding:40px;width:100%;max-width:400px;box-shadow:0 4px 24px rgba(0,0,0,0.06)}
.logo{display:flex;align-items:center;gap:10px;margin-bottom:28px}
.logo-mark{width:32px;height:32px;display:flex;align-items:center;justify-content:center}
.logo-mark img{height:32px;width:auto}
.logo-name{font-size:18px;font-weight:800;letter-spacing:0.5px;background:linear-gradient(135deg,#7c3aed,#6d28d9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
h1{font-size:22px;font-weight:700;margin-bottom:6px;color:#1a1a1a}
.sub{font-size:14px;color:#666;margin-bottom:24px}
label{display:block;font-size:12px;font-weight:600;color:#555;margin-bottom:5px;letter-spacing:0.3px}
input[type=email],input[type=password]{width:100%;padding:10px 12px;border:1.5px solid #e5e3df;border-radius:8px;font-size:14px;color:#1a1a1a;background:#fff;transition:border-color 0.12s;margin-bottom:14px}
input:focus{outline:none;border-color:#7c3aed}
.btn-primary{width:100%;padding:12px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;transition:background 0.12s;margin-top:4px}
.btn-primary:hover{background:#6d28d9}
.err{font-size:13px;color:#dc2626;background:#fef2f2;border:1px solid #fecaca;border-radius:7px;padding:9px 12px;margin-bottom:14px}
.footer{text-align:center;margin-top:20px;font-size:13px;color:#888}
.back{display:block;font-size:13px;color:#888;margin-bottom:20px;text-decoration:none}
.back:hover{color:#7c3aed;text-decoration:none}
</style>
</head>
<body>
<div class="card">
  <a href="/" class="back">&larr; Home</a>
  <div class="logo">
    <div class="logo-mark">
      <img src="/logo-icon.png" alt="Mighty">
    </div>
    <div class="logo-name">Mighty</div>
  </div>
  <h1>Create your account</h1>
  <p class="sub">Free to start. Set up in minutes.</p>
  {error}
  <form method="POST" action="/signup">
    <label>Email</label>
    <input type="email" name="email" placeholder="you@example.com" required autocomplete="email">
    <label>Password</label>
    <input type="password" name="password" placeholder="Choose a password" required autocomplete="new-password" minlength="6" maxlength="128">
    <button class="btn-primary" type="submit">Create free account &rarr;</button>
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
.logo-mark{width:32px;height:32px;display:flex;align-items:center;justify-content:center}
.logo-mark img{height:32px;width:auto}
.logo-name{font-size:18px;font-weight:800;letter-spacing:0.5px;background:linear-gradient(135deg,#7c3aed,#6d28d9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
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
      <img src="/logo-icon.png" alt="Mighty">
    </div>
    <div class="logo-name">Mighty</div>
  </div>
  <h1>Welcome back</h1>
  {error}
  <form method="POST" action="/login">
    <label>Email</label>
    <input type="email" name="email" placeholder="you@example.com" required autocomplete="email">
    <label>Password</label>
    <input type="password" name="password" placeholder="Your password" required autocomplete="current-password" maxlength="128">
    <button class="btn-primary" type="submit">Sign in →</button>
  </form>
  <div style="text-align:center;margin-top:12px;font-size:12px;color:#9ca3af">
    Forgot your password? <a href="mailto:support@mighty.app" style="color:#7c3aed">Contact support</a>
  </div>
  <div class="footer">No account? <a href="/signup">Sign up free</a> &middot; <a href="/">← Home</a></div>
</div>
</body>
</html>"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Mighty</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;flex-direction:column;height:100vh;overflow:hidden;background:#f8f7f5}
.topbar{background:#fff;border-bottom:1px solid #e5e3df;padding:0 24px;height:52px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
.topbar-logo{display:flex;align-items:center;gap:8px}
.topbar-logo-mark{width:26px;height:26px;display:flex;align-items:center;justify-content:center}
.topbar-logo-mark img{height:26px;width:auto}
.topbar-name{font-size:14px;font-weight:800;letter-spacing:0.5px;background:linear-gradient(135deg,#7c3aed,#6d28d9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.topbar-right{display:flex;align-items:center;gap:16px}
.topbar-email{font-size:12px;color:#9ca3af}
.btn-logout{font-size:12px;color:#6b7280;background:none;border:none;cursor:pointer;padding:4px 8px;border-radius:5px;transition:background 0.12s}
.btn-logout:hover{background:#f3f4f6;color:#1a1a1a}
.main{flex:1;min-height:0;display:grid;grid-template-columns:320px 1fr;gap:24px;max-width:1140px;width:100%;margin:0 auto;padding:28px 24px;box-sizing:border-box}
@media(max-width:768px){.main{grid-template-columns:1fr}}
.sidebar{display:flex;flex-direction:column;gap:14px;overflow-y:auto}
.feed-col{overflow-y:auto;min-height:0;padding-bottom:28px}
.card{background:#fff;border:1px solid #e5e3df;border-radius:12px;padding:20px}
.setup-heading{font-size:14px;font-weight:700;color:#1a1a1a;margin-bottom:14px}
.tab-bar{display:flex;gap:4px;background:#f3f4f6;border-radius:8px;padding:3px;margin-bottom:16px}
.tab{flex:1;padding:6px 10px;border:none;background:none;border-radius:6px;font-size:12px;font-weight:600;color:#6b7280;cursor:pointer;transition:all 0.12s}
.tab.active{background:#fff;color:#1a1a1a;box-shadow:0 1px 3px rgba(0,0,0,0.1)}
.tab-content{display:none}.tab-content.active{display:block}
.step{display:flex;gap:10px;margin-bottom:14px}
.step-num{width:20px;height:20px;border-radius:50%;background:#f3f0ff;color:#7c3aed;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px}
.step-body{min-width:0;flex:1}
.step-title{font-size:13px;font-weight:600;color:#1a1a1a;margin-bottom:4px}
.step-hint{font-size:12px;color:#6b7280;line-height:1.5;margin-top:4px}
.code-box{font-family:ui-monospace,monospace;font-size:10px;color:#6b7280;background:#f8f7f5;border:1px solid #e5e3df;border-radius:6px;padding:10px;white-space:pre;overflow-x:auto;margin:6px 0;max-width:100%;box-sizing:border-box}
.path-box{font-family:ui-monospace,monospace;font-size:10px;color:#7c3aed;background:#f8f7f5;border:1px solid #e5e3df;border-radius:6px;padding:10px;word-break:break-all;line-height:1.5;margin:6px 0}
.btn-action{width:100%;padding:10px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;transition:background 0.12s;cursor:pointer;margin-bottom:4px}
.btn-action:hover{background:#6d28d9}
.btn-secondary{width:100%;padding:9px;background:#f3f0ff;color:#7c3aed;border:1px solid #e9d5ff;border-radius:8px;font-size:13px;font-weight:600;transition:background 0.12s;cursor:pointer;text-decoration:none;display:block;text-align:center}
.btn-secondary:hover{background:#ede9fe;text-decoration:none}
details{margin-top:12px}
summary{font-size:12px;color:#6b7280;cursor:pointer;user-select:none;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"\\25B8 "}
details[open] summary::before{content:"\\25BE "}
.api-key-wrap{margin-top:10px;display:flex;align-items:center;gap:8px}
.api-key-val{flex:1;font-family:ui-monospace,monospace;font-size:10px;color:#6b7280;background:#f8f7f5;border:1px solid #e5e3df;border-radius:6px;padding:7px 10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.btn-copy-key{font-size:12px;font-weight:600;padding:5px 10px;background:#f3f0ff;color:#7c3aed;border:1px solid #e9d5ff;border-radius:6px;white-space:nowrap;cursor:pointer;transition:background 0.12s}
.btn-copy-key:hover{background:#ede9fe}
.status-row{display:flex;align-items:center;gap:12px;padding:2px 0}
.status-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;margin-top:2px}
.status-green{background:#16a34a;box-shadow:0 0 0 3px #dcfce7}
.status-title{font-size:14px;font-weight:600;color:#1a1a1a}
.status-sub{font-size:12px;color:#6b7280;margin-top:2px}
.prompt-hidden{display:none}
.feed-title{font-size:16px;font-weight:700;color:#1a1a1a;margin-bottom:4px}
.feed-sub{font-size:12px;color:#6b7280;margin-bottom:20px}
.feed{display:flex;flex-direction:column;gap:10px}
.pending-label{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:#7c3aed;display:flex;align-items:center;gap:6px;margin-bottom:10px}
.pending-dot{width:6px;height:6px;border-radius:50%;background:#7c3aed;animation:pulse 1.5s infinite;flex-shrink:0}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
.action-card{background:#fff;border:1px solid #e5e3df;border-radius:10px;overflow:hidden}
.action-card:hover{box-shadow:0 2px 10px rgba(0,0,0,0.06)}
.action-card.is-pending{border-color:#fbbf24}
.action-top{padding:14px 16px 0;display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.action-label{font-size:14px;font-weight:600;color:#1a1a1a;line-height:1.4}
.action-badges{display:flex;align-items:center;gap:6px;flex-shrink:0;flex-wrap:wrap;justify-content:flex-end}
.action-time{font-size:11px;color:#9ca3af;margin-top:4px;text-align:right}
.action-fields{padding:10px 16px 14px;display:flex;flex-direction:column;gap:5px}
.field-row{display:flex;gap:10px;font-size:12px}
.field-key{color:#9ca3af;font-weight:600;text-transform:uppercase;font-size:10px;letter-spacing:0.5px;min-width:80px;flex-shrink:0;padding-top:1px}
.field-val{color:#374151;line-height:1.4;word-break:break-word}
.action-buttons{padding:12px 16px;border-top:1px solid #f0ede8;display:flex;gap:8px}
.btn-authorize{flex:1;padding:9px;background:#16a34a;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:background 0.12s}
.btn-authorize:hover{background:#15803d}
.btn-reject{flex:1;padding:9px;background:#fff;color:#dc2626;border:1.5px solid #fecaca;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:all 0.12s}
.btn-reject:hover{background:#fef2f2}
.clevel-sensitive{display:inline-block;font-size:11px;font-weight:600;padding:2px 7px;border-radius:20px;background:#eff6ff;color:#2563eb;letter-spacing:0.3px}
.clevel-consequential{display:inline-block;font-size:11px;font-weight:600;padding:2px 7px;border-radius:20px;background:#fffbeb;color:#d97706;letter-spacing:0.3px}
.clevel-critical{display:inline-block;font-size:11px;font-weight:600;padding:2px 7px;border-radius:20px;background:#fef2f2;color:#dc2626;letter-spacing:0.3px}
.empty-state{text-align:center;padding:40px 20px}
.empty-state-icon{width:40px;height:40px;background:#f3f0ff;border-radius:10px;display:flex;align-items:center;justify-content:center;margin:0 auto 16px}
.empty-state-title{font-size:14px;font-weight:600;color:#6b7280;margin-bottom:6px}
.empty-state-sub{font-size:12px;color:#9ca3af;line-height:1.6}
</style>
</head>
<body>
<div class="topbar">
  <a href="/dashboard" style="text-decoration:none;display:flex;align-items:center;gap:8px">
    <div class="topbar-logo-mark">
      <img src="/logo-icon.png" alt="Mighty">
    </div>
    <span class="topbar-name">Mighty</span>
  </a>
  <div id="pending-badge" style="display:{pending_display};background:#f3f0ff;border:1px solid #e9d5ff;border-radius:20px;padding:4px 12px;font-size:12px;font-weight:600;color:#5b21b6">
    {pending_count} awaiting decision
  </div>
  <div class="topbar-right">
    <a href="/settings" style="font-size:12px;color:#6b7280;text-decoration:none">Settings</a>
    <span class="topbar-email">{email}</span>
    <form method="POST" action="/logout" style="margin:0"><button class="btn-logout" type="submit">Sign out</button></form>
  </div>
</div>

{onboarding_banner}
<div class="main">
  {sidebar_content}

  <div class="feed-col" {feed_col_hidden}>
    <div class="feed-title">Activity Log</div>
    <div class="feed-sub">Every action your agent takes or requests approval for</div>
    <div class="feed" id="feed">
      {feed_html}
    </div>
  </div>
</div>

<script>
function switchTab(name, btn) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
}
function copyMcpConfig(btn) {
  navigator.clipboard.writeText(document.getElementById('mcpConfigBox').textContent);
  btn.textContent = 'Copied!';
  setTimeout(() => btn.textContent = 'Copy config', 1800);
}
function copyPrompt(btn) {
  navigator.clipboard.writeText(document.getElementById('promptBox').value);
  btn.textContent = 'Copied!';
  setTimeout(() => btn.textContent = 'Copy system prompt', 1800);
}
function copyKey(btn) {
  navigator.clipboard.writeText(document.getElementById('apiKeyVal').textContent.trim());
  btn.textContent = 'Copied!';
  setTimeout(() => btn.textContent = 'Copy', 1800);
}
function decide(actionId, decision) {
  document.querySelectorAll(".btn-authorize, .btn-reject").forEach(function(b) { b.disabled = true; });
  fetch('/dashboard/decide/' + actionId, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({decision})
  }).then(() => location.reload());
}
var lastPending = document.querySelectorAll('.is-pending').length > 0;
setInterval(function() {
  fetch('/dashboard/has-pending').then(function(r) { return r.json(); }).then(function(d) {
    if (d.pending !== lastPending) {
      location.reload();
    }
  }).catch(function() {});
}, 5000);

// Register SW for push delivery (notifications managed in /settings)
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js');
}
</script>
</body>
</html>"""

ONBOARDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Get started — Mighty</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',sans-serif;background:#f8f7f5;color:#1a1a1a;min-height:100vh;overflow:auto;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;padding:20px 24px}
.wrap{width:100%;max-width:520px;display:flex;flex-direction:column;min-height:calc(100vh - 40px)}
.logo{display:flex;align-items:center;gap:8px;justify-content:center;margin-bottom:16px;flex-shrink:0}
.logo-mark{width:28px;height:28px;display:flex;align-items:center;justify-content:center}
.logo-mark img{height:28px;width:auto}
.logo-name{font-size:17px;font-weight:800;letter-spacing:0.5px;background:linear-gradient(135deg,#7c3aed,#6d28d9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.progress{display:flex;gap:6px;justify-content:center;margin-bottom:16px;flex-shrink:0}
.progress-dot{width:8px;height:8px;border-radius:50%;background:#e5e3df;transition:background 0.2s;padding:6px;margin:-6px;background-clip:content-box}
.progress-dot.active{background:#7c3aed;background-clip:content-box}
.progress-dot.done{background:#c4b5fd;background-clip:content-box;cursor:pointer}
.progress-dot.done:hover{background:#a78bfa;background-clip:content-box}
.card{background:#fff;border:1px solid #e5e3df;border-radius:16px;padding:24px 28px;box-shadow:0 4px 24px rgba(0,0,0,0.06);flex:1}
.step{display:none}.step.active{display:block}
.step-label{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#7c3aed;margin-bottom:8px}
.step-title{font-size:20px;font-weight:700;color:#1a1a1a;margin-bottom:8px;line-height:1.3}
.step-sub{font-size:14px;color:#6b7280;line-height:1.6;margin-bottom:20px}
.agent-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:16px}
.agent-card{border:2px solid #e5e3df;border-radius:12px;padding:16px 12px;text-align:center;cursor:pointer;transition:all 0.15s}
.agent-card:hover{border-color:#c4b5fd;background:#faf5ff}
.agent-card.selected{border-color:#7c3aed;background:#f5f3ff}
.agent-icon{font-size:24px;margin-bottom:8px}
.agent-name{font-size:13px;font-weight:600;color:#1a1a1a}
.agent-desc{font-size:12px;color:#9ca3af;margin-top:3px;line-height:1.4}
.cap-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px;margin-bottom:10px}
.cap-card{border:1.5px solid #e5e3df;border-radius:10px;padding:9px 11px;cursor:pointer;transition:all 0.15s;display:flex;align-items:flex-start;gap:9px;user-select:none}
.cap-card:hover{border-color:#c4b5fd;background:#faf5ff}
.cap-card.selected{border-color:#7c3aed;background:#f5f3ff}
.cap-icon{font-size:16px;flex-shrink:0;line-height:1.2}
.cap-name{font-size:12px;font-weight:600;color:#1a1a1a}
.cap-sub{font-size:11px;color:#9ca3af;margin-top:1px;line-height:1.3}
.setup-steps{display:flex;flex-direction:column;gap:12px;margin-bottom:14px}
.setup-step{display:flex;gap:12px}
.setup-step-num{width:24px;height:24px;border-radius:50%;background:#f3f0ff;color:#7c3aed;font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px}
.setup-step-body{flex:1;min-width:0}
.setup-step-title{font-size:13px;font-weight:600;color:#1a1a1a;margin-bottom:4px}
.setup-step-hint{font-size:12px;color:#6b7280;line-height:1.5}
.code-box{font-family:ui-monospace,monospace;font-size:10px;color:#6b7280;background:#f8f7f5;border:1px solid #e5e3df;border-radius:6px;padding:10px;white-space:pre;overflow-x:auto;overflow-y:auto;margin:5px 0;max-width:100%;max-height:82px}
.path-box{font-family:ui-monospace,monospace;font-size:10px;color:#7c3aed;background:#f8f7f5;border:1px solid #e5e3df;border-radius:6px;padding:7px 10px;word-break:break-all;margin:5px 0}
.btn{width:100%;padding:11px;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;transition:all 0.12s}
.btn-primary{background:#7c3aed;color:#fff}.btn-primary:hover{background:#6d28d9}.btn-primary.btn-dim{background:#c4b5fd !important;cursor:not-allowed}
.btn-secondary{background:#f3f0ff;color:#7c3aed;border:1px solid #e9d5ff}.btn-secondary:hover{background:#ede9fe}
.btn-copy{font-size:12px;font-weight:600;padding:5px 10px;background:#f3f0ff;color:#7c3aed;border:1px solid #e9d5ff;border-radius:6px;cursor:pointer;transition:background 0.12s;white-space:nowrap}
.btn-copy:hover{background:#ede9fe}
.btn-row{display:flex;gap:10px;margin-top:6px}
.test-waiting{text-align:center;padding:20px;background:#f8f7f5;border-radius:12px;margin-bottom:16px}
.test-spinner{width:32px;height:32px;border:3px solid #e5e3df;border-top-color:#7c3aed;border-radius:50%;animation:spin 0.8s linear infinite;margin:0 auto 10px}
@keyframes spin{to{transform:rotate(360deg)}}
.test-connected{text-align:center;padding:20px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:12px;margin-bottom:16px;display:none}
.test-connected-icon{font-size:28px;margin-bottom:6px}
.push-status{font-size:12px;color:#6b7280;margin-top:6px;min-height:16px}
.skip{text-align:center;margin-top:10px}
.skip a{font-size:12px;color:#9ca3af;text-decoration:none}.skip a:hover{color:#6b7280}
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">
    <div class="logo-mark"><img src="/logo-icon.png" alt="Mighty"></div>
    <span class="logo-name">Mighty</span>
  </div>
  <div class="progress">
    <div class="progress-dot active" id="dot-0" onclick="dotNav(0)"></div>
    <div class="progress-dot" id="dot-1" onclick="dotNav(1)"></div>
    <div class="progress-dot" id="dot-2" onclick="dotNav(2)"></div>
    <div class="progress-dot" id="dot-3" onclick="dotNav(3)"></div>
    <div class="progress-dot" id="dot-4" onclick="dotNav(4)"></div>
  </div>
  <div class="card">

    <!-- Step 0: Welcome -->
    <div class="step active" id="step-0">
      <div class="step-label">Welcome</div>
      <div class="step-title">You're in control of your AI agents.</div>
      <div class="step-sub">Mighty puts approval checkpoints in your agent's path. You define what's consequential — the agent pauses and waits for your decision. And every action is logged, in the agent's own words.</div>
      <button class="btn btn-primary" onclick="goTo(1)">Begin setup →</button>
    </div>

    <!-- Step 1: Pick agent -->
    <div class="step" id="step-1">
      <div class="step-label">Step 1 of 4</div>
      <div class="step-title">What are you using Mighty with?</div>
      <div class="step-sub">Pick your agent — we'll give you the exact setup steps.</div>
      <div class="agent-grid">
        <div class="agent-card" onclick="selectAgent(this,'claude')">
          <div class="agent-icon">⚡</div>
          <div class="agent-name">Claude Desktop</div>
          <div class="agent-desc">MCP plugin</div>
        </div>
        <div class="agent-card" onclick="selectAgent(this,'chatgpt')">
          <div class="agent-icon">🤖</div>
          <div class="agent-name">ChatGPT</div>
          <div class="agent-desc">System prompt</div>
        </div>
        <div class="agent-card" onclick="selectAgent(this,'custom')">
          <div class="agent-icon">🛠</div>
          <div class="agent-name">Custom agent</div>
          <div class="agent-desc">API / code</div>
        </div>
      </div>
      <div style="margin-top:14px">
        <div style="font-size:12px;font-weight:600;color:#6b7280;margin-bottom:8px">What can your agent do? <span style="font-weight:400;color:#9ca3af">Select all that apply</span></div>
        <div class="cap-grid">
          <div class="cap-card" onclick="toggleCap(this,'email')"><input type="checkbox" class="cap-check" value="email" style="display:none"><span class="cap-icon">✉️</span><div><div class="cap-name">Email</div><div class="cap-sub">Send, reply, forward</div></div></div>
          <div class="cap-card" onclick="toggleCap(this,'calendar')"><input type="checkbox" class="cap-check" value="calendar" style="display:none"><span class="cap-icon">📅</span><div><div class="cap-name">Calendar</div><div class="cap-sub">Schedule, cancel meetings</div></div></div>
          <div class="cap-card" onclick="toggleCap(this,'purchases')"><input type="checkbox" class="cap-check" value="purchases" style="display:none"><span class="cap-icon">🛒</span><div><div class="cap-name">Purchases</div><div class="cap-sub">Orders, transactions</div></div></div>
          <div class="cap-card" onclick="toggleCap(this,'files')"><input type="checkbox" class="cap-check" value="files" style="display:none"><span class="cap-icon">📁</span><div><div class="cap-name">File management</div><div class="cap-sub">Create, edit, delete</div></div></div>
          <div class="cap-card" onclick="toggleCap(this,'web')"><input type="checkbox" class="cap-check" value="web" style="display:none"><span class="cap-icon">🌐</span><div><div class="cap-name">Web &amp; forms</div><div class="cap-sub">Submit forms, browse</div></div></div>
          <div class="cap-card" onclick="toggleCap(this,'code')"><input type="checkbox" class="cap-check" value="code" style="display:none"><span class="cap-icon">💻</span><div><div class="cap-name">Code execution</div><div class="cap-sub">Run scripts, modify systems</div></div></div>
          <div class="cap-card" onclick="toggleCap(this,'social')"><input type="checkbox" class="cap-check" value="social" style="display:none"><span class="cap-icon">📢</span><div><div class="cap-name">Social media</div><div class="cap-sub">Post, publish content</div></div></div>
          <div class="cap-card" onclick="toggleCap(this,'apis')"><input type="checkbox" class="cap-check" value="apis" style="display:none"><span class="cap-icon">🔗</span><div><div class="cap-name">External APIs</div><div class="cap-sub">Third-party services</div></div></div>
        </div>
        <input id="cap-other" type="text" placeholder="Anything else? e.g. expense reports, Slack messages" style="width:100%;font-family:'Inter',sans-serif;font-size:13px;color:#1a1a1a;background:#fff;border:1.5px solid #e5e3df;border-radius:8px;padding:8px 10px;outline:none;transition:border 0.12s" onfocus="this.style.borderColor='#7c3aed'" onblur="this.style.borderColor='#e5e3df'">
        <div id="cap-caveat" style="display:none;margin-top:10px;padding:10px 12px;background:#f3f0ff;border:1px solid #e9d5ff;border-radius:8px;font-size:12px;color:#5b21b6;line-height:1.5"></div>
      </div>
      <div id="agent-nudge" style="display:none;font-size:12px;color:#7c3aed;text-align:center;margin-top:8px">Pick an agent type above to continue</div>
      <div class="btn-row" style="margin-top:8px">
        <button class="btn btn-secondary" onclick="goTo(0)">← Back</button>
        <button class="btn btn-primary btn-dim" id="btn-next-agent" onclick="continueFromAgent()" style="flex:1">Continue →</button>
      </div>
      <div class="skip"><a href="/onboarding/skip">Skip setup, go to dashboard</a></div>
    </div>

    <!-- Step 2: Setup -->
    <div class="step" id="step-2">
      <div class="step-label">Step 2 of 4</div>
      <div class="step-title" id="setup-title">Connect your agent</div>
      <div class="step-sub" id="setup-sub"></div>

      <!-- Claude Desktop setup panel -->
      <div id="setup-claude" class="agent-setup" style="display:none">
        <div class="setup-steps">
          <div class="setup-step">
            <div class="setup-step-num">1</div>
            <div class="setup-step-body">
              <div class="setup-step-title">Download the MCP server</div>
              <a href="/download/mighty_mcp.py" class="btn-copy" style="display:inline-block;margin-top:4px">⬇ Download mighty_mcp.py</a>
              <div class="setup-step-hint">Save it to your home folder (~/)</div>
            </div>
          </div>
          <div class="setup-step">
            <div class="setup-step-num">2</div>
            <div class="setup-step-body">
              <div class="setup-step-title">Open this file and paste the config</div>
              <div class="path-box">~/Library/Application Support/Claude/claude_desktop_config.json</div>
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                <span style="font-size:11px;color:#9ca3af;white-space:nowrap">Your Mac username:</span>
                <input id="mac-username" type="text" placeholder="e.g. john" style="flex:1;font-family:'Inter',sans-serif;font-size:13px;border:1.5px solid #e5e3df;border-radius:8px;padding:6px 10px;outline:none;background:#fff;color:#1a1a1a" oninput="updateMcpConfig(this.value)" onfocus="this.style.borderColor='#7c3aed'" onblur="this.style.borderColor='#e5e3df'">
              </div>
              <div style="display:flex;align-items:flex-start;gap:8px">
                <div class="code-box" id="mcp-config-box" style="flex:1;max-height:100px"></div>
                <button class="btn-copy" onclick="copyBox('mcp-config-box',this)" style="margin-top:6px">Copy</button>
              </div>
            </div>
          </div>
          <div class="setup-step">
            <div class="setup-step-num">3</div>
            <div class="setup-step-body">
              <div class="setup-step-title">Restart Claude Desktop</div>
              <div class="setup-step-hint">Quit and reopen Claude Desktop. (This setup is for macOS — on Windows, the config file is at <code style="font-size:11px;background:#f0ede8;padding:1px 4px;border-radius:3px">%APPDATA%\Claude\claude_desktop_config.json</code>)</div>
            </div>
          </div>
          <div class="setup-step">
            <div class="setup-step-num">4</div>
            <div class="setup-step-body">
              <div class="setup-step-title">Add the checkpoint prompt to your Claude Project</div>
              <div style="display:flex;align-items:flex-start;gap:8px">
                <textarea id="prompt-box-claude" style="flex:1;font-family:ui-monospace,monospace;font-size:11px;color:#374151;background:#f8f7f5;border:1.5px solid #e5e3df;border-radius:6px;padding:10px;height:90px;resize:none;overflow:auto"></textarea>
                <button class="btn-copy" onclick="copyBox('prompt-box-claude',this)" style="margin-top:6px">Copy</button>
              </div>
              <div class="setup-step-hint">Open your Claude Project → Instructions, and paste this at the top. It tells Claude when to call the Mighty tools.</div>
            </div>
          </div>
        </div>
      </div>

      <!-- ChatGPT setup panel -->
      <div id="setup-chatgpt" class="agent-setup" style="display:none">
        <div class="setup-steps">
          <div class="setup-step">
            <div class="setup-step-num">1</div>
            <div class="setup-step-body">
              <div class="setup-step-title">Open ChatGPT → your Project or Custom GPT</div>
              <div class="setup-step-hint">Go to the project or GPT you want to connect. Open its instructions/system prompt.</div>
            </div>
          </div>
          <div class="setup-step">
            <div class="setup-step-num">2</div>
            <div class="setup-step-body">
              <div class="setup-step-title">Paste the Mighty system prompt</div>
              <div style="display:flex;align-items:flex-start;gap:8px">
                <textarea id="prompt-box" style="flex:1;font-family:ui-monospace,monospace;font-size:11px;color:#374151;background:#f8f7f5;border:1.5px solid #e5e3df;border-radius:6px;padding:10px;height:90px;resize:none;overflow:auto"></textarea>
                <button class="btn-copy" onclick="copyBox('prompt-box',this)" style="margin-top:6px">Copy</button>
              </div>
              <div class="setup-step-hint">Add it at the top of the existing instructions.</div>
            </div>
          </div>
          <div class="setup-step">
            <div class="setup-step-num">3</div>
            <div class="setup-step-body">
              <div class="setup-step-title">Save</div>
              <div class="setup-step-hint">That's it — no plugin or download needed.</div>
            </div>
          </div>
        </div>
      </div>

      <!-- Custom agent setup panel -->
      <div id="setup-custom" class="agent-setup" style="display:none">
        <div class="setup-steps">
          <div class="setup-step">
            <div class="setup-step-num">1</div>
            <div class="setup-step-body">
              <div class="setup-step-title">Your API key</div>
              <div style="display:flex;align-items:center;gap:8px;margin-top:4px">
                <div class="path-box" id="api-key-box" style="margin:0;flex:1"></div>
                <button class="btn-copy" onclick="copyBox('api-key-box',this)">Copy</button>
              </div>
            </div>
          </div>
          <div class="setup-step">
            <div class="setup-step-num">2</div>
            <div class="setup-step-body">
              <div class="setup-step-title">Add the system prompt to your agent</div>
              <div style="display:flex;align-items:flex-start;gap:8px">
                <textarea id="prompt-box2" style="flex:1;font-family:ui-monospace,monospace;font-size:11px;color:#374151;background:#f8f7f5;border:1.5px solid #e5e3df;border-radius:6px;padding:10px;height:90px;resize:none;overflow:auto"></textarea>
                <button class="btn-copy" onclick="copyBox('prompt-box2',this)" style="margin-top:6px">Copy</button>
              </div>
              <div class="setup-step-hint">Or use the Python/JS SDK — see the <a id="docs-link" href="/settings" target="_blank" style="color:#7c3aed">API docs</a>.</div>
            </div>
          </div>
        </div>
      </div>

      <div class="btn-row" style="margin-top:4px">
        <button class="btn btn-secondary" onclick="goTo(1)">← Back</button>
        <button class="btn btn-primary" onclick="continueFromSetup()" style="flex:1">I've done this →</button>
      </div>
      <div class="skip"><a href="/onboarding/skip">Skip setup, go to dashboard</a></div>
    </div>

    <!-- Step 3: Test -->
    <div class="step" id="step-3">
      <div class="step-label">Step 3 of 4</div>
      <div class="step-title">Let's make sure it works</div>
      <div class="step-sub" id="test-sub">Ask your agent to do something that needs approval — like send an email. Mighty will pause it and ask you first. We'll detect it automatically.</div>
      <div class="test-waiting" id="test-waiting">
        <div class="test-spinner"></div>
        <div style="font-size:13px;font-weight:600;color:#1a1a1a;margin-bottom:4px">Waiting for your agent…</div>
        <div style="font-size:12px;color:#aaa">Or run the test command below in your terminal</div>
        <div id="test-slow-note" style="display:none;margin-top:12px;padding:10px 12px;background:#f3f0ff;border:1px solid #e9d5ff;border-radius:8px;font-size:12px;color:#5b21b6;line-height:1.5;text-align:left">
          <strong>Nothing showing up?</strong> Mighty only fires when your agent actually performs the action — not just describes it. Make sure your agent has the tools needed (email access, file permissions, etc.) to carry out what you asked. If it does not have those tools, it may promise to use Mighty but never actually trigger the flow.
        </div>
      </div>
      <div class="test-connected" id="test-connected">
        <div class="test-connected-icon">✅</div>
        <div style="font-size:15px;font-weight:700;color:#16a34a;margin-bottom:4px">Connected!</div>
        <div style="font-size:13px;color:#555">Mighty received a request from your agent.</div>
      </div>
      <div class="setup-step-body" style="margin-bottom:16px">
        <div style="font-size:12px;color:#aaa;margin-bottom:4px">Or test manually from Terminal:</div>
        <div style="display:flex;align-items:flex-start;gap:8px">
          <div class="code-box" id="test-curl" style="flex:1;font-size:10px">{test_curl}</div>
          <button class="btn-copy" onclick="copyCurl(this)" style="margin-top:6px">Copy</button>
        </div>
      </div>
      <div class="btn-row" style="margin-top:4px">
        <button class="btn btn-secondary" onclick="goTo(2)">← Back</button>
        <button class="btn btn-primary" id="btn-test-continue" onclick="goTo(4)" style="flex:1">Continue →</button>
      </div>
      <div class="skip"><a href="/onboarding/skip">Skip setup, go to dashboard</a></div>
    </div>

    <!-- Step 4: Notifications -->
    <div class="step" id="step-4">
      <div class="step-label">Step 4 of 4</div>
      <div class="step-title">Get notified instantly</div>
      <div class="step-sub">When your agent needs approval, you'll get a push notification — even when this tab is closed. Click Allow when your browser asks.</div>
      <button class="btn btn-primary" id="push-btn" onclick="enablePush()" style="margin-bottom:12px">Enable push notifications</button>
      <div class="push-status" id="push-status"></div>
      <div style="margin-top:20px;padding-top:20px;border-top:1px solid #f0ede8">
        <div style="font-size:12px;color:#aaa;margin-bottom:8px">Also want notifications on your phone? Install the free <a href="https://ntfy.sh" target="_blank" style="color:#555;font-weight:600">ntfy app</a> (iOS &amp; Android) and subscribe to your channel:</div>
        <div style="display:flex;align-items:center;gap:8px">
          <div class="path-box" id="ntfy-url-box" style="flex:1">{ntfy_url}</div>
          <button class="btn-copy" onclick="copyBox('ntfy-url-box',this)">Copy</button>
        </div>
      </div>
      <div class="btn-row" style="margin-top:20px">
        <button class="btn btn-secondary" onclick="goTo(3)">← Back</button>
        <button class="btn btn-primary" onclick="finish()" style="flex:1">Go to my dashboard →</button>
      </div>
      <div class="skip"><a href="/onboarding/skip">Skip for now</a></div>
    </div>

  </div>
</div>
<script type="application/json" id="__mighty_onboarding_data__">MIGHTY_ONBOARDING_DATA</script>
<script>
var currentStep = 0;
var selectedAgent = null;
var swReg = null;
var testPollTimer = null;

if ('serviceWorker' in navigator && 'PushManager' in window) {
  navigator.serviceWorker.register('/sw.js').then(function(reg) { swReg = reg; });
}

function dotNav(n) {
  var dot = document.getElementById("dot-" + n);
  if (dot && dot.classList.contains("done")) goTo(n);
}

function goTo(n) {
  var prev = currentStep;
  document.getElementById('step-' + prev).classList.remove('active');
  currentStep = n;
  document.getElementById('step-' + n).classList.add('active');
  // Update all dots based on new position
  for (var i = 0; i <= 4; i++) {
    var dot = document.getElementById("dot-" + i);
    if (!dot) continue;
    dot.classList.remove("active", "done");
    if (i < n) dot.classList.add("done");
    else if (i === n) dot.classList.add("active");
  }
  if (n === 3) {
    startTestPoll();
    var testSub = document.getElementById('test-sub');
    var AGENT_TEST_SUBS = {
      claude:  "In Claude Desktop, ask your agent to do something that needs approval. Mighty will intercept the request automatically.",
      chatgpt: "In your ChatGPT project, have the agent attempt a consequential action. If the system prompt is in place, it will call the Mighty API.",
      custom:  "Trigger an action in your agent that calls the Mighty authorization API. Detection is automatic."
    };
    if (testSub && selectedAgent && AGENT_TEST_SUBS[selectedAgent]) {
      testSub.textContent = AGENT_TEST_SUBS[selectedAgent];
    }
  }
  if (n !== 3 && testPollTimer) { clearInterval(testPollTimer); testPollTimer = null; }
}

var CAP_CAVEATS = {
  claude:  "Mighty checkpoints only fire for actions Claude Desktop can already perform via its connected MCP tools. If Claude does not have an email or calendar tool installed, those actions will not trigger a Mighty request — even if you select them here.",
  chatgpt: "Mighty checkpoints only fire for actions your ChatGPT project or Custom GPT can already perform. If your GPT does not have an email, calendar, or browsing tool connected, those actions will not trigger a Mighty request — ChatGPT will acknowledge the instructions but the flow will never run.",
  custom:  "Mighty checkpoints only fire when your agent actually calls the authorization API. Make sure your agent code invokes the Mighty endpoint before performing each action type you select here."
};

function selectAgent(el, agent) {
  selectedAgent = agent;
  document.querySelectorAll('.agent-card').forEach(function(c) { c.classList.remove('selected'); });
  el.classList.add('selected');
  updateContinueBtn();
  renderSetup(agent);
  var caveat = document.getElementById('cap-caveat');
  if (caveat && CAP_CAVEATS[agent]) {
    caveat.textContent = CAP_CAVEATS[agent];
    caveat.style.display = 'block';
  }
}

var CAPABILITY_ACTIONS = {
  email:     "sending, replying to, or forwarding emails",
  calendar:  "scheduling, rescheduling, or cancelling meetings or events",
  purchases: "making purchases, placing orders, or completing financial transactions",
  files:     "creating, editing, moving, or deleting files or documents",
  web:       "submitting web forms or interacting with websites on your behalf",
  code:      "running scripts or executing code that modifies systems",
  social:    "posting or publishing content to social media or public channels",
  apis:      "calling external services or APIs that take real-world actions",
};

function toggleCap(el, key) {
  el.classList.toggle('selected');
  el.querySelector('.cap-check').checked = el.classList.contains('selected');
  updateContinueBtn();
}

function updateContinueBtn() {
  var btn = document.getElementById('btn-next-agent');
  var hasCap = document.querySelectorAll('.cap-check:checked').length > 0;
  if (selectedAgent || hasCap) {
    btn.classList.remove('btn-dim');
  } else {
    btn.classList.add('btn-dim');
  }
}

function buildCheckpointPrompt(actions, agentType) {
  var list = actions.length > 0
    ? actions.map(function(a) { return '- ' + a; }).join('\\n')
    : '- any consequential action (emails, purchases, file edits, external calls)';
  if (agentType === 'claude') {
    return (
      "MIGHTY AUTHORIZATION — follow every session.\\n\\n"
      + "Before:\\n" + list + "\\n\\n"
      + "call request_authorization(action_type, label). Poll check_authorization every 3s until resolved.\\n"
      + "approved: proceed | denied or timeout: stop\\n\\n"
      + "For routine actions that don't need approval, call record_action to log them."
    );
  } else {
    return (
      "MIGHTY AUTHORIZATION — follow every session.\\n\\n"
      + "Before:\\n" + list + "\\n\\n"
      + "POST " + BASE_URL + "/api/authorize\\n"
      + '  {"api_key":"' + API_KEY + '","action_type":"<type>","label":"<desc>","fields":[["Key","Val"]]}\\n'
      + "approved: proceed | denied or timeout: stop | pending: poll GET " + BASE_URL + "/api/status/ID every 3s\\n\\n"
      + "Routine actions: POST " + BASE_URL + "/api/record\\n"
      + '  {"api_key":"' + API_KEY + '","action_type":"<type>","label":"<desc>","outcome":"completed"}'
    );
  }
}

function continueFromAgent() {
  if (!selectedAgent) {
    var nudge = document.getElementById('agent-nudge');
    if (nudge) { nudge.style.display = 'block'; }
    return;
  }
  var nudge = document.getElementById('agent-nudge');
  if (nudge) nudge.style.display = 'none';
  var selected = [];
  document.querySelectorAll('.cap-check:checked').forEach(function(cb) {
    if (CAPABILITY_ACTIONS[cb.value]) selected.push(CAPABILITY_ACTIONS[cb.value]);
  });
  var other = (document.getElementById('cap-other').value || '').trim();
  if (other) selected.push(other);
  // Update HTTP-style prompts for ChatGPT / Custom
  if (selectedAgent !== 'claude') {
    SYSTEM_PROMPT = buildCheckpointPrompt(selected, selectedAgent);
    var p1 = document.getElementById('prompt-box');
    if (p1) p1.value = SYSTEM_PROMPT;
    var p2 = document.getElementById('prompt-box2');
    if (p2) p2.value = SYSTEM_PROMPT;
  }
  // Always update MCP-style prompt for Claude Desktop
  var p0 = document.getElementById('prompt-box-claude');
  if (p0) p0.value = buildCheckpointPrompt(selected, 'claude');
  goTo(2);
}

function continueFromSetup() {
  if (selectedAgent === "claude") {
    var u = document.getElementById("mac-username");
    if (u && (!u.value || !u.value.trim())) {
      u.style.borderColor = "#dc2626";
      u.placeholder = "Required — enter your Mac username";
      u.focus();
      return;
    }
  }
  goTo(3);
}

var _d = JSON.parse(document.getElementById('__mighty_onboarding_data__').textContent);
var MCP_CONFIG   = _d.mcp_config;
var SYSTEM_PROMPT = _d.system_prompt;
var API_KEY      = _d.api_key;
var BASE_URL     = _d.base_url;

// Populate dynamic content into pre-rendered panels on page load
(function() {
  // MCP config box
  var mcpEl = document.getElementById('mcp-config-box');
  if (mcpEl) mcpEl.textContent = JSON.stringify(JSON.parse(MCP_CONFIG), null, 2);
  // System prompt textareas
  var p1 = document.getElementById('prompt-box');
  if (p1) p1.value = SYSTEM_PROMPT;
  var p2 = document.getElementById('prompt-box2');
  if (p2) p2.value = SYSTEM_PROMPT;
  // Claude Desktop MCP-style prompt (separate from HTTP-API prompt)
  var p0 = document.getElementById('prompt-box-claude');
  if (p0) p0.value = buildCheckpointPrompt([], 'claude');
  // API key box
  var akEl = document.getElementById('api-key-box');
  if (akEl) akEl.textContent = API_KEY;
  // Docs link — points to Settings (API key lives there)
  var dlEl = document.getElementById('docs-link');
  if (dlEl) dlEl.href = '/settings';
})();

var AGENT_TITLES = {
  claude:  'Connect Claude Desktop',
  chatgpt: 'Connect ChatGPT',
  custom:  'Connect your custom agent'
};
var AGENT_SUBS = {
  claude:  'Download the MCP server, add it to your config, and restart Claude Desktop.',
  chatgpt: 'Add the Mighty system prompt to a ChatGPT Project or Custom GPT.',
  custom:  'Add your API key and the Mighty system prompt to your agent.'
};

function renderSetup(agent) {
  document.getElementById('setup-title').textContent = AGENT_TITLES[agent] || 'Connect your agent';
  document.getElementById('setup-sub').textContent   = AGENT_SUBS[agent]   || '';
  document.querySelectorAll('.agent-setup').forEach(function(el) { el.style.display = 'none'; });
  var panel = document.getElementById('setup-' + agent);
  if (panel) panel.style.display = 'block';
}

function updateMcpConfig(username) {
  var box = document.getElementById('mcp-config-box');
  if (!box) return;
  try {
    var config = JSON.parse(MCP_CONFIG);
    var u = username.trim() || 'YOUR_USERNAME';
    config.mcpServers.mighty.args[0] = '/Users/' + u + '/mighty_mcp.py';
    box.textContent = JSON.stringify(config, null, 2);
  } catch(e) {}
}

function copyBox(id, btn) {
  var el = document.getElementById(id);
  var text = el.tagName === 'TEXTAREA' ? el.value : el.textContent;
  navigator.clipboard.writeText(text);
  btn.textContent = 'Copied!';
  setTimeout(function() { btn.textContent = 'Copy'; }, 1800);
}

function copyCurl(btn) {
  navigator.clipboard.writeText(document.getElementById('test-curl').textContent);
  btn.textContent = 'Copied!';
  setTimeout(function() { btn.textContent = 'Copy'; }, 1800);
}

function startTestPoll() {
  var since = Math.floor(Date.now() / 1000);
  var slowTimer = setTimeout(function() {
    var note = document.getElementById('test-slow-note');
    if (note) note.style.display = 'block';
  }, 20000);
  testPollTimer = setInterval(function() {
    fetch('/dashboard/has-pending?since=' + since).then(function(r) { return r.json(); }).then(function(d) {
      if (d.pending) {
        clearInterval(testPollTimer);
        clearTimeout(slowTimer);
        document.getElementById('test-waiting').style.display = 'none';
        document.getElementById('test-connected').style.display = 'block';
      }
    });
  }, 2000);
}

function enablePush() {
  if (!swReg) {
    document.getElementById('push-status').textContent = 'Push not supported in this browser.';
    return;
  }
  document.getElementById('push-status').textContent = 'Setting up…';
  fetch('/api/push/vapid-public-key').then(function(r) { return r.json(); }).then(function(d) {
    var converted = urlB64ToUint8Array(d.key);
    swReg.pushManager.getSubscription().then(function(existing) {
      return existing ? existing.unsubscribe() : Promise.resolve(true);
    }).then(function() {
      return swReg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: converted });
    }).then(function(sub) {
      return fetch('/api/push/subscribe', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({subscription: sub.toJSON()})
      });
    }).then(function() {
      document.getElementById('push-btn').textContent = 'Notifications enabled ✓';
      document.getElementById('push-status').textContent = "You're all set.";
    }).catch(function(e) {
      document.getElementById('push-status').textContent = 'Could not enable: ' + e.message;
    });
  });
}

function urlB64ToUint8Array(b) {
  var pad = '='.repeat((4 - b.length % 4) % 4);
  var base64 = (b + pad).replace(/-/g,'+').replace(/_/g,'/');
  var raw = atob(base64); var out = new Uint8Array(raw.length);
  for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function finish() {
  fetch('/onboarding/complete', {method:'POST'}).then(function() {
    window.location.href = '/dashboard';
  });
}
</script>
</body>
</html>"""

SETTINGS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Settings — Mighty</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;flex-direction:column;height:100vh;overflow:hidden;background:#f8f7f5}
.topbar{background:#fff;border-bottom:1px solid #e5e3df;padding:0 24px;height:52px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
.topbar-logo{display:flex;align-items:center;gap:8px}
.topbar-logo-mark{width:26px;height:26px;display:flex;align-items:center;justify-content:center}
.topbar-logo-mark img{height:26px;width:auto}
.topbar-name{font-size:14px;font-weight:800;letter-spacing:0.5px;background:linear-gradient(135deg,#7c3aed,#6d28d9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.topbar-right{display:flex;align-items:center;gap:16px}
.topbar-email{font-size:12px;color:#9ca3af}
.btn-logout{font-size:12px;color:#6b7280;background:none;border:none;cursor:pointer;padding:4px 8px;border-radius:5px;transition:background 0.12s}
.btn-logout:hover{background:#f3f4f6;color:#1a1a1a}
.settings-body{flex:1;min-height:0;overflow-y:auto}
.settings-wrap{max-width:520px;margin:0 auto;padding:32px 24px;display:flex;flex-direction:column;gap:16px}
.page-title{font-size:18px;font-weight:700;color:#1a1a1a;margin-bottom:4px}
.card{background:#fff;border:1px solid #e5e3df;border-radius:12px;padding:24px}
.section-title{font-size:14px;font-weight:700;color:#1a1a1a;margin-bottom:16px}
.toggle-row{display:flex;align-items:flex-start;gap:12px;padding:12px 0}
.toggle-row+.toggle-row{border-top:1px solid #f3f4f6}
.toggle-label{font-size:13px;font-weight:600;color:#1a1a1a;margin-bottom:3px}
.toggle-hint{font-size:12px;color:#6b7280;line-height:1.5}
.api-key-wrap{display:flex;align-items:center;gap:8px;margin-top:4px}
.api-key-val{flex:1;font-family:ui-monospace,monospace;font-size:10px;color:#6b7280;background:#f8f7f5;border:1px solid #e5e3df;border-radius:6px;padding:8px 10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.btn-copy-key{font-size:12px;font-weight:600;padding:6px 12px;background:#f3f0ff;color:#7c3aed;border:1px solid #e9d5ff;border-radius:6px;white-space:nowrap;cursor:pointer;transition:background 0.12s}
.btn-copy-key:hover{background:#ede9fe}
.push-status{font-size:12px;color:#6b7280;margin-top:6px;min-height:16px}
.push-btn{font-size:12px;font-weight:600;padding:6px 12px;background:#f3f0ff;color:#7c3aed;border:1px solid #e9d5ff;border-radius:6px;cursor:pointer;transition:background 0.12s;display:none;margin-top:6px}
.push-btn:hover{background:#ede9fe}
.btn-danger{font-size:12px;font-weight:600;padding:8px 12px;background:#fff;color:#dc2626;border:1.5px solid #fecaca;border-radius:6px;white-space:nowrap;cursor:pointer;transition:all 0.12s;width:100%;text-align:left}
.btn-danger:hover{background:#fef2f2}
.ntfy-link{display:inline-block;margin-top:6px;font-size:10px;font-family:ui-monospace,monospace;color:#7c3aed;background:#f8f7f5;border:1px solid #e5e3df;border-radius:6px;padding:6px 10px;text-decoration:none;word-break:break-all}
</style>
</head>
<body>
<div class="topbar">
  <a href="/dashboard" style="text-decoration:none;display:flex;align-items:center;gap:8px">
    <div class="topbar-logo-mark"><img src="/logo-icon.png" alt="Mighty"></div>
    <span class="topbar-name">Mighty</span>
  </a>
  <div class="topbar-right">
    <a href="/dashboard" style="font-size:12px;color:#6b7280;text-decoration:none">&#8592; Dashboard</a>
    <span class="topbar-email">{email}</span>
    <form method="POST" action="/logout" style="margin:0"><button class="btn-logout" type="submit">Sign out</button></form>
  </div>
</div>

<div class="settings-body">
  <div class="settings-wrap">
    <div class="page-title">Settings</div>

    <div class="card">
      <div class="section-title">Notifications</div>
      <div class="toggle-row">
        <input type="checkbox" id="notif-push" {push_checked} onchange="save()" style="width:16px;height:16px;accent-color:#7c3aed;flex-shrink:0;margin-top:2px">
        <div>
          <div class="toggle-label">Browser alerts</div>
          <div class="toggle-hint">Desktop popup when your agent needs a decision. Click Allow notifications below to activate.</div>
          <div id="push-status" class="push-status"></div>
          <button id="push-enable-btn" class="push-btn" onclick="enablePush()">Allow notifications &#8594;</button>
        </div>
      </div>
      <div class="toggle-row">
        <input type="checkbox" id="notif-ntfy" {ntfy_checked} onchange="save()" style="width:16px;height:16px;accent-color:#7c3aed;flex-shrink:0;margin-top:2px">
        <div>
          <div class="toggle-label">Phone alerts</div>
          <div class="toggle-hint">Install the free <a href="https://ntfy.sh" target="_blank" style="color:#7c3aed">ntfy app</a>, then subscribe to your channel on your phone.</div>
          <a href="https://ntfy.sh/{ntfy_topic}" target="_blank" class="ntfy-link">ntfy.sh/{ntfy_topic} &#8599;</a>
        </div>
      </div>
      <div class="toggle-row">
        <input type="checkbox" id="notif-email" {email_checked} onchange="save()" style="width:16px;height:16px;accent-color:#7c3aed;flex-shrink:0;margin-top:2px">
        <div>
          <div class="toggle-label">Email alerts</div>
          <div class="toggle-hint">Receive an email when your agent requests approval.</div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="section-title">Connection</div>
      <div style="font-size:12px;color:#6b7280;margin-bottom:8px">Your API key — used in the MCP server config and system prompt.</div>
      <div class="api-key-wrap">
        <div class="api-key-val" id="apiKeyVal">{api_key}</div>
        <button class="btn-copy-key" onclick="copyKey(this)">Copy</button>
      </div>
      <div style="margin-top:16px;padding-top:16px;border-top:1px solid #f3f4f6">
        <a href="/onboarding" style="font-size:13px;color:#7c3aed;text-decoration:none">&#8635; Re-run setup wizard</a>
      </div>
    </div>

    <div class="card">
      <div class="section-title">Data &amp; Privacy</div>
      <button class="btn-copy-key" onclick="window.location.href='/settings/export-csv'" style="margin-bottom:4px">Export activity log</button>
      <hr style="border:none;border-top:1px solid #f3f4f6;margin:16px 0">
      <div style="display:flex;flex-direction:column;gap:10px">
        <button class="btn-danger" id="del-activity-btn" onclick="deleteActivity()">Delete all activity</button>
        <span id="del-activity-msg" style="font-size:12px;color:#16a34a;display:none">Activity deleted.</span>
        <button class="btn-danger" onclick="deleteAccount()">Delete my account</button>
      </div>
    </div>
  </div>
</div>

<script>
var swReg = null;
if ('serviceWorker' in navigator && 'PushManager' in window) {
  navigator.serviceWorker.register('/sw.js').then(function(reg) {
    swReg = reg;
    reg.pushManager.getSubscription().then(function(sub) {
      var status = document.getElementById('push-status');
      var btn = document.getElementById('push-enable-btn');
      if (sub) {
        if (status) { status.textContent = 'Active ✓'; status.style.color = '#16a34a'; }
        if (btn) btn.style.display = 'none';
      } else if (Notification.permission === 'denied') {
        if (status) status.textContent = 'Blocked — allow in browser settings to enable.';
        if (btn) btn.style.display = 'none';
      } else {
        if (btn) btn.style.display = 'inline-block';
      }
    });
  });
}
function save() {
  fetch('/dashboard/notifications', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      ntfy: document.getElementById('notif-ntfy').checked,
      push: document.getElementById('notif-push').checked,
      email: document.getElementById('notif-email').checked
    })
  });
}
function enablePush() {
  if (!swReg) return;
  var status = document.getElementById('push-status');
  var btn = document.getElementById('push-enable-btn');
  if (status) status.textContent = 'Setting up…';
  if (btn) btn.style.display = 'none';
  fetch('/api/push/vapid-public-key').then(function(r) { return r.json(); }).then(function(d) {
    var converted = urlB64ToUint8Array(d.key);
    swReg.pushManager.getSubscription().then(function(e) {
      return e ? e.unsubscribe() : Promise.resolve(true);
    }).then(function() {
      return swReg.pushManager.subscribe({userVisibleOnly:true, applicationServerKey:converted});
    }).then(function(sub) {
      return fetch('/api/push/subscribe', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({subscription:sub.toJSON()})});
    }).then(function() {
      if (status) { status.textContent = 'Active ✓'; status.style.color = '#16a34a'; }
      if (btn) btn.style.display = 'none';
    }).catch(function(e) {
      if (Notification.permission === 'denied') {
        if (status) status.textContent = 'Blocked — allow in browser settings.';
      } else {
        if (status) status.textContent = 'Could not enable: ' + e.message;
        if (btn) btn.style.display = 'inline-block';
      }
    });
  });
}
function copyKey(btn) {
  navigator.clipboard.writeText(document.getElementById('apiKeyVal').textContent.trim());
  btn.textContent = 'Copied!';
  setTimeout(function() { btn.textContent = 'Copy'; }, 1800);
}
function urlB64ToUint8Array(b) {
  var pad = '='.repeat((4 - b.length % 4) % 4);
  var base64 = (b + pad).replace(/-/g,'+').replace(/_/g,'/');
  var raw = atob(base64); var out = new Uint8Array(raw.length);
  for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}
function deleteActivity() {
  if (!confirm("This will permanently delete your entire activity log. This cannot be undone.")) return;
  fetch('/settings/delete-activity', {method: 'POST'}).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) {
      var msg = document.getElementById('del-activity-msg');
      if (msg) { msg.style.display = 'inline'; setTimeout(function() { msg.style.display = 'none'; }, 3000); }
    }
  });
}
function deleteAccount() {
  if (!confirm("This will permanently delete your account and all data. This cannot be undone.")) return;
  fetch('/settings/delete-account', {method: 'POST'}).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) { window.location.href = '/'; }
  });
}
</script>
</body>
</html>"""

APPROVE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="theme-color" content="#7c3aed">
<title>Authorize action — Mighty</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px;background:#f8f7f5}
.wrap{width:100%;max-width:480px}
.brand{display:flex;align-items:center;gap:8px;margin-bottom:20px;justify-content:center}
.brand-mark{width:28px;height:28px;display:flex;align-items:center;justify-content:center}
.brand-mark img{height:28px;width:auto}
.brand-name{font-size:16px;font-weight:800;letter-spacing:0.5px;background:linear-gradient(135deg,#7c3aed,#6d28d9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.card{background:#fff;border:1px solid #e5e3df;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08)}
.card-header{background:#f5f3ff;border-bottom:1px solid #e9d5ff;padding:16px 20px;display:flex;align-items:center;gap:10px}
.card-header-dot{width:8px;height:8px;border-radius:50%;background:#7c3aed;animation:pulse 1.5s infinite;flex-shrink:0}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
.card-header-text{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#5b21b6}
.card-headline{font-size:18px;font-weight:700;color:#1a1a1a;padding:18px 20px 4px;line-height:1.4}
.card-type{font-size:12px;color:#9ca3af;padding:0 20px 16px;font-family:ui-monospace,monospace}
.card-fields{padding:0 20px 16px;display:flex;flex-direction:column;gap:10px;border-bottom:1px solid #f0ede8}
.field-label{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:#9ca3af;margin-bottom:2px}
.field-value{font-size:13px;color:#1a1a1a;line-height:1.5;word-break:break-word}
.card-actions{padding:16px 20px;display:flex;gap:10px}
.btn-approve{flex:1;padding:16px;background:#16a34a;color:#fff;border:none;border-radius:8px;font-size:16px;font-weight:700;transition:background 0.12s;touch-action:manipulation}
.btn-approve:hover{background:#15803d}
.btn-approve:active{transform:scale(0.98)}
.btn-deny{flex:1;padding:16px;background:#fff;color:#dc2626;border:2px solid #fecaca;border-radius:8px;font-size:16px;font-weight:700;transition:all 0.12s;touch-action:manipulation}
.btn-deny:hover{background:#fef2f2}
.btn-deny:active{transform:scale(0.98)}
.outcome{text-align:center;padding:28px 20px;font-size:15px;font-weight:600}
.outcome.approved{color:#16a34a;background:#f0fdf4}
.outcome.denied{color:#dc2626;background:#fef2f2}
.outcome.timeout{color:#9ca3af;background:#f9fafb}
.timeout-note{font-size:12px;color:#9ca3af;text-align:center;padding:10px 20px 16px;line-height:1.5}
</style>
</head>
<body>
<div class="wrap">
  <div class="brand">
    <div class="brand-mark">
      <img src="/logo-icon.png" alt="Mighty">
    </div>
    <span class="brand-name">Mighty</span>
  </div>
  <div class="card">
    {body}
  </div>
  <div id="agent-waiting-note" style="text-align:center;margin-top:16px;font-size:12px;color:#9ca3af">Your AI agent is waiting for this decision.</div>
</div>
</body>
</html>"""


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/logo-icon.png")
def logo_icon():
    import base64
    from flask import Response
    return Response(base64.b64decode(LOGO_ICON_B64), mimetype="image/png",
                    headers={"Cache-Control": "public, max-age=31536000"})

@app.route("/")
def landing():
    if "user_id" in session:
        return redirect("/dashboard")
    return LANDING_HTML

@app.route("/signup", methods=["GET"])
def signup_page():
    if "user_id" in session:
        return redirect("/dashboard")
    return SIGNUP_HTML.replace("{error}", "")

@app.route("/signup", methods=["POST"])
def signup():
    email    = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    if not email or not password or len(password) < 6:
        err = '<div class="err">Please enter a valid email and a password (6+ characters).</div>'
        return SIGNUP_HTML.replace("{error}", err)
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        err = '<div class="err">An account with that email already exists. <a href="/login">Sign in</a></div>'
        return SIGNUP_HTML.replace("{error}", err)
    uid = secrets.token_hex(16)
    key = "mk_" + secrets.token_hex(20)
    db.execute(
        "INSERT INTO users (id,email,password_hash,api_key,created_at) VALUES (?,?,?,?,?)",
        (uid, email, hash_pw(password), key, iso()),
    )
    db.commit()
    session["user_id"] = uid
    session["email"]   = email
    return redirect("/onboarding")

@app.route("/enterprise-interest", methods=["POST"])
def enterprise_interest():
    data    = request.get_json(force=True)
    name    = data.get("name", "").strip()
    email   = data.get("email", "").strip()
    company = data.get("company", "").strip()
    message = data.get("message", "").strip()
    if not name or not email:
        return jsonify({"error": "name and email required"}), 400
    db = get_db()
    db.execute(
        "INSERT INTO enterprise_leads (id,name,email,company,message,created_at) VALUES (?,?,?,?,?,?)",
        (secrets.token_hex(12), name, email, company, message, iso())
    )
    db.commit()
    return jsonify({"ok": True})

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
    return redirect("/")


# ── Dashboard ─────────────────────────────────────────────────────────────────

def build_prompt(api_key, url):
    return (
        f"MIGHTY AUTHORIZATION — follow every session.\n\n"
        f"Before any consequential action (emails, purchases, file edits, external APIs):\n"
        f"  POST {url}/api/authorize\n"
        f"  {{\"api_key\":\"{api_key}\",\"action_type\":\"<type>\",\"label\":\"<description>\",\"fields\":[[\"Key\",\"Val\"]]}}\n"
        f"  → approved: proceed | denied: stop | pending: poll GET {url}/api/status/<id> every 3s\n\n"
        f"Routine actions (log silently):\n"
        f"  POST {url}/api/record\n"
        f"  {{\"api_key\":\"{api_key}\",\"action_type\":\"<type>\",\"label\":\"<description>\",\"outcome\":\"completed\"}}"
    )

def call_claude_for_prompt(description, api_key, url):
    """Call Claude Haiku to generate a tailored checkpoint prompt from an agent description."""
    system = (
        "You generate concise system prompt instructions for AI agents that tell them when to call "
        "the Mighty authorization API. Given a description of what an agent does, produce checkpoint "
        "instructions that list the specific action types requiring authorization.\n\n"
        "Return a JSON object with exactly two fields:\n"
        "- \"prompt\": string — the complete checkpoint instructions, concise and specific to this agent\n"
        "- \"warning\": string or null — null if the description was specific enough; a short plain-English "
        "message (1 sentence) if the description was too vague to generate useful checkpoints\n\n"
        "The prompt must include:\n"
        "1. A specific list of action types derived from the agent's description\n"
        "2. The exact API call format using the provided api_key and url\n"
        "3. Brief instructions for polling and handling approved/denied/timeout responses\n\n"
        "Keep the prompt under 120 words. Return JSON only, no markdown fences."
    )
    user_msg = (
        f"Agent description: {description}\n"
        f"API key: {api_key}\n"
        f"Mighty URL: {url}\n\n"
        f"API endpoints:\n"
        f"  Authorize: POST {url}/api/authorize\n"
        f"    body: {{\"api_key\":\"{api_key}\",\"action_type\":\"<type>\",\"label\":\"<desc>\",\"fields\":[[\"Key\",\"Val\"]]}}\n"
        f"  Poll status: GET {url}/api/status/<request_id>  →  approved | denied | pending | timeout\n"
        f"  Record (no approval): POST {url}/api/record\n"
        f"    body: {{\"api_key\":\"{api_key}\",\"action_type\":\"<type>\",\"label\":\"<desc>\",\"outcome\":\"completed\"}}"
    )
    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 512,
        "system": system,
        "messages": [{"role": "user", "content": user_msg}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01"
        }
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
    text = result["content"][0]["text"].strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
        if text.endswith("```"):
            text = text[:-3].strip()
    return json.loads(text)

def build_mcp_config(api_key, url):
    return (
        '{\n'
        '  "mcpServers": {\n'
        '    "mighty": {\n'
        '      "command": "python3",\n'
        '      "args": ["/Users/YOUR_USERNAME/mighty_mcp.py"],\n'
        '      "env": {\n'
        f'        "MIGHTY_API_KEY": "{api_key}",\n'
        f'        "MIGHTY_BASE_URL": "{url}"\n'
        '      }\n'
        '    }\n'
        '  }\n'
        '}'
    )

def build_feed_html(actions, base):
    if not actions:
        return '''<div class="empty-state">
  <div class="empty-state-icon">
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none"><path d="M10 2L3 6v8l7 4 7-4V6l-7-4z" stroke="#7c3aed" stroke-width="1.5" stroke-linejoin="round"/><path d="M10 2v12M3 6l7 4 7-4" stroke="#7c3aed" stroke-width="1.5" stroke-linejoin="round"/></svg>
  </div>
  <div class="empty-state-title">No actions yet</div>
  <div class="empty-state-sub">When your agent logs an action or requests approval,<br>it will appear here.</div>
</div>'''
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
    badge = STATUS_BADGE.get(a["status"], "")
    # Consequence level badge (only show if not routine)
    clevel = ""
    try:
        lvl = a["consequence_level"] if "consequence_level" in a.keys() else "routine"
    except Exception:
        lvl = "routine"
    if lvl and lvl != "routine":
        clevel = f'<span class="clevel-{he(lvl)}">{he(lvl.title())}</span>'
    # Fields
    fields_html = ""
    if a["fields"]:
        try:
            flist = json.loads(a["fields"])
            for k, v in flist:
                val = v if isinstance(v, str) else json.dumps(v)
                fields_html += f'<div class="field-row"><span class="field-key">{he(k)}</span><span class="field-val">{he(val)}</span></div>'
        except Exception:
            pass
    pending_cls = " is-pending" if a["status"] == "pending" else ""
    btns = ""
    if show_buttons:
        btns = f'''<div class="action-buttons">
          <button class="btn-authorize" onclick="decide('{he(a["id"])}','approve')">Approve</button>
          <button class="btn-reject"    onclick="decide('{he(a["id"])}','deny')">Deny</button>
        </div>'''
    return f'''<div class="action-card{pending_cls}">
      <div class="action-top">
        <div style="min-width:0;flex:1">
          <div class="action-label">{he(a["label"])}</div>
        </div>
        <div class="action-badges">
          {clevel}
          {badge}
          <div class="action-time">{fmt_time(a["created_at"])}</div>
        </div>
      </div>
      {'<div class="action-fields">' + fields_html + '</div>' if fields_html else '<div style="height:14px"></div>'}
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
    url        = base_url()
    prompt     = build_prompt(user["api_key"], url)
    mcp_config = build_mcp_config(user["api_key"], url)
    feed       = build_feed_html(acts, url)
    topic      = ntfy_topic(user["api_key"])
    pending_count   = db.execute(
        "SELECT COUNT(*) FROM actions WHERE user_id=? AND status='pending'",
        (session["user_id"],),
    ).fetchone()[0]
    pending_display = "flex" if pending_count > 0 else "none"
    is_connected    = len(acts) > 0 and bool(user["onboarded"])


    onboarding_banner = ""
    if not user["onboarded"]:
        onboarding_banner = (
            '<div style="grid-column:1/-1;background:#f3f0ff;border:1px solid #e9d5ff;'
            'border-radius:10px;padding:14px 18px;display:flex;align-items:center;'
            'justify-content:space-between;gap:16px;margin-bottom:16px">'
            '<div style="font-size:13px;color:#5b21b6">'
            'Finish setting up Mighty to connect your first agent.</div>'
            '<a href="/onboarding" style="font-size:13px;font-weight:600;color:#7c3aed;white-space:nowrap">'
            'Complete setup &#8594;</a></div>'
        )

    if len(acts) == 0:
        # Empty state — full-width, centred, no sidebar
        sidebar_content = (
            '<div style="grid-column:1/-1;display:flex;flex-direction:column;'
            'align-items:center;justify-content:center;padding:60px 24px">'
            '<div style="width:100%;max-width:360px;text-align:center">'
            '<div style="width:52px;height:52px;background:#f3f0ff;border-radius:14px;'
            'display:flex;align-items:center;justify-content:center;margin:0 auto 20px;font-size:24px">&#9889;</div>'
            '<div style="font-size:22px;font-weight:700;color:#1a1a1a;margin-bottom:10px">'
            'Welcome to Mighty</div>'
            '<div style="font-size:14px;color:#6b7280;line-height:1.6;margin-bottom:28px">'
            'Set up Mighty to work with your agent in just a few steps. Once set up, '
            'you will be able to see and respond to approval requests.</div>'
            '<a href="/onboarding" style="display:block;padding:13px 20px;'
            'background:#7c3aed;color:#fff;border-radius:8px;font-size:14px;font-weight:600;'
            'text-decoration:none;margin-bottom:16px">Get started &#8594;</a>'
''
            '</div>'
            '</div>'
        )
        feed_col_hidden = 'style="display:none"'
    else:
        # Active state: two-column layout
        if is_connected:
            sidebar_card = (
                '<div class="card">'
                '<div class="status-row">'
                '<div class="status-dot status-green"></div>'
                '<div>'
                '<div class="status-title">Mighty is active</div>'
                '<div class="status-sub">Your agent is connected</div>'
                '</div></div>'
                '<div style="margin-top:14px;padding-top:14px;border-top:1px solid #f0ede8">'
                '<a href="/onboarding" style="font-size:13px;color:#7c3aed;text-decoration:none">'
                '&#43; Connect another agent</a>'
                '</div>'
                '</div>'
            )
        else:
            sidebar_card = (
                '<div class="card">'
                '<div class="setup-heading">Connect your agent</div>'
                '<p style="font-size:13px;color:#6b7280;line-height:1.5;margin-bottom:14px">'
                'Run the setup wizard to connect Claude Desktop, ChatGPT, or a custom agent.</p>'
                '<a href="/onboarding" style="display:block;text-align:center;padding:10px;'
                'background:#7c3aed;color:#fff;border-radius:8px;font-size:13px;font-weight:600;'
                'text-decoration:none">Set up another agent &#8594;</a>'
                '</div>'
            )
        sidebar_content = '<div class="sidebar">' + sidebar_card + '</div>'
        feed_col_hidden = ''

    return (DASHBOARD_HTML
            .replace("{email}",             user["email"])
            .replace("{feed_html}",         feed)
            .replace("{pending_count}",     str(pending_count))
            .replace("{pending_display}",   pending_display)
            .replace("{sidebar_content}",   sidebar_content)
            .replace("{feed_col_hidden}",   feed_col_hidden)
            .replace("{onboarding_banner}", onboarding_banner))

@app.route("/settings")
@require_login
def settings():
    db   = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    topic = ntfy_topic(user["api_key"])
    return (SETTINGS_HTML
            .replace("{email}",        user["email"])
            .replace("{api_key}",      user["api_key"])
            .replace("{ntfy_topic}",   topic)
            .replace("{push_checked}", "checked" if user["notify_push"]  else "")
            .replace("{ntfy_checked}", "checked" if user["notify_ntfy"]   else "")
            .replace("{email_checked}","checked" if user["notify_email"] else ""))

@app.route("/settings/export-csv")
@require_login
def export_csv():
    db      = get_db()
    user_id = session["user_id"]
    rows    = db.execute(
        "SELECT created_at, action_type, label, fields, status, outcome, decided_at "
        "FROM actions WHERE user_id=? ORDER BY created_at DESC",
        (user_id,)
    ).fetchall()
    si = io.StringIO()
    writer = csv.writer(si)
    writer.writerow(["Date", "Action Type", "Description", "Details", "Status", "Outcome", "Decided At"])
    for row in rows:
        try:
            fields_raw = json.loads(row["fields"]) if row["fields"] else []
            if isinstance(fields_raw, list):
                details = "; ".join(
                    "{}: {}".format(pair[0], pair[1])
                    for pair in fields_raw
                    if isinstance(pair, (list, tuple)) and len(pair) >= 2
                )
            elif isinstance(fields_raw, dict):
                details = "; ".join("{}: {}".format(k, v) for k, v in fields_raw.items())
            else:
                details = ""
        except Exception:
            details = ""
        writer.writerow([
            row["created_at"],
            row["action_type"],
            row["label"],
            details,
            row["status"],
            row["outcome"] or "",
            row["decided_at"] or "",
        ])
    output = si.getvalue()
    filename = "mighty-activity-{}.csv".format(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    resp = make_response(output)
    resp.headers["Content-Type"] = "text/csv"
    resp.headers["Content-Disposition"] = "attachment; filename={}".format(filename)
    return resp

@app.route("/settings/delete-activity", methods=["POST"])
@require_login
def delete_activity():
    db = get_db()
    db.execute("DELETE FROM actions WHERE user_id=?", (session["user_id"],))
    db.commit()
    return jsonify({"ok": True})

@app.route("/settings/delete-account", methods=["POST"])
@require_login
def delete_account():
    db      = get_db()
    user_id = session["user_id"]
    db.execute("DELETE FROM push_subscriptions WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM actions WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    session.clear()
    return jsonify({"ok": True})

@app.route("/download/mighty_mcp.py")
@require_login
def download_mcp():
    user = get_db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    api_key  = user["api_key"]
    base     = base_url()
    # Read the MCP server from disk if available, otherwise return a minimal version
    script_path = os.path.join(os.path.dirname(__file__), "mighty_mcp.py")
    if os.path.exists(script_path):
        with open(script_path) as f:
            script = f.read()
        # Inject credentials as defaults
        script = script.replace(
            'os.environ.get("MIGHTY_API_KEY", "")',
            f'os.environ.get("MIGHTY_API_KEY", "{api_key}")'
        ).replace(
            'os.environ.get("MIGHTY_BASE_URL", "")',
            f'os.environ.get("MIGHTY_BASE_URL", "{base}")'
        )
    else:
        script = f'# Mighty MCP Server\n# API Key: {api_key}\n# Visit {base} for setup instructions\n'
    resp = make_response(script)
    resp.headers["Content-Disposition"] = "attachment; filename=mighty_mcp.py"
    resp.headers["Content-Type"] = "text/x-python"
    return resp

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
    since = request.args.get("since")
    if since:
        try:
            since_dt = datetime.fromtimestamp(float(since), tz=timezone.utc).isoformat()
            row = get_db().execute(
                "SELECT 1 FROM actions WHERE user_id=? AND status='pending' AND created_at > ? LIMIT 1",
                (session["user_id"], since_dt),
            ).fetchone()
        except (ValueError, OSError):
            row = get_db().execute(
                "SELECT 1 FROM actions WHERE user_id=? AND status='pending' LIMIT 1",
                (session["user_id"],),
            ).fetchone()
    else:
        row = get_db().execute(
            "SELECT 1 FROM actions WHERE user_id=? AND status='pending' LIMIT 1",
            (session["user_id"],),
        ).fetchone()
    return jsonify({"pending": bool(row)})

@app.route("/dashboard/notifications", methods=["POST"])
@require_login
def update_notifications():
    data = request.get_json(force=True)
    db = get_db()
    db.execute(
        "UPDATE users SET notify_email=?, notify_ntfy=?, notify_push=? WHERE id=?",
        (1 if data.get("email") else 0,
         1 if data.get("ntfy") else 0,
         1 if data.get("push") else 0,
         session["user_id"])
    )
    db.commit()
    return jsonify({"ok": True})


# ── Onboarding wizard ────────────────────────────────────────────────────────

@app.route("/onboarding")
@require_login
def onboarding():
    user = get_db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    url  = base_url()
    import json as _json
    test_curl = (
        f'curl -X POST {url}/api/authorize \\\n'
        f'  -H "Content-Type: application/json" \\\n'
        f'  -d \'{{"api_key":"{user["api_key"]}","action_type":"test","label":"Connection test"}}\''
    )
    ntfy_url  = f"https://ntfy.sh/{ntfy_topic(user['api_key'])}"
    # Inject data safely via a JSON script element — avoids JS syntax issues
    onboarding_data = _json.dumps({
        "mcp_config":    build_mcp_config(user["api_key"], url),
        "system_prompt": build_prompt(user["api_key"], url),
        "api_key":       user["api_key"],
        "base_url":      url,
    })
    return (ONBOARDING_HTML
            .replace("MIGHTY_ONBOARDING_DATA", onboarding_data)
            .replace("{test_curl}",            test_curl)
            .replace("{ntfy_url}",             ntfy_url))

@app.route("/onboarding/complete", methods=["POST"])
@require_login
def onboarding_complete():
    get_db().execute("UPDATE users SET onboarded=1 WHERE id=?", (session["user_id"],))
    get_db().commit()
    return jsonify({"ok": True})

@app.route("/onboarding/generate-prompt", methods=["POST"])
@require_login
def onboarding_generate_prompt():
    data        = request.get_json(force=True)
    description = (data.get("description") or "").strip()
    user        = get_db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    url         = base_url()
    api_key     = user["api_key"]
    if not description or not ANTHROPIC_API_KEY:
        return jsonify({"prompt": build_prompt(api_key, url), "warning": None})
    try:
        result = call_claude_for_prompt(description, api_key, url)
        # Ensure both expected keys are present
        return jsonify({
            "prompt":  result.get("prompt",  build_prompt(api_key, url)),
            "warning": result.get("warning", None),
        })
    except Exception:
        return jsonify({"prompt": build_prompt(api_key, url), "warning": None})

@app.route("/onboarding/skip")
@require_login
def onboarding_skip():
    get_db().execute("UPDATE users SET onboarded=1 WHERE id=?", (session["user_id"],))
    get_db().commit()
    return redirect("/dashboard")


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
        labels = {"approved": "&#10003; Approved", "denied": "&#10007; Denied", "timeout": "Timed out"}
        label  = labels.get(row["status"], he(row["status"].title()))
        body   = f'<div class="outcome {he(row["status"])}">{label}</div>'
        return APPROVE_HTML.replace("{body}", body).replace(
            '<div id="agent-waiting-note"',
            '<div id="agent-waiting-note" style="display:none"'
        )
    # Build fields HTML
    fields_html = ""
    if row["fields"]:
        try:
            for k, v in json.loads(row["fields"]):
                val = v if isinstance(v, str) else json.dumps(v)
                fields_html += f'<div style="margin-bottom:12px"><div class="field-label">{he(k)}</div><div class="field-value">{he(val)}</div></div>'
        except Exception:
            pass
    expires_at_val = row["expires_at"] or ""
    body = f"""
      <div class="card-header"><div class="card-header-dot"></div><span class="card-header-text">Authorization Required</span></div>
      <div class="card-headline">{he(row["label"])}</div>
      <div class="card-type">{he(row["action_type"])}</div>
      {'<div class="card-fields">' + fields_html + '</div>' if fields_html else ''}
      <div class="card-actions">
        <button class="btn-approve" onclick="submit('approve')">Approve</button>
        <button class="btn-deny"    onclick="submit('deny')">Deny</button>
      </div>
      <div class="timeout-note">This request will time out in 5 minutes if not decided.</div>
      <div id="expiry-timer" style="text-align:center;padding:0 20px 14px;font-size:12px;color:#aaa"></div>
      <script>
      function submit(dec) {{
        document.querySelectorAll(".btn-approve, .btn-deny").forEach(function(b) {{ b.disabled = true; }});
        fetch('/approve/{token}', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{decision:dec}})}})
          .then(function(r) {{
            if (!r.ok) {{
              document.getElementById("agent-waiting-note").style.display = "none";
              document.querySelector(".card-actions").innerHTML = '<div style="text-align:center;padding:20px;font-size:14px;color:#9ca3af">This request has already expired or been decided.</div>';
              return;
            }}
            return r.json();
          }}).then(function(d) {{
            if (!d) return;
            document.querySelector('.card').innerHTML =
              '<div class="outcome ' + d.status + '">' + (d.status==='approved'?'Approved':'Denied') + '</div>'
              + '<div style="text-align:center;margin-top:16px"><a href="/dashboard" style="font-size:13px;color:#7c3aed;text-decoration:none">Go to dashboard →</a></div>';
            var note = document.getElementById('agent-waiting-note');
            if (note) note.style.display = 'none';
          }});
      }}
      (function() {{
        var expiresAt = new Date('{expires_at_val}');
        function updateTimer() {{
          var now = new Date();
          var diffMs = expiresAt - now;
          if (diffMs <= 0) {{
            document.getElementById('expiry-timer').textContent = 'Expired';
            return;
          }}
          var mins = Math.floor(diffMs / 60000);
          var secs = Math.floor((diffMs % 60000) / 1000);
          document.getElementById('expiry-timer').textContent =
            'Expires in ' + mins + 'm ' + secs + 's';
          setTimeout(updateTimer, 1000);
        }}
        updateTimer();
      }})();
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
    action_type       = data.get("action_type", "other")
    label             = data.get("label", "Action")
    fields            = data.get("fields")
    outcome           = data.get("outcome", "completed")
    consequence_level = data.get("consequence_level", "routine")
    action_id         = secrets.token_hex(16)
    get_db().execute(
        "INSERT INTO actions (id,user_id,action_type,label,fields,status,outcome,consequence_level,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (action_id, user["id"], action_type, label,
         json.dumps(fields) if fields else None, "logged", outcome, consequence_level, iso()),
    )
    get_db().commit()
    return jsonify({"status": "logged", "record_id": action_id})

@app.route("/api/authorize", methods=["POST"])
def api_authorize():
    """Request authorization for a consequential action. Returns pending + approval URL."""
    user, data = api_user()
    if not user:
        return jsonify({"error": "Invalid or missing api_key"}), 401
    action_type       = data.get("action_type", "other")
    label             = data.get("label", "Action")
    fields            = data.get("fields")
    consequence_level = data.get("consequence_level", "routine")
    action_id         = secrets.token_hex(16)
    approval_token    = secrets.token_urlsafe(24)
    expires_at        = (utcnow() + timedelta(seconds=TIMEOUT_SEC)).isoformat()
    get_db().execute(
        "INSERT INTO actions "
        "(id,user_id,action_type,label,fields,status,approval_token,consequence_level,created_at,expires_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (action_id, user["id"], action_type, label,
         json.dumps(fields) if fields else None,
         "pending", approval_token, consequence_level, iso(), expires_at),
    )
    get_db().commit()
    url          = base_url()
    approval_url = f"{url}/approve/{approval_token}"
    # Push notification via ntfy.sh (if user has enabled it)
    if user["notify_ntfy"]:
        send_ntfy_notification(
            api_key=user["api_key"],
            label=label,
            action_type=action_type,
            approval_url=approval_url,
        )
    # Email notification via Postmark (if user has enabled it)
    if user["notify_email"]:
        send_authorization_email(
            to_email=NOTIFY_EMAIL_OVERRIDE or user["email"],
            label=label,
            action_type=action_type,
            fields=fields,
            approval_url=approval_url,
        )
    # Web Push notification
    if user["notify_push"]:
        send_web_push(
            user_id=user["id"],
            title=f"Action needed: {action_type}",
            body=label,
            url=approval_url,
            action_id=action_id,
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


# ── Service worker ────────────────────────────────────────────────────────────

SW_JS = r"""
self.addEventListener('push', function(e) {
  var data = e.data ? e.data.json() : {};
  var title   = data.title   || 'Action needed';
  var body    = data.body    || 'Your agent needs permission.';
  var url     = data.url     || '/dashboard';
  var actions = data.actions || [];
  e.waitUntil(
    self.registration.showNotification(title, {
      body:    body,
      icon:    '/logo-icon.png',
      badge:   '/logo-icon.png',
      tag:     data.tag || 'mighty-auth',
      renotify: true,
      requireInteraction: true,
      actions: actions,
      data:    { url: url }
    })
  );
});

self.addEventListener('notificationclick', function(e) {
  e.notification.close();
  var url = e.notification.data.url;
  if (e.action === 'deny') {
    fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({decision:'deny'})}).catch(function(){});
    return;
  }
  e.waitUntil(clients.openWindow(e.notification.data.url));
});
"""

@app.route("/sw.js")
def service_worker():
    resp = make_response(SW_JS)
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


# ── Push subscription endpoints ───────────────────────────────────────────────

@app.route("/api/push/subscribe", methods=["POST"])
@require_login
def push_subscribe():
    data = request.get_json(force=True)
    sub  = data.get("subscription")
    if not sub:
        return jsonify({"error": "missing subscription"}), 400
    sub_str  = json.dumps(sub)
    endpoint = sub.get("endpoint", "")
    db = get_db()
    # Check if subscription with this endpoint already exists for the user
    existing = db.execute(
        "SELECT id FROM push_subscriptions WHERE user_id=? AND subscription LIKE ?",
        (session["user_id"], f'%{endpoint}%')
    ).fetchone()
    if existing:
        # Update the existing record (keys may have rotated)
        db.execute(
            "UPDATE push_subscriptions SET subscription=?, created_at=? WHERE id=?",
            (sub_str, iso(), existing["id"])
        )
    else:
        sub_id = secrets.token_hex(8)
        db.execute(
            "INSERT INTO push_subscriptions (id,user_id,subscription,created_at) VALUES (?,?,?,?)",
            (sub_id, session["user_id"], sub_str, iso())
        )
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/push/unsubscribe", methods=["POST"])
@require_login
def push_unsubscribe():
    get_db().execute("DELETE FROM push_subscriptions WHERE user_id=?", (session["user_id"],))
    get_db().commit()
    return jsonify({"ok": True})

@app.route("/api/push/vapid-public-key")
def vapid_public_key():
    return jsonify({"key": VAPID_PUBLIC})


# ── Health check ──────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"ok": True})


# ── Dev reset (protected by secret key) ───────────────────────────────────────

@app.route("/admin/reset-all-users", methods=["POST"])
def admin_reset():
    secret = request.headers.get("X-Reset-Secret", "")
    if not secret or secret != os.environ.get("RESET_SECRET", ""):
        return jsonify({"error": "forbidden"}), 403
    db = get_db()
    db.execute("DELETE FROM push_subscriptions")
    db.execute("DELETE FROM actions")
    db.execute("DELETE FROM users")
    db.commit()
    return jsonify({"ok": True, "message": "All users and actions deleted"})


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
