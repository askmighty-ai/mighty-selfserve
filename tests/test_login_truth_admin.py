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
    format_login_truth_display_row,
    format_status_label,
    friendly_source_label,
    login_truth_summary,
    resolve_access_state,
    resolve_current_account_access,
    resolve_login_truth,
    sort_access_state_rows,
    sort_current_account_access_rows,
    sort_login_truth_rows,
)
from mighty.provider_access_probe import AUTH_LOGIN_PAGE, record_probe_run


def _iso_hours_ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _iso_minutes_ago(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


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


def test_private_amex_mr_observation_connected_now_with_fresh_cache(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        ctx = _ctx(mighty)
        start_amex_connect(db, uid, **ctx)
        advance_amex_to_waiting(db, uid, **ctx)
        amex_extension_connected(db, uid, session_verified=True, **ctx)
        apply_amex_membership_rewards_extraction(db, uid, "142,500", **ctx)

        rows = compute_current_account_access_rows(
            db, uid, decrypt_account_fn=mighty.decrypt_account_data
        )
        amex = next(r for r in rows if r.provider == "amex")
        assert amex.current_access == "connected_now"
        assert amex.cached_data_state == "fresh"
        assert amex.evidence == "Observed Membership Rewards balance"
        assert amex.next_action_text == "Nothing. Mighty can monitor this account automatically."
        assert amex.source == "account_data.items"
        assert amex.last_private_data is not None


def test_newer_login_page_overrides_private_data_for_current_access(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        ctx = _ctx(mighty)
        start_amex_connect(db, uid, **ctx)
        advance_amex_to_waiting(db, uid, **ctx)
        amex_extension_connected(db, uid, session_verified=True, **ctx)
        apply_amex_membership_rewards_extraction(db, uid, "99,000", **ctx)

        # Private data is older; a newer login-page observation must win for Current Access.
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
                "probed_at": _iso_minutes_ago(5),
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
                "probed_at": _iso_minutes_ago(10),
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


def test_no_observations_unknown_with_no_cached_data(client):
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
            == "Sign into this account once. Mighty will detect it automatically."
            for r in rows
        )


def test_resolve_current_access_private_then_newer_login_signed_out():
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
    result = resolve_current_account_access("amex", observations, now=now)
    assert result.current_access == "signed_out"
    assert result.cached_data_state == "fresh"
    assert result.evidence == "login page detected"
    assert result.last_private_data == (now - timedelta(hours=2)).isoformat()


def test_resolve_current_access_login_then_newer_private_connected_now():
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    observations = [
        TruthObservation(
            observed_at=now - timedelta(hours=2),
            kind="login",
            evidence="login page detected",
            source="provider_access_probe",
        ),
        TruthObservation(
            observed_at=now - timedelta(minutes=30),
            kind="private",
            evidence="Observed Membership Rewards balance",
            source="account_data.items",
        ),
    ]
    result = resolve_current_account_access("amex", observations, now=now)
    assert result.current_access == "connected_now"
    assert result.cached_data_state == "fresh"
    assert result.evidence == "Observed Membership Rewards balance"
    assert result.last_verified == (now - timedelta(minutes=30)).isoformat()


def test_resolve_current_access_private_only_connected_now_fresh():
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
    assert result.current_access == "connected_now"
    assert result.cached_data_state == "fresh"
    assert result.last_private_data == (now - timedelta(minutes=5)).isoformat()


def test_resolve_current_access_login_only_signed_out_no_cache():
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    observations = [
        TruthObservation(
            observed_at=now - timedelta(minutes=10),
            kind="login",
            evidence="login page detected",
            source="provider_access_probe",
        ),
    ]
    result = resolve_current_account_access("delta", observations, now=now)
    assert result.current_access == "signed_out"
    assert result.cached_data_state == "none"
    assert result.last_private_data is None


def test_resolve_current_access_no_observations_unknown_none():
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    result = resolve_current_account_access("hilton", [], now=now)
    assert result.current_access == "unknown"
    assert result.cached_data_state == "none"
    assert result.last_verified is None
    assert result.last_private_data is None
    assert result.evidence == "—"


def test_resolve_current_access_stale_private_is_connected_with_stale_cache():
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
    assert result.current_access == "connected_now"
    assert result.cached_data_state == "stale"


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
        apply_amex_membership_rewards_extraction(db, uid, "55,000", **ctx)

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
    assert format_current_access_label("unknown") == "Unknown"
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
    ]
    summary = current_account_access_summary(rows)
    assert summary == {"connected_now": 1, "signed_out": 1, "unknown": 2}


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
