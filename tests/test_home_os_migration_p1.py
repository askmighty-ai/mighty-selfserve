"""Migration P1 — Home OS as default staging landing."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    AttentionClass,
    AttentionCtaKey,
    AttentionItem,
    AttentionReason,
    AttentionSourceKind,
    AttentionUrgency,
    REASON_LOGIN,
)
from mighty.home_os.adapters import attention_items_to_work_items
from mighty.home_os.compose import (
    SIM_AUTH_REPAIR_COMPLETION,
    SIM_EPHEMERAL_SCENARIO,
    compose_for_authenticated_user,
    compose_for_ephemeral,
)
from mighty.home_os.gate import (
    LEGACY_DASHBOARD_PATH,
    default_app_path,
    home_os_is_default_landing,
)
from mighty.workitem.projection import project_home
from mighty.workitem.projection_inputs import CanonicalModels


AS_OF = datetime(2026, 7, 25, 16, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def home_os_default_env(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("HOME_OS_ENABLED", "true")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "staging")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "staging")
    monkeypatch.delenv("MIGHTY_ENV", raising=False)


@pytest.fixture()
def client(tmp_path, monkeypatch, home_os_default_env):
    db_path = str(tmp_path / "home_os_mig.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()
    mighty.app.config["TESTING"] = True
    return mighty.app.test_client()


def _seed_user(app_module, *, email="mig@test.local"):
    import secrets

    uid = secrets.token_hex(8)
    with app_module.app.app_context():
        app_module.get_db().execute(
            "INSERT INTO users (id,email,password_hash,api_key,created_at,preferred_name) "
            "VALUES (?,?,?,?,?,?)",
            (
                uid,
                email,
                app_module.hash_pw("password123"),
                "mk_" + secrets.token_hex(8),
                "2026-07-25T00:00:00+00:00",
                "Alex",
            ),
        )
        app_module.get_db().commit()
    return uid


class TestDefaultLanding:
    def test_home_os_is_default_when_enabled(self, home_os_default_env):
        assert home_os_is_default_landing() is True
        assert default_app_path() == "/home"

    def test_legacy_dashboard_not_default(self, client):
        import app as mighty

        uid = _seed_user(mighty)
        with client.session_transaction() as sess:
            sess["user_id"] = uid
            sess["email"] = "mig@test.local"
        resp = client.get("/dashboard", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/home")

    def test_legacy_dashboard_explicit_route(self, client):
        import app as mighty

        uid = _seed_user(mighty)
        with client.session_transaction() as sess:
            sess["user_id"] = uid
        resp = client.get(LEGACY_DASHBOARD_PATH, follow_redirects=False)
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        # Legacy shell still has sidebar chrome; Home OS does not.
        assert "sidebar" in body.lower() or "Dashboard" in body

    def test_login_lands_on_home(self, client):
        import app as mighty

        _seed_user(mighty, email="login-home@test.local")
        # GET login page for csrf
        login_page = client.get("/login").get_data(as_text=True)
        import re

        token = re.search(r'name="_csrf" value="([^"]+)"', login_page).group(1)
        resp = client.post(
            "/login",
            data={
                "email": "login-home@test.local",
                "password": "password123",
                "_csrf": token,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/home")

    def test_research_home_redirects_to_home_os(self, client):
        resp = client.get("/research/home", follow_redirects=False)
        assert resp.status_code == 302
        assert "/research/home-os" in resp.headers["Location"]


class TestNoDuplicateConcepts:
    def test_home_os_has_single_status_work_coverage_proof(self, client):
        client.get("/research/home-os")
        body = client.get("/home").get_data(as_text=True)
        assert body.count('data-region="status"') == 1
        assert body.count('data-region="work-queue"') == 1
        assert body.count('data-region="coverage"') == 1
        assert body.count('data-region="proof"') == 1
        assert "home-v2" not in body
        assert 'data-research-preview="1"' not in body
        assert "class=\"sidebar\"" not in body
        # No parallel Attention/Living Calm hero markers
        assert "dash-truth" not in body
        assert "executive-briefing" not in body


class TestRealDataProjection:
    def test_attention_adapter_maps_auth_blocker(self):
        item = AttentionItem(
            schema_version=ATTENTION_ITEM_SCHEMA_VERSION,
            attention_id="att_amex_login",
            user_id="u1",
            attention_class=AttentionClass.AUTH_BLOCKER,
            urgency=AttentionUrgency.BLOCKER,
            provider="amex",
            fingerprint="auth:amex:needs_human",
            reason=AttentionReason(code=REASON_LOGIN),
            cta_key=AttentionCtaKey.START_PROVIDER_LOGIN,
            source_kind=AttentionSourceKind.AUTH,
            source_ref="auth_truth:u1:amex",
            observed_at="2026-07-25T12:00:00+00:00",
            becomes_stale_at=None,
            interruption_expected=True,
        )
        work = attention_items_to_work_items([item], as_of=AS_OF)
        assert len(work) == 1
        assert work[0].type.value == "interrupt"
        assert work[0].provider == "amex"
        home = project_home(CanonicalModels(work_items=work), (), as_of=AS_OF)
        assert home.expanded_work_item_id == work[0].id
        assert home.silence is False

    def test_ephemeral_simulation_tags_explicit(self):
        result = compose_for_ephemeral(as_of=AS_OF)
        assert SIM_EPHEMERAL_SCENARIO in result.simulation_tags
        assert result.authenticated is False

    def test_authenticated_compose_without_accounts_is_calm(self, client):
        import app as mighty

        uid = _seed_user(mighty)
        with mighty.app.app_context():
            result = compose_for_authenticated_user(
                mighty.get_db(),
                uid,
                as_of=AS_OF,
                display_name="Alex",
            )
        assert result.authenticated is True
        assert SIM_EPHEMERAL_SCENARIO not in result.simulation_tags
        home = project_home(result.models, (), as_of=AS_OF)
        # No attention / coverage → calm is valid
        assert home.status.value == "calm"
        assert home.silence is True

    def test_home_marks_simulation_tags_for_ephemeral(self, client):
        client.get("/research/home-os")
        body = client.get("/home").get_data(as_text=True)
        assert 'data-home-os="1"' in body
        assert SIM_EPHEMERAL_SCENARIO in body
        assert SIM_AUTH_REPAIR_COMPLETION in body or "ephemeral_marriott" in body
