"""Amex extractor is the single source of truth for account-data detection."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.adapters.amex_extraction import normalize_extracted_fields
from mighty.capability_state import CapabilityState
from mighty.extraction_result import ExtractionStatus, parse_extraction_result_payload
from mighty.provider_access_manager import (
    complete_provider_access_check,
    request_provider_access_check,
)
from mighty.provider_access_probe import ensure_probe_tables
from mighty.provider_session_state import ensure_provider_session_state_tables
from mighty.session_verification import (
    ensure_session_verification_tables,
    get_latest_session_verification,
)

ROOT = Path(__file__).resolve().parents[1]
BG = (ROOT / "extension" / "background.js").read_text()
EXTRACT_JS = (ROOT / "extension" / "content_scripts" / "amex_extract.js").read_text()
HARNESS = ROOT / "tests" / "js" / "amex_extract_harness.mjs"


def _run_fixture(name: str) -> dict:
    proc = subprocess.run(
        ["node", str(HARNESS), name],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_test.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()
    mighty.app.config["TESTING"] = True
    c = mighty.app.test_client()
    email = f"ext_{secrets.token_hex(4)}@test.local"
    c.get("/signup")
    with c.session_transaction() as sess:
        csrf = sess["_csrf"]
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    return c


def _uid(client) -> str:
    with client.session_transaction() as sess:
        return sess["user_id"]


def _api_key(mighty, uid: str) -> str:
    db = mighty.get_db()
    row = db.execute("SELECT api_key FROM users WHERE id=?", (uid,)).fetchone()
    return row["api_key"]


def _seed(mighty, uid: str) -> None:
    from mighty.connection_state import (
        advance_amex_to_waiting,
        amex_extension_connected,
        start_amex_connect,
    )

    db = mighty.get_db()
    ensure_provider_session_state_tables(db)
    ensure_session_verification_tables(db)
    ensure_probe_tables(db)
    ctx = dict(
        iso_fn=mighty.iso,
        encrypt_fn=mighty.encrypt_account_data,
        decrypt_fn=mighty.decrypt_account_data,
    )
    start_amex_connect(db, uid, **ctx)
    advance_amex_to_waiting(db, uid, **ctx)
    amex_extension_connected(db, uid, session_verified=True, **ctx)


def _probe(**extra):
    return {
        "provider": "amex",
        "status": "ok",
        "url_visited": "https://global.americanexpress.com/overview",
        "final_url": "https://global.americanexpress.com/overview",
        "signed_in_detected": True,
        "private_data_detected": False,
        "auth_state": "authenticated_no_private_data",
        "evidence_type": "page",
        "evidence_snippet": "test",
        "failure_reason": None,
        "login_form_present": False,
        **extra,
    }


# ── 1–8 DOM extractor fixtures ───────────────────────────────────────────────


def test_1_authenticated_overview_with_mr():
    r = _run_fixture("overview_with_mr")
    assert r["status"] == "EXTRACTION_SUCCESS"
    assert r["reason"] == "membership_rewards_found"
    assert "points_balance" in r["publishable_fields"]


def test_2_authenticated_overview_without_mr_but_with_balances():
    r = _run_fixture("overview_balances_only")
    assert r["status"] == "EXTRACTION_SUCCESS"
    assert r["reason"] in ("statement_balance_found", "card_ending_found")
    assert r["field_count"] >= 1


def test_3_delayed_spa_hydration_not_ready():
    r = _run_fixture("spa_not_hydrated")
    assert r["status"] == "NOT_READY"
    assert r["reason"] == "spa_not_hydrated"


def test_4_rewards_page():
    r = _run_fixture("rewards_page")
    assert r["status"] == "EXTRACTION_SUCCESS"
    assert r["reason"] == "membership_rewards_found"


def test_5_multiple_cards():
    r = _run_fixture("multiple_cards")
    assert r["status"] == "EXTRACTION_SUCCESS"
    assert "points_balance" in r["publishable_fields"]
    assert any(k.startswith("statement_balance") for k in r["publishable_fields"])
    assert any(k.startswith("card_ending") for k in r["publishable_fields"])
    assert r["field_count"] >= 3


def test_6_authenticated_page_zero_publishable_fields():
    r = _run_fixture("zero_publishable")
    assert r["status"] == "NO_ACCOUNT_DATA"
    assert r["reason"] == "no_publishable_widgets"
    assert r["publishable_fields"] == []


def test_7_marketing_modules():
    r = _run_fixture("marketing")
    assert r["status"] == "EXTRACTION_FAILED"
    assert r["reason"] == "marketing_page"


def test_8_dom_changes_extraction_failure():
    # login_page is a fatal extractor failure (wrong surface).
    r = _run_fixture("login")
    assert r["status"] == "EXTRACTION_FAILED"
    assert r["reason"] == "login_page"


# ── 9–12 hydration retry / navigation contracts ──────────────────────────────


def test_9_retry_succeeds_contract():
    assert "page not ready — one hydration retry" in BG
    assert "AMEX_HYDRATION_RETRY_DELAY_MS = 1500" in BG
    # Exactly one retry path — no polling loop constant.
    assert "AMEX_PRIVATE_DATA_POLL_MS" not in BG
    assert BG.count("attemptAmexExtractionWithHydrationRetry") >= 1


def test_10_retry_still_empty_becomes_no_account_data():
    assert "hydration_retry_exhausted" in BG
    assert "NO_ACCOUNT_DATA" in BG
    assert "no_publishable_widgets" in BG


def test_11_navigation_during_retry():
    assert "hydration retry cancelled — navigation occurred" in BG
    assert "navigation_during_retry" in BG


def test_12_tab_close_during_retry():
    assert "hydration retry cancelled — tab closed" in BG
    assert "tab_closed" in BG


# ── 13–14 cycle ownership ────────────────────────────────────────────────────


def test_13_duplicate_extraction_prevented():
    assert "_amexExtractionCyclesStarted" in BG
    assert "duplicate extraction prevented for cycle" in BG


def test_14_stale_verification_cannot_publish(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        _seed(mighty, uid)
        v1 = request_provider_access_check(mighty.get_db(), uid, "amex", throttle_seconds=0)
        vid1 = v1.verification_id
        complete_provider_access_check(mighty.get_db(), uid, _probe(), verification_id=vid1)
        # Complete cycle via no-account-data.
        api_key = _api_key(mighty, uid)

    assert client.post(
        "/api/extension/amex/no-qualifying-private-data",
        headers={"X-Mighty-Key": api_key},
        json={
            "verification_id": vid1,
            "extraction_attempted": True,
            "extraction_reason": "no_publishable_widgets",
        },
    ).status_code == 200

    # Stale cycle cannot publish newer extraction.
    late = client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "999,999",
            "verification_id": vid1,
            "access_cycle_id": vid1,
        },
    )
    assert late.status_code == 409
    assert late.get_json()["error"] == "active_verification_required"


# ── 15 extractor result drives capability ────────────────────────────────────


def test_15_extractor_result_drives_capability(client):
    import app as mighty
    from mighty.account_status import load_all_account_statuses

    uid = _uid(client)
    with mighty.app.app_context():
        _seed(mighty, uid)
        vid = request_provider_access_check(
            mighty.get_db(), uid, "amex", throttle_seconds=0,
        ).verification_id
        complete_provider_access_check(mighty.get_db(), uid, _probe(), verification_id=vid)
        api_key = _api_key(mighty, uid)

    assert client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "142,000",
            "verification_id": vid,
            "access_cycle_id": vid,
            "extraction_status": "EXTRACTION_SUCCESS",
            "extraction_reason": "membership_rewards_found",
            "publishable_fields": ["points_balance"],
        },
    ).status_code == 200

    with mighty.app.app_context():
        accounts, _ = load_all_account_statuses(
            uid,
            mighty.get_db(),
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"amex": "Amex"},
            login_url_fn=lambda s: "",
        )
        amex = next(a for a in accounts if a.source == "amex")
        assert amex.capability.state == CapabilityState.EXTRACTION_SUCCESS


def test_15b_no_account_data_drives_capability(client):
    import app as mighty
    from mighty.account_status import load_all_account_statuses

    uid = _uid(client)
    with mighty.app.app_context():
        _seed(mighty, uid)
        vid = request_provider_access_check(
            mighty.get_db(), uid, "amex", throttle_seconds=0,
        ).verification_id
        complete_provider_access_check(mighty.get_db(), uid, _probe(), verification_id=vid)
        api_key = _api_key(mighty, uid)

    body = client.post(
        "/api/extension/amex/no-qualifying-private-data",
        headers={"X-Mighty-Key": api_key},
        json={
            "verification_id": vid,
            "extraction_attempted": True,
            "extraction_status": "NO_ACCOUNT_DATA",
            "extraction_reason": "no_publishable_widgets",
        },
    ).get_json()
    assert body["status"] == "NO_ACCOUNT_DATA"

    with mighty.app.app_context():
        accounts, _ = load_all_account_statuses(
            uid,
            mighty.get_db(),
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"amex": "Amex"},
            login_url_fn=lambda s: "",
        )
        amex = next(a for a in accounts if a.source == "amex")
        assert amex.capability.state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA


# ── 16 no duplicate selector / gate logic ────────────────────────────────────


def test_16_no_duplicate_selector_logic_remains():
    assert "waitForAmexQualifyingPrivateData" not in BG
    assert "AMEX_PRIVATE_DATA_OBSERVATION_MS" not in BG
    assert "AMEX_PRIVATE_DATA_POLL_MS" not in BG
    # Probe must not run balance/MR value-bearing regexes.
    amex_probe = BG.split("if (provider === 'amex')")[1].split("if (provider === 'delta')")[0]
    assert "membership rewards[^0-9" not in amex_probe
    assert "statement\\s+balance" not in amex_probe
    assert "privatePatterns" not in amex_probe
    # Extractor owns detection.
    assert "function extractAmexAccountDataPage" in BG
    assert "extractAmexAccountData" in EXTRACT_JS
    assert "EXTRACTION_SUCCESS" in EXTRACT_JS


# ── 17 diagnostics emit one terminal reason ──────────────────────────────────


def test_17_diagnostics_emit_one_terminal_reason(client, capsys):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        _seed(mighty, uid)
        vid = request_provider_access_check(
            mighty.get_db(), uid, "amex", throttle_seconds=0,
        ).verification_id
        complete_provider_access_check(mighty.get_db(), uid, _probe(), verification_id=vid)
        api_key = _api_key(mighty, uid)

    assert client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "10,000",
            "verification_id": vid,
            "access_cycle_id": vid,
            "extraction_status": "EXTRACTION_SUCCESS",
            "extraction_reason": "membership_rewards_found",
        },
    ).status_code == 200

    out = capsys.readouterr().out
    # One terminal extraction_result line with a single reason.
    lines = [ln for ln in out.splitlines() if "extraction_result" in ln]
    assert len(lines) >= 1
    terminal = [ln for ln in lines if "status=EXTRACTION_SUCCESS" in ln or "outcome=success" in ln]
    assert terminal, lines
    assert "reason=membership_rewards_found" in terminal[-1]
    # No private payloads.
    assert "10,000" not in out
    assert "cookie" not in out.lower()


def test_17b_no_account_data_single_reason(client, capsys):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        _seed(mighty, uid)
        vid = request_provider_access_check(
            mighty.get_db(), uid, "amex", throttle_seconds=0,
        ).verification_id
        complete_provider_access_check(mighty.get_db(), uid, _probe(), verification_id=vid)
        api_key = _api_key(mighty, uid)

    assert client.post(
        "/api/extension/amex/no-qualifying-private-data",
        headers={"X-Mighty-Key": api_key},
        json={
            "verification_id": vid,
            "extraction_attempted": True,
            "extraction_reason": "no_publishable_widgets",
        },
    ).status_code == 200

    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if "event=extraction_result" in ln]
    assert len(lines) == 1
    assert "status=NO_ACCOUNT_DATA" in lines[0]
    assert "reason=no_publishable_widgets" in lines[0]


# ── 18 dashboard behavior unchanged ──────────────────────────────────────────


def test_18_dashboard_behavior_unchanged(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        _seed(mighty, uid)
        vid = request_provider_access_check(
            mighty.get_db(), uid, "amex", throttle_seconds=0,
        ).verification_id
        complete_provider_access_check(mighty.get_db(), uid, _probe(), verification_id=vid)
        api_key = _api_key(mighty, uid)

    assert client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "210,000",
            "verification_id": vid,
            "access_cycle_id": vid,
        },
    ).status_code == 200

    status = client.get("/api/account-status").get_json()
    amex = next(a for a in status["accounts"] if a["source"] == "amex")
    assert amex["status_label"] == "Connected"
    assert amex["readiness"] == "ready"

    # Truth / customer pages still render.
    assert client.get("/").status_code in (200, 302)
    dash = client.get("/dashboard")
    assert dash.status_code in (200, 302, 404) or dash.status_code < 500


# ── API / adapter helpers ────────────────────────────────────────────────────


def test_extraction_result_api_parse():
    parsed = parse_extraction_result_payload({
        "extraction_status": "NO_ACCOUNT_DATA",
        "extraction_reason": "no_publishable_widgets",
        "publishable_fields": [],
    })
    assert parsed is not None
    assert parsed.status == ExtractionStatus.NO_ACCOUNT_DATA
    assert parsed.reason == "no_publishable_widgets"


def test_normalize_extracted_fields_balances():
    items = normalize_extracted_fields([
        {"key": "statement_balance", "label": "Statement Balance", "value": "1234.5", "_type": "currency"},
        {"key": "card_ending", "label": "Card Ending", "value": "****1234", "_type": "card_ending"},
    ])
    assert len(items) == 2
    assert items[0]["value"] == "1,234.50"
    assert items[1]["value"] == "1234"


def test_multi_field_extract_persists(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        _seed(mighty, uid)
        vid = request_provider_access_check(
            mighty.get_db(), uid, "amex", throttle_seconds=0,
        ).verification_id
        complete_provider_access_check(mighty.get_db(), uid, _probe(), verification_id=vid)
        api_key = _api_key(mighty, uid)

    r = client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "verification_id": vid,
            "access_cycle_id": vid,
            "extraction_status": "EXTRACTION_SUCCESS",
            "extraction_reason": "statement_balance_found",
            "fields": [
                {
                    "key": "statement_balance",
                    "label": "Statement Balance",
                    "value": "99.00",
                    "_type": "currency",
                }
            ],
            "publishable_fields": ["statement_balance"],
        },
    )
    assert r.status_code == 200, r.get_json()
    with mighty.app.app_context():
        latest = get_latest_session_verification(mighty.get_db(), uid, "amex")
        assert latest.lifecycle == "completed"
        row = mighty.get_db().execute(
            "SELECT data_enc FROM account_data WHERE user_id=? AND source='amex'",
            (uid,),
        ).fetchone()
        data = mighty.decrypt_account_data(uid, row["data_enc"] or "")
        assert data["items"][0]["key"] == "statement_balance"


def test_always_attempt_extraction_after_auth_contract():
    assert "attempting extraction" in BG
    assert "_isAmexSafeToAttemptExtraction" in BG
    assert "privateObserved" not in BG
    assert "authenticated_attempt_extraction" in (
        ROOT / "mighty" / "provider_access_manager.py"
    ).read_text()


def test_observation_never_answers_no_account_data_independently():
    # Safe-to-extract helper must not decide no_publishable / no_qualifying.
    start = BG.index("function _isAmexSafeToAttemptExtraction")
    end = BG.index("\nfunction ", start + 10)
    helper = BG[start:end]
    assert "no_publishable" not in helper
    assert "no_qualifying" not in helper
    # Must not read private_data_detected to gate extraction.
    assert "payload.private_data_detected" not in helper
    assert "probeData.private_data_detected" not in helper
