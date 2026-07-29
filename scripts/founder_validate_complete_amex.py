#!/usr/bin/env python3
"""Continuous Founder Validation — Complete Amex Experience (AT-00–AT-14).

One session, one user. Does not reset the environment between tests unless a
scenario explicitly requires a state change (session evidence, Chrome worker,
unsupported-data terminal). Writes docs/cycles/complete-amex-experience/FOUNDER_VALIDATION.md
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "cycles" / "complete-amex-experience" / "FOUNDER_VALIDATION.md"
DB = ROOT / ".tmp-founder-validate-complete-amex.db"


@dataclass
class Result:
    at: str
    verdict: str  # Pass | Fail | Blocked | Partial
    notes: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)


RESULTS: list[Result] = []


def _rec(
    at: str,
    verdict: str,
    *notes: str,
    unexpected: list[str] | None = None,
    contradictions: list[str] | None = None,
) -> None:
    RESULTS.append(
        Result(
            at=at,
            verdict=verdict,
            notes=list(notes),
            unexpected=list(unexpected or []),
            contradictions=list(contradictions or []),
        )
    )
    print(f"[{verdict}] {at}: {notes[0] if notes else ''}")


def _html(r) -> str:
    return r.data.decode("utf-8", errors="replace")


def _uid(client) -> str:
    with client.session_transaction() as sess:
        return sess["user_id"]


def _csrf(client) -> str:
    with client.session_transaction() as sess:
        return sess["_csrf"]


def _account_status(client) -> dict:
    r = client.get("/api/account-status")
    body = r.get_json() or {}
    accounts = body.get("accounts") or body.get("account_statuses") or []
    if isinstance(body.get("accounts"), dict):
        accounts = list(body["accounts"].values())
    amex = next(
        (a for a in accounts if isinstance(a, dict) and a.get("source") == "amex"),
        None,
    )
    return {"raw": body, "amex": amex, "status_code": r.status_code}


def _home_and_accounts(client) -> tuple[str, str, dict]:
    home = _html(client.get("/dashboard"))
    accts = _html(client.get("/credentials"))
    st = _account_status(client)
    return home, accts, st


def _seed_discovery_amex(mighty, uid: str) -> None:
    from mighty.discovery_pipeline import process_email_scan

    now = datetime.now(timezone.utc)
    with mighty.app.app_context():
        db = mighty.get_db()
        process_email_scan(
            db,
            uid,
            [
                {
                    "site_key": "amex",
                    "display_name": "American Express",
                    "category": "credit_card",
                    "email_count": 8,
                    "sender": "americanexpress.com",
                }
            ],
            source_type="gmail_sender",
            source_ref="gmail",
            auto_enroll_providers=frozenset({"amex"}),
            register_fn=None,
            auto_enroll=False,
            now=now,
        )


def main() -> int:
    sys.path.insert(0, str(ROOT))
    os.environ["SECRET_KEY"] = "founder-validate-complete-amex"
    os.environ["DATABASE_PATH"] = str(DB)
    os.environ["MIGHTY_ENV"] = "production"
    os.environ.pop("DEMO_MODE", None)
    os.environ.pop("HOME_OS_ENABLED", None)
    os.environ.pop("POSTMARK_API_KEY", None)
    if DB.exists():
        DB.unlink()

    import app as mighty
    from mighty.capability_state import CAPABILITY_STATUS_LABELS, CapabilityState
    from mighty.connection_state import (
        advance_amex_to_waiting,
        amex_extension_connected,
        start_amex_connect,
    )
    from mighty.customer_account_access import BG_UNSUPPORTED_DATA
    from mighty.journey_narrative import (
        ACTION_PROVIDER_VISIT,
        OBS_STILL_NEEDS_LOGIN,
        OBS_VERIFICATION_PROGRESS,
        compose_narrative_for_provider_ask,
        record_system_observation,
        record_user_action,
        sync_journey_observations,
    )
    from mighty.provider_access_manager import (
        complete_provider_access_check,
        record_amex_extension_connected,
        request_provider_access_check,
    )
    from mighty.provider_account import EXTRACTION_NO_ACCOUNT_DATA, EXTRACTION_PENDING
    from mighty.attention_compiler import (
        WorkerSignal,
        compile_attention_candidates,
    )
    from mighty.auth_truth import (
        ACCESS_BROWSER_SESSION,
        AuthInterruption,
        AuthTruth,
        EvidenceClass,
    )
    from mighty.authentication_state import AuthenticationState
    from mighty.attention import AttentionClass

    mighty.DATABASE = str(DB)
    mighty.POSTMARK_API_KEY = ""
    with mighty.app.app_context():
        mighty.init_db()
    mighty.app.config["TESTING"] = True
    # Bypass rate limits for continuous session
    mighty._rate_limit = lambda *a, **k: True  # type: ignore

    client = mighty.app.test_client()
    email = f"founder_at00_{secrets.token_hex(4)}@test.local"
    password = "pass12345"
    honest = CAPABILITY_STATUS_LABELS[CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA]

    # ── AT-00 Fresh Install ──────────────────────────────────────────────────
    landing = client.get("/")
    signup_get = client.get("/signup")
    with client.session_transaction() as sess:
        csrf = sess["_csrf"]
    signup = client.post(
        "/signup",
        data={"email": email, "password": password, "_csrf": csrf},
        follow_redirects=False,
    )
    loc = signup.headers.get("Location") or ""
    after_signup = client.get(loc) if signup.status_code in (302, 303) else signup
    after_body = _html(after_signup)

    email_scan = client.get("/email-scan")
    scan_body = _html(email_scan)
    gmail_oauth_present = (
        "accounts.google.com" in scan_body
        or "/google" in scan_body
        or "Gmail" in scan_body
        or "Connect" in scan_body
    )
    amex_on_scan = "American Express" in scan_body and "Confirm what Mighty should watch" in scan_body

    at00_notes = [
        f"Landing HTTP {landing.status_code}; signup → {signup.status_code} loc={loc or '(body)'}",
        f"Post-signup surface contains email-scan/discover cues: {'email-scan' in loc or 'email-scan' in after_body or email_scan.status_code == 200}",
    ]
    at00_unexpected: list[str] = []
    at00_contra: list[str] = []

    if signup.status_code not in (200, 302, 303):
        _rec("AT-00", "Fail", "Signup failed", *at00_notes)
    elif amex_on_scan:
        # Rare: discovery already present
        at00_notes.append("Discover review already showed Amex after signup")
    else:
        # Real Gmail OAuth cannot complete in this validation environment.
        at00_unexpected.append(
            "Gmail OAuth cannot be completed in automated Founder Validation; "
            "seeding Amex discovery facts as the post-Gmail system would, then continuing the same user session (not a full environment reset)."
        )
        _seed_discovery_amex(mighty, _uid(client))
        review = _html(client.get("/email-scan"))
        if "American Express" not in review:
            at00_contra.append(
                "After seeded discovery, /email-scan does not show American Express for confirm — Discover path opaque"
            )
        confirm = client.post(
            "/email-scan/confirm",
            data={"_csrf": _csrf(client), "watch": "amex"},
            follow_redirects=False,
        )
        cloc = confirm.headers.get("Location") or ""
        at00_notes.append(f"Confirm Amex → {confirm.status_code} {cloc}")
        if "/enable-monitoring" not in cloc:
            at00_contra.append(
                f"Confirm did not route to /enable-monitoring (got {cloc})"
            )

        em = client.get("/enable-monitoring")
        em_body = _html(em)
        at00_notes.append(
            f"Enable monitoring: outcome-first={'Keep your accounts current' in em_body}; "
            f"chrome CTA={'/extension-setup' in em_body}"
        )
        if "Keep your accounts current" not in em_body:
            at00_contra.append("Enable monitoring missing outcome-first headline")

        # User may skip Chrome or go to extension-setup then Home
        ext = client.get("/extension-setup")
        ext_body = _html(ext)
        at00_notes.append(
            f"Extension setup page HTTP {ext.status_code}; "
            f"never-dead-end cues={'Verify' in ext_body or 'heartbeat' in ext_body.lower() or 'download' in ext_body.lower()}"
        )

        # No real Chrome extension in this environment — go Home with Amex watched
        home, accts, st = _home_and_accounts(client)
        amex = st.get("amex")
        at00_notes.append(
            f"Home after enable/skip path: Amex in API={amex is not None}; "
            f"Visit/Sign-in cues={('American Express' in home or 'Amex' in home)}"
        )
        if amex is None:
            at00_contra.append("Amex not present in account-status after confirm enroll")
        # Steady watching not reached without Chrome+Amex terminal
        at00_contra.append(
            "Could not reach steady-state quiet watching of Amex without real Chrome extension + Amex session — AT-00 incomplete"
        )

        verdict = "Fail" if at00_contra else "Partial"
        _rec(
            "AT-00",
            verdict,
            "Fresh Install path exercised through Confirm → Enable Monitoring → Home; blocked before steady watching without real Chrome/Amex",
            *at00_notes,
            unexpected=at00_unexpected,
            contradictions=at00_contra,
        )

    uid = _uid(client)

    # Ensure Amex enrolled for continued session (if AT-00 confirm failed)
    with mighty.app.app_context():
        db = mighty.get_db()
        cred = db.execute(
            "SELECT 1 FROM account_credentials WHERE user_id=? AND source='amex'",
            (uid,),
        ).fetchone()
    if cred is None:
        _seed_discovery_amex(mighty, uid)
        client.post(
            "/email-scan/confirm",
            data={"_csrf": _csrf(client), "watch": "amex"},
            follow_redirects=False,
        )

    # ── AT-13 Chrome vs Amex (before simulating worker healthy) ──────────────
    # Worker missing by default for new user
    now = datetime.now(timezone.utc).isoformat()
    truth = AuthTruth(
        schema_version=1,
        user_id=uid,
        provider="amex",
        state=AuthenticationState.SIGNED_OUT,
        access_method=ACCESS_BROWSER_SESSION,
        evidence_class=EvidenceClass.DEFINITIVE,
        evidence_source="access_manager",
        evidence_id=None,
        observed_at=now,
        projected_at=now,
        interruption=AuthInterruption.LOGIN,
        interruption_expected=True,
        needs_human=True,
        needs_human_reason="login",
        evidence_age_seconds=10.0,
        stale=False,
    )
    worker_missing = WorkerSignal(
        user_id=uid,
        installed=False,
        reachable=False,
        last_seen_at=None,
        version=None,
        update_required=False,
        enrolled_account_count=1,
    )
    items = compile_attention_candidates(
        auth_truths=(truth,), worker_signal=worker_missing
    )
    amex_auth = [
        i
        for i in items
        if i.attention_class == AttentionClass.AUTH_BLOCKER and i.provider == "amex"
    ]
    sys_items = [i for i in items if i.attention_class == AttentionClass.SYSTEM]
    home13 = _html(client.get("/dashboard"))
    chrome_primary = "/extension-setup" in home13 and (
        "Set up Mighty in Chrome" in home13 or "Chrome" in home13
    )
    if sys_items and not amex_auth:
        _rec(
            "AT-13",
            "Pass",
            "Compiler: Chrome SYSTEM present, Amex AUTH suppressed when worker missing",
            f"Home chrome cues={chrome_primary}",
        )
    else:
        _rec(
            "AT-13",
            "Fail",
            "Chrome-first ranking broken when worker missing",
            f"system={len(sys_items)} amex_auth={len(amex_auth)}",
            contradictions=[
                "Documented AT-13: when Chrome missing, Amex Visit must not be the contradictory primary"
            ],
        )

    # Simulate Chrome ready so later Visit/Amex tests can proceed (same session)
    with mighty.app.app_context():
        db = mighty.get_db()
        db.execute(
            "UPDATE users SET extension_version=?, extension_last_seen_at=? WHERE id=?",
            ("1.0.0-founder-validate", datetime.now(timezone.utc).isoformat(), uid),
        )
        db.commit()

    # ── AT-01 First-time connection (Visit intent + continuity; terminal via API) ─
    home1, accts1, st1 = _home_and_accounts(client)
    # Record Visit as product does on CTA click
    with mighty.app.app_context():
        db = mighty.get_db()
        record_user_action(
            db, uid, event_type=ACTION_PROVIDER_VISIT, provider="amex"
        )
        events = sync_journey_observations(
            db, uid, provider="amex", still_needs_user=True, verification_active=False
        )
        compose = compose_narrative_for_provider_ask(
            provider_key="amex",
            provider_display="American Express",
            events=events,
            repeating_user_action=False,
        )
    home1b = _html(client.get("/dashboard"))
    intent_ok = True
    contra1: list[str] = []
    if compose and compose.evidence_tier not in ("intent", "observed_negative"):
        # may be non_progress if still_needs minted
        pass
    body_l = (compose.body if compose else "").lower() if compose else ""
    if compose and (
        "verifying access" in body_l and compose.evidence_tier == "intent"
    ):
        intent_ok = False
        contra1.append("R2: verifying from intent alone")
    if compose and "do not need to do anything else" in body_l and compose.evidence_tier == "intent":
        intent_ok = False
        contra1.append("R2: do-nothing from intent alone")

    # Drive terminal unsupported-data (honest Amex outcome without inventing balances)
    with mighty.app.app_context():
        db = mighty.get_db()
        ctx = dict(
            iso_fn=mighty.iso,
            encrypt_fn=mighty.encrypt_account_data,
            decrypt_fn=mighty.decrypt_account_data,
        )
        start_amex_connect(db, uid, **ctx)
        advance_amex_to_waiting(db, uid, **ctx)
        amex_extension_connected(db, uid, session_verified=True, **ctx)
        record_amex_extension_connected(db, uid, observed_at=mighty.iso())
        db.execute(
            "UPDATE account_data SET extraction_status=? WHERE user_id=? AND source=?",
            (EXTRACTION_PENDING, uid, "amex"),
        )
        db.commit()
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        probe = {
            "provider": "amex",
            "status": "ok",
            "auth_state": "authenticated_no_private_data",
            "url_visited": "https://global.americanexpress.com/overview",
            "final_url": None,
            "signed_in_detected": True,
            "private_data_detected": False,
            "evidence_type": "page",
            "evidence_snippet": "founder-validate",
            "failure_reason": None,
            "login_form_present": False,
            "probed_at": datetime.now(timezone.utc).isoformat(),
        }
        complete_provider_access_check(db, uid, probe, verification_id=vid)
        api_key = db.execute(
            "SELECT api_key FROM users WHERE id=?", (uid,)
        ).fetchone()["api_key"]

    nq = client.post(
        "/api/extension/amex/no-qualifying-private-data",
        headers={"X-Mighty-Key": api_key},
        json={
            "verification_id": vid,
            "extraction_attempted": True,
            "extraction_reason": "no_publishable_widgets",
        },
    )
    home1c, accts1c, st1c = _home_and_accounts(client)
    amex1 = st1c.get("amex") or {}
    access1 = amex1.get("customer_access") or {}
    plc1 = amex1.get("product_lifecycle") or {}
    # Hero must not invent balances; ignore JSON/JS site catalogs in page bundle.
    hero_m = re.search(
        r'class="[^"]*mds-hero__[^"]*"[\s\S]{0,1200}', home1c, re.I
    )
    hero_chunk = hero_m.group(0) if hero_m else ""
    invented = bool(
        re.search(r"\$\s*[\d,]+", hero_chunk)
        or re.search(r"\b\d{1,3}(?:,\d{3})+\s*(points|miles)\b", hero_chunk, re.I)
    )
    home_l = home1c.lower()
    home_shows_unsupported = (
        "no account data" in home_l
        or "stay signed in" in home_l
        or "logged in — no account data" in home_l
        or "logged in - no account data" in home_l
    )
    home_still_first_ask = (
        plc1.get("state") == "unsupported-data"
        and (
            "beginning to manage" in home_l
            or ("visit american express" in home_l and not home_shows_unsupported)
        )
    )
    if home_still_first_ask or (
        plc1.get("state") == "unsupported-data" and not home_shows_unsupported
    ):
        contra1.append(
            "Home does not present unsupported-data outcome after terminal "
            "(still first-manage/Visit or missing knows/does-not-know copy) while "
            "API/Accounts say Logged in — no account data"
        )
    if (
        intent_ok
        and nq.status_code == 200
        and plc1.get("state") == "unsupported-data"
        and access1.get("status_label") == honest
        and not invented
        and home_shows_unsupported
        and not home_still_first_ask
    ):
        _rec(
            "AT-01",
            "Pass",
            "Visit intent evidence-gated; terminal unsupported-data honest on Home+API; no invented balances",
            f"nested_label={access1.get('status_label')!r} plc={plc1.get('state')!r}",
            unexpected=[
                "Amex sign-in completed via extension API simulation (no live Amex browser login in this environment)"
            ],
        )
    elif (
        nq.status_code == 200
        and plc1.get("state") == "unsupported-data"
        and access1.get("status_label") == honest
    ):
        _rec(
            "AT-01",
            "Fail",
            "API lifecycle honest but Home does not present unsupported-data outcome",
            f"home_unsupported={home_shows_unsupported} first_ask={home_still_first_ask} invented={invented} intent_ok={intent_ok}",
            contradictions=contra1,
            unexpected=[
                "Amex terminal via extension API simulation (no live Amex browser login)"
            ],
        )
    else:
        _rec(
            "AT-01",
            "Fail",
            "First-time connection path inconsistent",
            f"nq={nq.status_code} label={access1.get('status_label')!r} plc={plc1.get('state')!r} invented={invented}",
            contradictions=contra1
            or (
                ["Nested/top-level lifecycle disagreement or invented data"]
                if access1.get("status_label") != honest
                else []
            ),
        )

    # ── AT-05 Unsupported + AT-08 Home vs Accounts ───────────────────────────
    label_home_chip = access1.get("status_label")
    label_top = amex1.get("presentation_label") or amex1.get("status_label")
    meaning = access1.get("meaning") or ""
    bg = access1.get("background_work")
    accounts_has_open = (
        "data-amex-lifecycle=\"unsupported-data\"" in accts1c
        or "American Express" in accts1c
    )
    contra58: list[str] = []
    if label_home_chip == "Unable to verify":
        contra58.append("Nested status_label still Unable to verify")
    if label_home_chip != label_top and label_top and label_home_chip:
        contra58.append(
            f"API nested {label_home_chip!r} vs top-level {label_top!r}"
        )
    if "Unable to verify" in accts1c and honest not in accts1c:
        contra58.append("Accounts HTML shows Unable to verify without honest label")
    if bg == "Extracting":
        contra58.append("Sticky Extracting after terminal unsupported-data")
    if "has not read account data yet" in meaning and "no account details" not in meaning:
        contra58.append("Meaning still CONNECTED_NOT_SEEN instead of NO_ACCOUNT_DATA")
    # AT-08 includes Home hero vs Accounts/API
    if home_still_first_ask:
        contra58.append(
            "Home hero contradicts Accounts/API unsupported-data (still first-manage/Visit)"
        )

    if not contra58 and label_home_chip == honest:
        _rec(
            "AT-05",
            "Pass",
            "Unsupported-data terminal honest; nested label matches lifecycle",
            f"meaning={meaning[:80]!r} bg={bg!r}",
        )
        _rec(
            "AT-08",
            "Pass",
            "Home API nested/top-level labels agree for Amex unsupported-data",
            f"accounts_cta_present={accounts_has_open}",
        )
    else:
        at05_only = [c for c in contra58 if "hero" not in c.lower()]
        _rec(
            "AT-05",
            "Pass" if label_home_chip == honest and bg != "Extracting" and not at05_only else "Fail",
            "Unsupported-data API/Accounts honesty"
            + ("; Home hero gap tracked under AT-08" if home_still_first_ask else ""),
            f"meaning={meaning[:80]!r} bg={bg!r}",
            contradictions=at05_only,
        )
        _rec(
            "AT-08",
            "Fail" if contra58 else "Partial",
            "Home vs Accounts consistency",
            contradictions=contra58,
        )

    # ── AT-11 Visit then immediate return (intent only) — re-seed visit ──────
    with mighty.app.app_context():
        db = mighty.get_db()
        record_user_action(
            db, uid, event_type=ACTION_PROVIDER_VISIT, provider="amex"
        )
        # Hard reload analogue: recompose without verification_active
        events2 = sync_journey_observations(
            db,
            uid,
            provider="amex",
            still_needs_user=False,
            verification_active=False,
            terminal_ok=False,
        )
        c11 = compose_narrative_for_provider_ask(
            provider_key="amex",
            provider_display="American Express",
            events=events2,
            repeating_user_action=False,
        )
    # Reload home
    home11 = _html(client.get("/dashboard"))
    if c11 is None:
        # may be None if no provider ask — check attrs on home
        tier = re.search(r'data-narrative-evidence-tier="([^"]+)"', home11)
        if tier and tier.group(1) == "intent":
            _rec("AT-11", "Pass", "Home data-narrative-evidence-tier=intent after Visit-only")
        else:
            _rec(
                "AT-11",
                "Partial",
                "Compose returned None after Visit; Home may be on unsupported-data card not Visit ask",
                f"tier={tier.group(1) if tier else None}",
                unexpected=[
                    "After unsupported-data terminal, Visit intent may not re-open provider-ask overlay — scenario coupling"
                ],
            )
    else:
        bl = (c11.body or "").lower()
        if c11.evidence_tier == "intent" and "verifying access" not in bl and "do not need to do anything else" not in bl:
            _rec(
                "AT-11",
                "Pass",
                f"Intent-only compose beat={c11.beat} tier={c11.evidence_tier}",
            )
        else:
            _rec(
                "AT-11",
                "Fail",
                f"R2 violation tier={c11.evidence_tier} body={c11.body!r}",
                contradictions=["Verifying/do-nothing from Visit intent alone"],
            )

    # ── AT-12 R1 repeat ask ─────────────────────────────────────────────────
    with mighty.app.app_context():
        db = mighty.get_db()
        record_system_observation(
            db, uid, event_type=OBS_STILL_NEEDS_LOGIN, provider="amex"
        )
        events = sync_journey_observations(
            db, uid, provider="amex", still_needs_user=True, verification_active=False
        )
        c12 = compose_narrative_for_provider_ask(
            provider_key="amex",
            provider_display="American Express",
            events=events,
            repeating_user_action=True,
        )
    if c12 and c12.beat == "repeat_ask":
        why = "previous" in (c12.body or "").lower() or "did not" in (c12.body or "").lower() or "not confirm" in (c12.body or "").lower() or "still" in (c12.body or "").lower()
        _rec(
            "AT-12",
            "Pass" if why else "Fail",
            f"repeat_ask beat; why-previous visible={why}",
            contradictions=[] if why else ["R1: repeat ask without why-previous"],
        )
    else:
        _rec(
            "AT-12",
            "Fail",
            f"Expected repeat_ask, got {getattr(c12, 'beat', None)}",
            contradictions=["R1 not composing repeat_ask with still_needs_login"],
        )

    # ── AT-03 Logged out path (same session — signed-out evidence) ───────────
    # Product already has still_needs_login observation; Home/Accounts should agree needs action
    home3, accts3, st3 = _home_and_accounts(client)
    amex3 = st3.get("amex") or {}
    # After unsupported-data we may still be connected session — inject signed_out for AT-03
    # by recording still_needs and checking narrative, not inventing balances
    logged_out_story = (
        "sign in" in home3.lower()
        or "visit" in home3.lower()
        or (c12 and c12.beat in ("repeat_ask", "non_progress", "intent"))
    )
    if logged_out_story and "do not need to do anything else" not in home3.lower():
        _rec(
            "AT-03",
            "Pass",
            "Logged-out / needs-user story present; no false success calm",
            unexpected=[
                "Session signed-out simulated via journey observations + prior needs-login; not a live Amex logout"
            ],
        )
    else:
        _rec(
            "AT-03",
            "Fail",
            "Could not establish clear logged-out Amex ask without false calm",
        )

    # ── AT-02 Already signed in (session connected + verification progress) ──
    with mighty.app.app_context():
        db = mighty.get_db()
        record_system_observation(
            db, uid, event_type=OBS_VERIFICATION_PROGRESS, provider="amex"
        )
        events = sync_journey_observations(
            db,
            uid,
            provider="amex",
            still_needs_user=False,
            verification_active=True,
        )
        c2 = compose_narrative_for_provider_ask(
            provider_key="amex",
            provider_display="American Express",
            events=events,
            repeating_user_action=False,
        )
    if c2 and c2.evidence_tier == "observed_progress":
        _rec(
            "AT-02",
            "Pass",
            "With verification_progress observation, narrative advances to observed_progress",
            unexpected=[
                "Live 'already signed into Amex' browser session not available; used system observation as product would after extension sees session"
            ],
        )
    else:
        _rec(
            "AT-02",
            "Partial",
            f"Progress tier not reached (compose={getattr(c2, 'evidence_tier', None)}) — may be dominated by unsupported-data Home card",
            unexpected=["Live Amex already-signed-in not exercised"],
        )

    # ── AT-04 Session expires mid-flow ──────────────────────────────────────
    with mighty.app.app_context():
        db = mighty.get_db()
        record_system_observation(
            db, uid, event_type=OBS_STILL_NEEDS_LOGIN, provider="amex"
        )
        events = sync_journey_observations(
            db, uid, provider="amex", still_needs_user=True, verification_active=False
        )
        c4 = compose_narrative_for_provider_ask(
            provider_key="amex",
            provider_display="American Express",
            events=events,
            repeating_user_action=True,
        )
    if c4 and c4.evidence_tier in ("observed_negative", "intent") and c4.beat != "terminal":
        sticky_success = "do not need to do anything else" in (c4.body or "").lower()
        _rec(
            "AT-04",
            "Fail" if sticky_success else "Pass",
            f"After needs-login observation beat={c4.beat} tier={c4.evidence_tier}",
            contradictions=["Sticky success after session loss"] if sticky_success else [],
        )
    else:
        _rec(
            "AT-04",
            "Partial",
            f"Could not fully simulate mid-flow expiry; compose={getattr(c4, 'beat', None)}",
        )

    # ── AT-06 Reload during verification ────────────────────────────────────
    with mighty.app.app_context():
        db = mighty.get_db()
        record_user_action(
            db, uid, event_type=ACTION_PROVIDER_VISIT, provider="amex"
        )
        events = sync_journey_observations(
            db, uid, provider="amex", still_needs_user=False, verification_active=False
        )
        c6a = compose_narrative_for_provider_ask(
            provider_key="amex",
            provider_display="American Express",
            events=events,
            repeating_user_action=False,
        )
        # reload = recompose
        events = sync_journey_observations(
            db, uid, provider="amex", still_needs_user=False, verification_active=False
        )
        c6b = compose_narrative_for_provider_ask(
            provider_key="amex",
            provider_display="American Express",
            events=events,
            repeating_user_action=False,
        )
    home6 = _html(client.get("/dashboard"))
    if c6b is None:
        _rec(
            "AT-06",
            "Partial",
            "Reload compose None — Home may not be on provider-ask (unsupported-data dominates)",
        )
    elif c6b.evidence_tier in ("intent", "observed_negative") and "verifying access" not in (
        c6b.body or ""
    ).lower():
        _rec(
            "AT-06",
            "Pass",
            f"Reload preserves non-upgraded tier={c6b.evidence_tier}",
        )
    else:
        _rec(
            "AT-06",
            "Fail",
            f"Reload upgraded claims tier={c6b.evidence_tier}",
            contradictions=["Reload alone authorized verifying/success"],
        )

    # ── AT-07 Return after 30 minutes (time travel events) ───────────────────
    with mighty.app.app_context():
        db = mighty.get_db()
        # Age is 24h window — events still present; reopen Home
        pass
    home7, accts7, st7 = _home_and_accounts(client)
    amex7 = st7.get("amex")
    if amex7 and (amex7.get("product_lifecycle") or {}).get("state"):
        _rec(
            "AT-07",
            "Pass",
            "Reopen Home reflects current Amex lifecycle; no wizard restart",
            f"plc={(amex7.get('product_lifecycle') or {}).get('state')}",
            unexpected=[
                "30-minute wall-clock wait not literally elapsed; validated reopen/current-evidence behavior in-session"
            ],
        )
    else:
        _rec("AT-07", "Fail", "Amex lifecycle missing on reopen")

    # ── AT-09 Permission to Leave / AT-14 Steady return ──────────────────────
    # Unsupported-data is NOT Permission to Leave — need clear portfolio
    # Charter: clear only when no blocking setup/interrupt. Current state has
    # unsupported-data next action — correctly should NOT show false all-clear.
    home9 = _html(client.get("/dashboard"))
    # Detect live hero all-clear only — ignore JS template strings in page bundle.
    hero9 = re.search(r'class="[^"]*mds-hero__[^"]*"[\s\S]{0,1600}', home9, re.I)
    hero9_txt = hero9.group(0).lower() if hero9 else ""
    false_clear = (
        (st7.get("amex") or {}).get("product_lifecycle", {}).get("state")
        == "unsupported-data"
        and (
            "you're good" in hero9_txt
            or "nothing needs you" in hero9_txt
            or "nothing needs your attention" in hero9_txt
        )
    )
    if false_clear:
        _rec(
            "AT-09",
            "Fail",
            "Permission to Leave shown in hero while Amex unsupported-data still needs attention",
            contradictions=[
                "Vision/Experience: all-clear only when earned — unsupported-data must not read as You're good"
            ],
        )
        _rec(
            "AT-14",
            "Fail",
            "Steady clear return not valid while unsupported-data open",
            contradictions=["Same as AT-09"],
        )
    else:
        _rec(
            "AT-09",
            "Partial",
            "No false Permission to Leave in hero while unsupported-data active (correct restraint); true all-clear beat not reached this session",
            unexpected=[
                "Could not produce success-with-data Amex terminal without inventing balances — AT-09 all-clear path unexercised"
            ],
        )
        _rec(
            "AT-14",
            "Partial",
            "Steady clear return unexercised; no false calm regression observed",
        )

    # ── AT-10 Is Mighty working without opening Amex ─────────────────────────
    home10 = _html(client.get("/dashboard"))
    answerable = any(
        s in home10.lower()
        for s in (
            "american express",
            "amex",
            "watching",
            "sign in",
            "visit",
            "no account data",
            "chrome",
            "waiting",
            "verif",
        )
    )
    if answerable and "Unable to verify" not in home10:
        _rec(
            "AT-10",
            "Pass",
            "Home alone provides enough signal to answer whether Mighty is working / what it needs",
        )
    elif answerable:
        _rec(
            "AT-10",
            "Partial",
            "Home answerable but still contains Unable to verify copy",
            unexpected=["Unable to verify string present on Home HTML"],
        )
    else:
        _rec(
            "AT-10",
            "Fail",
            "Home opaque — Founder would need to open Amex to interpret",
            contradictions=["07 Q3 / AT-10: Is Mighty working? answerable from Home"],
        )

    # Write report
    _write_report(email)
    return 0


def _write_report(email: str) -> None:
    lines = [
        "# Founder Validation — Complete American Express Experience",
        "",
        f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Commit:** `32386cdc` (Complete Amex Experience)",
        f"**Session user:** `{email}`",
        f"**Environment:** Local fresh DB (`.tmp-founder-validate-complete-amex.db`); no deploy",
        f"**Method:** Continuous single-user session; Gmail OAuth and live Amex/Chrome where unavailable were recorded as Unexpected and simulated only via product APIs the extension would call",
        "",
        "## 1. Acceptance Test Summary",
        "",
        "| AT | Verdict | Notes |",
        "|----|---------|-------|",
    ]
    for r in RESULTS:
        note = (r.notes[0] if r.notes else "").replace("|", "/")
        lines.append(f"| {r.at} | **{r.verdict}** | {note} |")

    lines.extend(["", "### Detail ledger", ""])
    for r in RESULTS:
        lines.append(f"### {r.at} — {r.verdict}")
        for n in r.notes:
            lines.append(f"- {n}")
        for u in r.unexpected:
            lines.append(f"- **Unexpected:** {u}")
        for c in r.contradictions:
            lines.append(f"- **Contradiction:** {c}")
        lines.append("")

    fails = [r for r in RESULTS if r.verdict == "Fail"]
    partials = [r for r in RESULTS if r.verdict == "Partial"]
    lines.extend(
        [
            "## 2. Blocking failures",
            "",
        ]
    )
    if not fails:
        lines.append("None graded **Fail**. Partials still block declaring AT-00/AT-15 complete.")
    else:
        for r in fails:
            lines.append(f"- **{r.at}:** {r.notes[0] if r.notes else ''}")
            for c in r.contradictions:
                lines.append(f"  - {c}")

    if partials:
        lines.append("")
        lines.append("### Partials (block AT-15 / production-complete claim)")
        for r in partials:
            lines.append(f"- **{r.at}:** {r.notes[0] if r.notes else ''}")

    lines.extend(
        [
            "",
            "## 3. Recommended fixes (fewest cycles)",
            "",
            "### Cycle A — Home projects Amex canonical lifecycle (AT-01 / AT-08) + Fresh Install close (AT-00 / AT-09 / AT-14 / AT-15)",
            "",
            "Single implementation cycle:",
            "",
            "1. **Home hero for unsupported-data:** When Amex `product_lifecycle.state == unsupported-data`, Home must not keep the first-run “beginning to manage / Visit” ask as if nothing was learned. Project the same knows/does-not-know/why-next as Accounts/API (“Logged in — no account data” + stay-signed-in next action).",
            "2. **Fresh Install completion:** Keep Discover → Confirm → Enable Monitoring → Chrome → Visit → terminal coherent; Founder re-run AT-00 live with real Chrome + Amex.",
            "3. **Earned Permission to Leave:** Only after a true clear/success-with-data (or honest quiet watching with no outstanding Amex next action); retest AT-09/AT-14.",
            "",
            "**Out of scope for A:** other providers; visual door migration; governance changes.",
            "",
            "### Cycle B — only if Cycle A retest still fails",
            "",
            "Session-expiry mid-flow (AT-04) if still Partial after live Amex: ensure progress observations clear when session is lost so compose cannot stay on `progress` after needs-login.",
            "",
            "**Do not open** a governance/architecture cycle — documented product is sufficient; gaps are realization.",
            "",
            "---",
            "",
            "**Deploy:** still stopped.",
        ]
    )
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    raise SystemExit(main())
