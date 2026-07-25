"""Home OS Marriott auth-repair vertical slice tests."""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.home_os.commands import (
    cancel_repair,
    complete_repair,
    fail_repair,
    project_slice,
    start_repair,
)
from mighty.home_os.marriott_scenario import (
    WORK_ITEM_ID,
    build_marriott_interrupt,
    initial_canonical_models,
)
from mighty.home_os.session_state import (
    RepairPhase,
    new_slice_state,
)
from mighty.workitem.home_state import HomeStatusMode
from mighty.workitem.model import WorkItemState
from mighty.workitem.projection import project_home
from mighty.workitem.projection_inputs import CanonicalModels


AS_OF = datetime(2026, 7, 25, 15, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def staging_env(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "staging")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "staging")
    monkeypatch.delenv("MIGHTY_ENV", raising=False)
    monkeypatch.setenv("HOME_OS_ENABLED", "true")


@pytest.fixture()
def client(tmp_path, monkeypatch, staging_env):
    db_path = str(tmp_path / "home_os.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)

    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()

    mighty.app.config["TESTING"] = True
    return mighty.app.test_client()


def _csrf(html: str) -> str:
    match = re.search(r'name="_csrf" value="([^"]+)"', html)
    assert match, "csrf token missing"
    return match.group(1)


def _users_count(app_module) -> int:
    with app_module.app.app_context():
        row = app_module.get_db().execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"])


class TestCanonicalProjection:
    def test_interrupt_projected_onto_home(self):
        models = initial_canonical_models(as_of=AS_OF)
        home = project_home(models, (), as_of=AS_OF)
        assert home.silence is False
        assert home.status is HomeStatusMode.NEEDS_USER
        assert home.expanded_work_item_id == WORK_ITEM_ID
        assert home.work_queue[0].title == "Marriott needs a sign-in"
        assert "signed out" in home.work_queue[0].summary.lower()
        assert home.coverage[0].provider == "marriott"
        assert home.coverage[0].authentication.value == "missing"

    def test_projection_determinism(self):
        models = initial_canonical_models(as_of=AS_OF)
        a = project_home(models, (), as_of=AS_OF)
        b = project_home(
            CanonicalModels(
                work_items=tuple(reversed(models.work_items)),
                coverage=tuple(reversed(models.coverage)),
                proof=tuple(reversed(models.proof)),
            ),
            (),
            as_of=AS_OF,
        )
        assert a.to_dict() == b.to_dict()


class TestCommands:
    def test_successful_repair_completes_workitem_and_proof(self):
        slice_state = new_slice_state(as_of=AS_OF)
        start_repair(slice_state, work_item_id=WORK_ITEM_ID, as_of=AS_OF)
        result = complete_repair(
            slice_state, work_item_id=WORK_ITEM_ID, as_of=AS_OF + timedelta(minutes=1)
        )
        assert result.state.repair_phase is RepairPhase.SUCCEEDED
        item = next(i for i in result.state.work_items if i.id == WORK_ITEM_ID)
        assert item.state is WorkItemState.ARCHIVED
        assert item.proof_reference
        assert any("Marriott access restored" in p.summary for p in result.state.proof)
        marriott = next(c for c in result.state.coverage if c.provider == "marriott")
        assert marriott.authentication.value == "valid"
        assert result.home.silence is True
        assert result.home.status is HomeStatusMode.CALM

    def test_failure_preserves_actionable_workitem(self):
        slice_state = new_slice_state(as_of=AS_OF)
        start_repair(slice_state, work_item_id=WORK_ITEM_ID, as_of=AS_OF)
        result = fail_repair(slice_state, work_item_id=WORK_ITEM_ID, as_of=AS_OF)
        assert result.state.repair_phase is RepairPhase.FAILED
        assert result.home.expanded_work_item_id == WORK_ITEM_ID
        assert result.home.silence is False
        marriott = next(c for c in result.state.coverage if c.provider == "marriott")
        assert marriott.authentication.value == "missing"

    def test_cancel_returns_to_same_home_interrupt(self):
        slice_state = new_slice_state(as_of=AS_OF)
        before = project_slice(slice_state, as_of=AS_OF)
        start_repair(slice_state, work_item_id=WORK_ITEM_ID, as_of=AS_OF)
        result = cancel_repair(slice_state, work_item_id=WORK_ITEM_ID, as_of=AS_OF)
        assert result.state.repair_phase is RepairPhase.IDLE
        assert result.home.expanded_work_item_id == before.expanded_work_item_id
        assert result.home.status is before.status


class TestHomeOsHttp:
    def test_research_entry_redirects_to_home_without_users_row(self, client):
        import app as mighty

        before = _users_count(mighty)
        resp = client.get("/research/home-os", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/home")
        assert _users_count(mighty) == before

    def test_home_renders_interrupt_without_sidebar(self, client):
        client.get("/research/home-os", follow_redirects=False)
        resp = client.get("/home")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert 'data-home-os="1"' in body
        assert "Marriott needs a sign-in" in body
        assert "signed out" in body.lower()
        assert 'data-region="status"' in body
        assert 'data-region="work-queue"' in body
        assert 'data-region="coverage"' in body
        assert 'data-region="proof"' in body
        assert "sidebar" not in body.lower()
        assert 'class="sidebar"' not in body
        assert "/credentials" not in body
        assert 'href="/credentials"' not in body
        assert 'href="/activity"' not in body
        assert "Amex · Annual credit refreshed" in body

    def test_no_routine_repair_deeplink(self, client):
        client.get("/research/home-os")
        body = client.get("/home").get_data(as_text=True)
        assert 'action="/home/work/' in body
        assert re.search(r'href="/(credentials|activity|accounts)"', body) is None

    def test_successful_repair_flow_http(self, client):
        client.get("/research/home-os")
        home = client.get("/home").get_data(as_text=True)
        token = _csrf(home)
        start = client.post(
            f"/home/work/{WORK_ITEM_ID}/start",
            data={"_csrf": token},
            follow_redirects=False,
        )
        assert start.status_code == 302
        mid = client.get("/home").get_data(as_text=True)
        assert 'data-repair-phase="in_progress"' in mid
        assert 'role="dialog"' in mid
        assert "Restore Marriott access" in mid
        token = _csrf(mid)
        done = client.post(
            f"/home/work/{WORK_ITEM_ID}/complete",
            data={"_csrf": token},
            follow_redirects=True,
        )
        body = done.get_data(as_text=True)
        assert "You're good." in body or "You&#x27;re good." in body
        assert "Marriott access restored" in body
        assert 'data-auth="valid"' in body
        assert "Marriott needs a sign-in" not in body
        assert 'data-repair-phase="succeeded"' in body

    def test_failure_keeps_actionable_item(self, client):
        client.get("/research/home-os")
        home = client.get("/home").get_data(as_text=True)
        token = _csrf(home)
        client.post(
            f"/home/work/{WORK_ITEM_ID}/start",
            data={"_csrf": token},
            follow_redirects=True,
        )
        mid = client.get("/home").get_data(as_text=True)
        token = _csrf(mid)
        client.post(
            f"/home/work/{WORK_ITEM_ID}/fail",
            data={"_csrf": token},
            follow_redirects=True,
        )
        body = client.get("/home").get_data(as_text=True)
        assert 'data-repair-phase="failed"' in body
        assert "Marriott needs a sign-in" in body
        assert "Try again" in body
        assert 'data-auth="missing"' in body

    def test_cancel_returns_to_interrupt(self, client):
        client.get("/research/home-os")
        home = client.get("/home").get_data(as_text=True)
        token = _csrf(home)
        client.post(
            f"/home/work/{WORK_ITEM_ID}/start",
            data={"_csrf": token},
            follow_redirects=True,
        )
        mid = client.get("/home").get_data(as_text=True)
        token = _csrf(mid)
        client.post(
            f"/home/work/{WORK_ITEM_ID}/cancel",
            data={"_csrf": token},
            follow_redirects=True,
        )
        body = client.get("/home").get_data(as_text=True)
        assert 'data-repair-phase="idle"' in body
        assert "Marriott needs a sign-in" in body
        assert 'role="dialog"' not in body or 'hidden' in body

    def test_modal_accessibility_markers(self, client):
        client.get("/research/home-os")
        home = client.get("/home").get_data(as_text=True)
        token = _csrf(home)
        client.post(
            f"/home/work/{WORK_ITEM_ID}/start",
            data={"_csrf": token},
            follow_redirects=True,
        )
        body = client.get("/home").get_data(as_text=True)
        assert 'role="dialog"' in body
        assert 'aria-modal="true"' in body
        assert "home-os-repair-title" in body
        assert "focus-visible" in body or ":focus-visible" in body

    def test_production_gate_blocks_home(self, client, monkeypatch):
        monkeypatch.setenv("MIGHTY_ENV", "production")
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
        resp = client.get("/research/home-os")
        assert resp.status_code == 404
        resp = client.get("/home")
        assert resp.status_code == 404

    def test_expired_interrupt_superseded(self):
        expired_at = AS_OF - timedelta(minutes=1)
        item = build_marriott_interrupt(
            as_of=AS_OF - timedelta(days=1),
            expires_at=expired_at,
        )
        slice_state = new_slice_state(as_of=AS_OF - timedelta(days=1))
        slice_state.work_items = [item]
        from mighty.home_os.commands import apply_expiration_if_needed

        apply_expiration_if_needed(slice_state, as_of=AS_OF)
        assert slice_state.repair_phase is RepairPhase.EXPIRED
        active = [
            i
            for i in slice_state.work_items
            if i.state in (WorkItemState.VISIBLE, WorkItemState.EXPANDED)
        ]
        assert active
        assert active[0].id != WORK_ITEM_ID or active[0].expires_at > AS_OF
