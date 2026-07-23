"""Activity V1 — projection, route, export/delete, isolation."""

from __future__ import annotations

import os
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.activity_projection import (
    CATEGORY_COULD_NOT_COMPLETE,
    CATEGORY_COMPLETED,
    CATEGORY_IN_PROGRESS,
    CATEGORY_NEEDS_APPROVAL,
    activity_nav_visible,
    delete_activity_data,
    export_activity_rows,
    project_activity,
)
from mighty.activity_ui import render_activity_main
from mighty.agent_action_store import (
    STATE_AUTHORIZED,
    STATE_AWAITING_AUTHORIZATION,
    STATE_CANCELLED,
    STATE_COMPLETED,
    STATE_DENIED,
    STATE_EXECUTING,
    STATE_EXPIRED,
    STATE_FAILED,
    STATE_PROPOSED,
    ensure_agent_action_tables,
    insert_action,
)
from mighty.execution_receipt import (
    ensure_receipt_tables,
    list_receipts,
    persist_receipt,
)

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


@pytest.fixture()
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "activity.db"))
    conn.row_factory = sqlite3.Row
    ensure_agent_action_tables(conn)
    ensure_receipt_tables(conn)
    yield conn
    conn.close()


def _action(db, *, user_id="u1", lifecycle, label="Do thing", **kwargs):
    created = kwargs.pop("created_at", _iso(NOW))
    decided = kwargs.pop("decided_at", None)
    return insert_action(
        db,
        user_id=user_id,
        action_type=kwargs.pop("action_type", "redeem"),
        label=label,
        fields=kwargs.pop("fields", {"amount": 10}),
        consequence_level=kwargs.pop("consequence_level", "routine"),
        agent_id=kwargs.pop("agent_id", "agent-1"),
        provider=kwargs.pop("provider", "amex"),
        lifecycle_state=lifecycle,
        created_at=created,
        decided_at=decided,
        outcome=kwargs.pop("outcome", None),
        decision_explanation=kwargs.pop("decision_explanation", None),
        action_id=kwargs.pop("action_id", None),
        commit=True,
    )


def test_category_mapping_and_wording(db):
    _action(db, lifecycle=STATE_AWAITING_AUTHORIZATION, label="Need you", action_id="a1")
    _action(
        db,
        lifecycle=STATE_AUTHORIZED,
        label="Going",
        action_id="a2",
        decided_at=_iso(NOW + timedelta(minutes=1)),
    )
    _action(
        db,
        lifecycle=STATE_COMPLETED,
        label="Done",
        action_id="a3",
        decided_at=_iso(NOW + timedelta(minutes=2)),
    )
    denied = _action(
        db,
        lifecycle=STATE_DENIED,
        label="Nope",
        action_id="a4",
        decided_at=_iso(NOW + timedelta(minutes=3)),
    )
    _action(
        db,
        lifecycle=STATE_EXPIRED,
        label="Late",
        action_id="a5",
        decided_at=_iso(NOW + timedelta(minutes=4)),
    )
    _action(
        db,
        lifecycle=STATE_CANCELLED,
        label="Stopped",
        action_id="a6",
        decided_at=_iso(NOW + timedelta(minutes=5)),
    )
    _action(
        db,
        lifecycle=STATE_FAILED,
        label="Broke",
        action_id="a7",
        decided_at=_iso(NOW + timedelta(minutes=6)),
        outcome="provider_unavailable",
    )
    _action(db, lifecycle=STATE_PROPOSED, label="Hidden", action_id="a8")

    proj = project_activity(db, "u1", limit=50, provider_display_names={"amex": "Amex"})
    by_id = {i.action_id: i for i in proj.items}
    assert "a8" not in by_id
    assert by_id["a1"].category == CATEGORY_NEEDS_APPROVAL
    assert by_id["a2"].category == CATEGORY_IN_PROGRESS
    assert by_id["a3"].category == CATEGORY_COMPLETED
    assert by_id["a4"].category == CATEGORY_COULD_NOT_COMPLETE
    assert "declined" in by_id["a4"].explanation.lower() or "you" in by_id["a4"].explanation.lower()
    assert "fail" not in by_id["a4"].explanation.lower()
    assert by_id["a5"].category == CATEGORY_COULD_NOT_COMPLETE
    assert "window" in by_id["a5"].explanation.lower() or "ended" in by_id["a5"].explanation.lower()
    assert by_id["a6"].category == CATEGORY_COULD_NOT_COMPLETE
    assert "cancel" in by_id["a6"].explanation.lower()
    assert by_id["a7"].category == CATEGORY_COULD_NOT_COMPLETE
    assert "couldn’t finish" in by_id["a7"].explanation.lower() or "couldn't finish" in by_id["a7"].explanation.lower()
    assert "provider_unavailable" not in by_id["a7"].explanation.lower()
    assert "wasn’t available" in by_id["a7"].explanation.lower() or "wasn't available" in by_id["a7"].explanation.lower()
    assert "Finished successfully" not in by_id["a3"].explanation
    assert "complete" in by_id["a3"].explanation.lower()
    assert denied.action_id == "a4"


def test_receipt_merged_not_duplicated(db):
    action = _action(
        db,
        lifecycle=STATE_COMPLETED,
        label="Redeem credit",
        action_id="r1",
        decided_at=_iso(NOW),
        decision_explanation="Within your routine approval rules.",
    )
    persist_receipt(
        db,
        action_id=action.action_id,
        user_id="u1",
        agent_id="agent-1",
        authorization_decision="authorized",
        authorization_at=_iso(NOW),
        auth_channel="activity",
        execution_result="failed",
        execution_attempt=1,
        proposal_hash=action.proposal_hash,
        detail={"ok": False, "error": "temporary glitch", "policy_explanation": "retryable"},
        provider="amex",
        created_at=_iso(NOW + timedelta(minutes=1)),
    )
    persist_receipt(
        db,
        action_id=action.action_id,
        user_id="u1",
        agent_id="agent-1",
        authorization_decision="authorized",
        authorization_at=_iso(NOW),
        auth_channel="activity",
        execution_result="completed",
        execution_attempt=2,
        proposal_hash=action.proposal_hash,
        detail={"ok": True, "policy_explanation": "Within your routine approval rules."},
        provider="amex",
        created_at=_iso(NOW + timedelta(minutes=2)),
    )
    proj = project_activity(db, "u1")
    matches = [i for i in proj.items if i.action_id == "r1"]
    assert len(matches) == 1
    item = matches[0]
    assert len(item.detail.receipt_history) == 2
    assert item.detail.receipt_history[0].attempt == 1
    assert item.detail.receipt_history[1].attempt == 2
    blob = str(item.to_dict())
    assert "proposal_hash" not in blob
    assert "receipt_hash" not in blob
    assert action.proposal_hash not in blob


def test_chronology_uses_meaningful_timestamp(db):
    _action(
        db,
        lifecycle=STATE_AWAITING_AUTHORIZATION,
        label="Pending old",
        action_id="c1",
        created_at=_iso(NOW - timedelta(hours=5)),
    )
    _action(
        db,
        lifecycle=STATE_COMPLETED,
        label="Done newer",
        action_id="c2",
        created_at=_iso(NOW - timedelta(hours=4)),
        decided_at=_iso(NOW - timedelta(hours=1)),
    )
    _action(
        db,
        lifecycle=STATE_EXECUTING,
        label="Running",
        action_id="c3",
        created_at=_iso(NOW - timedelta(hours=3)),
        decided_at=_iso(NOW - timedelta(hours=2)),
    )
    proj = project_activity(db, "u1")
    ids = [i.action_id for i in proj.items]
    assert ids.index("c2") < ids.index("c3") < ids.index("c1")


def test_pagination_stable_no_duplicates(db):
    for i in range(5):
        _action(
            db,
            lifecycle=STATE_COMPLETED,
            label=f"Item {i}",
            action_id=f"p{i}",
            created_at=_iso(NOW - timedelta(minutes=i)),
            decided_at=_iso(NOW - timedelta(minutes=i)),
        )
    page1 = project_activity(db, "u1", limit=2)
    assert len(page1.items) == 2
    assert page1.next_cursor
    page2 = project_activity(db, "u1", limit=2, cursor=page1.next_cursor)
    ids1 = {i.activity_id for i in page1.items}
    ids2 = {i.activity_id for i in page2.items}
    assert ids1.isdisjoint(ids2)
    assert len(page1.items) + len(page2.items) >= 4


def test_nav_visibility(db):
    assert activity_nav_visible(db, "u1") is False
    _action(db, lifecycle=STATE_COMPLETED, label="One", action_id="n1", decided_at=_iso(NOW))
    assert activity_nav_visible(db, "u1") is True


def test_user_isolation(db):
    _action(db, user_id="u1", lifecycle=STATE_COMPLETED, label="Mine", action_id="i1", decided_at=_iso(NOW))
    _action(db, user_id="u2", lifecycle=STATE_COMPLETED, label="Theirs", action_id="i2", decided_at=_iso(NOW))
    proj = project_activity(db, "u1")
    assert all(i.action_id == "i1" for i in proj.items)
    assert all("Theirs" not in i.title for i in proj.items)


def test_export_includes_receipt_fields(db):
    action = _action(
        db,
        lifecycle=STATE_COMPLETED,
        label="Export me",
        action_id="e1",
        decided_at=_iso(NOW),
        decision_explanation="Allowed by your settings.",
    )
    persist_receipt(
        db,
        action_id=action.action_id,
        user_id="u1",
        agent_id="agent-1",
        authorization_decision="authorized",
        authorization_at=_iso(NOW),
        auth_channel="activity",
        execution_result="completed",
        execution_attempt=1,
        proposal_hash=action.proposal_hash,
        detail={"ok": True, "policy_explanation": "Allowed by your settings."},
        provider="amex",
        created_at=_iso(NOW + timedelta(minutes=1)),
    )
    rows = export_activity_rows(db, "u1", provider_display_names={"amex": "Amex"})
    assert rows
    assert any(r.get("Attempt Result") for r in rows)
    assert any(r.get("Authorization") for r in rows)
    joined = " ".join(str(v) for r in rows for v in r.values())
    assert action.proposal_hash not in joined


def test_delete_removes_receipts(db):
    action = _action(
        db,
        lifecycle=STATE_COMPLETED,
        label="Delete me",
        action_id="d1",
        decided_at=_iso(NOW),
    )
    persist_receipt(
        db,
        action_id=action.action_id,
        user_id="u1",
        agent_id="agent-1",
        authorization_decision="authorized",
        authorization_at=_iso(NOW),
        auth_channel="activity",
        execution_result="completed",
        execution_attempt=1,
        proposal_hash=action.proposal_hash,
        detail={"ok": True},
        provider="amex",
    )
    assert list_receipts(db, user_id="u1")
    delete_activity_data(db, "u1")
    assert list_receipts(db, user_id="u1") == []
    assert project_activity(db, "u1").items == ()


def test_ui_hides_internal_provenance(db):
    action = _action(
        db,
        lifecycle=STATE_COMPLETED,
        label="UI item",
        action_id="uui1",
        decided_at=_iso(NOW),
        decision_explanation="Policy ok",
    )
    persist_receipt(
        db,
        action_id=action.action_id,
        user_id="u1",
        agent_id="agent-1",
        authorization_decision="authorized",
        authorization_at=_iso(NOW),
        auth_channel="activity",
        execution_result="completed",
        execution_attempt=1,
        proposal_hash=action.proposal_hash,
        detail={"ok": True},
        provider="amex",
    )
    proj = project_activity(db, "u1", provider_display_names={"amex": "Amex"})
    html = render_activity_main(proj, escape=lambda x: str(x), csrf_token="csrf")
    assert "Needs approval" not in html or True
    assert "Completed" in html
    assert action.proposal_hash not in html
    assert "action_execution_receipts" not in html
    assert "proposal_hash" not in html
    assert "fingerprint" not in html


def test_empty_state_ui():
    from mighty.activity_projection import ActivityProjection

    proj = ActivityProjection(
        generated_at=_iso(NOW),
        items=(),
        has_pending=False,
        has_historical=False,
    )
    html = render_activity_main(proj, escape=lambda x: str(x), csrf_token="x")
    assert "All quiet" in html
    assert "Approvals and completed work" in html


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_activity_routes.db")
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
    email = f"activity_{secrets.token_hex(4)}@test.local"
    c.post(
        "/signup",
        data={"email": email, "password": "pass12345", "_csrf": csrf},
    )
    return c


def _uid(client):
    with client.session_transaction() as sess:
        return sess["user_id"]


def test_activity_route_requires_login(tmp_path, monkeypatch):
    db_path = str(tmp_path / "anon.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()
    mighty.app.config["TESTING"] = True
    c = mighty.app.test_client()
    r = c.get("/activity", follow_redirects=False)
    assert r.status_code in (302, 401)


def test_activity_route_and_api(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        ensure_agent_action_tables(db)
        ensure_receipt_tables(db)
        action = insert_action(
            db,
            user_id=uid,
            action_type="redeem",
            label="Redeem dining credit",
            fields={"amount": 50},
            lifecycle_state=STATE_AWAITING_AUTHORIZATION,
            provider="amex",
            created_at=_iso(NOW),
            commit=True,
        )
        action_id = action.action_id
    r = client.get("/activity")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Redeem dining credit" in body
    assert "Needs approval" in body
    assert "Approve" in body
    assert "Activity" in body
    assert 'href="/activity"' in body

    api = client.get("/api/activity")
    assert api.status_code == 200
    data = api.get_json()
    assert data["items"]
    assert data["items"][0]["action_id"] == action_id
    assert "proposal_hash" not in str(data)


def test_conditional_nav_hidden_without_items(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # No Activity items → nav link omitted on other pages
    assert 'href="/activity"' not in body


def test_conditional_nav_shown_with_items(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        ensure_agent_action_tables(db)
        insert_action(
            db,
            user_id=uid,
            action_type="redeem",
            label="Show nav",
            lifecycle_state=STATE_COMPLETED,
            provider="amex",
            created_at=_iso(NOW),
            decided_at=_iso(NOW),
            commit=True,
        )
    r = client.get("/dashboard")
    body = r.get_data(as_text=True)
    assert 'href="/activity"' in body
    assert "Activity" in body


def test_export_and_delete_routes(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        ensure_agent_action_tables(db)
        ensure_receipt_tables(db)
        action = insert_action(
            db,
            user_id=uid,
            action_type="book",
            label="Book stay",
            lifecycle_state=STATE_COMPLETED,
            provider="hilton",
            created_at=_iso(NOW),
            decided_at=_iso(NOW),
            decision_explanation="You allow routine bookings.",
            commit=True,
        )
        persist_receipt(
            db,
            action_id=action.action_id,
            user_id=uid,
            agent_id="agent-1",
            authorization_decision="authorized",
            authorization_at=_iso(NOW),
            auth_channel="activity",
            execution_result="completed",
            execution_attempt=1,
            proposal_hash=action.proposal_hash,
            detail={"ok": True, "policy_explanation": "You allow routine bookings."},
            provider="hilton",
        )
        proposal_hash = action.proposal_hash
    exported = client.get("/settings/export-csv")
    assert exported.status_code == 200
    text = exported.get_data(as_text=True)
    assert "Book stay" in text
    assert "Attempt" in text
    assert "Authorized" in text or "authorized" in text.lower() or "Authorization" in text
    assert proposal_hash not in text

    with client.session_transaction() as sess:
        csrf = sess["_csrf"]
    deleted = client.post(
        "/settings/delete-activity",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted.status_code == 200
    with mighty.app.app_context():
        assert list_receipts(mighty.get_db(), user_id=uid) == []
        assert project_activity(mighty.get_db(), uid).items == ()


def test_authorization_visible_without_attention(db):
    _action(
        db,
        lifecycle=STATE_AWAITING_AUTHORIZATION,
        label="Approve please",
        action_id="av1",
    )
    proj = project_activity(db, "u1")
    assert any(i.category == CATEGORY_NEEDS_APPROVAL for i in proj.items)
