"""Deterministic Home OS Future Preview — review-only scenario tests."""

from __future__ import annotations

import os
import re
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.home_os.compose import SIM_FUTURE_PREVIEW_SCENARIO, compose_for_future_preview
from mighty.home_os.future_preview import (
    FIXED_AS_OF,
    PERSONA_DISPLAY_NAME,
    initial_canonical_models,
    provider_count,
)
from mighty.home_os.gate import is_future_preview_session
from mighty.workitem.home_state import HomeStatusMode
from mighty.workitem.model import WorkItemType
from mighty.workitem.projection import project_home
from mighty.workitem.projection_inputs import CanonicalModels


@pytest.fixture()
def staging_env(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "staging")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "staging")
    monkeypatch.delenv("MIGHTY_ENV", raising=False)
    monkeypatch.setenv("HOME_OS_ENABLED", "true")


@pytest.fixture()
def client(tmp_path, monkeypatch, staging_env):
    db_path = str(tmp_path / "home_os_future.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)

    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()

    mighty.app.config["TESTING"] = True
    return mighty.app.test_client()


def _users_count(app_module) -> int:
    with app_module.app.app_context():
        row = app_module.get_db().execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"])


class TestCanonicalScenario:
    def test_provider_count_in_range(self):
        assert 20 <= provider_count() <= 30
        models = initial_canonical_models(as_of=FIXED_AS_OF, state="full")
        assert 20 <= len(models.coverage) <= 30

    def test_full_scenario_shape(self):
        models = initial_canonical_models(as_of=FIXED_AS_OF, state="full")
        types = [w.type for w in models.work_items]
        assert types.count(WorkItemType.APPROVAL) == 1
        assert types.count(WorkItemType.OPPORTUNITY) == 3
        assert types.count(WorkItemType.INTERRUPT) == 0
        assert len(models.proof) >= 8

    def test_projection_statuses(self):
        full = project_home(
            initial_canonical_models(as_of=FIXED_AS_OF, state="full"),
            (),
            as_of=FIXED_AS_OF,
        )
        assert full.status is HomeStatusMode.NEEDS_USER
        assert full.work_queue[0].type is WorkItemType.APPROVAL

        opp = project_home(
            initial_canonical_models(as_of=FIXED_AS_OF, state="opportunity"),
            (),
            as_of=FIXED_AS_OF,
        )
        assert opp.status is HomeStatusMode.VALUE_WAITING
        assert all(w.type is WorkItemType.OPPORTUNITY for w in opp.work_queue)

        calm = project_home(
            initial_canonical_models(as_of=FIXED_AS_OF, state="all-clear"),
            (),
            as_of=FIXED_AS_OF,
        )
        assert calm.status is HomeStatusMode.CALM
        assert calm.silence is True
        assert calm.work_queue == ()

    def test_determinism(self):
        a = project_home(
            initial_canonical_models(as_of=FIXED_AS_OF, state="full"),
            (),
            as_of=FIXED_AS_OF,
        )
        b = project_home(
            CanonicalModels(
                work_items=tuple(
                    reversed(initial_canonical_models(as_of=FIXED_AS_OF, state="full").work_items)
                ),
                coverage=tuple(
                    reversed(initial_canonical_models(as_of=FIXED_AS_OF, state="full").coverage)
                ),
                proof=tuple(
                    reversed(initial_canonical_models(as_of=FIXED_AS_OF, state="full").proof)
                ),
            ),
            (),
            as_of=FIXED_AS_OF,
        )
        assert a.to_dict() == b.to_dict()

    def test_compose_tags(self):
        result = compose_for_future_preview(as_of=FIXED_AS_OF, state="full")
        assert result.source == "future_preview"
        assert result.authenticated is False
        assert result.display_name == PERSONA_DISPLAY_NAME
        assert SIM_FUTURE_PREVIEW_SCENARIO in result.simulation_tags


class TestHttpEntry:
    def test_future_preview_entry_seeds_home(self, client):
        import app as mighty

        before = _users_count(mighty)
        resp = client.get("/research/home-os/future", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/home")
        assert _users_count(mighty) == before

        with client.session_transaction() as sess:
            assert is_future_preview_session(sess)

        home = client.get("/home")
        assert home.status_code == 200
        html = home.get_data(as_text=True)
        assert 'data-home-os="1"' in html
        assert "ephemeral_future_preview" in html
        assert "Approve United award booking" in html
        assert PERSONA_DISPLAY_NAME in html
        assert "Sat Jul 25" in html  # fixed clock chrome date

    def test_opportunity_state(self, client):
        client.get("/research/home-os/future?state=opportunity")
        home = client.get("/home")
        html = home.get_data(as_text=True)
        assert "Value is waiting" in html
        assert "Chase → United transfer bonus live" in html
        assert "Approve United award booking" not in html

    def test_all_clear_state(self, client):
        client.get("/research/home-os/future?state=all-clear")
        home = client.get("/home")
        html = home.get_data(as_text=True)
        assert 'data-silence="true"' in html
        assert "good." in html
        assert 'data-region="work-queue" hidden' in html
        assert "Amex · $35 dining credit" in html  # proof still present

    def test_commands_are_review_stubs(self, client):
        client.get("/research/home-os/future")
        home = client.get("/home")
        html = home.get_data(as_text=True)
        assert "Approve United award booking" in html
        match = re.search(r'name="_csrf" value="([^"]+)"', html)
        assert match
        resp = client.post(
            "/home/work/wi_approval_united_award/start",
            data={"_csrf": match.group(1)},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        home2 = client.get("/home")
        html2 = home2.get_data(as_text=True)
        assert "Approve United award booking" in html2
        assert 'data-repair-phase="idle"' in html2

    def test_unavailable_in_production(self, client, monkeypatch):
        monkeypatch.setenv("MIGHTY_ENV", "production")
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
        resp = client.get("/research/home-os/future")
        assert resp.status_code == 404
