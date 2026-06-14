#!/usr/bin/env python3
"""
scrape.py — Mighty Account Dashboard scraper

Credentials are stored in your Mighty dashboard — no local credential file needed.
Works from any machine where MIGHTY_API_KEY is set.

Setup:
  1. Add credentials at https://mighty-selfserve-production.up.railway.app/credentials
  2. export MIGHTY_API_KEY="your-key-from-settings"
  3. python3 scrape.py

Requirements: pip3 install playwright cryptography pyotp
              python3 -m playwright install chromium
"""
from __future__ import annotations

import email as email_lib
import imaplib
import json
import os
import re
import sys
import time
import threading
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# ── Auto-install ──────────────────────────────────────────────────────────────
def _install(pkg: str) -> None:
    os.system(f'"{sys.executable}" -m pip install {pkg} --quiet')

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    _install("playwright")
    os.system(f'"{sys.executable}" -m playwright install chromium')
    from playwright.sync_api import sync_playwright

try:
    import pyotp
except ImportError:
    _install("pyotp")
    import pyotp

# ── Config ────────────────────────────────────────────────────────────────────
MIGHTY_API_KEY = os.environ.get("MIGHTY_API_KEY", "")
MIGHTY_URL     = os.environ.get("MIGHTY_URL",
                  "https://mighty-selfserve-production.up.railway.app")

# ── Residential proxy (optional) ─────────────────────────────────────────────
# Set PROXY_URL in Railway env vars to route scrapers through a residential IP.
# Format: http://username:password@gate.smartproxy.com:7000
# Needed for sites that block cloud IPs (Southwest, Delta, Chase, Amex, etc.)
_PROXY_URL = os.environ.get("PROXY_URL", "").strip()
def _proxy_cfg() -> dict | None:
    """Return Playwright proxy dict if PROXY_URL is set, else None."""
    if not _PROXY_URL:
        return None
    # Parse http://user:pass@host:port into Playwright's format
    import urllib.parse
    p = urllib.parse.urlparse(_PROXY_URL)
    cfg: dict = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
    if p.username:
        cfg["username"] = urllib.parse.unquote(p.username)
    if p.password:
        cfg["password"] = urllib.parse.unquote(p.password)
    return cfg
BASE_DIR       = Path(__file__).parent
# On Railway, use persistent volume path for sessions; locally use ./sessions/
SESSIONS_DIR   = Path(os.environ.get("SESSIONS_DIR",
                  str(Path("/app/data/sessions") if os.environ.get("RAILWAY_ENVIRONMENT")
                      else BASE_DIR / "sessions")))
DASHBOARD_FILE = BASE_DIR / "dashboard.html"

NAV_TIMEOUT   = 60_000
LOGIN_TIMEOUT = 120_000
MAX_WORKERS   = 8

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

STEALTH_JS = """
    Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
    Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
    window.chrome={runtime:{}};
"""

# ── Fetch credentials from Mighty ────────────────────────────────────────────
def fetch_credentials() -> tuple[dict, dict | None]:
    """Pull credentials and email config from the Mighty API.
    Returns (credentials_dict, email_config_or_None).
    """
    if not MIGHTY_API_KEY:
        sys.exit(
            "MIGHTY_API_KEY not set.\n"
            "1. Get your key from /settings in your Mighty dashboard.\n"
            "2. Run: export MIGHTY_API_KEY=your-key"
        )
    url = MIGHTY_URL.rstrip("/") + "/api/credentials"
    req = urllib.request.Request(
        url,
        headers={"X-Mighty-Key": MIGHTY_API_KEY},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        sys.exit(f"Could not reach Mighty API: {e}\nCheck MIGHTY_URL and your internet connection.")

    if not data.get("ok"):
        sys.exit(f"Mighty API error: {data.get('error', 'unknown')}")

    return data.get("credentials", {}), data.get("email")

# ── Cloud sync ────────────────────────────────────────────────────────────────
def push_to_cloud(key: str, result: dict, synced_at: str) -> bool:
    payload = json.dumps({
        "api_key":   MIGHTY_API_KEY,
        "source":    key,
        "data":      result,
        "synced_at": synced_at,
    }).encode()
    req = urllib.request.Request(
        MIGHTY_URL.rstrip("/") + "/api/data/sync",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()).get("ok", False)
    except Exception:
        return False

# ── DOM helpers ───────────────────────────────────────────────────────────────
def _fill(page, selectors: list, value: str) -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2_000):
                el.fill(value)
                return True
        except Exception:
            pass
    return False

def _click(page, selectors: list) -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2_000):
                el.click()
                return True
        except Exception:
            pass
    return False

# ── Text extraction ───────────────────────────────────────────────────────────
def _dollars(text: str) -> list:
    return re.findall(r'\$[\d,]+(?:\.\d{2})?', text)

def _points(text: str, keywords=('miles','points','honors','bonvoy','skymiles')) -> str | None:
    for kw in keywords:
        m = re.search(rf'([\d,]+)\s*{kw}', text, re.IGNORECASE)
        if m:
            try: return f"{int(m.group(1).replace(',','')):,}"
            except ValueError: pass
    return None

def _date(text: str) -> str | None:
    m = re.search(
        r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?',
        text, re.IGNORECASE)
    if m: return m.group()
    m = re.search(r'\b\d{1,2}/\d{1,2}/\d{2,4}\b', text)
    return m.group() if m else None

# ── Email code fetcher ────────────────────────────────────────────────────────
class EmailCodeFetcher:
    def __init__(self, address: str, app_password: str):
        self._imap = imaplib.IMAP4_SSL("imap.gmail.com")
        self._imap.login(address, app_password.replace(" ", ""))
        self._baseline: bytes = b"0"

    def _today(self) -> str:
        return datetime.now().strftime("%d-%b-%Y")

    def _search(self):
        try: self._imap.select('"[Gmail]/All Mail"')
        except: self._imap.select("INBOX")
        _, data = self._imap.search(None, "SINCE", self._today())
        return data[0].split()

    def mark(self) -> None:
        uids = self._search()
        self._baseline = uids[-1] if uids else b"0"

    def wait_for_code(self, timeout: int = 90) -> str | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            uids = self._search()
            for uid in reversed([u for u in uids if u > self._baseline]):
                _, msg_data = self._imap.fetch(uid, "(RFC822)")
                msg = email_lib.message_from_bytes(msg_data[0][1])
                body = _email_text(msg)
                code = _extract_code(body)
                if code:
                    self._baseline = uid
                    return code
            time.sleep(3)
        return None

    def close(self):
        try: self._imap.logout()
        except: pass

def _email_text(msg) -> str:
    parts = []
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                parts.append(part.get_payload(decode=True).decode(errors="ignore"))
            elif ct == "text/html" and not parts:
                parts.append(re.sub(r'<[^>]+>', ' ',
                    part.get_payload(decode=True).decode(errors="ignore")))
    else:
        parts.append(msg.get_payload(decode=True).decode(errors="ignore"))
    return " ".join(parts)

def _extract_code(text: str) -> str | None:
    m = re.findall(r'\b(\d{6})\b', text)
    if m: return m[0]
    m = re.findall(r'\b(\d{4})\b', text)
    return m[0] if m else None

# ── 2FA handling ──────────────────────────────────────────────────────────────
_PUSH_HINTS  = ['check your app','open your app','push notification','tap approve','amex app']
_2FA_HINTS   = ['verification code','one-time','we sent','check your email',
                'enter the code','security code','authentication code','two-factor']

def _inbox_mark(ctx: dict) -> None:
    if ctx.get("fetcher"): ctx["fetcher"].mark()

def _cloud_2fa_request(ctx: dict, source: str, page_text: str) -> str | None:
    """Create a pending 2FA challenge via Mighty API and poll for user response."""
    creds      = ctx.get("creds", {})
    acct_name  = creds.get("name", source)
    is_push    = any(h in page_text.lower() for h in _PUSH_HINTS)
    is_sms     = not is_push
    msg        = ""
    # Try to extract the message shown on the 2FA page
    for hint in ["sent to", "ending in", "code was sent", "verify your"]:
        import re as _re
        m = _re.search(rf'[^\n]{{0,80}}{hint}[^\n]{{0,80}}', page_text, _re.IGNORECASE)
        if m:
            msg = m.group().strip()[:120]
            break

    payload = json.dumps({
        "api_key":        MIGHTY_API_KEY,
        "source":         source,
        "account_name":   acct_name,
        "challenge_type": "push" if is_push else "sms",
        "message":        msg,
    }).encode()
    req = urllib.request.Request(
        MIGHTY_URL.rstrip("/") + "/api/2fa/request",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            challenge_id = data.get("challenge_id")
    except Exception as e:
        print(f"  2FA request failed: {e}", flush=True)
        return None

    if not challenge_id:
        return None

    # Poll every 5s for up to 10 minutes
    poll_url = MIGHTY_URL.rstrip("/") + f"/api/2fa/poll/{challenge_id}"
    poll_req = urllib.request.Request(
        poll_url,
        headers={"X-Mighty-Key": MIGHTY_API_KEY},
        method="GET",
    )
    for _ in range(120):
        time.sleep(5)
        try:
            with urllib.request.urlopen(poll_req, timeout=10) as resp:
                result = json.loads(resp.read())
                if result.get("status") == "resolved":
                    return result.get("code", "confirmed") or "confirmed"
                if result.get("status") == "expired":
                    return None
        except Exception:
            pass
    return None


def _handle_2fa(page, ctx: dict) -> None:
    page.wait_for_timeout(3_000)
    try:
        url  = page.url.lower()
        text = page.inner_text("body").lower()
    except:
        return

    on_2fa = (any(h in url for h in ['verify','challenge','mfa','2fa','otp','confirm']) or
              any(h in text for h in _2FA_HINTS))
    if not on_2fa:
        return

    print("2FA → ", end="", flush=True)
    fetcher = ctx.get("fetcher")
    creds   = ctx.get("creds", {})
    totp_secret = creds.get("totp_secret")

    if any(h in text for h in _PUSH_HINTS):
        print("push — approve on your phone.", flush=True)
        return

    code: str | None = None
    if fetcher and any(h in text for h in ['email','inbox','sent to']):
        print("fetching email code...", end=" ", flush=True)
        code = fetcher.wait_for_code()
    if not code and totp_secret:
        print("TOTP...", end=" ", flush=True)
        code = pyotp.TOTP(totp_secret).now()
    account_key = ctx.get("account_key", "account")
    if not code:
        if os.environ.get("RAILWAY_ENVIRONMENT") and MIGHTY_API_KEY:
            print("SMS/push 2FA — requesting approval from Mighty dashboard...", flush=True)
            code = _cloud_2fa_request(ctx, account_key, text)
            if not code:
                print("2FA timed out.", flush=True)
                return
        else:
            print("SMS/unknown — enter code: ", end="", flush=True)
            try: code = input().strip()
            except: return

    if code:
        print(f"filling {code}...", end=" ", flush=True)
        _fill(page, ['input[type="tel"]','input[type="number"]',
                     'input[name*="code"]','input[id*="code"]','input[type="text"]'], code)
        page.wait_for_timeout(500)
        _click(page, ['button[type="submit"]','input[type="submit"]'])
        print("done.", flush=True)

# ── Browser context factory ───────────────────────────────────────────────────
def _new_context(pw, key: str):
    SESSIONS_DIR.mkdir(exist_ok=True)
    kwargs = dict(
        headless=True,
        viewport={"width": 1280, "height": 800},
        user_agent=USER_AGENT,
        args=["--disable-blink-features=AutomationControlled",
              "--no-sandbox", "--disable-dev-shm-usage"],
        ignore_default_args=["--enable-automation"],
    )
    chrome = _chrome_path()
    if chrome:
        kwargs["executable_path"] = chrome
    # Route through residential proxy if configured
    proxy = _proxy_cfg()
    if proxy:
        kwargs["proxy"] = proxy
        print(f"[Proxy] Using residential proxy: {proxy['server']}", flush=True)
    # else: Playwright uses its bundled Chromium (cloud/Railway environment)
    return pw.chromium.launch_persistent_context(str(SESSIONS_DIR / key), **kwargs)


def _launch_browser(pw):
    """Launch a single shared Chromium instance for all contexts."""
    kwargs = dict(
        headless=True,
        args=["--disable-blink-features=AutomationControlled",
              "--no-sandbox", "--disable-dev-shm-usage"],
        ignore_default_args=["--enable-automation"],
    )
    chrome = _chrome_path()
    if chrome:
        kwargs["executable_path"] = chrome
    proxy = _proxy_cfg()
    if proxy:
        kwargs["proxy"] = proxy
    return pw.chromium.launch(**kwargs)


def _new_browser_context(browser, key: str):
    """Create an isolated browser context for one account, reusing saved cookies."""
    SESSIONS_DIR.mkdir(exist_ok=True)
    state_file = SESSIONS_DIR / f"{key}_state.json"
    kwargs = dict(
        viewport={"width": 1280, "height": 800},
        user_agent=USER_AGENT,
    )
    if state_file.exists():
        kwargs["storage_state"] = str(state_file)
    proxy = _proxy_cfg()
    if proxy:
        kwargs["proxy"] = proxy
    ctx = browser.new_context(**kwargs)
    ctx.add_init_script(STEALTH_JS)
    return ctx, state_file


def _chrome_path() -> str | None:
    """Return path to Chrome, or None to use Playwright's bundled Chromium (cloud)."""
    for p in [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]:
        if Path(p).exists(): return p
    return None  # Railway/cloud: Playwright uses its bundled Chromium

# ── Base result ───────────────────────────────────────────────────────────────
def _base(name, icon, color, url):
    return {"name":name,"icon":icon,"color":color,"url":url,"status":"skipped","items":[]}

# ── Automatic post-login account exploration ──────────────────────────────────
_ACCOUNT_KEYWORDS = [
    'account', 'loyalty', 'rewards', 'profile', 'points', 'miles',
    'credits', 'dashboard', 'membership', 'frequent', 'my trip',
    'my booking', 'benefit', 'tier', 'status', 'balance', 'history',
]
_SKIP_PATTERNS = [
    'logout', 'signout', 'sign-out', 'register', 'signup', 'sign-up',
    'help', 'support', 'faq', 'careers', 'press', 'javascript:',
    'mailto:', 'tel:', '#', 'privacy', 'terms', 'cookie',
]

def _explore_account_pages(page, max_extra: int = 2) -> str:
    """After login, find and visit the most relevant account/loyalty pages.
    Returns combined page text from all visited pages — richer than any single page."""
    texts = []
    visited: set = {page.url.rstrip('/')}

    # Capture the current (post-login) page
    try:
        texts.append(page.inner_text("body")[:4000])
    except Exception:
        pass

    # Find all links on this page
    try:
        base_domain = '/'.join(page.url.split('/')[:3])
        links = page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                href: a.href || '',
                text: (a.textContent || a.getAttribute('aria-label') || '').trim().toLowerCase()
            }))
        """)
    except Exception:
        return "\n".join(texts)

    # Score links by relevance to personal account data
    scored = []
    for lnk in links:
        href = lnk.get("href", "")
        text = lnk.get("text", "")
        if not href or not href.startswith(base_domain):
            continue
        if any(p in href.lower() or p in text for p in _SKIP_PATTERNS):
            continue
        score = sum(kw in text or kw in href.lower() for kw in _ACCOUNT_KEYWORDS)
        if score:
            scored.append((score, href.rstrip('/')))

    # Deduplicate and visit top N
    seen: set = set()
    for _, url in sorted(scored, reverse=True):
        if len(seen) >= max_extra:
            break
        if url in visited or url in seen:
            continue
        seen.add(url)
        try:
            page.goto(url, timeout=NAV_TIMEOUT)
            page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
            page.wait_for_timeout(2_000)
            page_text = page.inner_text("body")[:4000]
            texts.append(f"\n\n=== {url} ===\n{page_text}")
            visited.add(url)
        except Exception:
            pass

    return "\n".join(texts)[:12_000]


# ── Generic scraper factory ───────────────────────────────────────────────────
# For sites where login follows standard patterns — AI discovery handles fields.
def _make_scraper(cfg: dict):
    name      = cfg["name"];  icon  = cfg["icon"];  color = cfg["color"]
    login_url = cfg["login_url"]
    u_sels    = cfg.get("u_sels",   ['input[type="email"]', 'input[type="text"]'])
    p_sels    = cfg.get("p_sels",   ['input[type="password"]'])
    s_sels    = cfg.get("s_sels",   ['button[type="submit"]', 'input[type="submit"]'])
    ok_url         = cfg.get("ok_url")
    post_url       = cfg.get("post_url")         # navigate here after login to get the right page
    wait_for_not   = cfg.get("wait_for_not")     # wait until this text disappears (dynamic content)
    wait_for_login = cfg.get("wait_for_login")   # wait until this text APPEARS (confirms login done)
    wait_ms        = cfg.get("wait_ms",  3_000)
    multistep      = cfg.get("multistep", False)

    def scraper(page, c, ctx):
        r = _base(name, icon, color, login_url)
        try:
            page.goto(login_url, timeout=NAV_TIMEOUT)
            page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
            page.wait_for_timeout(2_000)
            _fill(page, u_sels, c["username"])
            if multistep:
                _click(page, ['button[type="submit"]', 'button:has-text("Next")',
                              'button:has-text("Continue")'])
                page.wait_for_timeout(2_000)
            _fill(page, p_sels, c["password"])
            _inbox_mark(ctx)
            _click(page, s_sels)
            _handle_2fa(page, ctx)
            if ok_url:
                page.wait_for_url(ok_url, timeout=LOGIN_TIMEOUT)
            # wait_for_login: wait until this text APPEARS — confirms login before post_url nav
            if wait_for_login:
                try:
                    page.wait_for_function(
                        f"() => document.body.innerText.includes({json.dumps(wait_for_login)})",
                        timeout=LOGIN_TIMEOUT
                    )
                except Exception:
                    pass
            # wait_for_not: wait until this text DISAPPEARS — also confirms login before post_url nav
            if wait_for_not:
                try:
                    page.wait_for_function(
                        f"() => !document.body.innerText.includes({json.dumps(wait_for_not)})",
                        timeout=LOGIN_TIMEOUT
                    )
                except Exception:
                    pass
            if post_url:
                # Navigate to the richest account page once login is confirmed
                page.goto(post_url, timeout=NAV_TIMEOUT)
                page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
            page.wait_for_timeout(wait_ms)
            # Auto-explore account pages — finds loyalty/account pages regardless of URL structure
            raw_text = _explore_account_pages(page)
            r.update({"status": "ok", "items": [], "raw_text": raw_text})
        except Exception as e:
            r.update({"status": "error", "error": str(e).split('\n')[0][:120]})
        return r
    return scraper

# ── Site configs for generic scrapers ─────────────────────────────────────────
_SITE_CFGS = {
    # Banking & Finance
    "wells_fargo":   {"name":"Wells Fargo",      "icon":"🏦","color":"#fef3c7",
        "login_url":"https://connect.secure.wellsfargo.com/auth/login/present",
        "u_sels":['#userid','input[name="userid"]','input[type="text"]'],
        "p_sels":['#password','input[name="password"]','input[type="password"]'],
        "s_sels":['button[type="submit"]','#btnSignon'],
        "ok_url":"**wellsfargo.com/**"},
    "bofa":          {"name":"Bank of America",  "icon":"🏦","color":"#fee2e2",
        "login_url":"https://www.bankofamerica.com/",
        "u_sels":['#onlineId1','input[name="onlineId"]','input[type="text"]'],
        "p_sels":['#tlpvt-passcode1','input[name="passcode"]','input[type="password"]'],
        "s_sels":['#signIn','button[type="submit"]'],
        "ok_url":"**bankofamerica.com/myaccounts**"},
    "capital_one":   {"name":"Capital One",      "icon":"💳","color":"#fce7f3",
        "login_url":"https://verified.capitalone.com/auth/signin",
        "u_sels":['input[type="email"]','input[name="username"]'],
        "p_sels":['input[type="password"]'],
        "s_sels":['button[type="submit"]'],
        "ok_url":"**capitalone.com/**","wait_ms":5_000,"multistep":True},
    "discover":      {"name":"Discover",         "icon":"💳","color":"#fff7ed",
        "login_url":"https://portal.discover.com/",
        "u_sels":['#userid-content','input[name="userId"]','input[type="text"]'],
        "p_sels":['#password-content','input[type="password"]'],
        "s_sels":['button[type="submit"]','#log-in-button'],
        "ok_url":"**discover.com/account**"},
    "citi":          {"name":"Citi",             "icon":"💳","color":"#ecfdf5",
        "login_url":"https://online.citi.com/US/login.do",
        "u_sels":['#username','input[type="text"]'],
        "p_sels":['#password','input[type="password"]'],
        "s_sels":['button[type="submit"]','#signInBtn'],
        "ok_url":"**online.citi.com/**"},
    "paypal":        {"name":"PayPal",           "icon":"💰","color":"#eff6ff",
        "login_url":"https://www.paypal.com/signin",
        "u_sels":['#email','input[type="email"]'],
        "p_sels":['#password','input[type="password"]'],
        "s_sels":['#btnLogin','button[type="submit"]'],
        "ok_url":"**paypal.com/myaccount**","wait_ms":5_000,"multistep":True},
    "fidelity":      {"name":"Fidelity",         "icon":"📈","color":"#ecfdf5",
        "login_url":"https://digital.fidelity.com/prgw/digital/login/full-page",
        "u_sels":['#userId-input','input[type="text"]'],
        "p_sels":['#password','input[type="password"]'],
        "s_sels":['button[type="submit"]','#fs-login-button'],
        "ok_url":"**fidelity.com/**"},
    "schwab":        {"name":"Charles Schwab",   "icon":"📈","color":"#eff6ff",
        "login_url":"https://client.schwab.com/Login/SignOn/CustomerCenterLogin.aspx",
        "u_sels":['#txtLoginID','input[name="LoginId"]','input[type="text"]'],
        "p_sels":['#txtPassword','input[name="Password"]','input[type="password"]'],
        "s_sels":['#btnLogin','button[type="submit"]'],
        "ok_url":"**schwab.com/**"},
    # Airlines
    "united":        {"name":"United Airlines",  "icon":"✈️","color":"#eff6ff",
        "login_url":"https://www.united.com/en/us/myaccount/profile",
        "u_sels":['input[name="userName"]','input[type="text"]'],
        "p_sels":['input[type="password"]'],
        "s_sels":['button[type="submit"]'],
        "ok_url":"**united.com/en/us/myaccount**"},
    "southwest":     {"name":"Southwest",        "icon":"✈️","color":"#fef3c7",
        "login_url":"https://www.southwest.com/account/",
        "u_sels":['input[name="userNameOrAccountNumber"]','input[id*="username"]','input[type="text"]'],
        "p_sels":['input[name="password"]','input[type="password"]'],
        "s_sels":['button[type="submit"]','button[id*="login"]','button[id*="sign"]'],
        # Wait for the login form's submit button text to disappear — reliable sign login completed
        "wait_for_not":"LOG IN",
        "post_url":"https://www.southwest.com/loyalty/myaccount/",
        "wait_ms":6_000},
    "american_air":  {"name":"American Airlines","icon":"✈️","color":"#fce7f3",
        "login_url":"https://www.aa.com/homePage.do",
        "u_sels":['input[name="accountNumber"]','input[type="text"]'],
        "p_sels":['input[type="password"]'],
        "s_sels":['button[type="submit"]'],
        "ok_url":"**aa.com/aadvantage**"},
    "alaska_air":    {"name":"Alaska Airlines",  "icon":"✈️","color":"#ecfdf5",
        "login_url":"https://www.alaskaair.com/account",
        "u_sels":['input[type="email"]','input[type="text"]'],
        "p_sels":['input[type="password"]'],
        "s_sels":['button[type="submit"]'],
        "ok_url":"**alaskaair.com/account**"},
    # Hotels
    "hyatt":         {"name":"Hyatt",            "icon":"🏨","color":"#f5f3ff",
        "login_url":"https://www.hyatt.com/en-US/my-account",
        "u_sels":['input[type="email"]','input[type="text"]'],
        "p_sels":['input[type="password"]'],
        "s_sels":['button[type="submit"]'],
        "ok_url":"**hyatt.com/en-US/my-account**"},
    "ihg":           {"name":"IHG / Holiday Inn","icon":"🏨","color":"#fff7ed",
        "login_url":"https://www.ihg.com/rewardsclub/content/us/en/member-home",
        "u_sels":['input[type="email"]','input[type="text"]'],
        "p_sels":['input[type="password"]'],
        "s_sels":['button[type="submit"]'],
        "ok_url":"**ihg.com/**"},
    "wyndham":       {"name":"Wyndham Rewards",  "icon":"🏨","color":"#fce7f3",
        "login_url":"https://www.wyndhamhotels.com/registry",
        "u_sels":['input[type="email"]','input[type="text"]'],
        "p_sels":['input[type="password"]'],
        "s_sels":['button[type="submit"]'],
        "ok_url":"**wyndhamhotels.com/**"},
    # Telecom
    "att":           {"name":"AT&T",             "icon":"📱","color":"#eff6ff",
        "login_url":"https://www.att.com/my/#/",
        "u_sels":['#userID','input[name="userId"]','input[type="text"]'],
        "p_sels":['#password','input[type="password"]'],
        "s_sels":['button[type="submit"]','#submitBtn'],
        "ok_url":"**att.com/**"},
    "verizon":       {"name":"Verizon",          "icon":"📱","color":"#fce7f3",
        "login_url":"https://login.verizonwireless.com/vzauth/UI/Login",
        "u_sels":['#IDToken1','input[name="IDToken1"]','input[type="text"]'],
        "p_sels":['#IDToken2','input[name="IDToken2"]','input[type="password"]'],
        "s_sels":['button[type="submit"]','#IDButton2'],
        "ok_url":"**verizon.com/**"},
    "tmobile":       {"name":"T-Mobile",         "icon":"📱","color":"#fce7f3",
        "login_url":"https://account.t-mobile.com/",
        "u_sels":['input[type="email"]','input[type="text"]'],
        "p_sels":['input[type="password"]'],
        "s_sels":['button[type="submit"]'],
        "ok_url":"**t-mobile.com/account**"},
    # Streaming
    "netflix":       {"name":"Netflix",          "icon":"🎬","color":"#fee2e2",
        "login_url":"https://www.netflix.com/login",
        "u_sels":['input[type="email"]','input[name="userLoginId"]'],
        "p_sels":['input[type="password"]','input[name="password"]'],
        "s_sels":['button[type="submit"]','.login-button'],
        "ok_url":"**netflix.com/browse**"},
    "hulu":          {"name":"Hulu",             "icon":"📺","color":"#ecfdf5",
        "login_url":"https://auth.hulu.com/web/login",
        "u_sels":['input[type="email"]','input[type="text"]'],
        "p_sels":['input[type="password"]'],
        "s_sels":['button[type="submit"]'],
        "ok_url":"**hulu.com/**"},
    "spotify":       {"name":"Spotify",          "icon":"🎵","color":"#ecfdf5",
        "login_url":"https://accounts.spotify.com/en/login",
        "u_sels":['#login-username','input[data-testid="login-username"]','input[type="text"]'],
        "p_sels":['#login-password','input[data-testid="login-password"]','input[type="password"]'],
        "s_sels":['#login-button','button[data-testid="login-button"]','button[type="submit"]'],
        "ok_url":"**open.spotify.com/**","wait_ms":5_000},
    "max":           {"name":"Max",              "icon":"🎬","color":"#f5f3ff",
        "login_url":"https://www.max.com/sign-in",
        "u_sels":['input[type="email"]'],
        "p_sels":['input[type="password"]'],
        "s_sels":['button[type="submit"]'],
        "ok_url":"**max.com/**"},
    "peacock":       {"name":"Peacock",          "icon":"🦚","color":"#fef3c7",
        "login_url":"https://www.peacocktv.com/signin",
        "u_sels":['input[type="email"]'],
        "p_sels":['input[type="password"]'],
        "s_sels":['button[type="submit"]'],
        "ok_url":"**peacocktv.com/**"},
    "paramount_plus":{"name":"Paramount+",      "icon":"🎬","color":"#eff6ff",
        "login_url":"https://www.paramountplus.com/account/signin/",
        "u_sels":['input[type="email"]'],
        "p_sels":['input[type="password"]'],
        "s_sels":['button[type="submit"]'],
        "ok_url":"**paramountplus.com/**"},
    # Shopping
    "target":        {"name":"Target",           "icon":"🎯","color":"#fee2e2",
        "login_url":"https://www.target.com/account",
        "u_sels":['input[type="email"]'],
        "p_sels":['input[type="password"]'],
        "s_sels":['button[type="submit"]'],
        "ok_url":"**target.com/account/**"},
    "walmart":       {"name":"Walmart",          "icon":"🛒","color":"#eff6ff",
        "login_url":"https://www.walmart.com/account/login",
        "u_sels":['input[type="email"]'],
        "p_sels":['input[type="password"]'],
        "s_sels":['button[type="submit"]'],
        "ok_url":"**walmart.com/**"},
    "costco":        {"name":"Costco",           "icon":"🛒","color":"#eff6ff",
        "login_url":"https://www.costco.com/logon-instacart.html",
        "u_sels":['input[type="email"]','input[name="userId"]','input[type="text"]'],
        "p_sels":['input[type="password"]'],
        "s_sels":['button[type="submit"]'],
        "ok_url":"**costco.com/**"},
    # Healthcare
    "kaiser":        {"name":"Kaiser Permanente","icon":"🏥","color":"#ecfdf5",
        "login_url":"https://healthy.kaiserpermanente.org/",
        "u_sels":['input[type="email"]','input[type="text"]'],
        "p_sels":['input[type="password"]'],
        "s_sels":['button[type="submit"]'],
        "ok_url":"**kaiserpermanente.org/**"},
    "cvs":           {"name":"CVS Pharmacy",     "icon":"💊","color":"#fee2e2",
        "login_url":"https://www.cvs.com/account/login/",
        "u_sels":['#username','input[name="username"]','input[type="text"]'],
        "p_sels":['#password','input[type="password"]'],
        "s_sels":['button[type="submit"]','#signIn'],
        "ok_url":"**cvs.com/**"},
    "walgreens":     {"name":"Walgreens",        "icon":"💊","color":"#eff6ff",
        "login_url":"https://www.walgreens.com/login.jsp",
        "u_sels":['#user_name','input[name="user_name"]','input[type="text"]'],
        "p_sels":['#user_password','input[type="password"]'],
        "s_sels":['button[type="submit"]'],
        "ok_url":"**walgreens.com/**"},
    # Insurance
    "state_farm":    {"name":"State Farm",       "icon":"🏠","color":"#fef3c7",
        "login_url":"https://www.statefarm.com/customer-care/sign-in-to-my-account",
        "u_sels":['#sfg-user-name-input','input[name="username"]','input[type="text"]'],
        "p_sels":['#sfg-password-input','input[name="password"]','input[type="password"]'],
        "s_sels":['button[type="submit"]','#ciam-sign-in-btn'],
        "ok_url":"**statefarm.com/**","wait_ms":4_000},
    # Telecom — AT&T Wireless (wireless billing portal)
    "att_wireless":  {"name":"AT&T Wireless",    "icon":"📱","color":"#dbeafe",
        "login_url":"https://www.att.com/my/#/",
        "u_sels":['#userID','input[name="userId"]','input[type="text"]'],
        "p_sels":['#password','input[type="password"]'],
        "s_sels":['button[type="submit"]','#submitBtn'],
        "ok_url":"**att.com/**",
        "post_url":"https://myatt.att.com/exp/myconsumerdashboard/",
        "wait_ms":5_000},
    # Coffee / Loyalty
    "starbucks":     {"name":"Starbucks",         "icon":"☕","color":"#ecfdf5",
        "login_url":"https://www.starbucks.com/account/signin",
        "u_sels":['input[type="email"]','input[name="email"]','#email'],
        "p_sels":['input[type="password"]','input[name="password"]'],
        "s_sels":['button[type="submit"]'],
        "ok_url":"**starbucks.com/account**",
        "post_url":"https://www.starbucks.com/rewards/",
        "wait_ms":4_000},
}

# Build scrapers from configs — same pattern, AI discovery handles field extraction
for _k, _cfg in _SITE_CFGS.items():
    globals()[f"scrape_{_k}"] = _make_scraper(_cfg)

# ── Southwest mobile API scraper ──────────────────────────────────────────────
# Southwest's mobile app uses a documented JSON API that's far more reliable than
# browser scraping — no bot detection, structured data, no headless browser needed.
# Overrides the generic Playwright scraper for 'southwest'.

_SW_API_KEY  = "l7xx12ebcbc825eb480faa276e7f192d98d1"   # Southwest mobile app client ID
_SW_BASE_URL = "https://mobile.southwest.com"

def _sw_api_request(session, method: str, path: str, **kwargs):
    """Make a Southwest mobile API request, returning parsed JSON or None."""
    try:
        import requests as _requests
    except ImportError:
        os.system(f'"{sys.executable}" -m pip install requests --quiet')
        import requests as _requests

    url = _SW_BASE_URL + path
    headers = {
        "X-API-Key": _SW_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Southwest/10.0 iOS/17.0",
    }
    if hasattr(session, "_token") and session._token:
        headers["token"] = session._token

    proxies = {"http": _PROXY_URL, "https": _PROXY_URL} if _PROXY_URL else None

    resp = _requests.request(
        method.upper(), url,
        json=kwargs.get("json"),
        headers=headers,
        proxies=proxies,
        timeout=15,
    )
    if not resp.ok:
        raise Exception(f"SW API {method} {path} → HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def scrape_southwest(page, c, ctx):
    r = _base("Southwest", "✈️", "#fef3c7", "https://www.southwest.com/loyalty/myaccount/")
    try:
        # ── Step 1: Login via mobile API ──────────────────────────────────
        class _Session:
            _token = None

        session = _Session()
        print("[SW-API] Logging in...", flush=True)
        login = _sw_api_request(session, "POST", "/api/customer/v1/accounts/login", json={
            "accountNumberOrUserName": c["username"],
            "password": c["password"],
        })

        session._token    = login.get("accessToken", "")
        account_number    = login.get("accessTokenDetails", {}).get("accountNumber", "")
        if not account_number:
            raise Exception("Login succeeded but no account number returned")

        print(f"[SW-API] Logged in, account #{account_number}", flush=True)

        # ── Step 2: Fetch account data ────────────────────────────────────
        texts = [f"Southwest Rapid Rewards Account: {account_number}\n"]

        # Account summary (points, tier, companion pass, etc.)
        try:
            acct = _sw_api_request(session, "GET",
                f"/api/customer/v1/accounts/account-number/{account_number}")
            texts.append("=== Account Summary ===\n" + json.dumps(acct, indent=2))
        except Exception as e:
            print(f"[SW-API] account summary: {e}", flush=True)

        # Upcoming trips
        try:
            trips = _sw_api_request(session, "GET",
                f"/api/customer/v1/accounts/account-number/{account_number}/upcoming-trips")
            texts.append("=== Upcoming Trips ===\n" + json.dumps(trips, indent=2))
        except Exception as e:
            print(f"[SW-API] upcoming trips: {e}", flush=True)

        # Points activity / history
        try:
            activity = _sw_api_request(session, "GET",
                f"/api/customer/v1/accounts/account-number/{account_number}/activity")
            texts.append("=== Points Activity ===\n" + json.dumps(activity, indent=2))
        except Exception as e:
            print(f"[SW-API] activity: {e}", flush=True)

        raw_text = "\n\n".join(texts)[:12_000]
        if len(raw_text) < 100:
            raise Exception("API returned unexpectedly empty data")

        print(f"[SW-API] ✓ Got {len(raw_text)} chars of account data", flush=True)
        r.update({"status": "ok", "items": [], "raw_text": raw_text})

    except Exception as e:
        # Fall back to Playwright browser scraping
        print(f"[SW-API] Failed ({e}) — falling back to browser scraper", flush=True)
        fallback = _make_scraper(_SITE_CFGS["southwest"])
        r = fallback(page, c, ctx)

    return r


# ── Account scrapers ──────────────────────────────────────────────────────────
def scrape_amex(page, c, ctx):
    r = _base("American Express","💳","#e8f0fe",
               "https://www.americanexpress.com/en-us/account/login")
    try:
        page.goto("https://www.americanexpress.com/en-us/account/login", timeout=NAV_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        _fill(page, ["#eliloUserID",'input[name="username"]','input[type="email"]'], c["username"])
        _fill(page, ["#eliloPassword",'input[name="password"]','input[type="password"]'], c["password"])
        _inbox_mark(ctx)
        _click(page, ["#loginSubmit",'button[type="submit"]'])
        _handle_2fa(page, ctx)
        page.wait_for_url("**americanexpress.com/en-us/account/**", timeout=LOGIN_TIMEOUT)
        page.goto("https://www.americanexpress.com/en-us/account/pay/summary", timeout=NAV_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        raw_text = _explore_account_pages(page)
        r.update({"status":"ok","items":[],"raw_text":raw_text})
    except Exception as e:
        r.update({"status":"error","error":str(e).split('\n')[0][:120]})
    return r


def scrape_chase(page, c, ctx):
    r = _base("Chase","🏦","#e3f2fd","https://secure.chase.com")
    try:
        page.goto("https://secure.chase.com/", timeout=NAV_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        page.wait_for_timeout(2_000)
        _fill(page, ["#userId-input-field",'input[name="userId"]','input[type="text"]'], c["username"])
        _click(page, ["#signin-button",'button[id*="next"]'])
        page.wait_for_timeout(2_000)
        _fill(page, ["#password-input-field",'input[name="password"]','input[type="password"]'], c["password"])
        _inbox_mark(ctx)
        _click(page, ["#signin-button",'button[type="submit"]'])
        _handle_2fa(page, ctx)
        page.wait_for_url("**chase.com/**", timeout=LOGIN_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        raw_text = _explore_account_pages(page)
        r.update({"status":"ok","items":[],"raw_text":raw_text})
    except Exception as e:
        r.update({"status":"error","error":str(e).split('\n')[0][:120]})
    return r


def scrape_sfcu(page, c, ctx):
    r = _base("Stanford FCU","🏦","#dbeafe","https://www.sfcu.org/accounts/online-banking")
    try:
        page.goto("https://www.sfcu.org/", timeout=NAV_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        _fill(page, ['input[name="username"]','input[id*="user"]','input[type="text"]'], c["username"])
        _fill(page, ['input[name="password"]','input[type="password"]'], c["password"])
        _inbox_mark(ctx)
        _click(page, ['button[type="submit"]','input[type="submit"]'])
        _handle_2fa(page, ctx)
        page.wait_for_load_state("domcontentloaded", timeout=LOGIN_TIMEOUT)
        raw_text = _explore_account_pages(page)
        r.update({"status":"ok","items":[],"raw_text":raw_text})
    except Exception as e:
        r.update({"status":"error","error":str(e).split('\n')[0][:120]})
    return r


def scrape_amazon(page, c, ctx):
    r = _base("Amazon","📦","#fff8e1","https://www.amazon.com/gp/css/order-history")
    try:
        page.goto("https://www.amazon.com/ap/signin?openid.return_to=https://www.amazon.com/gp/css/order-history",
                  timeout=NAV_TIMEOUT)
        _fill(page, ["#ap_email",'input[name="email"]'], c["username"])
        _click(page, ["#continue",'button[type="submit"]'])
        page.wait_for_timeout(1_500)
        _fill(page, ["#ap_password",'input[type="password"]'], c["password"])
        _inbox_mark(ctx)
        _click(page, ["#signInSubmit",'button[type="submit"]'])
        _handle_2fa(page, ctx)
        page.wait_for_url("**order-history**", timeout=LOGIN_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        t = page.inner_text("body")
        orders = re.findall(r"Order #([\d\-]+)", t)[:3]
        r.update({"status":"ok","items":(
            [{"label":f"Order #{i+1}","value":o} for i,o in enumerate(orders)]
            or [{"label":"Orders","value":"See site"}]
        )})
    except Exception as e:
        r.update({"status":"error","error":str(e).split('\n')[0][:120]})
    return r


def scrape_delta(page, c, ctx):
    r = _base("Delta","✈️","#e3f2fd","https://www.delta.com/us/en/my-account/overview")
    try:
        # Go directly to account page — saved session will load it without login
        page.goto("https://www.delta.com/us/en/my-account/overview", timeout=NAV_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        page.wait_for_timeout(3_000)

        # If redirected to login, do the login flow
        if any(x in page.url.lower() for x in ["sign-in", "login", "signin"]):
            page.goto("https://www.delta.com", timeout=NAV_TIMEOUT)
            page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
            page.wait_for_timeout(2_000)
            _fill(page, ["#input_loginId",'input[name="userId"]','input[type="text"]'], c["username"])
            _fill(page, ["#input_password",'input[name="password"]','input[type="password"]'], c["password"])
            _inbox_mark(ctx)
            _click(page, ['button[type="submit"]',"#login-btn"])
            _handle_2fa(page, ctx)
            page.wait_for_url("**delta.com/us/en/my-**", timeout=30_000)
            page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
            page.wait_for_timeout(4_000)

        # Use real Chrome cookies (Akamai trusts these) + direct API call.
        # Playwright cookies get detected; real Chrome cookies don't.
        import requests as _req
        try:
            import browser_cookie3
            chrome_cookies = browser_cookie3.chrome(domain_name="delta.com")
        except Exception:
            chrome_cookies = None

        s = _req.Session()
        s.headers.update({
            "User-Agent":   USER_AGENT,
            "Content-Type": "application/json",
            "Referer":      "https://www.delta.com/",
            "Origin":       "https://www.delta.com",
            "Accept":       "application/json, text/plain, */*",
        })
        if chrome_cookies:
            s.cookies = chrome_cookies

        resp = s.post(
            "https://www.delta.com/login/login/getDashBrdData",
            json={},
            timeout=30,
        )

        pts = None
        tier = None
        if resp.ok:
            try:
                data = resp.json()
                bal = data.get("smBalance", "")
                if bal:
                    try: pts = f"{int(str(bal).replace(',','')):,}"
                    except: pts = str(bal)
                tier = data.get("medallionMemberDesc")
            except Exception:
                pass

        items = [{"label": "SkyMiles", "value": pts or "–"}]
        if tier:
            items.append({"label": "Status", "value": tier})
        raw_text = _explore_account_pages(page)
        r.update({"status":"ok","items":[],"raw_text":raw_text})
    except Exception as e:
        r.update({"status":"error","error":str(e).split('\n')[0][:120]})
    return r


def scrape_hertz(page, c, ctx):
    r = _base("Hertz","🚗","#fff3e0","https://www.hertz.com/rentacar/myaccount/account-summary")
    try:
        page.goto("https://www.hertz.com/rentacar/myaccount/account-summary", timeout=NAV_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        _fill(page, ["#username",'input[name="username"]','input[type="email"]'], c["username"])
        _fill(page, ["#password",'input[name="password"]','input[type="password"]'], c["password"])
        _inbox_mark(ctx)
        _click(page, ['button[type="submit"]','input[type="submit"]'])
        _handle_2fa(page, ctx)
        page.wait_for_load_state("domcontentloaded", timeout=LOGIN_TIMEOUT)
        raw_text = _explore_account_pages(page)
        r.update({"status":"ok","items":[],"raw_text":raw_text})
    except Exception as e:
        r.update({"status":"error","error":str(e).split('\n')[0][:120]})
    return r


def scrape_marriott(page, c, ctx):
    r = _base("Marriott Bonvoy","🏨","#fce8e6","https://www.marriott.com/loyalty/myAccount/default.mi")
    try:
        page.goto("https://www.marriott.com/loyalty/myAccount/default.mi", timeout=NAV_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        _fill(page, ["#username",'input[name="username"]','input[id*="user"]'], c["username"])
        _fill(page, ["#password",'input[name="password"]','input[type="password"]'], c["password"])
        _inbox_mark(ctx)
        _click(page, ['button[type="submit"]',"#login-form-submit"])
        _handle_2fa(page, ctx)
        page.wait_for_url("**myAccount**", timeout=LOGIN_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        raw_text = _explore_account_pages(page)
        r.update({"status":"ok","items":[],"raw_text":raw_text})
    except Exception as e:
        r.update({"status":"error","error":str(e).split('\n')[0][:120]})
    return r


def scrape_hilton(page, c, ctx):
    r = _base("Hilton Honors","🏨","#e8f5e9","https://www.hilton.com/en/hilton-honors/profile/")
    try:
        page.goto("https://www.hilton.com/en/hilton-honors/profile/", timeout=NAV_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        _fill(page, ["#username",'input[name="username"]','input[type="email"]'], c["username"])
        _fill(page, ["#password",'input[name="password"]','input[type="password"]'], c["password"])
        _inbox_mark(ctx)
        _click(page, ['button[type="submit"]'])
        _handle_2fa(page, ctx)
        page.wait_for_url("**hilton.com**profile**", timeout=LOGIN_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        raw_text = _explore_account_pages(page)
        r.update({"status":"ok","items":[],"raw_text":raw_text})
    except Exception as e:
        r.update({"status":"error","error":str(e).split('\n')[0][:120]})
    return r


def scrape_disney_plus(page, c, ctx):
    r = _base("Disney+","🎬","#e8f0fe","https://www.disneyplus.com/account")
    try:
        page.goto("https://www.disneyplus.com/login", timeout=NAV_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        page.wait_for_timeout(2_000)
        _fill(page, ['input[type="email"]','input[name="email"]'], c["username"])
        _click(page, ['button[type="submit"]'])
        page.wait_for_timeout(2_000)
        if _fill(page, ['input[type="password"]','input[name="password"]'], c["password"]):
            _inbox_mark(ctx)
            _click(page, ['button[type="submit"]'])
        else:
            _inbox_mark(ctx)
        _handle_2fa(page, ctx)
        try: page.wait_for_url("**disneyplus.com/**", timeout=LOGIN_TIMEOUT)
        except: pass
        for _ in range(20):
            if "/login" not in page.url and "/signin" not in page.url: break
            page.wait_for_timeout(3_000)
        page.goto("https://www.disneyplus.com/account", timeout=NAV_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        raw_text = _explore_account_pages(page)
        r.update({"status":"ok","items":[],"raw_text":raw_text})
    except Exception as e:
        r.update({"status":"error","error":str(e).split('\n')[0][:120]})
    return r


def scrape_ticketmaster(page, c, ctx):
    r = _base("Ticketmaster","🎟️","#fce8e6","https://www.ticketmaster.com/member/orders")
    try:
        page.goto("https://www.ticketmaster.com/login", timeout=NAV_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        _fill(page, ['input[type="email"]','input[name="email"]',"#email"], c["username"])
        _fill(page, ['input[type="password"]','input[name="password"]',"#password"], c["password"])
        _inbox_mark(ctx)
        _click(page, ['button[type="submit"]',"#sign-in"])
        _handle_2fa(page, ctx)
        page.wait_for_url("**ticketmaster.com/member**", timeout=LOGIN_TIMEOUT)
        page.goto("https://www.ticketmaster.com/member/orders", timeout=NAV_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        page.wait_for_timeout(3_000)
        t = page.inner_text("body")
        events = re.findall(r'(?:upcoming[^\n]*\n)([^\n]{5,60})', t, re.IGNORECASE)[:3]
        r.update({"status":"ok","items":(
            [{"label":"Event","value":e.strip()} for e in events]
            or [{"label":"Tickets","value":"See site"}]
        )})
    except Exception as e:
        r.update({"status":"error","error":str(e).split('\n')[0][:120]})
    return r


def scrape_xfinity(page, c, ctx):
    r = _base("Xfinity","📡","#e8f5e9","https://www.xfinity.com/overview")
    try:
        page.goto("https://login.xfinity.com/login", timeout=NAV_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        page.wait_for_timeout(2_000)

        # Step 1: username
        _fill(page, ["#user", 'input[name="user"]', 'input[type="email"]', 'input[type="text"]'], c["username"])
        _click(page, ["#sign_in", 'button[type="submit"]'])

        # Step 2: wait for password field to actually appear (Xfinity is multi-step)
        pw_appeared = False
        for sel in ["#passwd", 'input[name="passwd"]', 'input[type="password"]']:
            try:
                page.locator(sel).first.wait_for(state="visible", timeout=10_000)
                pw_appeared = True
                break
            except Exception:
                pass

        if not pw_appeared:
            raise Exception("Password field did not appear after entering username")

        _fill(page, ["#passwd", 'input[name="passwd"]', 'input[type="password"]'], c["password"])
        _inbox_mark(ctx)
        _click(page, ["#sign_in", 'button[type="submit"]'])
        _handle_2fa(page, ctx)

        # Wait until we leave login.xfinity.com (not just any xfinity.com page)
        page.wait_for_url(
            lambda url: "xfinity.com" in url and "login.xfinity.com" not in url,
            timeout=LOGIN_TIMEOUT
        )
        page.goto("https://www.xfinity.com/overview", timeout=NAV_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        raw_text = _explore_account_pages(page)
        r.update({"status":"ok","items":[],"raw_text":raw_text})
    except Exception as e:
        r.update({"status":"error","error":str(e).split('\n')[0][:120]})
    return r


def scrape_pa_utilities(page, c, ctx):
    r = _base("Palo Alto Utilities","⚡","#fff3e0",
               "https://mycpau.cityofpaloalto.org/portal/")
    try:
        page.goto("https://mycpau.cityofpaloalto.org/portal/", timeout=NAV_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        page.wait_for_timeout(3_000)

        # ASP.NET WebForms portal with honeypot fields — target real fields by ID
        _fill(page, ["#txtLogin"], c["username"])
        _fill(page, ["#txtpwd"], c["password"])

        _inbox_mark(ctx)
        page.wait_for_timeout(500)
        _click(page, ["#btnlogin", 'input[type="submit"]', 'button[type="submit"]'])

        _handle_2fa(page, ctx)
        page.wait_for_load_state("domcontentloaded", timeout=LOGIN_TIMEOUT)
        page.wait_for_timeout(5_000)

        t = page.inner_text("body")

        # Balance
        bm = re.search(r'\$\s*([\d,]+\.\d{2})', t)
        balance = f"${bm.group(1)}" if bm else "–"

        # Due date
        dm = re.search(r'(?:Due Date|Payment Due)[^\d]*(\d{1,2}/\d{1,2}/\d{2,4})',
                       t, re.IGNORECASE)
        due = dm.group(1) if dm else _date(t) or "–"

        # Auto-pay status
        auto_pay = "Auto-pay on" if re.search(r'auto.?pay|auto.?payment', t, re.IGNORECASE) else None

        # kWh extraction removed — homepage chart is canvas-based (not in page text)
        # Gemini field discovery handles this from the raw page text instead
        latest_kwh = None
        prev_kwh   = None
        kwh_label = None
        if latest_kwh and prev_kwh:
            try:
                diff = int(latest_kwh.replace(',','')) - int(prev_kwh.replace(',',''))
                sign = "↑" if diff > 0 else "↓"
                kwh_label = f"{latest_kwh} kWh  {sign}{abs(diff):,} vs prev month"
            except Exception:
                kwh_label = f"{latest_kwh} kWh"
        elif latest_kwh:
            kwh_label = f"{latest_kwh} kWh"

        # Notification counts
        notif_total = sum(int(n) for n in re.findall(r'\b(\d+)\b(?=\s*(?:notification|alert|message|question))', t, re.IGNORECASE))

        # Items include a "key" so Mighty can filter by user field preferences
        items = [
            {"key": "balance",  "label": "Balance",  "value": balance},
            {"key": "due_date", "label": "Due Date", "value": due},
        ]
        if auto_pay:
            items.append({"key": "auto_pay", "label": "Payment",  "value": auto_pay})
        if kwh_label:
            items.append({"key": "usage",    "label": "Avg Monthly kWh", "value": kwh_label})
        if notif_total:
            items.append({"key": "alerts",   "label": "Alerts",   "value": f"{notif_total} notification{'s' if notif_total != 1 else ''}"})

        # Ways to Save navigation removed — Gemini discovers these from page text

        raw_text = _explore_account_pages(page)
        r.update({"status":"ok","items":[],"raw_text":raw_text})
    except Exception as e:
        r.update({"status":"error","error":str(e).split('\n')[0][:120]})
    return r


def scrape_pamf(page, c, ctx):
    r = _base("PAMF MyChart","🏥","#e8f5e9","https://mychart.pamf.org")
    try:
        page.goto("https://mychart.pamf.org/MyChart/Authentication/Login", timeout=NAV_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        _fill(page, ["#Login",'input[name="Login"]','input[type="text"]'], c["username"])
        _fill(page, ["#Password",'input[name="Password"]','input[type="password"]'], c["password"])
        _inbox_mark(ctx)
        _click(page, ['button[type="submit"]',"#Submit",'input[type="submit"]'])
        _handle_2fa(page, ctx)
        page.wait_for_url("**mychart.pamf.org/MyChart/**", timeout=LOGIN_TIMEOUT)
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT)
        page.wait_for_timeout(3_000)
        t = page.inner_text("body")
        msgs  = re.search(r'(\d+)\s*(?:new\s+)?message', t, re.IGNORECASE)
        appts = re.findall(
            r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?',
            t, re.IGNORECASE)[:1]
        raw_text = _explore_account_pages(page)
        r.update({"status":"ok","items":[],"raw_text":raw_text})
    except Exception as e:
        r.update({"status":"error","error":str(e).split('\n')[0][:120]})
    return r

# ── Registry & categories ─────────────────────────────────────────────────────
SCRAPERS = {
    # Original hand-coded scrapers
    "amex":          scrape_amex,
    "chase":         scrape_chase,
    "sfcu":          scrape_sfcu,
    "amazon":        scrape_amazon,
    "delta":         scrape_delta,
    "hertz":         scrape_hertz,
    "marriott":      scrape_marriott,
    "hilton":        scrape_hilton,
    "disney_plus":   scrape_disney_plus,
    "ticketmaster":  scrape_ticketmaster,
    "xfinity":       scrape_xfinity,
    "pa_utilities":  scrape_pa_utilities,
    "pamf":          scrape_pamf,
    # Generic scrapers (login + AI discovery)
    **{k: globals()[f"scrape_{k}"] for k in _SITE_CFGS}
}

CATEGORIES = [
    ("Banking & Finance",  ["amex","chase","sfcu"]),
    ("Shopping",           ["amazon","starbucks"]),
    ("Travel",             ["delta","hertz","marriott","hilton"]),
    ("Entertainment",      ["disney_plus","ticketmaster"]),
    ("Utilities & Bills",  ["xfinity","pa_utilities","att","att_wireless"]),
    ("Insurance",          ["state_farm"]),
    ("Health",             ["pamf"]),
]

# ── Dashboard generation ──────────────────────────────────────────────────────
def _card_html(key, data):
    status = data.get("status","skipped")
    if status == "ok":
        body = "".join(
            f'<div class="row"><span class="lbl">{i["label"]}</span>'
            f'<span class="val">{i["value"]}</span></div>'
            for i in data.get("items",[])
        ) or '<div class="dim">No data extracted</div>'
    elif status == "error":
        body = f'<div class="err">⚠ {data.get("error","")}</div>'
    else:
        body = '<div class="dim">Not configured</div>'
    dot = {"ok":"#30d158","error":"#ff3b30"}.get(status,"#aeaeb2")
    return f"""<div class="card">
      <div class="card-header">
        <div class="icon" style="background:{data.get('color','#f0f0f0')}">{data.get('icon','?')}</div>
        <div><div class="card-name">{data.get('name',key)}</div>
        <div class="status"><span style="background:{dot}" class="dot"></span>{status}</div></div>
      </div>
      <div class="card-body">{body}</div>
      <a class="open-btn" href="{data.get('url','#')}" target="_blank">↗ Open</a>
    </div>"""

def generate_dashboard(results, scraped_at):
    sections = ""
    for cat, keys in CATEGORIES:
        cards = "".join(_card_html(k,results[k]) for k in keys if k in results)
        if cards:
            sections += (f'<div class="section"><div class="section-title">{cat}</div>'
                         f'<div class="cards">{cards}</div></div>')
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>My Accounts</title><style>
:root{{color-scheme:light}}*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
     background:#f5f5f7;color:#1d1d1f;padding:24px;min-height:100vh}}
header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:28px}}
header h1{{font-size:22px;font-weight:600}}.meta{{font-size:12px;color:#86868b}}
.section{{margin-bottom:28px}}
.section-title{{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;
               color:#86868b;margin-bottom:12px;padding-left:2px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px}}
.card{{background:#fff;border-radius:14px;padding:16px 18px;border:1px solid #e5e5ea;
      display:flex;flex-direction:column;gap:10px}}
.card-header{{display:flex;align-items:center;gap:12px}}
.icon{{width:36px;height:36px;border-radius:10px;display:flex;align-items:center;
      justify-content:center;font-size:17px;flex-shrink:0}}
.card-name{{font-size:13px;font-weight:600;color:#1d1d1f}}
.status{{font-size:11px;color:#86868b;display:flex;align-items:center;gap:5px;margin-top:2px}}
.dot{{width:7px;height:7px;border-radius:50%;display:inline-block}}
.card-body{{display:flex;flex-direction:column;gap:6px;flex:1}}
.row{{display:flex;justify-content:space-between;align-items:baseline}}
.lbl{{font-size:12px;color:#86868b}}.val{{font-size:14px;font-weight:600;color:#1d1d1f}}
.err{{font-size:11px;color:#ff3b30;font-style:italic}}
.dim{{font-size:11px;color:#aeaeb2;font-style:italic}}
.open-btn{{display:block;padding:7px 14px;border-radius:8px;background:#0071e3;
          color:#fff;font-size:12px;font-weight:500;text-decoration:none;text-align:center}}
.tip{{background:#e8f4fd;border:1px solid #b3d9f7;border-radius:10px;
     padding:12px 16px;font-size:12px;color:#3a7fb5;margin-top:8px;line-height:1.6}}
code{{background:#d0e8f7;border-radius:4px;padding:1px 5px;font-size:11px}}
</style></head><body>
<header><h1>My Accounts</h1><div class="meta">Updated {scraped_at}</div></header>
{sections}
<div class="tip">Refresh: <code>python3 scrape.py</code> &nbsp;|&nbsp;
Manage credentials: <a href="{MIGHTY_URL}/credentials" style="color:#3a7fb5">{MIGHTY_URL}/credentials</a>
</div></body></html>"""

# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    print("=" * 54)
    print("  Mighty Account Dashboard Scraper")
    print("=" * 54)

    # Fetch credentials from Mighty cloud
    print("\nFetching credentials from Mighty...", end=" ", flush=True)
    all_creds, email_cfg = fetch_credentials()
    configured = [k for k in all_creds if k in SCRAPERS and all_creds[k].get("username")]
    print(f"✓  {len(configured)} account(s) configured\n")

    if not configured:
        print("No credentials found. Add them at:")
        print(f"  {MIGHTY_URL}/credentials")
        return

    # Set up email 2FA fetcher
    fetcher = None
    if email_cfg and email_cfg.get("address") and email_cfg.get("app_password"):
        print("Connecting to Gmail for email code auto-fill...", end=" ", flush=True)
        try:
            fetcher = EmailCodeFetcher(email_cfg["address"], email_cfg["app_password"])
            print("✓")
        except Exception as e:
            print(f"✗ ({e})")

    print(f"Running {len(configured)} accounts ({MAX_WORKERS} at a time)...\n")

    SESSIONS_DIR.mkdir(exist_ok=True)
    print_lock = threading.Lock()
    def log(msg):
        with print_lock: print(msg, flush=True)

    results   = {k: _base(k,"?","#f0f0f0","#") for k in SCRAPERS}
    synced_at = datetime.now().isoformat()

    with sync_playwright() as pw:
        browser = _launch_browser(pw)

        def run(key: str, stagger: float) -> tuple:
            time.sleep(stagger)
            creds = all_creds[key]
            ctx   = {"fetcher": fetcher, "creds": creds, "log": log}
            bctx, state_file = _new_browser_context(browser, key)
            try:
                page   = bctx.new_page()
                result = SCRAPERS[key](page, creds, ctx)
                if result["status"] == "ok":
                    vals = ", ".join(i["value"] for i in result.get("items",[]))
                    log(f"✓  {key}: {vals}")
                    if push_to_cloud(key, result, synced_at):
                        log(f"   ↑ synced to Mighty")
                else:
                    log(f"✗  {key}: {result.get('error','')}")
            except Exception as e:
                result = {**_base(key,"?","#f0f0f0","#"),
                          "status":"error","error":str(e).split('\n')[0][:120]}
                log(f"✗  {key}: {e}")
            finally:
                try:
                    bctx.storage_state(path=str(state_file))
                except Exception:
                    pass
                bctx.close()
            return key, result

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(run, k, i * 0.3): k
                       for i, k in enumerate(configured)}
            for future in as_completed(futures):
                k, result = future.result()
                results[k] = result

        browser.close()

    if fetcher: fetcher.close()

    scraped_at = datetime.now().strftime("%b %d, %Y at %I:%M %p")
    html = generate_dashboard(results, scraped_at)
    DASHBOARD_FILE.write_text(html, encoding="utf-8")
    print(f"\n✓  Dashboard → {DASHBOARD_FILE}")
    webbrowser.open(f"file://{DASHBOARD_FILE.resolve()}")


# ── Programmatic entry point (used by MightySync.app) ─────────────────────────
def run_sync(api_key: str, mighty_url: str = MIGHTY_URL,
             log: callable = print, only_source: str | None = None) -> dict:
    """Run a full sync and return a summary dict.
    Designed to be called from the MightySync menu bar app (no CLI needed).

    Returns:
        {"ok": bool, "synced": int, "errors": int, "results": dict}
    """
    global MIGHTY_API_KEY, MIGHTY_URL
    MIGHTY_API_KEY = api_key
    MIGHTY_URL     = mighty_url

    log("Fetching credentials...")
    try:
        all_creds, email_cfg = fetch_credentials()
    except SystemExit as e:
        return {"ok": False, "error": str(e), "synced": 0, "errors": 0, "results": {}}

    configured = [k for k in all_creds if k in SCRAPERS and all_creds[k].get("username")]
    if only_source:
        configured = [only_source] if only_source in configured else []
    if not configured:
        return {"ok": False, "error": "No credentials configured", "synced": 0, "errors": 0, "results": {}}

    fetcher = None
    if email_cfg and email_cfg.get("address"):
        try:
            fetcher = EmailCodeFetcher(email_cfg["address"], email_cfg["app_password"])
        except Exception:
            pass

    SESSIONS_DIR.mkdir(exist_ok=True)
    lock = threading.Lock()
    results   = {k: _base(k,"?","#f0f0f0","#") for k in SCRAPERS}
    synced_at = datetime.now().isoformat()

    with sync_playwright() as pw:
        browser = _launch_browser(pw)

        def run(key: str, stagger: float) -> tuple:
            time.sleep(stagger)
            creds = all_creds[key]
            ctx   = {"fetcher": fetcher, "creds": creds, "log": log}
            bctx, state_file = _new_browser_context(browser, key)
            try:
                page   = bctx.new_page()
                result = SCRAPERS[key](page, creds, ctx)
                # Capture raw page text for AI field discovery
                if result.get("status") == "ok" and "raw_text" not in result:
                    try:
                        result["raw_text"] = page.inner_text("body")[:8000]
                    except Exception:
                        pass
                if result["status"] == "ok":
                    vals = ", ".join(i["value"] for i in result.get("items",[]))
                    with lock: log(f"✓  {key}: {vals}")
                    push_to_cloud(key, result, synced_at)
                else:
                    with lock: log(f"✗  {key}: {result.get('error','')}")
            except Exception as e:
                result = {**_base(key,"?","#f0f0f0","#"),
                          "status":"error","error":str(e).split('\n')[0][:120]}
                with lock: log(f"✗  {key}: {e}")
            finally:
                try:
                    bctx.storage_state(path=str(state_file))
                except Exception:
                    pass
                bctx.close()
            return key, result

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(run, k, i * 0.3): k for i, k in enumerate(configured)}
            for future in as_completed(futures):
                k, result = future.result()
                results[k] = result

        browser.close()

    if fetcher: fetcher.close()

    synced = sum(1 for r in results.values() if r.get("status") == "ok")
    errors = sum(1 for r in results.values() if r.get("status") == "error")
    return {"ok": errors == 0, "synced": synced, "errors": errors, "results": results}


if __name__ == "__main__":
    main()
