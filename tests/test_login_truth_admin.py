"""Tests for Login Truth / Current Account Access diagnostic model and admin page."""

import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.adapters.amex_extraction import apply_amex_membership_rewards_extraction
from mighty.connection_state import advance_amex_to_waiting, amex_extension_connected, start_amex_connect
from mighty.login_truth import (
    AccessStateRow,
    CurrentAccountAccess,
    LoginTruthRow,
    TruthObservation,
    access_state_summary,
    compute_current_account_access_rows,
    compute_login_truth_rows,
    current_account_access_summary,
    format_access_state_display_row,
    format_access_state_label,
    format_cached_data_label,
    format_current_access_label,
    format_current_account_access_display_row,
    format_current_winner_line,
    format_login_truth_display_row,
    format_status_label,
    friendly_source_label,
    gather_session_evidence_timeline,
    login_truth_summary,
    resolve_access_state,
    resolve_current_account_access,
    resolve_login_truth,
    sort_access_state_rows,
    sort_current_account_access_rows,
    sort_login_truth_rows,
)
from mighty.provider_access_probe import (
    AUTH_LOGIN_PAGE,
    AUTH_PRIVATE_DATA_VISIBLE,
    record_probe_run,
)
from mighty.provider_session_state import (
    ProviderSessionState,
    SessionEvidence,
    derive_session_evidence_from_probe,
    get_provider_session_state,
    should_replace_session_evidence,
    upsert_provider_session_state,
)


def _iso_hours_ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _iso_minutes_ago(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _iso_seconds_ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_login_truth.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()
    mighty.app.config["TESTING"] = True
    c = mighty.app.test_client()
    c.get("/signup")
    with c.session_transaction() as sess:
        csrf = sess["_csrf"]
        email = f"login_truth_{secrets.token_hex(4)}@test.local"
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    c.email = email
    return c


@pytest.fixture()
def admin_client(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", client.email)
    return client


def _ctx(mighty):
    return dict(
        iso_fn=mighty.iso,
        encrypt_fn=mighty.encrypt_account_data,
        decrypt_fn=mighty.decrypt_account_data,
    )


def _uid(client):
    with client.session_transaction() as sess:
        return sess["user_id"]


def _api_key(mighty, client):
    uid = _uid(client)
    with mighty.app.app_context():
        return mighty.get_db().execute(
            "SELECT api_key FROM users WHERE id=?", (uid,),
        ).fetchone()["api_key"]


def _prepare_amex_waiting(client, mighty):
    with client.session_transaction() as sess:
        csrf = sess["_csrf"]
    headers = {"X-CSRF-Token": csrf}
    assert client.post("/api/connect/amex", headers=headers).status_code == 200
    assert client.post("/api/connect/amex/waiting", headers=headers).status_code == 200
    return _api_key(mighty, client)


def test_mr_balance_only_unknown_current_access_with_fresh_cache(client):
    """Cached MR balance alone must not mark Amex as currently connected."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        ctx = _ctx(mighty)
        start_amex_connect(db, uid, **ctx)
        advance_amex_to_waiting(db, uid, **ctx)
        amex_extension_connected(db, uid, session_verified=True, **ctx)
        apply_amex_membership_rewards_extraction(
            db, uid, "142,500",
            access_cycle_id="test-cycle",
            verification_id="test-cycle",
            **ctx,
        )

        rows = compute_current_account_access_rows(
            db, uid, decrypt_account_fn=mighty.decrypt_account_data
        )
        amex = next(r for r in rows if r.provider == "amex")
        assert amex.current_access == "unknown"
        assert amex.cached_data_state == "fresh"
        assert amex.last_private_data is not None
        assert amex.last_verified is None
        assert amex.next_action_text == (
            "Mighty could not verify this account automatically."
        )


def test_fresh_mr_balance_plus_newer_login_page_signed_out_fresh_cache(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        ctx = _ctx(mighty)
        start_amex_connect(db, uid, **ctx)
        advance_amex_to_waiting(db, uid, **ctx)
        amex_extension_connected(db, uid, session_verified=True, **ctx)
        apply_amex_membership_rewards_extraction(
            db, uid, "99,000",
            access_cycle_id="test-cycle",
            verification_id="test-cycle",
            **ctx,
        )

        older_private_at = _iso_minutes_ago(30)
        db.execute(
            "UPDATE account_data SET synced_at=? WHERE user_id=? AND source=?",
            (older_private_at, uid, "amex"),
        )
        db.execute(
            "UPDATE field_observations SET last_seen=? WHERE user_id=? AND source=?",
            (older_private_at, uid, "amex"),
        )
        db.commit()

        record_probe_run(
            db,
            uid,
            {
                "provider": "amex",
                "status": "needs_sign_in",
                "auth_state": AUTH_LOGIN_PAGE,
                "private_data_detected": False,
                "signed_in_detected": False,
                "failure_reason": "login_required",
                "probed_at": _iso_seconds_ago(30),
            },
        )

        rows = compute_current_account_access_rows(
            db, uid, decrypt_account_fn=mighty.decrypt_account_data
        )
        amex = next(r for r in rows if r.provider == "amex")
        assert amex.current_access == "signed_out"
        assert amex.cached_data_state == "fresh"
        assert amex.evidence == "login page detected"
        assert amex.source == "provider_access_probe"
        assert amex.next_action_text == "Sign into this account again."

        session = get_provider_session_state(db, uid, "amex")
        assert session is not None
        assert session.state == "signed_out"


def test_old_connected_session_plus_newer_login_required_signed_out(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="connected",
                evidence_type="session_api",
                evidence_summary="ReadUserSession.v1 returned 200",
                observed_at=datetime.now(timezone.utc) - timedelta(hours=2),
                source="provider_access_probe",
                confidence="high",
            ),
        )
        record_probe_run(
            db,
            uid,
            {
                "provider": "amex",
                "status": "needs_sign_in",
                "auth_state": AUTH_LOGIN_PAGE,
                "private_data_detected": False,
                "signed_in_detected": False,
                "failure_reason": "login_required",
                "probed_at": _iso_seconds_ago(30),
            },
        )

        rows = compute_current_account_access_rows(
            db, uid, decrypt_account_fn=mighty.decrypt_account_data
        )
        amex = next(r for r in rows if r.provider == "amex")
        assert amex.current_access == "signed_out"
        assert amex.evidence == "login page detected"


def test_session_api_200_after_login_required_connected(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        record_probe_run(
            db,
            uid,
            {
                "provider": "amex",
                "status": "needs_sign_in",
                "auth_state": AUTH_LOGIN_PAGE,
                "private_data_detected": False,
                "signed_in_detected": False,
                "failure_reason": "login_required",
                "probed_at": _iso_minutes_ago(20),
            },
        )
        record_probe_run(
            db,
            uid,
            {
                "provider": "amex",
                "status": "signed_in_data",
                "auth_state": AUTH_PRIVATE_DATA_VISIBLE,
                "private_data_detected": True,
                "signed_in_detected": True,
                "probed_at": _iso_seconds_ago(30),
                "deep_inspect": {
                    "auth_network_trace": {
                        "highlighted_requests": [
                            {
                                "url": "https://global.americanexpress.com/api/servicing/v1/ReadUserSession.v1",
                                "status_code": 200,
                            }
                        ]
                    }
                },
            },
        )

        rows = compute_current_account_access_rows(
            db, uid, decrypt_account_fn=mighty.decrypt_account_data
        )
        amex = next(r for r in rows if r.provider == "amex")
        assert amex.current_access == "connected_now"
        assert "ReadUserSession.v1 returned 200" in amex.evidence


def test_login_page_with_no_private_data_signed_out(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        record_probe_run(
            db,
            uid,
            {
                "provider": "delta",
                "status": "needs_sign_in",
                "auth_state": AUTH_LOGIN_PAGE,
                "private_data_detected": False,
                "signed_in_detected": False,
                "failure_reason": "login_required",
                "probed_at": _iso_seconds_ago(30),
            },
        )

        rows = compute_current_account_access_rows(
            db, uid, decrypt_account_fn=mighty.decrypt_account_data
        )
        delta = next(r for r in rows if r.provider == "delta")
        assert delta.current_access == "signed_out"
        assert delta.cached_data_state == "none"
        assert delta.evidence == "login page detected"
        assert delta.next_action_text == "Sign into this account again."
        assert delta.source == "provider_access_probe"
        assert delta.last_private_data is None


def test_no_evidence_unknown_with_no_cached_data(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        rows = compute_current_account_access_rows(
            db, uid, decrypt_account_fn=mighty.decrypt_account_data
        )
        assert all(r.current_access == "unknown" for r in rows)
        assert all(r.cached_data_state == "none" for r in rows)
        assert all(r.evidence == "—" for r in rows)
        assert all(r.last_verified is None for r in rows)
        assert all(r.last_private_data is None for r in rows)
        assert all(
            r.next_action_text
            == "Mighty could not verify this account automatically."
            for r in rows
        )


def test_resolve_current_access_uses_session_state_not_private_cache():
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    observations = [
        TruthObservation(
            observed_at=now - timedelta(minutes=5),
            kind="private",
            evidence="Observed Membership Rewards balance",
            source="account_data.items",
        ),
    ]
    session = ProviderSessionState(
        provider="amex",
        state="signed_out",
        evidence_type="login_page",
        evidence_summary="login page detected",
        observed_at=(now - timedelta(minutes=1)).isoformat(),
        source="provider_access_probe",
        confidence="high",
    )
    result = resolve_current_account_access(
        "amex", observations, session_state=session, now=now
    )
    assert result.current_access == "signed_out"
    assert result.cached_data_state == "fresh"
    assert result.evidence == "login page detected"
    assert result.last_private_data == (now - timedelta(minutes=5)).isoformat()


def test_resolve_current_access_mr_only_unknown_fresh():
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    observations = [
        TruthObservation(
            observed_at=now - timedelta(minutes=5),
            kind="private",
            evidence="Observed Membership Rewards balance",
            source="account_data.items",
        ),
    ]
    result = resolve_current_account_access("amex", observations, now=now)
    assert result.current_access == "unknown"
    assert result.cached_data_state == "fresh"
    assert result.last_verified is None


def test_resolve_current_access_no_observations_unknown_none():
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    result = resolve_current_account_access("hilton", [], now=now)
    assert result.current_access == "unknown"
    assert result.cached_data_state == "none"
    assert result.last_verified is None
    assert result.last_private_data is None
    assert result.evidence == "—"


def test_resolve_current_access_stale_private_unknown_with_stale_cache():
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    observations = [
        TruthObservation(
            observed_at=now - timedelta(days=5),
            kind="private",
            evidence="saw miles balance",
            source="field_observations",
        ),
    ]
    result = resolve_current_account_access("delta", observations, now=now)
    assert result.current_access == "unknown"
    assert result.cached_data_state == "stale"


def test_derive_session_evidence_from_session_api_401():
    evidence = derive_session_evidence_from_probe(
        {
            "provider": "amex",
            "auth_state": AUTH_PRIVATE_DATA_VISIBLE,
            "probed_at": "2026-07-08T12:00:00+00:00",
            "deep_inspect": {
                "auth_network_trace": {
                    "highlighted_requests": [
                        {
                            "url": "https://global.americanexpress.com/api/servicing/v1/ReadUserSession.v1",
                            "status_code": 401,
                        }
                    ]
                }
            },
        }
    )
    assert evidence is not None
    assert evidence.state == "signed_out"
    assert evidence.evidence_summary == "ReadUserSession.v1 returned 401"


def test_extension_amex_connected_writes_provider_session_state(client):
    import app as mighty
    from mighty.provider_session_state import get_provider_session_state

    api_key = _prepare_amex_waiting(client, mighty)
    r = client.post(
        "/api/extension/amex/connected",
        headers={"X-Mighty-Key": api_key},
        json={"session_verified": True},
    )
    assert r.status_code == 200

    uid = _uid(client)
    with mighty.app.app_context():
        session = get_provider_session_state(mighty.get_db(), uid, "amex")
        assert session is not None
        assert session.state == "connected"
        assert session.evidence_type == "session_verified"
        assert session.source == "extension_amex_connected"
        assert session.evidence_summary == (
            "Amex extension reported verified authenticated session"
        )
        assert session.confidence == "high"


def test_extension_amex_needs_login_writes_provider_session_state(client):
    import app as mighty
    from mighty.provider_session_state import get_provider_session_state

    api_key = _prepare_amex_waiting(client, mighty)
    r = client.post(
        "/api/extension/amex/needs-login",
        headers={"X-Mighty-Key": api_key},
    )
    assert r.status_code == 200

    uid = _uid(client)
    with mighty.app.app_context():
        session = get_provider_session_state(mighty.get_db(), uid, "amex")
        assert session is not None
        assert session.state == "signed_out"
        assert session.evidence_type == "login_required"
        assert session.source == "extension_amex_needs_login"
        assert session.evidence_summary == "Amex extension reported login required"


def test_newer_needs_login_overrides_prior_connected(client):
    import app as mighty
    from mighty.provider_session_state import get_provider_session_state

    api_key = _prepare_amex_waiting(client, mighty)
    assert client.post(
        "/api/extension/amex/connected",
        headers={"X-Mighty-Key": api_key},
        json={"session_verified": True},
    ).status_code == 200
    assert client.post(
        "/api/extension/amex/needs-login",
        headers={"X-Mighty-Key": api_key},
    ).status_code == 200

    uid = _uid(client)
    with mighty.app.app_context():
        session = get_provider_session_state(mighty.get_db(), uid, "amex")
        assert session is not None
        assert session.state == "signed_out"
        assert session.source == "extension_amex_needs_login"


def test_newer_connected_overrides_prior_needs_login(client):
    import app as mighty
    from mighty.provider_session_state import get_provider_session_state

    api_key = _prepare_amex_waiting(client, mighty)
    assert client.post(
        "/api/extension/amex/needs-login",
        headers={"X-Mighty-Key": api_key},
    ).status_code == 200
    assert client.post(
        "/api/extension/amex/connected",
        headers={"X-Mighty-Key": api_key},
        json={"session_verified": True},
    ).status_code == 200

    uid = _uid(client)
    with mighty.app.app_context():
        session = get_provider_session_state(mighty.get_db(), uid, "amex")
        assert session is not None
        assert session.state == "connected"
        assert session.source == "extension_amex_connected"


def test_mr_extraction_without_session_endpoint_does_not_write_connected(client):
    """Direct MR cache write (no session_verified endpoint) must not set connected."""
    import app as mighty
    from mighty.provider_session_state import get_provider_session_state

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        ctx = _ctx(mighty)
        start_amex_connect(db, uid, **ctx)
        advance_amex_to_waiting(db, uid, **ctx)
        amex_extension_connected(db, uid, session_verified=True, **ctx)
        apply_amex_membership_rewards_extraction(
            db, uid, "55,000",
            access_cycle_id="test-cycle",
            verification_id="test-cycle",
            **ctx,
        )

        session = get_provider_session_state(db, uid, "amex")
        assert session is None

        rows = compute_current_account_access_rows(
            db, uid, decrypt_account_fn=mighty.decrypt_account_data
        )
        amex = next(r for r in rows if r.provider == "amex")
        assert amex.current_access == "unknown"
        assert amex.cached_data_state == "fresh"


def test_admin_login_truth_reflects_extension_session_state_immediately(admin_client):
    import app as mighty

    api_key = _prepare_amex_waiting(admin_client, mighty)
    assert admin_client.post(
        "/api/extension/amex/connected",
        headers={"X-Mighty-Key": api_key},
        json={"session_verified": True},
    ).status_code == 200
    assert admin_client.post(
        "/api/extension/amex/needs-login",
        headers={"X-Mighty-Key": api_key},
    ).status_code == 200

    r = admin_client.get("/admin/login-truth")
    assert r.status_code == 200
    assert b"Signed out" in r.data
    assert b"Amex extension reported login required" in r.data or b"Signed out" in r.data

    uid = _uid(admin_client)
    with mighty.app.app_context():
        rows = compute_current_account_access_rows(
            mighty.get_db(), uid, decrypt_account_fn=mighty.decrypt_account_data
        )
        amex = next(row for row in rows if row.provider == "amex")
        assert amex.current_access == "signed_out"
        assert amex.source == "extension_amex_needs_login"
        assert amex.evidence == "Amex extension reported login required"


def test_extension_amex_extract_with_session_verified_writes_connected(client):
    import app as mighty
    from mighty.provider_access_manager import (
        complete_provider_access_check,
        request_provider_access_check,
    )
    from mighty.provider_access_probe import AUTH_AUTHENTICATED_NO_PRIVATE_DATA
    from mighty.provider_session_state import get_provider_session_state

    api_key = _prepare_amex_waiting(client, mighty)
    # Extract requires waiting→connected path for account row; connect first.
    assert client.post(
        "/api/extension/amex/connected",
        headers={"X-Mighty-Key": api_key},
        json={"session_verified": True},
    ).status_code == 200

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        verification = request_provider_access_check(db, uid, "amex")
        assert verification is not None
        vid = verification.verification_id
        complete_provider_access_check(
            db,
            uid,
            {
                "provider": "amex",
                "status": "ok",
                "auth_state": AUTH_AUTHENTICATED_NO_PRIVATE_DATA,
                "url_visited": "https://global.americanexpress.com/overview",
                "signed_in_detected": True,
                "private_data_detected": False,
                "evidence_type": "page",
                "evidence_snippet": "test",
                "probed_at": datetime.now(timezone.utc).isoformat(),
            },
            verification_id=vid,
        )

    r = client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "88,000",
            "verification_id": vid,
            "access_cycle_id": vid,
        },
    )
    assert r.status_code == 200

    with mighty.app.app_context():
        session = get_provider_session_state(mighty.get_db(), uid, "amex")
        assert session is not None
        assert session.state == "connected"
        assert session.evidence_type == "session_verified_extract"
        assert session.source == "extension_amex_extract"
        assert "Membership Rewards" not in session.evidence_summary


def test_resolve_login_truth_private_beats_login():
    """Legacy Login Truth still uses the 24h private-data-wins rule."""
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    observations = [
        TruthObservation(
            observed_at=now - timedelta(hours=2),
            kind="private",
            evidence="Observed Membership Rewards balance",
            source="account_data.items",
        ),
        TruthObservation(
            observed_at=now - timedelta(minutes=30),
            kind="login",
            evidence="login page detected",
            source="provider_access_probe",
        ),
    ]
    result = resolve_login_truth(observations, now=now)
    assert result.login_known == "YES"
    assert result.evidence == "Observed Membership Rewards balance"


def test_format_status_labels():
    assert format_status_label("YES") == "Logged in"
    assert format_status_label("NO") == "Not logged in"
    assert format_status_label("UNKNOWN") == "Unknown"


def test_friendly_source_label_maps_account_data_items():
    label, internal = friendly_source_label("account_data.items")
    assert label == "Extracted account data"
    assert internal == "account_data.items"


def test_login_truth_summary_counts():
    rows = [
        LoginTruthRow("amex", "YES", "Observed Membership Rewards balance", "2026-07-08T12:00:00+00:00", "account_data.items"),
        LoginTruthRow("delta", "NO", "login page detected", "2026-07-08T11:00:00+00:00", "provider_access_probe"),
        LoginTruthRow("hilton", "UNKNOWN", "—", None, "—"),
        LoginTruthRow("united", "UNKNOWN", "—", None, "—"),
    ]
    summary = login_truth_summary(rows)
    assert summary == {"logged_in": 1, "not_logged_in": 1, "unknown": 2}


def test_sort_login_truth_rows_by_status_then_provider():
    rows = [
        LoginTruthRow("hilton", "UNKNOWN", "—", None, "—"),
        LoginTruthRow("delta", "NO", "login page detected", "2026-07-08T11:00:00+00:00", "provider_access_probe"),
        LoginTruthRow("amex", "YES", "Observed Membership Rewards balance", "2026-07-08T12:00:00+00:00", "account_data.items"),
        LoginTruthRow("marriott", "UNKNOWN", "—", None, "—"),
        LoginTruthRow("united", "NO", "login page detected", "2026-07-08T10:00:00+00:00", "provider_access_probe"),
    ]
    sorted_rows = sort_login_truth_rows(rows)
    assert [row.provider for row in sorted_rows] == ["amex", "delta", "united", "hilton", "marriott"]


def test_format_login_truth_display_row():
    row = LoginTruthRow(
        "amex",
        "YES",
        "Observed Membership Rewards balance",
        "2026-07-08T12:00:00+00:00",
        "account_data.items",
    )
    display = format_login_truth_display_row(row)
    assert display.status_label == "Logged in"
    assert display.evidence == "Observed Membership Rewards balance"
    assert display.source_label == "Extracted account data"
    assert display.source_internal == "account_data.items"


def test_amex_evidence_text(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        ctx = _ctx(mighty)
        start_amex_connect(db, uid, **ctx)
        advance_amex_to_waiting(db, uid, **ctx)
        amex_extension_connected(db, uid, session_verified=True, **ctx)
        apply_amex_membership_rewards_extraction(
            db, uid, "55,000",
            access_cycle_id="test-cycle",
            verification_id="test-cycle",
            **ctx,
        )

        rows = compute_login_truth_rows(db, uid, decrypt_account_fn=mighty.decrypt_account_data)
        amex = next(r for r in rows if r.provider == "amex")
        assert amex.evidence == "Observed Membership Rewards balance"


def test_resolve_access_state_unknown_with_stale_private():
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    observations = [
        TruthObservation(
            observed_at=now - timedelta(days=5),
            kind="private",
            evidence="saw miles balance",
            source="field_observations",
        ),
    ]
    login_row = LoginTruthRow("delta", "UNKNOWN", "—", None, "—")
    access = resolve_access_state(observations, login_row, now=now)
    assert access.access_state == "unknown"
    assert access.next_action_text == "Visit this account while signed in so Mighty can check it."


def test_resolve_access_state_contradictory_private_and_newer_login():
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    observations = [
        TruthObservation(
            observed_at=now - timedelta(hours=2),
            kind="private",
            evidence="Observed Membership Rewards balance",
            source="account_data.items",
        ),
        TruthObservation(
            observed_at=now - timedelta(minutes=30),
            kind="login",
            evidence="login page detected",
            source="provider_access_probe",
        ),
    ]
    login_row = LoginTruthRow(
        "amex",
        "NO",
        "login page detected",
        (now - timedelta(minutes=30)).isoformat(),
        "provider_access_probe",
    )
    access = resolve_access_state(observations, login_row, now=now)
    assert access.access_state == "unexpected_problem"
    assert access.next_action_text == "Mighty saw conflicting signals. This may be a bug."


def test_access_state_labels():
    assert format_access_state_label("accessible") == "Accessible"
    assert format_access_state_label("needs_reauthentication") == "Sign in needed"
    assert format_access_state_label("needs_first_connection") == "Not connected yet"
    assert format_access_state_label("unknown") == "Unknown"
    assert format_access_state_label("unexpected_problem") == "Needs investigation"


def test_current_access_and_cached_data_labels():
    assert format_current_access_label("connected_now") == "Connected now"
    assert format_current_access_label("signed_out") == "Signed out"
    assert format_current_access_label("checking") == "Checking"
    assert format_current_access_label("unknown") == "Unknown"
    assert format_current_access_label("error") == "Error"
    assert format_cached_data_label("fresh") == "Fresh"
    assert format_cached_data_label("stale") == "Stale"
    assert format_cached_data_label("none") == "None"


def test_access_state_summary_counts():
    rows = [
        AccessStateRow("amex", "YES", "accessible", "none", "Nothing.", "mr", "t", "account_data.items"),
        AccessStateRow("delta", "NO", "needs_reauthentication", "reauthenticate", "Sign in.", "login", "t", "provider_access_probe"),
        AccessStateRow("hilton", "UNKNOWN", "needs_first_connection", "connect_account", "Connect.", "—", None, "—"),
        AccessStateRow("united", "UNKNOWN", "unknown", "wait_for_observation", "Visit.", "old", "t", "field_observations"),
        AccessStateRow("marriott", "NO", "unexpected_problem", "report_problem", "Bug.", "conflict", "t", "provider_access_probe"),
    ]
    summary = access_state_summary(rows)
    assert summary == {
        "accessible": 1,
        "sign_in_needed": 1,
        "not_connected_or_unknown": 2,
        "needs_investigation": 1,
    }


def test_current_account_access_summary_counts():
    rows = [
        CurrentAccountAccess(
            "amex", "connected_now", "fresh", "t", "t", "mr", "account_data.items", "none", "Nothing."
        ),
        CurrentAccountAccess(
            "delta", "signed_out", "none", "t", None, "login", "provider_access_probe", "reauthenticate", "Sign in."
        ),
        CurrentAccountAccess(
            "hilton", "unknown", "none", None, None, "—", "—", "connect_account", "Connect."
        ),
        CurrentAccountAccess(
            "united", "unknown", "stale", None, "t", "—", "—", "connect_account", "Connect."
        ),
        CurrentAccountAccess(
            "marriott", "checking", "fresh", "t", "t", "mr", "provider_session_state", "verifying", "Checking."
        ),
    ]
    summary = current_account_access_summary(rows)
    assert summary == {
        "connected_now": 1,
        "signed_out": 1,
        "checking": 1,
        "unknown": 2,
        "error": 0,
    }


def test_sort_access_state_rows_by_state_then_provider():
    rows = [
        AccessStateRow("hilton", "UNKNOWN", "unknown", "wait_for_observation", "Visit.", "—", None, "—"),
        AccessStateRow("delta", "NO", "needs_reauthentication", "reauthenticate", "Sign in.", "login", "t", "provider_access_probe"),
        AccessStateRow("amex", "YES", "accessible", "none", "Nothing.", "mr", "t", "account_data.items"),
        AccessStateRow("marriott", "UNKNOWN", "needs_first_connection", "connect_account", "Connect.", "—", None, "—"),
        AccessStateRow("united", "NO", "unexpected_problem", "report_problem", "Bug.", "conflict", "t", "provider_access_probe"),
    ]
    sorted_rows = sort_access_state_rows(rows)
    assert [row.provider for row in sorted_rows] == [
        "amex",
        "delta",
        "marriott",
        "hilton",
        "united",
    ]


def test_sort_current_account_access_rows_by_access_then_provider():
    rows = [
        CurrentAccountAccess(
            "hilton", "unknown", "none", None, None, "—", "—", "connect_account", "Connect."
        ),
        CurrentAccountAccess(
            "delta", "signed_out", "none", "t", None, "login", "provider_access_probe", "reauthenticate", "Sign in."
        ),
        CurrentAccountAccess(
            "amex", "connected_now", "fresh", "t", "t", "mr", "account_data.items", "none", "Nothing."
        ),
        CurrentAccountAccess(
            "united", "signed_out", "fresh", "t", "t", "login", "provider_access_probe", "reauthenticate", "Sign in."
        ),
        CurrentAccountAccess(
            "marriott", "unknown", "stale", None, "t", "—", "—", "connect_account", "Connect."
        ),
    ]
    sorted_rows = sort_current_account_access_rows(rows)
    assert [row.provider for row in sorted_rows] == [
        "amex",
        "delta",
        "united",
        "hilton",
        "marriott",
    ]


def test_format_access_state_display_row_uses_friendly_source():
    row = AccessStateRow(
        "amex",
        "YES",
        "accessible",
        "none",
        "Nothing. Mighty can monitor this account automatically.",
        "Observed Membership Rewards balance",
        "2026-07-08T12:00:00+00:00",
        "account_data.items",
    )
    display = format_access_state_display_row(row)
    assert display.access_label == "Accessible"
    assert display.evidence == "Observed Membership Rewards balance"
    assert display.source_label == "Extracted account data"
    assert display.source_internal == "account_data.items"
    assert display.next_action_text == "Nothing. Mighty can monitor this account automatically."


def test_format_current_account_access_display_row():
    row = CurrentAccountAccess(
        provider="amex",
        current_access="signed_out",
        cached_data_state="fresh",
        last_verified="2026-07-08T12:00:00+00:00",
        last_private_data="2026-07-08T11:00:00+00:00",
        evidence="login page detected",
        source="provider_access_probe",
        next_action_type="reauthenticate",
        next_action_text="Sign into this account again.",
    )
    display = format_current_account_access_display_row(row)
    assert display.current_access_label == "Signed out"
    assert display.cached_data_label == "Fresh"
    assert display.evidence == "login page detected"
    assert display.source_label == "Account access probe"
    assert display.source_internal == "provider_access_probe"
    assert display.next_action_text == "Sign into this account again."


def test_admin_login_truth_page_forbidden(client):
    assert client.get("/admin/login-truth").status_code == 403


def test_admin_login_truth_page_loads(admin_client):
    r = admin_client.get("/admin/login-truth")
    assert r.status_code == 200
    assert b"Current Account Access" in r.data
    assert b"Current access" in r.data
    assert b"Cached data" in r.data
    assert b"Last verified" in r.data
    assert b"Next action" in r.data
    assert b"Why this matters" in r.data
    assert b"Connected now" in r.data
    assert b"Signed out" in r.data
    assert b"Unknown" in r.data
    assert b"What Mighty knows" not in r.data
    assert b"Can Mighty access it?" not in r.data
    assert b"account_data.items" not in r.data


def test_admin_session_evidence_page_forbidden(client):
    assert client.get("/admin/session-evidence").status_code == 403


def test_admin_session_evidence_page_loads(admin_client):
    r = admin_client.get("/admin/session-evidence")
    assert r.status_code == 200
    assert b"Session Evidence Timeline" in r.data
    assert b"Current winner" in r.data
    assert b"Evidence timeline" in r.data
    assert b"Evidence precedence" in r.data
    assert b"Include cached data" in r.data
    assert b"Show legacy compatibility events" in r.data
    assert b"connection_status" in r.data  # listed in precedence card
    assert b"sync_status" in r.data


def test_session_evidence_timeline_shows_provider_session_state_and_winner(client):
    import app as mighty

    uid = _uid(client)
    observed = datetime.now(timezone.utc) - timedelta(minutes=10)
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="connected",
                evidence_type="session_verified",
                evidence_summary="Amex extension reported verified authenticated session",
                observed_at=observed,
                source="extension_amex_connected",
                confidence="high",
            ),
        )
        sections = gather_session_evidence_timeline(
            db,
            uid,
            decrypt_account_fn=mighty.decrypt_account_data,
            provider="amex",
            include_cached_data=False,
        )
        assert len(sections) == 1
        section = sections[0]
        assert section.current is not None
        assert section.current.state == "connected"
        assert section.current.evidence_type == "session_verified"
        winner = format_current_winner_line(section)
        assert winner.startswith("Current winner: connected because of ")
        assert "session_verified" in winner
        explanation = section.winner_explanation
        assert explanation is not None
        assert explanation.state_label == "Connected"
        assert explanation.evidence_type == "session_verified"
        assert "HIGH" in explanation.reason_headline
        session_events = [e for e in section.events if e.category == "session"]
        assert any(
            e.source == "extension_amex_connected"
            and e.result == "connected"
            and e.evidence_type == "session_verified"
            for e in session_events
        )


def test_session_evidence_cached_data_not_connected_evidence(client):
    """Cached MR balance appears as cached-data evidence, not connected session evidence."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        ctx = _ctx(mighty)
        start_amex_connect(db, uid, **ctx)
        advance_amex_to_waiting(db, uid, **ctx)
        amex_extension_connected(db, uid, session_verified=True, **ctx)
        apply_amex_membership_rewards_extraction(
            db, uid, "142,500",
            access_cycle_id="test-cycle",
            verification_id="test-cycle",
            **ctx,
        )

        sections = gather_session_evidence_timeline(
            db,
            uid,
            decrypt_account_fn=mighty.decrypt_account_data,
            provider="amex",
            include_cached_data=True,
        )
        section = sections[0]
        # Extraction alone does not write session state; current access stays unknown.
        assert section.current is None or section.current.state == "unknown"
        cached = [e for e in section.events if e.category == "cached_data"]
        assert cached
        assert all(e.result == "cached_data" for e in cached)
        assert all(e.evidence_type == "cached_private_data" for e in cached)
        assert any("Membership Rewards" in e.summary for e in cached)
        connected_from_cache = [
            e
            for e in section.events
            if e.category == "session" and e.result == "connected" and e.source in {
                "account_data.items",
                "field_observations",
            }
        ]
        assert connected_from_cache == []

        session_only = gather_session_evidence_timeline(
            db,
            uid,
            decrypt_account_fn=mighty.decrypt_account_data,
            provider="amex",
            include_cached_data=False,
        )[0]
        assert all(e.category == "session" for e in session_only.events)
        # Cached data is ignored for Current Access even when not shown in the timeline.
        assert session_only.winner_explanation is not None
        assert any(
            "Membership Rewards" in item.label
            and "Cached data never proves current login" in item.reason
            for item in session_only.winner_explanation.ignored
        )


def test_session_evidence_timeline_sorted_newest_first(client):
    import app as mighty

    uid = _uid(client)
    older = datetime.now(timezone.utc) - timedelta(hours=2)
    newer = datetime.now(timezone.utc) - timedelta(minutes=5)
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="connected",
                evidence_type="session_api",
                evidence_summary="ReadUserSession.v1 returned 200",
                observed_at=older,
                source="provider_access_probe",
                confidence="high",
            ),
        )
        record_probe_run(
            db,
            uid,
            {
                "provider": "amex",
                "status": "needs_sign_in",
                "auth_state": AUTH_LOGIN_PAGE,
                "private_data_detected": False,
                "signed_in_detected": False,
                "failure_reason": "login_required",
                "probed_at": newer.isoformat(),
            },
        )
        section = gather_session_evidence_timeline(
            db,
            uid,
            decrypt_account_fn=mighty.decrypt_account_data,
            provider="amex",
            include_cached_data=False,
        )[0]
        assert section.events
        times = [e.observed_at for e in section.events]
        assert times == sorted(times, reverse=True)
        assert section.events[0].result == "signed_out"
        assert "Current winner: signed out" in format_current_winner_line(section)


def test_admin_session_evidence_page_shows_winner_and_cached(admin_client):
    import app as mighty

    uid = _uid(admin_client)
    with mighty.app.app_context():
        db = mighty.get_db()
        ctx = _ctx(mighty)
        start_amex_connect(db, uid, **ctx)
        advance_amex_to_waiting(db, uid, **ctx)
        apply_amex_membership_rewards_extraction(
            db, uid, "50,000",
            access_cycle_id="test-cycle",
            verification_id="test-cycle",
            **ctx,
        )
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="signed_out",
                evidence_type="login_page",
                evidence_summary="login page detected",
                observed_at=datetime.now(timezone.utc) - timedelta(minutes=3),
                source="provider_access_probe",
                confidence="high",
            ),
        )

    r = admin_client.get("/admin/session-evidence?provider=amex&include_cached=1")
    assert r.status_code == 200
    assert b"Current winner" in r.data
    assert b"Signed out" in r.data
    assert b"login_page" in r.data
    assert b"Latest HIGH confidence evidence" in r.data
    assert b"cached_private_data" in r.data or b"Cached data" in r.data
    assert b"Ignored evidence" in r.data
    assert b"Cached data never proves current login" in r.data
    # Cached data badge must not be presented as Connected session evidence from items.
    assert b"Membership Rewards" in r.data


def test_session_evidence_default_hides_legacy_rows(client):
    """Default timeline shows canonical session only — not connection_status / sync_status."""
    import app as mighty

    uid = _uid(client)
    when = datetime.now(timezone.utc) - timedelta(minutes=8)
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="connected",
                evidence_type="session_verified",
                evidence_summary="Amex extension reported verified authenticated session",
                observed_at=when,
                source="extension_amex_connected",
                confidence="high",
            ),
        )
        db.execute(
            "INSERT OR REPLACE INTO account_data "
            "(user_id, source, display_name, icon, color, data_enc, synced_at, "
            "sync_status, connection_status) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                uid,
                "amex",
                "Amex",
                "",
                "",
                "",
                when.isoformat(),
                "login_required",
                "connected",
            ),
        )
        db.commit()

        default = gather_session_evidence_timeline(
            db,
            uid,
            decrypt_account_fn=mighty.decrypt_account_data,
            provider="amex",
        )[0]
        assert all(e.category == "session" for e in default.events)
        assert not any(e.evidence_type in {"connection_status", "sync_status"} for e in default.events)
        assert default.winner_explanation is not None
        assert default.winner_explanation.state_label == "Connected"
        assert default.winner_explanation.evidence_type == "session_verified"
        ignored_labels = [i.label for i in default.winner_explanation.ignored]
        assert any(label.startswith("sync_status=") for label in ignored_labels)
        assert any(label.startswith("connection_status=") for label in ignored_labels)
        assert all(
            i.reason == "Legacy compatibility signal"
            for i in default.winner_explanation.ignored
            if i.label.startswith(("sync_status=", "connection_status="))
        )

        with_legacy = gather_session_evidence_timeline(
            db,
            uid,
            decrypt_account_fn=mighty.decrypt_account_data,
            provider="amex",
            include_legacy=True,
        )[0]
        legacy = [e for e in with_legacy.events if e.category == "legacy"]
        assert legacy
        assert any(e.evidence_type == "connection_status" for e in legacy)
        assert any(e.evidence_type == "sync_status" for e in legacy)
        # Legacy never appears as the current winner.
        assert with_legacy.current is not None
        assert with_legacy.current.evidence_type == "session_verified"
        assert with_legacy.current.source == "extension_amex_connected"


def test_session_evidence_cached_data_never_explains_connected(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        ctx = _ctx(mighty)
        start_amex_connect(db, uid, **ctx)
        advance_amex_to_waiting(db, uid, **ctx)
        apply_amex_membership_rewards_extraction(
            db, uid, "77,000",
            access_cycle_id="test-cycle",
            verification_id="test-cycle",
            **ctx,
        )

        section = gather_session_evidence_timeline(
            db,
            uid,
            decrypt_account_fn=mighty.decrypt_account_data,
            provider="amex",
            include_cached_data=True,
        )[0]
        assert section.winner_explanation is not None
        assert section.winner_explanation.state_label == "Unknown"
        assert section.winner_explanation.evidence_type is None
        assert not any(
            e.category == "cached_data" and e.result == "connected" for e in section.events
        )


def test_admin_session_evidence_legacy_toggle(admin_client):
    import app as mighty

    uid = _uid(admin_client)
    when = datetime.now(timezone.utc) - timedelta(minutes=4)
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="connected",
                evidence_type="session_api",
                evidence_summary="ReadUserSession.v1 returned 200",
                observed_at=when,
                source="provider_access_probe",
                confidence="high",
            ),
        )
        db.execute(
            "INSERT OR REPLACE INTO account_data "
            "(user_id, source, display_name, icon, color, data_enc, synced_at, "
            "sync_status, connection_status) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                uid,
                "amex",
                "Amex",
                "",
                "",
                "",
                when.isoformat(),
                "ok",
                "connected",
            ),
        )
        db.commit()

    default_page = admin_client.get("/admin/session-evidence?provider=amex")
    assert default_page.status_code == 200
    assert b"Evidence precedence" in default_page.data
    assert b"Show legacy compatibility events" in default_page.data
    # Timeline rows for legacy are hidden by default (badge only appears on rows).
    assert b">legacy</span>" not in default_page.data
    assert b"Ignored evidence" in default_page.data
    assert b"Legacy compatibility signal" in default_page.data

    legacy_page = admin_client.get(
        "/admin/session-evidence?provider=amex&include_legacy=1"
    )
    assert legacy_page.status_code == 200
    assert b">legacy</span>" in legacy_page.data
    assert b"connection_status" in legacy_page.data


def _pss_snapshot(db, uid):
    rows = db.execute(
        "SELECT provider, state, evidence_type, evidence_summary, observed_at, source, "
        "confidence, updated_at FROM provider_session_state WHERE user_id=? "
        "ORDER BY provider",
        (uid,),
    ).fetchall()
    return [dict(r) for r in rows]


def test_admin_pages_are_read_only_for_provider_session_state(admin_client):
    """Loading login-truth / session-evidence must not write provider_session_state."""
    import app as mighty

    uid = _uid(admin_client)
    when = datetime.now(timezone.utc) - timedelta(minutes=15)
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="connected",
                evidence_type="session_verified",
                evidence_summary="Amex extension reported verified authenticated session",
                observed_at=when,
                source="extension_amex_connected",
                confidence="high",
            ),
        )
        # Conflicting legacy fields at the same synced_at — previously lazy-written into PSS.
        db.execute(
            "INSERT OR REPLACE INTO account_data "
            "(user_id, source, display_name, icon, color, data_enc, synced_at, "
            "sync_status, connection_status) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                uid,
                "amex",
                "Amex",
                "",
                "",
                "",
                when.isoformat(),
                "login_required",
                "connected",
            ),
        )
        db.commit()
        before = _pss_snapshot(db, uid)

    assert admin_client.get("/admin/login-truth").status_code == 200
    assert admin_client.get("/admin/session-evidence").status_code == 200

    with mighty.app.app_context():
        after = _pss_snapshot(mighty.get_db(), uid)
    assert after == before


def test_same_timestamp_session_api_200_beats_legacy_login_required(client):
    import app as mighty

    uid = _uid(client)
    when = datetime.now(timezone.utc)
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="signed_out",
                evidence_type="login_required",
                evidence_summary="sync_status: login_required",
                observed_at=when,
                source="account_data.sync_status",
                confidence="medium",
            ),
        )
        winner = upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="connected",
                evidence_type="session_api",
                evidence_summary="ReadUserSession.v1 returned 200",
                observed_at=when,
                source="provider_access_probe",
                confidence="high",
            ),
        )
        assert winner.state == "connected"
        assert winner.evidence_type == "session_api"
        assert get_provider_session_state(db, uid, "amex").state == "connected"


def test_same_timestamp_session_api_401_beats_legacy_connected(client):
    import app as mighty

    uid = _uid(client)
    when = datetime.now(timezone.utc)
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="connected",
                evidence_type="connection_status",
                evidence_summary="connection_status: connected",
                observed_at=when,
                source="account_data.connection_status",
                confidence="medium",
            ),
        )
        winner = upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="signed_out",
                evidence_type="session_api",
                evidence_summary="ReadUserSession.v1 returned 401",
                observed_at=when,
                source="provider_access_probe",
                confidence="high",
            ),
        )
        assert winner.state == "signed_out"
        assert winner.evidence_type == "session_api"


def test_newer_explicit_event_beats_older_event(client):
    import app as mighty

    uid = _uid(client)
    older = datetime.now(timezone.utc) - timedelta(hours=1)
    newer = datetime.now(timezone.utc) - timedelta(minutes=5)
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="connected",
                evidence_type="session_api",
                evidence_summary="ReadUserSession.v1 returned 200",
                observed_at=older,
                source="provider_access_probe",
                confidence="high",
            ),
        )
        winner = upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="signed_out",
                evidence_type="login_page",
                evidence_summary="login page detected",
                observed_at=newer,
                source="provider_access_probe",
                confidence="high",
            ),
        )
        assert winner.state == "signed_out"
        # Older high-priority evidence must not overwrite newer.
        kept = upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="connected",
                evidence_type="session_api",
                evidence_summary="ReadUserSession.v1 returned 200",
                observed_at=older,
                source="provider_access_probe",
                confidence="high",
            ),
        )
        assert kept.state == "signed_out"


def test_identical_timestamp_connected_and_login_required_resolve_deterministically():
    when = datetime.now(timezone.utc)
    legacy_login = SessionEvidence(
        provider="amex",
        state="signed_out",
        evidence_type="login_required",
        evidence_summary="sync_status: login_required",
        observed_at=when,
        source="account_data.sync_status",
        confidence="medium",
    )
    legacy_connected = ProviderSessionState(
        provider="amex",
        state="connected",
        evidence_type="connection_status",
        evidence_summary="connection_status: connected",
        observed_at=when.isoformat(),
        source="account_data.connection_status",
        confidence="medium",
    )
    # Equal confidence + equal time: both legacy → keep existing (no thrash).
    assert should_replace_session_evidence(legacy_connected, legacy_login) is False
    # Explicit session API always beats legacy at the same timestamp.
    api = SessionEvidence(
        provider="amex",
        state="signed_out",
        evidence_type="session_api",
        evidence_summary="ReadUserSession.v1 returned 401",
        observed_at=when,
        source="provider_access_probe",
        confidence="high",
    )
    assert should_replace_session_evidence(legacy_connected, api) is True


def test_cached_data_never_changes_current_session_state(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        ctx = _ctx(mighty)
        start_amex_connect(db, uid, **ctx)
        advance_amex_to_waiting(db, uid, **ctx)
        apply_amex_membership_rewards_extraction(
            db, uid, "88,000",
            access_cycle_id="test-cycle",
            verification_id="test-cycle",
            **ctx,
        )
        before = get_provider_session_state(db, uid, "amex")
        rows = compute_current_account_access_rows(
            db, uid, decrypt_account_fn=mighty.decrypt_account_data
        )
        amex = next(r for r in rows if r.provider == "amex")
        assert amex.cached_data_state == "fresh"
        assert amex.current_access == "unknown"
        after = get_provider_session_state(db, uid, "amex")
        assert after == before


# ── Session freshness + automatic re-verification ─────────────────────────────


def _connected_session(observed_at: datetime) -> ProviderSessionState:
    return ProviderSessionState(
        provider="amex",
        state="connected",
        evidence_type="session_verified",
        evidence_summary="Amex extension reported verified authenticated session",
        observed_at=observed_at.isoformat(),
        source="extension_amex_connected",
        confidence="high",
    )


def test_fresh_connected_evidence_30s_is_connected_now():
    from mighty.login_truth import CURRENT_SESSION_FRESHNESS_SECONDS

    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    session = _connected_session(now - timedelta(seconds=30))
    result = resolve_current_account_access(
        "amex", [], session_state=session, now=now
    )
    assert CURRENT_SESSION_FRESHNESS_SECONDS == 120
    assert result.current_access == "connected_now"
    assert result.next_action_text == (
        "Nothing. Mighty can monitor this account automatically."
    )
    assert result.verification_lifecycle is None


def test_stale_connected_evidence_53m_is_not_connected_now():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    session = _connected_session(now - timedelta(minutes=53))
    result = resolve_current_account_access(
        "amex", [], session_state=session, now=now
    )
    assert result.current_access != "connected_now"
    assert result.current_access == "unknown"
    assert result.next_action_text == (
        "Mighty could not verify this account automatically."
    )
    assert result.last_verified == session.observed_at


def test_stale_connected_plus_verification_requested_is_checking():
    from mighty.session_verification import SessionVerification

    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    session = _connected_session(now - timedelta(minutes=53))
    verification = SessionVerification(
        verification_id="v1",
        provider="amex",
        lifecycle="requested",
        requested_at=now.isoformat(),
        entry_url="https://global.americanexpress.com/overview",
    )
    result = resolve_current_account_access(
        "amex",
        [],
        session_state=session,
        verification=verification,
        now=now,
    )
    assert result.current_access == "checking"
    assert result.verification_lifecycle == "requested"
    assert result.next_action_text == "Mighty is verifying this account now."


def test_stale_connected_plus_no_verification_is_unknown():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    session = _connected_session(now - timedelta(minutes=53))
    result = resolve_current_account_access(
        "amex", [], session_state=session, now=now
    )
    assert result.current_access == "unknown"


def test_stale_connected_plus_new_login_required_is_signed_out():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    observations = [
        TruthObservation(
            observed_at=now - timedelta(hours=1),
            kind="private",
            evidence="Observed Membership Rewards balance",
            source="account_data.items",
        ),
    ]
    session = ProviderSessionState(
        provider="amex",
        state="signed_out",
        evidence_type="login_required",
        evidence_summary="Amex extension reported login required",
        observed_at=(now - timedelta(seconds=20)).isoformat(),
        source="extension_amex_needs_login",
        confidence="high",
    )
    result = resolve_current_account_access(
        "amex", observations, session_state=session, now=now
    )
    assert result.current_access == "signed_out"
    assert result.cached_data_state == "fresh"
    assert result.next_action_text == "Sign into this account again."


def test_stale_connected_plus_new_session_verified_is_connected_now():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    session = _connected_session(now - timedelta(seconds=15))
    result = resolve_current_account_access(
        "amex", [], session_state=session, now=now
    )
    assert result.current_access == "connected_now"



def _verification_count(db, uid, provider="amex") -> int:
    return db.execute(
        "SELECT COUNT(*) AS n FROM provider_session_verification "
        "WHERE user_id=? AND provider=?",
        (uid, provider),
    ).fetchone()["n"]


def _seed_stale_amex_connected(db, uid, *, minutes_ago: float = 53):
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    upsert_provider_session_state(
        db,
        uid,
        SessionEvidence(
            provider="amex",
            state="connected",
            evidence_type="session_verified",
            evidence_summary="Amex extension reported verified authenticated session",
            observed_at=when,
            source="extension_amex_connected",
            confidence="high",
        ),
    )
    return when


def test_admin_login_truth_and_session_evidence_are_passive(admin_client):
    """Admin diagnostic pages must not enqueue or mutate verification jobs."""
    import app as mighty
    from mighty.session_verification import get_latest_session_verification

    uid = _uid(admin_client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_stale_amex_connected(db, uid)
        before_pss = _pss_snapshot(db, uid)
        before_count = _verification_count(db, uid)

    for _ in range(3):
        assert admin_client.get("/admin/login-truth").status_code == 200
        assert admin_client.get("/admin/session-evidence").status_code == 200
        assert admin_client.get("/admin/session-evidence?provider=amex").status_code == 200

    r = admin_client.get("/admin/login-truth")
    assert r.status_code == 200
    amex_idx = r.data.find(b"<strong>amex</strong>")
    assert amex_idx != -1
    next_row = r.data.find(b"<strong>", amex_idx + 1)
    amex_row = r.data[amex_idx: next_row if next_row != -1 else None]
    assert b"Connected now" not in amex_row
    assert b"Unknown" in amex_row
    assert b"Checking" not in amex_row

    with mighty.app.app_context():
        db = mighty.get_db()
        assert _pss_snapshot(db, uid) == before_pss
        assert _verification_count(db, uid) == before_count == 0
        assert get_latest_session_verification(db, uid, "amex") is None


def test_account_status_creates_one_stale_verification_job(client):
    import app as mighty
    from mighty.session_verification import get_latest_session_verification

    uid = _uid(client)
    with mighty.app.app_context():
        _seed_stale_amex_connected(mighty.get_db(), uid)

    assert client.get("/api/account-status").status_code == 200
    assert client.get("/api/account-status").status_code == 200
    assert client.get("/api/account-status").status_code == 200

    with mighty.app.app_context():
        db = mighty.get_db()
        latest = get_latest_session_verification(db, uid, "amex")
        assert latest is not None
        assert latest.lifecycle == "requested"
        assert latest.entry_url == "https://global.americanexpress.com/overview"
        assert _verification_count(db, uid) == 1


def test_fresh_evidence_creates_no_verification_job(client):
    import app as mighty
    from mighty.session_verification import (
        ensure_provider_session_verification_if_stale,
        get_latest_session_verification,
    )

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="connected",
                evidence_type="session_verified",
                evidence_summary="Amex extension reported verified authenticated session",
                observed_at=datetime.now(timezone.utc) - timedelta(seconds=30),
                source="extension_amex_connected",
                confidence="high",
            ),
        )
        created = ensure_provider_session_verification_if_stale(db, uid, "amex")
        assert created is None
        assert get_latest_session_verification(db, uid, "amex") is None

    assert client.get("/api/account-status").status_code == 200
    with mighty.app.app_context():
        assert get_latest_session_verification(mighty.get_db(), uid, "amex") is None
        assert _verification_count(mighty.get_db(), uid) == 0


def test_active_verification_job_is_reused(client):
    import app as mighty
    from mighty.session_verification import (
        ensure_provider_session_verification_if_stale,
        get_latest_session_verification,
    )

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_stale_amex_connected(db, uid)
        first = ensure_provider_session_verification_if_stale(db, uid, "amex")
        second = ensure_provider_session_verification_if_stale(db, uid, "amex")
        assert first is not None and second is not None
        assert first.verification_id == second.verification_id
        assert _verification_count(db, uid) == 1
        assert get_latest_session_verification(db, uid, "amex").verification_id == first.verification_id


def test_extension_pending_poll_sees_queued_job(client):
    import app as mighty

    uid = _uid(client)
    api_key = _api_key(mighty, client)
    with mighty.app.app_context():
        _seed_stale_amex_connected(mighty.get_db(), uid)

    assert client.get("/api/account-status").status_code == 200

    pending = client.get(
        "/api/extension/session-verification/pending",
        headers={"X-Mighty-Key": api_key},
    )
    assert pending.status_code == 200
    data = pending.get_json()
    assert data["lifecycle"] == "requested"
    assert data["provider"] == "amex"
    assert data["entry_url"] == "https://global.americanexpress.com/overview"
    assert data["verification_id"]


def test_extension_pending_ensure_stale_enqueues_without_account_status(client):
    """Extension lifecycle poll can enqueue stale work without a webpage view."""
    import app as mighty

    uid = _uid(client)
    api_key = _api_key(mighty, client)
    with mighty.app.app_context():
        _seed_stale_amex_connected(mighty.get_db(), uid)
        assert _verification_count(mighty.get_db(), uid) == 0

    pending = client.get(
        "/api/extension/session-verification/pending",
        headers={"X-Mighty-Key": api_key},
    )
    assert pending.status_code == 200
    data = pending.get_json()
    assert data["lifecycle"] == "requested"
    assert data["provider"] == "amex"

    pending2 = client.get(
        "/api/extension/session-verification/pending",
        headers={"X-Mighty-Key": api_key},
    )
    assert pending2.get_json()["verification_id"] == data["verification_id"]
    with mighty.app.app_context():
        assert _verification_count(mighty.get_db(), uid) == 1


def test_timeout_does_not_mark_signed_out(client):
    import app as mighty
    from mighty.session_verification import (
        ensure_provider_session_verification_if_stale,
        expire_timed_out_verifications,
        get_latest_session_verification,
    )

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        when = _seed_stale_amex_connected(db, uid)
        before = get_provider_session_state(db, uid, "amex")
        job = ensure_provider_session_verification_if_stale(db, uid, "amex")
        assert job is not None
        old = (datetime.now(timezone.utc) - timedelta(seconds=40)).isoformat()
        db.execute(
            "UPDATE provider_session_verification SET requested_at=? WHERE verification_id=?",
            (old, job.verification_id),
        )
        db.commit()
        n = expire_timed_out_verifications(db, uid)
        assert n == 1
        latest = get_latest_session_verification(db, uid, "amex")
        assert latest is not None
        assert latest.lifecycle == "timed_out"
        after = get_provider_session_state(db, uid, "amex")
        assert after is not None
        assert after.state == "connected"
        assert after.observed_at == before.observed_at == when.isoformat()


def test_inconclusive_verification_does_not_overwrite_pss(client):
    import app as mighty
    from mighty.session_verification import (
        ensure_provider_session_verification_if_stale,
        get_latest_session_verification,
        mark_session_verification_running,
    )

    uid = _uid(client)
    api_key = _api_key(mighty, client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_stale_amex_connected(db, uid)
        before = _pss_snapshot(db, uid)
        job = ensure_provider_session_verification_if_stale(db, uid, "amex")
        assert job is not None
        mark_session_verification_running(db, uid, job.verification_id)
        verification_id = job.verification_id

    r = client.post(
        "/api/extension/provider-access-probe",
        headers={"X-Mighty-Key": api_key},
        json={
            "provider": "amex",
            "url_visited": "https://global.americanexpress.com/overview",
            "final_url": "https://global.americanexpress.com/overview",
            "signed_in_detected": False,
            "private_data_detected": False,
            "error": "network_timeout",
            "verification_id": verification_id,
        },
    )
    assert r.status_code == 200

    with mighty.app.app_context():
        db = mighty.get_db()
        assert _pss_snapshot(db, uid) == before
        latest = get_latest_session_verification(db, uid, "amex")
        assert latest is not None
        assert latest.lifecycle == "failed"
        assert get_provider_session_state(db, uid, "amex").state == "connected"


def test_definitive_login_evidence_marks_signed_out(client):
    import app as mighty
    from mighty.session_verification import (
        ensure_provider_session_verification_if_stale,
        mark_session_verification_running,
    )

    uid = _uid(client)
    api_key = _api_key(mighty, client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_stale_amex_connected(db, uid)
        job = ensure_provider_session_verification_if_stale(db, uid, "amex")
        mark_session_verification_running(db, uid, job.verification_id)
        verification_id = job.verification_id

    r = client.post(
        "/api/extension/provider-access-probe",
        headers={"X-Mighty-Key": api_key},
        json={
            "provider": "amex",
            "url_visited": "https://global.americanexpress.com/overview",
            "final_url": "https://www.americanexpress.com/en-us/account/log-in",
            "dom_text": "Log in to your account User ID Password Remember me",
            "signed_in_detected": False,
            "private_data_detected": False,
            "auth_state": AUTH_LOGIN_PAGE,
            "status": "needs_sign_in",
            "failure_reason": "login_required",
            "verification_id": verification_id,
            "probed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert r.status_code == 200

    with mighty.app.app_context():
        session = get_provider_session_state(mighty.get_db(), uid, "amex")
        assert session is not None
        assert session.state == "signed_out"
        rows = compute_current_account_access_rows(
            mighty.get_db(), uid, decrypt_account_fn=mighty.decrypt_account_data
        )
        amex = next(row for row in rows if row.provider == "amex")
        assert amex.current_access == "signed_out"


def test_definitive_authenticated_evidence_marks_connected(client):
    import app as mighty
    from mighty.session_verification import (
        ensure_provider_session_verification_if_stale,
        mark_session_verification_running,
    )

    uid = _uid(client)
    api_key = _api_key(mighty, client)
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="signed_out",
                evidence_type="login_required",
                evidence_summary="login required",
                observed_at=datetime.now(timezone.utc) - timedelta(minutes=53),
                source="extension_amex_needs_login",
                confidence="high",
            ),
        )
        job = ensure_provider_session_verification_if_stale(db, uid, "amex")
        mark_session_verification_running(db, uid, job.verification_id)
        verification_id = job.verification_id

    now = datetime.now(timezone.utc).isoformat()
    r = client.post(
        "/api/extension/provider-access-probe",
        headers={"X-Mighty-Key": api_key},
        json={
            "provider": "amex",
            "url_visited": "https://global.americanexpress.com/overview",
            "final_url": "https://global.americanexpress.com/overview",
            "signed_in_detected": True,
            "private_data_detected": True,
            "auth_state": AUTH_PRIVATE_DATA_VISIBLE,
            "status": "signed_in_data",
            "verification_id": verification_id,
            "probed_at": now,
            "deep_inspect": {
                "auth_network_trace": {
                    "highlighted_requests": [
                        {
                            "url": "https://global.americanexpress.com/api/servicing/v1/ReadUserSession.v1",
                            "status_code": 200,
                        }
                    ]
                }
            },
        },
    )
    assert r.status_code == 200

    with mighty.app.app_context():
        session = get_provider_session_state(mighty.get_db(), uid, "amex")
        assert session is not None
        assert session.state == "connected"
        rows = compute_current_account_access_rows(
            mighty.get_db(), uid, decrypt_account_fn=mighty.decrypt_account_data
        )
        amex = next(row for row in rows if row.provider == "amex")
        assert amex.current_access == "connected_now"


def test_cached_data_fresh_while_current_access_checking_or_signed_out():
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    observations = [
        TruthObservation(
            observed_at=now - timedelta(minutes=10),
            kind="private",
            evidence="Observed Membership Rewards balance",
            source="account_data.items",
        ),
    ]
    from mighty.session_verification import SessionVerification

    checking = resolve_current_account_access(
        "amex",
        observations,
        session_state=_connected_session(now - timedelta(minutes=53)),
        verification=SessionVerification(
            verification_id="v-check",
            provider="amex",
            lifecycle="running",
            requested_at=now.isoformat(),
            started_at=now.isoformat(),
            entry_url="https://global.americanexpress.com/overview",
        ),
        now=now,
    )
    assert checking.current_access == "checking"
    assert checking.cached_data_state == "fresh"

    signed_out = resolve_current_account_access(
        "amex",
        observations,
        session_state=ProviderSessionState(
            provider="amex",
            state="signed_out",
            evidence_type="login_required",
            evidence_summary="login required",
            observed_at=(now - timedelta(seconds=10)).isoformat(),
            source="extension_amex_needs_login",
            confidence="high",
        ),
        now=now,
    )
    assert signed_out.current_access == "signed_out"
    assert signed_out.cached_data_state == "fresh"


def test_amex_automatic_verification_uses_global_overview_entry():
    from mighty.session_verification import (
        AMEX_SESSION_VERIFICATION_ENTRY_URL,
        verification_entry_url,
    )

    assert (
        AMEX_SESSION_VERIFICATION_ENTRY_URL
        == "https://global.americanexpress.com/overview"
    )
    assert verification_entry_url("amex") == AMEX_SESSION_VERIFICATION_ENTRY_URL
    assert verification_entry_url("delta") is None
