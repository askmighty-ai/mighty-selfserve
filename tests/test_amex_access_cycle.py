"""Amex access cycle — verification owns correlated private-data extraction."""

from __future__ import annotations

import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_readiness import CHECKING, READY, UNVERIFIED
from mighty.account_status import UP_TO_DATE, load_all_account_statuses
from mighty.connection_state import (
    advance_amex_to_waiting,
    amex_extension_connected,
    start_amex_connect,
)
from mighty.provider_access_manager import (
    complete_access_check_after_extraction,
    complete_provider_access_check,
    mark_access_check_extracting,
    mark_provider_access_check_running,
    request_provider_access_check,
)
from mighty.provider_access_probe import (
    AUTH_AUTHENTICATED_NO_PRIVATE_DATA,
    AUTH_LOGIN_PAGE,
    ensure_probe_tables,
)
from mighty.provider_session_state import (
    ensure_provider_session_state_tables,
    get_provider_session_state,
)
from mighty.session_verification import (
    ensure_session_verification_tables,
    get_latest_session_verification,
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_amex_cycle.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()
    mighty.app.config["TESTING"] = True
    c = mighty.app.test_client()
    email = f"amex_cycle_{secrets.token_hex(4)}@test.local"
    c.get("/signup")
    with c.session_transaction() as sess:
        csrf = sess["_csrf"]
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    c.email = email
    return c


def _ctx(mighty):
    return dict(
        iso_fn=mighty.iso,
        encrypt_fn=mighty.encrypt_account_data,
        decrypt_fn=mighty.decrypt_account_data,
    )


def _uid(client) -> str:
    with client.session_transaction() as sess:
        return sess["user_id"]


def _api_key(mighty, uid: str) -> str:
    return mighty.get_db().execute(
        "SELECT api_key FROM users WHERE id=?", (uid,),
    ).fetchone()["api_key"]


def _seed_amex_connected(mighty, uid: str) -> None:
    from mighty.provider_access_manager import record_amex_extension_connected

    db = mighty.get_db()
    start_amex_connect(db, uid, **_ctx(mighty))
    advance_amex_to_waiting(db, uid, **_ctx(mighty))
    amex_extension_connected(db, uid, session_verified=True, **_ctx(mighty))
    record_amex_extension_connected(db, uid, observed_at=mighty.iso())


def _probe(auth_state: str = AUTH_AUTHENTICATED_NO_PRIVATE_DATA, **extra) -> dict:
    default_url = (
        "https://www.americanexpress.com/en-us/account/login"
        if auth_state == AUTH_LOGIN_PAGE
        else "https://global.americanexpress.com/overview"
    )
    return {
        "provider": "amex",
        "status": extra.pop("status", "ok"),
        "auth_state": auth_state,
        "url_visited": extra.pop("url_visited", default_url),
        "final_url": extra.pop("final_url", None),
        "signed_in_detected": auth_state == AUTH_AUTHENTICATED_NO_PRIVATE_DATA,
        "private_data_detected": False,
        "evidence_type": "page",
        "evidence_snippet": "test",
        "failure_reason": extra.pop("failure_reason", None),
        "login_form_present": extra.pop(
            "login_form_present",
            auth_state == AUTH_LOGIN_PAGE,
        ),
        "probed_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


def test_authenticated_amex_verification_triggers_extraction_requirement(client):
    """1. Authenticated Amex verification requires extraction (does not complete)."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        ensure_provider_session_state_tables(db)
        ensure_session_verification_tables(db)
        ensure_probe_tables(db)
        _seed_amex_connected(mighty, uid)
        verification = request_provider_access_check(db, uid, "amex")
        assert verification is not None
        mark_provider_access_check_running(db, uid, verification.verification_id)
        result = complete_provider_access_check(
            db,
            uid,
            _probe(),
            verification_id=verification.verification_id,
        )
        assert result["extraction_required"] is True
        assert get_latest_session_verification(db, uid, "amex").lifecycle == "session_verified"


def test_verification_id_reaches_extraction_endpoint(client):
    """2. verification_id / access_cycle_id reaches POST /amex/extract and account_data."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        verification = request_provider_access_check(db, uid, "amex")
        assert verification is not None
        vid = verification.verification_id
        complete_provider_access_check(
            db, uid, _probe(), verification_id=vid,
        )
        api_key = _api_key(mighty, uid)

    r = client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "125,000",
            "verification_id": vid,
            "access_cycle_id": vid,
        },
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["access_cycle_id"] == vid
    assert body["verification_id"] == vid

    with mighty.app.app_context():
        db = mighty.get_db()
        row = db.execute(
            "SELECT data_enc FROM account_data WHERE user_id=? AND source='amex'",
            (uid,),
        ).fetchone()
        data = mighty.decrypt_account_data(uid, row["data_enc"] or "")
        assert data.get("access_cycle_id") == vid
        assert data.get("extraction_access_cycle_id") == vid
        latest = get_latest_session_verification(db, uid, "amex")
        assert latest is not None
        assert latest.lifecycle == "completed"


def test_successful_correlated_extraction_produces_ready(client):
    """3. Successful correlated extraction → readiness ready / Connected."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        complete_provider_access_check(db, uid, _probe(), verification_id=vid)
        api_key = _api_key(mighty, uid)

    assert client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "99,000",
            "verification_id": vid,
            "access_cycle_id": vid,
        },
    ).status_code == 200

    with mighty.app.app_context():
        db = mighty.get_db()
        accounts, _ = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"amex": "Amex"},
            login_url_fn=lambda s: f"https://example.com/{s}",
        )
    amex = next(a for a in accounts if a.source == "amex")
    assert amex.readiness == READY
    assert amex.presentation_label == "Connected"
    assert amex.status == UP_TO_DATE


def test_session_verified_without_extraction_does_not_produce_ready(client):
    """4. Session verified without extraction → not ready."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        verification = request_provider_access_check(db, uid, "amex")
        complete_provider_access_check(
            db, uid, _probe(), verification_id=verification.verification_id,
        )
        pss = get_provider_session_state(db, uid, "amex")
        assert pss is not None and pss.state == "connected"
        accounts, _ = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"amex": "Amex"},
            login_url_fn=lambda s: f"https://example.com/{s}",
        )
    amex = next(a for a in accounts if a.source == "amex")
    assert amex.readiness != READY
    assert amex.presentation_label != "Connected"
    assert amex.readiness == CHECKING



def test_extraction_with_different_cycle_id_does_not_produce_ready(client):
    """5. Extraction correlated to a different cycle id → not ready."""
    import app as mighty
    from mighty.adapters.amex_extraction import apply_amex_membership_rewards_extraction

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        complete_provider_access_check(db, uid, _probe(), verification_id=vid)
        # Persist extraction under a *different* cycle id.
        apply_amex_membership_rewards_extraction(
            db,
            uid,
            "50,000",
            access_cycle_id="other-cycle",
            verification_id="other-cycle",
            **_ctx(mighty),
        )
        complete_access_check_after_extraction(db, uid, vid, success=True)
        accounts, _ = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"amex": "Amex"},
            login_url_fn=lambda s: f"https://example.com/{s}",
        )
    amex = next(a for a in accounts if a.source == "amex")
    assert amex.readiness != READY
    assert amex.presentation_label != "Connected"


def test_extraction_failure_leaves_readiness_unverified(client):
    """6. Extraction failure → readiness unverified; PSS may stay connected."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        complete_provider_access_check(db, uid, _probe(), verification_id=vid)
        api_key = _api_key(mighty, uid)

    r = client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "0",  # invalid MR value
            "verification_id": vid,
            "access_cycle_id": vid,
        },
    )
    assert r.status_code == 400

    with mighty.app.app_context():
        db = mighty.get_db()
        assert get_provider_session_state(db, uid, "amex").state == "connected"
        latest = get_latest_session_verification(db, uid, "amex")
        assert latest.lifecycle == "failed"
        accounts, _ = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"amex": "Amex"},
            login_url_fn=lambda s: f"https://example.com/{s}",
        )
    amex = next(a for a in accounts if a.source == "amex")
    assert amex.readiness == UNVERIFIED
    assert amex.presentation_label != "Connected"


def test_signed_out_verification_skips_extraction(client):
    """7. Signed-out verification completes without extraction; readiness signed_out."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        complete_provider_access_check(
            db,
            uid,
            _probe(auth_state=AUTH_LOGIN_PAGE, failure_reason="login_required"),
            verification_id=vid,
        )
        latest = get_latest_session_verification(db, uid, "amex")
        assert latest.lifecycle == "completed"
        assert get_provider_session_state(db, uid, "amex").state == "signed_out"
        row = db.execute(
            "SELECT data_enc, synced_at FROM account_data WHERE user_id=? AND source='amex'",
            (uid,),
        ).fetchone()
        # No new correlated extraction required for signed_out terminal.
        accounts, _ = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"amex": "Amex"},
            login_url_fn=lambda s: f"https://example.com/{s}",
        )
    amex = next(a for a in accounts if a.source == "amex")
    assert amex.readiness == "signed_out"
    assert amex.presentation_label != "Connected"


def test_timeout_network_failure_does_not_become_signed_out(client):
    """8. Timeout / network failure does not become signed_out."""
    import app as mighty
    from mighty.session_verification import expire_timed_out_verifications

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        before = get_provider_session_state(db, uid, "amex")
        assert before.state == "connected"
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        complete_provider_access_check(
            db,
            uid,
            _probe(auth_state="unknown", status="error", failure_reason="network_issue"),
            verification_id=vid,
        )
        after = get_provider_session_state(db, uid, "amex")
        assert after.state == "connected"
        assert after.observed_at == before.observed_at
        assert get_latest_session_verification(db, uid, "amex").lifecycle == "failed"

        # Timed-out mid-cycle also never implies signed_out.
        v2 = request_provider_access_check(db, uid, "amex", throttle_seconds=0)
        assert v2 is not None
        complete_provider_access_check(
            db, uid, _probe(), verification_id=v2.verification_id,
        )
        row = db.execute(
            "SELECT lifecycle FROM provider_session_verification WHERE verification_id=?",
            (v2.verification_id,),
        ).fetchone()
        assert row["lifecycle"] == "session_verified"
        old = datetime.now(timezone.utc) - timedelta(seconds=120)
        db.execute(
            "UPDATE provider_session_verification SET requested_at=? WHERE verification_id=?",
            (old.isoformat(), v2.verification_id),
        )
        db.commit()
        expire_timed_out_verifications(db, uid)
        row = db.execute(
            "SELECT lifecycle FROM provider_session_verification WHERE verification_id=?",
            (v2.verification_id,),
        ).fetchone()
        assert row["lifecycle"] == "timed_out"
        assert get_provider_session_state(db, uid, "amex").state == "connected"


def test_verification_lifecycle_includes_extracting(client):
    """9. Verification lifecycle includes extracting stage."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        assert get_latest_session_verification(db, uid, "amex").lifecycle == "requested"
        mark_provider_access_check_running(db, uid, vid)
        assert get_latest_session_verification(db, uid, "amex").lifecycle == "running"
        complete_provider_access_check(db, uid, _probe(), verification_id=vid)
        assert get_latest_session_verification(db, uid, "amex").lifecycle == "session_verified"
        mark_access_check_extracting(db, uid, vid)
        assert get_latest_session_verification(db, uid, "amex").lifecycle == "extracting"
        complete_access_check_after_extraction(db, uid, vid, success=True)
        assert get_latest_session_verification(db, uid, "amex").lifecycle == "completed"


def test_customer_surfaces_show_connected_only_after_correlated_extraction(client):
    """10. Dashboard/API show Connected only after correlated extraction succeeds."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        complete_provider_access_check(db, uid, _probe(), verification_id=vid)
        api_key = _api_key(mighty, uid)

    # Before extraction: not Connected.
    before = client.get("/api/account-status").get_json()
    amex_before = next(a for a in before["accounts"] if a["source"] == "amex")
    assert amex_before["status_label"] != "Connected"
    assert amex_before.get("readiness") != READY

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

    after = client.get("/api/account-status").get_json()
    amex_after = next(a for a in after["accounts"] if a["source"] == "amex")
    assert amex_after["readiness"] == READY
    assert amex_after["status_label"] == "Connected"
    assert amex_after["status"] == UP_TO_DATE


def test_no_qualifying_private_data_completes_without_extraction(client):
    """Authenticated cycle + extractor NO_ACCOUNT_DATA → LOGGED_IN_NO_ACCOUNT_DATA."""
    import app as mighty
    from mighty.capability_state import CapabilityState

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        result = complete_provider_access_check(db, uid, _probe(), verification_id=vid)
        assert result.get("extraction_required") is True
        api_key = _api_key(mighty, uid)

    r = client.post(
        "/api/extension/amex/no-qualifying-private-data",
        headers={"X-Mighty-Key": api_key},
        json={
            "verification_id": vid,
            "access_cycle_id": vid,
            "extraction_attempted": True,
            "extraction_status": "NO_ACCOUNT_DATA",
            "extraction_reason": "no_publishable_widgets",
            "observation_counts": {
                "authenticated_private_api_responses": 0,
                "qualifying_dom_observations": 0,
                "candidate_payloads": 0,
                "rejection_reason": "no_publishable_widgets",
            },
        },
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["extraction"] == "no_account_data"
    assert body["capability_hint"] == "logged_in_no_account_data"
    assert body["status"] == "NO_ACCOUNT_DATA"

    with mighty.app.app_context():
        db = mighty.get_db()
        latest = get_latest_session_verification(db, uid, "amex")
        assert latest is not None
        assert latest.lifecycle == "completed"
        assert latest.error_message == "no_qualifying_private_data"
        # No publishable fields — readiness stays unverified.
        accounts, _ = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"amex": "Amex"},
            login_url_fn=lambda s: "",
        )
        amex = next(a for a in accounts if a.source == "amex")
        assert amex.readiness != READY
        assert amex.capability is not None
        assert amex.capability.state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA
        extract = next(s for s in amex.capability.pipeline if s.name == "Extraction")
        # Presentation unchanged: empty account data does not claim extraction PASS.
        assert extract.verdict in ("NOT_RUN", "UNKNOWN", "FAIL")


def test_extraction_diagnostics_correlated_by_access_cycle_id(client, capsys):
    """6–7. Diagnostics correlated by access_cycle_id; no private payloads logged."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        complete_provider_access_check(db, uid, _probe(), verification_id=vid)
        api_key = _api_key(mighty, uid)

    assert client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "125,000",
            "verification_id": vid,
            "access_cycle_id": vid,
            "input_source": "dom",
        },
    ).status_code == 200

    captured = capsys.readouterr().out
    assert f"access_cycle_id={vid}" in captured
    assert "extraction_request_received" in captured
    assert "extraction_result" in captured
    assert "snapshot_result" in captured
    assert "extracted_field_names=points_balance" in captured
    # Never log private values / bodies
    low = captured.lower()
    assert "125,000" not in captured
    assert "cookie" not in low
    assert "authorization" not in low
    assert "bearer" not in low


def test_extension_gates_extraction_on_qualifying_private_data():
    """After auth, extension always attempts extraction (no DOM qualification gate)."""
    from pathlib import Path

    bg = (Path(__file__).resolve().parents[1] / "extension" / "background.js").read_text()
    assert "waitForAmexQualifyingPrivateData" not in bg
    assert "AMEX_PRIVATE_DATA_OBSERVATION_MS" not in bg
    assert "attemptAmexExtractionWithHydrationRetry" in bg
    assert "extractAmexAccountDataPage" in bg
    assert "runAmexExtractionForAccessCycle" in bg
    assert "/api/extension/amex/no-qualifying-private-data" in bg
    assert "attempting extraction" in bg


def test_no_qualifying_endpoint_idempotent_and_rejects_bad_cycles(client):
    """Duplicate no-data POST is idempotent; wrong/completed/missing IDs rejected."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        complete_provider_access_check(db, uid, _probe(), verification_id=vid)
        api_key = _api_key(mighty, uid)

    payload = {
        "verification_id": vid,
        "access_cycle_id": vid,
        "extraction_attempted": True,
        "extraction_reason": "no_publishable_widgets",
        "observation_counts": {
            "authenticated_private_api_responses": 0,
            "qualifying_dom_observations": 0,
            "candidate_payloads": 0,
            "rejection_reason": "no_publishable_widgets",
        },
    }
    first = client.post(
        "/api/extension/amex/no-qualifying-private-data",
        headers={"X-Mighty-Key": api_key},
        json=payload,
    )
    assert first.status_code == 200, first.get_json()
    assert first.get_json().get("idempotent") is False

    second = client.post(
        "/api/extension/amex/no-qualifying-private-data",
        headers={"X-Mighty-Key": api_key},
        json=payload,
    )
    assert second.status_code == 200, second.get_json()
    assert second.get_json().get("idempotent") is True

    missing = client.post(
        "/api/extension/amex/no-qualifying-private-data",
        headers={"X-Mighty-Key": api_key},
        json={"verification_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert missing.status_code == 404

    # Extract against the completed no-data cycle must not write/overwrite.
    extract = client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "125,000",
            "verification_id": vid,
            "access_cycle_id": vid,
        },
    )
    assert extract.status_code == 409
    assert extract.get_json()["error"] == "active_verification_required"
    assert extract.get_json()["reason"] == "cycle_already_terminal"

    with mighty.app.app_context():
        db = mighty.get_db()
        latest = get_latest_session_verification(db, uid, "amex")
        assert latest.lifecycle == "completed"
        assert latest.error_message == "no_qualifying_private_data"
        row = db.execute(
            "SELECT data_enc FROM account_data WHERE user_id=? AND source='amex'",
            (uid,),
        ).fetchone()
        data = mighty.decrypt_account_data(uid, row["data_enc"] or "")
        # Historical/seeded data may exist, but this cycle must not correlate extract.
        assert data.get("access_cycle_id") != vid or data.get("extraction_access_cycle_id") != vid


def test_no_qualifying_rejects_after_successful_extraction(client):
    """Completed successful cycle cannot be overwritten by a late no-data POST."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        complete_provider_access_check(db, uid, _probe(), verification_id=vid)
        api_key = _api_key(mighty, uid)

    assert client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "88,000",
            "verification_id": vid,
            "access_cycle_id": vid,
        },
    ).status_code == 200

    late = client.post(
        "/api/extension/amex/no-qualifying-private-data",
        headers={"X-Mighty-Key": api_key},
        json={"verification_id": vid, "access_cycle_id": vid},
    )
    assert late.status_code == 409
    assert late.get_json()["error"] == "cycle_already_terminal"

    with mighty.app.app_context():
        db = mighty.get_db()
        latest = get_latest_session_verification(db, uid, "amex")
        assert latest.lifecycle == "completed"
        assert latest.error_message != "no_qualifying_private_data"
        row = db.execute(
            "SELECT data_enc FROM account_data WHERE user_id=? AND source='amex'",
            (uid,),
        ).fetchone()
        data = mighty.decrypt_account_data(uid, row["data_enc"] or "")
        assert data.get("access_cycle_id") == vid


def test_extension_private_data_wait_contract():
    """Hydration retry: one delay, cancel on tab close / navigation; extractor owns detection."""
    from pathlib import Path

    bg = (Path(__file__).resolve().parents[1] / "extension" / "background.js").read_text()
    assert "AMEX_HYDRATION_RETRY_DELAY_MS = 1500" in bg
    assert "attemptAmexExtractionWithHydrationRetry" in bg
    assert "hydration retry cancelled — tab closed" in bg
    assert "hydration retry cancelled — left Amex surface" in bg
    assert "hydration retry cancelled — navigation occurred" in bg
    assert "_amexExtractionCyclesStarted" in bg
    # Extractor owns Membership Rewards / balance detection — not a separate gate.
    assert "function extractAmexAccountDataPage" in bg
    assert "Membership Rewards[^0-9\\n]{0,120}([\\d][\\d,]*)" in bg
    # Observation must not gate on private_data_detected.
    auth_branch = bg.split("Amex session verified — attempting extraction")[0]
    assert "waitForAmexQualifyingPrivateData" not in auth_branch
    assert "_isAmexSafeToAttemptExtraction" in bg


def _start_mid_cycle(mighty, uid: str) -> tuple[str, str]:
    """Seed Amex + authenticated mid-cycle; return (vid, api_key)."""
    db = mighty.get_db()
    ensure_provider_session_state_tables(db)
    ensure_session_verification_tables(db)
    ensure_probe_tables(db)
    _seed_amex_connected(mighty, uid)
    verification = request_provider_access_check(
        db, uid, "amex", throttle_seconds=0,
    )
    assert verification is not None
    vid = verification.verification_id
    # Must be a fresh active cycle — never reuse a terminal id under throttle.
    from mighty.session_verification import get_active_session_verification

    active = get_active_session_verification(db, uid, "amex")
    if active is None or active.verification_id != vid:
        verification = request_provider_access_check(
            db, uid, "amex", throttle_seconds=0,
        )
        vid = verification.verification_id
    complete_provider_access_check(db, uid, _probe(), verification_id=vid)
    return vid, _api_key(mighty, uid)


def test_extract_without_cycle_returns_409(client, capsys):
    """extraction without cycle → 409 active_verification_required."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        api_key = _api_key(mighty, uid)

    r = client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={"session_verified": True, "value": "10,000"},
    )
    assert r.status_code == 409
    body = r.get_json()
    assert body["error"] == "active_verification_required"
    assert "ARCHITECTURE VIOLATION: uncorrelated extraction" in capsys.readouterr().out

    with mighty.app.app_context():
        db = mighty.get_db()
        row = db.execute(
            "SELECT data_enc FROM account_data WHERE user_id=? AND source='amex'",
            (uid,),
        ).fetchone()
        data = mighty.decrypt_account_data(uid, row["data_enc"] or "")
        assert data.get("access_cycle_id") in (None, "")
        assert not (data.get("items") or [])


def test_extract_with_wrong_cycle_returns_409(client):
    """extraction with wrong / unknown cycle → 409."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        vid, api_key = _start_mid_cycle(mighty, uid)

    r = client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "10,000",
            "verification_id": "00000000-0000-0000-0000-000000000099",
            "access_cycle_id": "00000000-0000-0000-0000-000000000099",
        },
    )
    assert r.status_code == 409
    assert r.get_json()["error"] == "active_verification_required"

    mismatched = client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "10,000",
            "verification_id": vid,
            "access_cycle_id": "other-cycle-id",
        },
    )
    assert mismatched.status_code == 409
    assert mismatched.get_json()["error"] == "active_verification_required"
    assert mismatched.get_json()["reason"] == "cycle_id_mismatch"


def test_extract_after_terminal_cycle_returns_409(client):
    """extraction after terminal cycle → 409."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        vid, api_key = _start_mid_cycle(mighty, uid)

    assert client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "12,000",
            "verification_id": vid,
            "access_cycle_id": vid,
        },
    ).status_code == 200

    late = client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "99,000",
            "verification_id": vid,
            "access_cycle_id": vid,
        },
    )
    assert late.status_code == 409
    assert late.get_json()["error"] == "active_verification_required"
    assert late.get_json()["reason"] == "cycle_already_terminal"


def test_extract_before_authenticated_rejected(client):
    """requested/running cycles cannot accept extraction."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        assert get_latest_session_verification(db, uid, "amex").lifecycle == "requested"
        api_key = _api_key(mighty, uid)

    r = client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "10,000",
            "verification_id": vid,
            "access_cycle_id": vid,
        },
    )
    assert r.status_code == 409
    assert r.get_json()["error"] == "active_verification_required"
    assert r.get_json()["reason"] == "cycle_not_extractable"


def test_late_extract_after_timeout_rejected(client):
    """late extraction after timeout rejected."""
    import app as mighty
    from mighty.session_verification import expire_timed_out_verifications

    uid = _uid(client)
    with mighty.app.app_context():
        vid, api_key = _start_mid_cycle(mighty, uid)
        db = mighty.get_db()
        old = datetime.now(timezone.utc) - timedelta(seconds=120)
        db.execute(
            "UPDATE provider_session_verification SET requested_at=? WHERE verification_id=?",
            (old.isoformat(), vid),
        )
        db.commit()
        expire_timed_out_verifications(db, uid)
        assert get_latest_session_verification(db, uid, "amex").lifecycle == "timed_out"

    r = client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "10,000",
            "verification_id": vid,
            "access_cycle_id": vid,
        },
    )
    assert r.status_code == 409
    assert r.get_json()["error"] == "active_verification_required"


def test_duplicate_extract_same_cycle_rejected(client):
    """duplicate extraction same cycle rejected (cycle already terminal)."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        vid, api_key = _start_mid_cycle(mighty, uid)

    payload = {
        "session_verified": True,
        "value": "44,000",
        "verification_id": vid,
        "access_cycle_id": vid,
    }
    assert client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json=payload,
    ).status_code == 200
    dup = client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json=payload,
    )
    assert dup.status_code == 409
    assert dup.get_json()["error"] == "active_verification_required"


def test_successful_extract_always_has_non_null_cycle_ids(client):
    """every successful extraction has non-null verification_id and access_cycle_id."""
    import app as mighty
    from mighty.account_snapshot import get_latest_successful_snapshot

    uid = _uid(client)
    with mighty.app.app_context():
        vid, api_key = _start_mid_cycle(mighty, uid)

    r = client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "77,000",
            "verification_id": vid,
            "access_cycle_id": vid,
        },
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["verification_id"] == vid
    assert body["access_cycle_id"] == vid
    assert body["verification_id"] is not None
    assert body["access_cycle_id"] is not None

    with mighty.app.app_context():
        db = mighty.get_db()
        row = db.execute(
            "SELECT data_enc FROM account_data WHERE user_id=? AND source='amex'",
            (uid,),
        ).fetchone()
        data = mighty.decrypt_account_data(uid, row["data_enc"] or "")
        assert data.get("access_cycle_id") == vid
        assert data.get("extraction_access_cycle_id") == vid
        snap = get_latest_successful_snapshot(db, uid, "amex")
        assert snap is not None
        assert snap.access_cycle_id == vid


def test_snapshot_cannot_be_written_without_correlated_cycle(client):
    """snapshot cannot be written without correlated cycle."""
    import app as mighty
    from mighty.account_snapshot import create_account_snapshot_from_extraction

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        refused = create_account_snapshot_from_extraction(
            db,
            user_id=uid,
            provider="amex",
            fields=[{
                "key": "points_balance",
                "label": "Membership Rewards Points",
                "value": "1,000",
                "_type": "points_balance",
            }],
            verified_at=mighty.iso(),
            access_cycle_id=None,
        )
        assert refused is None


def test_published_state_references_same_cycle(client):
    """published customer state always references the same cycle."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        vid, api_key = _start_mid_cycle(mighty, uid)

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

    after = client.get("/api/account-status").get_json()
    amex = next(a for a in after["accounts"] if a["source"] == "amex")
    assert amex["readiness"] == READY
    assert amex["status_label"] == "Connected"

    with mighty.app.app_context():
        db = mighty.get_db()
        row = db.execute(
            "SELECT data_enc FROM account_data WHERE user_id=? AND source='amex'",
            (uid,),
        ).fetchone()
        data = mighty.decrypt_account_data(uid, row["data_enc"] or "")
        assert data.get("access_cycle_id") == vid
        assert data.get("extraction_access_cycle_id") == vid
        latest = get_latest_session_verification(db, uid, "amex")
        assert latest.verification_id == vid
        assert latest.lifecycle == "completed"


def test_passive_content_script_cannot_post_without_active_cycle():
    """passive content script cannot POST after login unless verification created the cycle."""
    from pathlib import Path

    bg = (Path(__file__).resolve().parents[1] / "extension" / "background.js").read_text()
    assert "content extract ignored — no active verification cycle" in bg
    assert "refuse extract POST — no active verification/access cycle" in bg
    # AMEX_MR_EXTRACTED must attach active cycle ids — never bare _pushAmexExtraction.
    handler = bg.split("if (msg.type === 'AMEX_MR_EXTRACTED')")[1].split(
        "if (msg.action === 'sync_now')"
    )[0]
    assert "_activeSessionVerificationId" in handler
    assert "_sessionVerificationInProgress" in handler
    assert "verificationId: vid" in handler
    assert "_pushAmexExtraction(api_key, msg.value, 'content-script')" not in handler
    push = bg.split("async function _pushAmexExtraction")[1].split(
        "async function extractAmexRewardsInTab"
    )[0]
    assert "verification_id: verificationId" in push
    assert "access_cycle_id: accessCycleId" in push
    assert "if (!verificationId || !accessCycleId)" in push


def test_cycle_diagnostics_emit_required_milestones(client, capsys):
    """Once-per-cycle diagnostics include verification_id and access_cycle_id."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        vid, api_key = _start_mid_cycle(mighty, uid)

    assert client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "125,000",
            "verification_id": vid,
            "access_cycle_id": vid,
            "input_source": "dom",
        },
    ).status_code == 200

    captured = capsys.readouterr().out
    for event in (
        "verification started",
        "verification authenticated",
        "extraction dispatched",
        "extraction accepted",
        "snapshot written",
        "customer published",
        "verification completed",
    ):
        assert f"event={event}" in captured, event
        assert f"verification_id={vid}" in captured
        assert f"access_cycle_id={vid}" in captured
        assert "verification_state=" in captured
        assert "cycle_age_ms=" in captured


def test_only_one_active_verification_exists(client):
    """Exactly one active verification per user/provider."""
    import app as mighty
    from mighty.session_verification import (
        count_active_session_verifications,
        get_active_session_verification,
        request_session_verification,
    )

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        first = request_provider_access_check(db, uid, "amex")
        second = request_provider_access_check(db, uid, "amex")
        third = request_session_verification(db, uid, "amex", throttle_seconds=0)
        assert first is not None and second is not None and third is not None
        assert first.verification_id == second.verification_id == third.verification_id
        assert count_active_session_verifications(db, uid, "amex") == 1
        active = get_active_session_verification(db, uid, "amex")
        assert active is not None
        assert active.verification_id == first.verification_id


def test_duplicate_starts_reuse_same_verification(client):
    """dashboard refresh / repeated clicks reuse the same verification_id."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        ids = [
            request_provider_access_check(db, uid, "amex").verification_id
            for _ in range(5)
        ]
        assert len(set(ids)) == 1
        n = db.execute(
            """
            SELECT COUNT(*) AS n FROM provider_session_verification
            WHERE user_id=? AND provider='amex'
              AND lifecycle IN ('requested','running','session_verified','extracting')
            """,
            (uid,),
        ).fetchone()["n"]
        assert n == 1


def test_timeout_clears_active_pointer_server(client):
    """timeout clears active verification (no active remains)."""
    import app as mighty
    from mighty.session_verification import (
        count_active_session_verifications,
        expire_timed_out_verifications,
        get_active_session_verification,
    )

    uid = _uid(client)
    with mighty.app.app_context():
        vid, _api = _start_mid_cycle(mighty, uid)
        db = mighty.get_db()
        assert get_active_session_verification(db, uid, "amex") is not None
        old = datetime.now(timezone.utc) - timedelta(seconds=120)
        db.execute(
            "UPDATE provider_session_verification SET requested_at=? WHERE verification_id=?",
            (old.isoformat(), vid),
        )
        db.commit()
        expire_timed_out_verifications(db, uid)
        assert get_active_session_verification(db, uid, "amex") is None
        assert count_active_session_verifications(db, uid, "amex") == 0
        assert get_latest_session_verification(db, uid, "amex").lifecycle == "timed_out"


def test_signed_out_clears_active_verification(client):
    """signed_out terminal clears active verification."""
    import app as mighty
    from mighty.session_verification import get_active_session_verification

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        complete_provider_access_check(
            db,
            uid,
            _probe(auth_state=AUTH_LOGIN_PAGE, failure_reason="login_required"),
            verification_id=vid,
        )
        assert get_active_session_verification(db, uid, "amex") is None
        assert get_latest_session_verification(db, uid, "amex").terminal_reason == "signed_out"


def test_authenticated_completion_clears_active_verification(client):
    """authenticated completion clears active verification."""
    import app as mighty
    from mighty.session_verification import get_active_session_verification

    uid = _uid(client)
    with mighty.app.app_context():
        vid, api_key = _start_mid_cycle(mighty, uid)
        assert get_active_session_verification(mighty.get_db(), uid, "amex") is not None

    assert client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "33,000",
            "verification_id": vid,
            "access_cycle_id": vid,
        },
    ).status_code == 200

    with mighty.app.app_context():
        assert get_active_session_verification(mighty.get_db(), uid, "amex") is None
        latest = get_latest_session_verification(mighty.get_db(), uid, "amex")
        assert latest.lifecycle == "completed"
        assert latest.terminal_reason == "authenticated"


def test_cancelled_clears_active_verification(client):
    """cancelled clears active verification."""
    import app as mighty
    from mighty.provider_access_manager import finish_provider_access_check
    from mighty.session_verification import get_active_session_verification

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        mark_provider_access_check_running(db, uid, vid)
        finish_provider_access_check(
            db,
            uid,
            vid,
            lifecycle="failed",
            error_message="verification cancelled — tab closed",
            terminal_reason="cancelled",
            terminal_source="extension_tab_closed",
        )
        assert get_active_session_verification(db, uid, "amex") is None
        assert get_latest_session_verification(db, uid, "amex").terminal_reason == "cancelled"


def test_navigation_failure_clears_active_verification(client):
    """navigation failure clears active verification."""
    import app as mighty
    from mighty.provider_access_manager import fail_provider_access_check
    from mighty.session_verification import get_active_session_verification

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_connected(mighty, uid)
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        mark_provider_access_check_running(db, uid, vid)
        fail_provider_access_check(
            db,
            uid,
            error_message="probe_navigation_error",
            verification_id=vid,
            terminal_reason="navigation_failed",
            terminal_source="extension_navigation",
        )
        assert get_active_session_verification(db, uid, "amex") is None
        assert (
            get_latest_session_verification(db, uid, "amex").terminal_reason
            == "navigation_failed"
        )


def test_extension_reload_clears_active_verification(client):
    """extension reload cancels active cycles and forces a clean verification."""
    import app as mighty
    from mighty.session_verification import get_active_session_verification

    uid = _uid(client)
    with mighty.app.app_context():
        vid, api_key = _start_mid_cycle(mighty, uid)
        assert get_active_session_verification(mighty.get_db(), uid, "amex") is not None

    r = client.post(
        "/api/extension/session-verification/reset-on-reload",
        headers={"X-Mighty-Key": api_key},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["count"] == 1
    assert vid in body["cancelled"]

    with mighty.app.app_context():
        db = mighty.get_db()
        assert get_active_session_verification(db, uid, "amex") is None
        latest = get_latest_session_verification(db, uid, "amex")
        assert latest.lifecycle == "failed"
        assert latest.terminal_source == "extension_reload"

    # A new start after reload creates a fresh verification_id.
    with mighty.app.app_context():
        db = mighty.get_db()
        fresh = request_provider_access_check(db, uid, "amex", throttle_seconds=0)
        assert fresh is not None
        assert fresh.verification_id != vid


def test_second_extraction_cannot_attach_to_older_verification(client):
    """second extraction cannot attach to an older verification."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        old_vid, api_key = _start_mid_cycle(mighty, uid)

    assert client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "11,000",
            "verification_id": old_vid,
            "access_cycle_id": old_vid,
        },
    ).status_code == 200

    with mighty.app.app_context():
        db = mighty.get_db()
        new_vid, api_key = _start_mid_cycle(mighty, uid)
        assert new_vid != old_vid

    late_old = client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "22,000",
            "verification_id": old_vid,
            "access_cycle_id": old_vid,
        },
    )
    assert late_old.status_code == 409
    assert late_old.get_json()["error"] == "active_verification_required"

    ok = client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "22,000",
            "verification_id": new_vid,
            "access_cycle_id": new_vid,
        },
    )
    assert ok.status_code == 200
    body = ok.get_json()
    assert body["verification_id"] == new_vid
    assert body["access_cycle_id"] == new_vid


def test_extension_active_pointer_fsm_contract():
    """Extension active pointer clears on terminal/reload; never resurrects stale ids."""
    from pathlib import Path

    bg = (Path(__file__).resolve().parents[1] / "extension" / "background.js").read_text()
    assert "function _clearActiveSessionVerification" in bg
    assert "function _resetVerificationsAfterExtensionReload" in bg
    assert "/api/extension/session-verification/reset-on-reload" in bg
    assert "_clearActiveSessionVerification('tab_closed')" in bg
    assert "_clearActiveSessionVerification('verification_finished')" in bg
    assert "_resetVerificationsAfterExtensionReload" in bg
    # onInstalled / onStartup must force clean verification.
    assert "_resetVerificationsAfterExtensionReload(`install:" in bg
    assert "_resetVerificationsAfterExtensionReload('browser_startup')" in bg
    # runSessionVerification finally clears via the FSM helper.
    run_fn = bg.split("async function runSessionVerification(")[1].split(
        "function _isAmexAccessCycleAuthenticated"
    )[0]
    assert "_clearActiveSessionVerification('verification_finished')" in run_fn
