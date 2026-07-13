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
    """Authenticated cycle + no qualifying private data → extraction NOT RUN."""
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
            "observation_counts": {
                "authenticated_private_api_responses": 0,
                "qualifying_dom_observations": 0,
                "candidate_payloads": 0,
                "rejection_reason": "no_qualifying_private_data",
            },
        },
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["extraction"] == "not_run"
    assert body["capability_hint"] == "logged_in_no_account_data"

    with mighty.app.app_context():
        db = mighty.get_db()
        latest = get_latest_session_verification(db, uid, "amex")
        assert latest is not None
        assert latest.lifecycle == "completed"
        assert latest.error_message == "no_qualifying_private_data"
        # No new correlated extraction — readiness stays unverified.
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
        assert extract.verdict == "NOT_RUN"


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
    """Extension waits for qualifying private data before same-cycle extract."""
    from pathlib import Path

    bg = (Path(__file__).resolve().parents[1] / "extension" / "background.js").read_text()
    assert "waitForAmexQualifyingPrivateData" in bg
    assert "AMEX_PRIVATE_DATA_OBSERVATION_MS" in bg
    assert "/api/extension/amex/no-qualifying-private-data" in bg
    assert "extraction NOT RUN" in bg


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
        "observation_counts": {
            "authenticated_private_api_responses": 0,
            "qualifying_dom_observations": 0,
            "candidate_payloads": 0,
            "rejection_reason": "no_qualifying_private_data",
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
    assert extract.get_json()["error"] == "cycle_already_terminal"

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
    """Bounded wait: hard deadline, tab-close cancel, DOM-only qualifying rules."""
    from pathlib import Path

    bg = (Path(__file__).resolve().parents[1] / "extension" / "background.js").read_text()
    assert "AMEX_PRIVATE_DATA_OBSERVATION_MS = 20000" in bg
    assert "AMEX_PRIVATE_DATA_POLL_MS = 1000" in bg
    assert "waitForAmexQualifyingPrivateData" in bg
    assert "private-data wait cancelled — tab closed" in bg
    assert "private-data wait cancelled — left Amex surface" in bg
    assert "Date.now() + Math.max(0, timeoutMs)" in bg
    # Qualifying = value-bearing DOM patterns, not session API alone / static chrome.
    assert "membership rewards[^0-9\\n]{0,120}([\\d][\\d,]*)" in bg
    assert "session_api" not in bg.split("waitForAmexQualifyingPrivateData")[1].split(
        "async function _postAmexNoQualifyingPrivateData"
    )[0]
    # Gate uses current-cycle probe private_data_detected, not prior-cycle cache.
    assert "payload.private_data_detected" in bg
    assert "probeData.private_data_detected" in bg
