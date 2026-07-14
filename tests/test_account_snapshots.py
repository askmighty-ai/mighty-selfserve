"""Regression tests for Canonical Account Snapshots (PR #94)."""

from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_snapshot import (
    SNAPSHOT_SCHEMA_VERSION,
    create_account_snapshot_from_extraction,
    ensure_account_snapshot_tables,
    get_latest_successful_snapshot,
    list_account_snapshots,
    load_customer_snapshot_items,
    load_latest_snapshots_by_provider,
    load_snapshot_display_items,
)
from mighty.account_status import load_all_account_statuses
from mighty.admin_debug import render_account_snapshots_page
from mighty.provider_account import (
    EXTRACTION_COMPLETE,
    EXTRACTION_FAILED,
    EXTRACTION_PENDING,
    apply_adapter_payload,
)
from mighty.provider_session_state import (
    SessionEvidence,
    ensure_provider_session_state_tables,
    upsert_provider_session_state,
)
from mighty.session_verification import (
    ensure_session_verification_tables,
    request_session_verification,
)


def _plain_encrypt(_uid: str, data: dict) -> str:
    return "plain:" + json.dumps(data)


def _plain_decrypt(_uid: str, stored: str) -> dict:
    if not stored:
        return {}
    if stored.startswith("plain:"):
        return json.loads(stored[6:])
    return json.loads(stored)


@pytest.fixture()
def snap_db(tmp_path):
    import sqlite3

    db_path = tmp_path / "snapshots.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            email TEXT,
            sync_running INTEGER DEFAULT 0,
            sync_started_at TEXT,
            sync_current_source TEXT
        );
        CREATE TABLE account_data (
            user_id TEXT NOT NULL,
            source TEXT NOT NULL,
            display_name TEXT NOT NULL,
            icon TEXT,
            color TEXT,
            data_enc TEXT,
            synced_at TEXT,
            sync_failure_reason TEXT,
            sync_status TEXT,
            connection_status TEXT,
            extraction_status TEXT,
            entry_url TEXT,
            PRIMARY KEY (user_id, source)
        );
        CREATE TABLE account_credentials (
            user_id TEXT NOT NULL,
            source TEXT NOT NULL,
            username_enc TEXT,
            password_enc TEXT,
            extra_enc TEXT,
            PRIMARY KEY (user_id, source)
        );
        """
    )
    ensure_account_snapshot_tables(conn)
    ensure_provider_session_state_tables(conn)
    ensure_session_verification_tables(conn)
    conn.execute("INSERT INTO users (id, email) VALUES ('user-1', 't@example.com')")
    conn.execute(
        "INSERT INTO account_credentials (user_id, source) VALUES ('user-1', 'amex')"
    )
    conn.execute(
        """
        INSERT INTO account_data
        (user_id, source, display_name, icon, color, data_enc, synced_at,
         sync_status, extraction_status)
        VALUES ('user-1', 'amex', 'Amex', '', '', ?, ?, 'ok', ?)
        """,
        (
            _plain_encrypt("user-1", {"items": [], "sync_status": "ok"}),
            datetime.now(timezone.utc).isoformat(),
            EXTRACTION_PENDING,
        ),
    )
    conn.commit()
    yield conn
    conn.close()


def _points_item(value: str = "125,000") -> dict:
    return {
        "key": "points_balance",
        "label": "Membership Rewards Points",
        "value": value,
        "_type": "points_balance",
        "confidence": 0.95,
    }


def test_successful_extraction_creates_snapshot(snap_db):
    snap = create_account_snapshot_from_extraction(
        snap_db,
        user_id="user-1",
        provider="amex",
        fields=[_points_item()],
        verified_at="2026-07-12T18:00:00+00:00",
        access_cycle_id="cycle-a",
        correlation_id="cycle-a",
        data_source="extension",
    )
    assert snap is not None
    assert snap.schema_version == SNAPSHOT_SCHEMA_VERSION
    assert snap.access_cycle_id == "cycle-a"
    assert snap.evidence_refs
    assert snap.evidence_refs[0].kind == "account_data"
    assert "points_balance" in snap.evidence_refs[0].field_keys
    assert snap.rewards
    assert snap.rewards[0]["key"] == "points_balance"

    latest = get_latest_successful_snapshot(snap_db, "user-1", "amex")
    assert latest is not None
    assert latest.snapshot_id == snap.snapshot_id


def test_failed_extraction_preserves_prior_snapshot(snap_db):
    first = create_account_snapshot_from_extraction(
        snap_db,
        user_id="user-1",
        provider="amex",
        fields=[_points_item("100,000")],
        verified_at="2026-07-12T17:00:00+00:00",
        access_cycle_id="cycle-a",
    )
    assert first is not None

    failed = create_account_snapshot_from_extraction(
        snap_db,
        user_id="user-1",
        provider="amex",
        fields=[],
        verified_at="2026-07-12T18:00:00+00:00",
        access_cycle_id="cycle-b",
    )
    assert failed is None

    latest = get_latest_successful_snapshot(snap_db, "user-1", "amex")
    assert latest is not None
    assert latest.snapshot_id == first.snapshot_id
    assert latest.display_items()[0]["value"] == "100,000"


def test_partial_and_running_extraction_do_not_replace_snapshot(snap_db):
    first = create_account_snapshot_from_extraction(
        snap_db,
        user_id="user-1",
        provider="amex",
        fields=[_points_item("90,000")],
        verified_at="2026-07-12T16:00:00+00:00",
        access_cycle_id="cycle-test",
    )
    assert first is not None

    partial = create_account_snapshot_from_extraction(
        snap_db,
        user_id="user-1",
        provider="amex",
        fields=[
            {
                "key": "points_balance",
                "label": "Points",
                "value": "—",
                "_type": "points_balance",
            }
        ],
        verified_at="2026-07-12T17:00:00+00:00",
        access_cycle_id="cycle-test",
    )
    assert partial is None

    latest = get_latest_successful_snapshot(snap_db, "user-1", "amex")
    assert latest.snapshot_id == first.snapshot_id


def test_multiple_snapshots_retain_history_newest_active(snap_db):
    a = create_account_snapshot_from_extraction(
        snap_db,
        user_id="user-1",
        provider="amex",
        fields=[_points_item("10,000")],
        verified_at="2026-07-12T10:00:00+00:00",
        access_cycle_id="cycle-a",
    )
    b = create_account_snapshot_from_extraction(
        snap_db,
        user_id="user-1",
        provider="amex",
        fields=[_points_item("20,000")],
        verified_at="2026-07-12T11:00:00+00:00",
        access_cycle_id="cycle-b",
    )
    history = list_account_snapshots(snap_db, "user-1", "amex")
    assert len(history) == 2
    latest = get_latest_successful_snapshot(snap_db, "user-1", "amex")
    assert latest.snapshot_id == b.snapshot_id
    assert latest.display_items()[0]["value"] == "20,000"
    assert {h.snapshot_id for h in history} == {a.snapshot_id, b.snapshot_id}


def test_schema_version_and_evidence_ref_stored(snap_db):
    snap = create_account_snapshot_from_extraction(
        snap_db,
        user_id="user-1",
        provider="amex",
        fields=[_points_item()],
        verified_at="2026-07-12T12:00:00+00:00",
        access_cycle_id="corr-1",
        pipeline_run_id="run-xyz",
    )
    meta = snap.to_metadata_dict()
    assert meta["schema_version"] == SNAPSHOT_SCHEMA_VERSION
    assert meta["evidence_ref_count"] == 1
    assert snap.evidence_refs[0].pipeline_run_id == "run-xyz"
    assert snap.correlation_id == "corr-1"


def test_apply_adapter_payload_creates_snapshot(snap_db):
    account = apply_adapter_payload(
        snap_db,
        "user-1",
        "amex",
        {"items": [_points_item("55,000")], "sync_status": "ok"},
        data_source="extension",
        synced_at="2026-07-12T13:00:00+00:00",
        encrypt_fn=_plain_encrypt,
        decrypt_fn=_plain_decrypt,
        access_cycle_id="adapter-cycle",
    )
    assert account.extraction_status == EXTRACTION_COMPLETE
    latest = get_latest_successful_snapshot(snap_db, "user-1", "amex")
    assert latest is not None
    assert latest.display_items()[0]["value"] == "55,000"
    assert latest.access_cycle_id == "adapter-cycle"


def test_customer_ui_renders_latest_snapshot_not_live_blob(snap_db):
    create_account_snapshot_from_extraction(
        snap_db,
        user_id="user-1",
        provider="amex",
        fields=[_points_item("77,000")],
        verified_at="2026-07-12T14:00:00+00:00",
        access_cycle_id="cycle-test",
    )
    snap_db.execute(
        "UPDATE account_data SET data_enc=?, extraction_status=? WHERE user_id=? AND source=?",
        (
            _plain_encrypt(
                "user-1",
                {
                    "items": [
                        {
                            "key": "points_balance",
                            "label": "Points",
                            "value": "1",
                            "_type": "points_balance",
                        }
                    ],
                    "sync_status": "ok",
                    "extraction_status": EXTRACTION_PENDING,
                },
            ),
            EXTRACTION_PENDING,
            "user-1",
            "amex",
        ),
    )
    snap_db.commit()

    items = load_snapshot_display_items(snap_db, "user-1", "amex")
    assert items[0]["value"] == "77,000"

    items2, snap = load_customer_snapshot_items(snap_db, "user-1", "amex")
    assert snap is not None
    assert items2[0]["value"] == "77,000"


def test_background_verification_preserves_snapshot(snap_db):
    first = create_account_snapshot_from_extraction(
        snap_db,
        user_id="user-1",
        provider="amex",
        fields=[_points_item("88,000")],
        verified_at="2026-07-12T09:00:00+00:00",
        access_cycle_id="cycle-ready",
    )
    request_session_verification(snap_db, "user-1", "amex")
    snap_db.execute(
        "UPDATE account_data SET extraction_status=? WHERE user_id=? AND source=?",
        (EXTRACTION_PENDING, "user-1", "amex"),
    )
    snap_db.commit()

    latest = get_latest_successful_snapshot(snap_db, "user-1", "amex")
    assert latest.snapshot_id == first.snapshot_id
    assert load_snapshot_display_items(snap_db, "user-1", "amex")[0]["value"] == "88,000"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_snapshots.db")
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
    email = f"snap_{secrets.token_hex(4)}@test.local"
    c.post(
        "/signup",
        data={"email": email, "password": "pass12345", "_csrf": csrf},
    )
    return c, mighty, email


def _uid(client):
    with client.session_transaction() as sess:
        return sess["user_id"]


def _seed_ready_amex(mighty, uid: str, *, points: str = "42,000", cycle: str = "cycle-1"):
    db = mighty.get_db()
    ensure_account_snapshot_tables(db)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    item = _points_item(points)
    ts = mighty.iso()
    db.execute(
        "INSERT OR REPLACE INTO account_credentials "
        "(user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (uid, "amex", "", "", "", ts, ts),
    )
    db.execute(
        """
        INSERT OR REPLACE INTO account_data
        (user_id, source, display_name, icon, color, data_enc, synced_at,
         sync_status, extraction_status, connection_status)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            uid,
            "amex",
            "American Express",
            "",
            "",
            mighty.encrypt_account_data(
                uid,
                {
                    "items": [item],
                    "sync_status": "ok",
                    "extraction_status": EXTRACTION_COMPLETE,
                    "access_cycle_id": cycle,
                    "extraction_access_cycle_id": cycle,
                    "data_source": "extension",
                },
            ),
            now_iso,
            "ok",
            EXTRACTION_COMPLETE,
            "connected",
        ),
    )
    upsert_provider_session_state(
        db,
        uid,
        SessionEvidence(
            provider="amex",
            state="connected",
            evidence_type="authenticated_private_data",
            evidence_summary="private data visible",
            observed_at=now,
            source="test",
            confidence="high",
        ),
    )
    create_account_snapshot_from_extraction(
        db,
        user_id=uid,
        provider="amex",
        fields=[item],
        verified_at=now_iso,
        access_cycle_id=cycle,
        correlation_id=cycle,
        data_source="extension",
    )
    db.commit()


def test_dashboard_home_popup_api_use_identical_snapshot(client):
    c, mighty, _email = client
    uid = _uid(c)

    with mighty.app.app_context():
        _seed_ready_amex(mighty, uid, points="42,000", cycle="shared-cycle")
        db = mighty.get_db()
        latest = get_latest_successful_snapshot(db, uid, "amex")
        assert latest is not None
        snap_id = latest.snapshot_id

        accounts, _ = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"amex": "American Express"},
            login_url_fn=lambda _s: "https://example.com",
        )
        amex = next(a for a in accounts if a.source == "amex")
        assert amex.snapshot_id == snap_id
        assert amex.snapshot_schema_version == SNAPSHOT_SCHEMA_VERSION

    resp = c.get("/api/account-status")
    assert resp.status_code == 200
    payload = resp.get_json()
    api_amex = next(a for a in payload["accounts"] if a["source"] == "amex")
    assert api_amex["snapshot_id"] == snap_id
    assert api_amex["snapshot_schema_version"] == SNAPSHOT_SCHEMA_VERSION

    dash = c.get("/dashboard")
    assert dash.status_code == 200

    with mighty.app.app_context():
        items = load_snapshot_display_items(mighty.get_db(), uid, "amex")
        assert items[0]["value"] == "42,000"
        by_provider = load_latest_snapshots_by_provider(mighty.get_db(), uid)
        assert by_provider["amex"].snapshot_id == snap_id


def test_no_customer_ui_reads_extraction_state_when_snapshot_exists(client):
    c, mighty, _email = client
    uid = _uid(c)

    with mighty.app.app_context():
        _seed_ready_amex(mighty, uid, points="11,000", cycle="cycle-old")
        db = mighty.get_db()
        prior = get_latest_successful_snapshot(db, uid, "amex")
        db.execute(
            "UPDATE account_data SET data_enc=?, extraction_status=? WHERE user_id=? AND source=?",
            (
                mighty.encrypt_account_data(
                    uid,
                    {
                        "items": [],
                        "sync_status": "ok",
                        "extraction_status": EXTRACTION_FAILED,
                    },
                ),
                EXTRACTION_FAILED,
                uid,
                "amex",
            ),
        )
        db.commit()

        accounts, _ = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"amex": "American Express"},
            login_url_fn=lambda _s: "https://example.com",
        )
        amex = next(a for a in accounts if a.source == "amex")
        assert amex.snapshot_id == prior.snapshot_id

    resp = c.get("/api/account-status")
    api_amex = next(a for a in resp.get_json()["accounts"] if a["source"] == "amex")
    assert api_amex["snapshot_id"] == prior.snapshot_id


def test_admin_snapshot_viewer_and_api(client, monkeypatch):
    c, mighty, email = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    uid = _uid(c)
    snap_id = None

    with mighty.app.app_context():
        _seed_ready_amex(mighty, uid)
        db = mighty.get_db()
        snaps = list_account_snapshots(db, uid, "amex")
        active = get_latest_successful_snapshot(db, uid, "amex")
        snap_id = active.snapshot_id
        html = render_account_snapshots_page(["amex"], "amex", snaps, active=active)
        assert active.snapshot_id[:8] in html
        assert "Evidence" in html or "Normalized" in html

    page = c.get("/admin/account-snapshots?source=amex")
    assert page.status_code == 200
    assert b"Account Snapshots" in page.data or b"Snapshot" in page.data

    api = c.get("/api/admin/account-snapshots?provider=amex")
    assert api.status_code == 200
    body = api.get_json()
    assert body["ok"] is True
    assert body["latest"]["snapshot_id"] == snap_id
    assert body["latest"]["schema_version"] == SNAPSHOT_SCHEMA_VERSION
    assert len(body["history"]) >= 1
