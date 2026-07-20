"""Tests for Provider Registry discovery, capabilities, and multi-provider UI."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from html import escape as html_escape

import pytest

from mighty.access_state_publication import serialize_access_state
from mighty.provider_registry import (
    ManagedProvider,
    ProviderPlatformCapabilities,
    ProviderRegistry,
    build_amex_provider,
    get_provider_registry,
    register_amex,
    reset_provider_registry_for_tests,
)
from mighty.provider_runtime_control_center import (
    ACCESS_HEALTH_HEALTHY,
    BROWSER_STATUS_HEALTHY,
    RECOVERY_STATUS_IDLE,
    RUNTIME_STATUS_RUNNING,
    AccessState,
)
from mighty.runtime_access_state import (
    load_and_render_runtime_access_provider_list,
    load_runtime_access_provider_cards,
    render_runtime_access_card,
    render_runtime_access_provider_list,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_provider_registry_for_tests(include_amex=True)
    yield
    reset_provider_registry_for_tests(include_amex=True)


def _amex_payload(**overrides):
    state = AccessState(
        provider="amex",
        runtime_status=RUNTIME_STATUS_RUNNING,
        browser_status=BROWSER_STATUS_HEALTHY,
        recovery_planner_status=RECOVERY_STATUS_IDLE,
        authentication_state="SIGNED_IN",
        access_health=ACCESS_HEALTH_HEALTHY,
        session_started_at="2026-07-20T10:00:00+00:00",
        last_verification_at="2026-07-20T11:00:00+00:00",
        last_keepalive_at="2026-07-20T11:05:00+00:00",
        ready_for_extraction=True,
        ready_for_connector=True,
        updated_at="2026-07-20T11:06:00+00:00",
    )
    payload = serialize_access_state(state, runtime_instance_id="inst-amex-1")
    payload.update(overrides)
    return payload


def _delta_provider() -> ManagedProvider:
    return ManagedProvider(
        provider_id="delta",
        display_name="Delta",
        capabilities=ProviderPlatformCapabilities(
            verification=True,
            keepalive=False,
            recovery=False,
            snapshots=True,
            connector_readiness=False,
        ),
        open_url="https://www.delta.com/",
        sort_order=20,
    )


def test_registry_discovers_amex_by_default():
    registry = get_provider_registry()
    providers = registry.list_providers()
    assert len(providers) == 1
    assert providers[0].provider_id == "amex"
    assert providers[0].display_name == "American Express"
    assert registry.is_registered("amex")
    assert registry.provider_ids() == ("amex",)


def test_register_amex_idempotent_and_capabilities():
    registry = ProviderRegistry()
    first = register_amex(registry)
    second = register_amex(registry)
    assert first.provider_id == second.provider_id == "amex"
    assert registry.list_providers() == (first,)
    caps = first.capabilities
    assert caps.verification is True
    assert caps.keepalive is True
    assert caps.recovery is True
    assert caps.snapshots is True
    assert caps.connector_readiness is True
    assert "verification" in caps.enabled_names()
    assert build_amex_provider().to_dict()["provider_id"] == "amex"


def test_adding_provider_requires_registration_not_dashboard_branch():
    registry = reset_provider_registry_for_tests(include_amex=True)
    registry.register(_delta_provider())
    ids = get_provider_registry().provider_ids()
    assert ids == ("amex", "delta")
    assert get_provider_registry().require("delta").display_name == "Delta"


def test_capability_presentation_omits_unsupported_rows():
    limited = ManagedProvider(
        provider_id="stub",
        display_name="Stub Bank",
        capabilities=ProviderPlatformCapabilities(
            verification=True,
            keepalive=False,
            recovery=False,
            snapshots=False,
            connector_readiness=False,
        ),
    )
    reset_provider_registry_for_tests(providers=(limited,), include_amex=False)
    from mighty.runtime_access_state import build_runtime_access_presentation

    presentation = build_runtime_access_presentation(None, provider="stub")
    html = render_runtime_access_card(
        presentation,
        escape=html_escape,
        capabilities=limited.capabilities,
    )
    assert "Stub Bank access" in html
    assert "Last verified" in html
    assert "Ready for extraction" not in html
    assert "Ready for connector" not in html
    assert "Recovery state" not in html
    assert "Recovery counts" not in html
    assert "Last keepalive" not in html
    assert "Snapshot freshness" not in html
    assert 'data-provider-capabilities="verification"' in html


def test_amex_card_still_shows_full_capability_surface():
    presentation_row = {
        "payload": _amex_payload(),
        "updated_at": "2026-07-20T11:06:00+00:00",
    }
    from mighty.runtime_access_state import build_runtime_access_presentation

    presentation = build_runtime_access_presentation(presentation_row, provider="amex")
    html = render_runtime_access_card(presentation, escape=html_escape)
    assert "American Express access" in html
    assert "Last verified" in html
    assert "Recovery state" in html
    assert "Ready for connector" in html
    assert "View details" in html
    assert "Last keepalive" in html
    assert "Snapshot freshness" in html
    assert "verification" in html
    assert "keepalive" in html
    assert "recovery" in html
    assert "snapshots" in html
    assert "connector_readiness" in html


def test_provider_list_renders_one_card_per_registered_provider():
    from mighty.access_timeline import build_provider_operations_details
    from mighty.runtime_access_state import (
        RuntimeAccessProviderCard,
        build_runtime_access_presentation,
    )

    registry = reset_provider_registry_for_tests(include_amex=True)
    registry.register(_delta_provider())
    amex = registry.require("amex")
    delta = registry.require("delta")
    cards = [
        RuntimeAccessProviderCard(
            managed=amex,
            presentation=build_runtime_access_presentation(None, provider="amex"),
            operations=build_provider_operations_details(None, [], provider="amex"),
        ),
        RuntimeAccessProviderCard(
            managed=delta,
            presentation=build_runtime_access_presentation(None, provider="delta"),
            operations=build_provider_operations_details(None, [], provider="delta"),
        ),
    ]
    html = render_runtime_access_provider_list(cards, escape=html_escape)
    assert 'data-provider-manager="1"' in html
    assert html.count('data-runtime-access="1"') == 2
    assert 'data-provider="amex"' in html
    assert 'data-provider="delta"' in html
    assert "American Express access" in html
    assert "Delta access" in html
    # Delta lacks recovery/connector readiness in ops + compact rows
    amex_idx = html.index('data-provider="amex"')
    delta_idx = html.index('data-provider="delta"')
    assert amex_idx < delta_idx
    delta_slice = html[delta_idx:]
    assert "Ready for connector" not in delta_slice
    assert "Last keepalive" not in delta_slice
    assert "Recovery metrics" not in delta_slice
    assert "Last verified" in delta_slice
    assert "Snapshot freshness" in delta_slice


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_provider_manager.db")
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
    c.post(
        "/signup",
        data={
            "email": f"pm_{secrets.token_hex(4)}@test.local",
            "password": "pass12345",
            "_csrf": csrf,
        },
    )
    return c


def _user_api(client):
    import app as mighty

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        row = mighty.get_db().execute(
            "SELECT id, api_key FROM users WHERE id=?",
            (uid,),
        ).fetchone()
        return row["id"], row["api_key"]


def test_api_rejects_unregistered_provider(client):
    _, api_key = _user_api(client)
    resp = client.get(
        "/api/runtime/access-state?provider=not-a-provider",
        headers={"X-Mighty-Key": api_key},
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["ok"] is False
    assert "not registered" in body["error"]
    assert "amex" in body["registered_providers"]


def test_api_returns_capabilities_for_registered_provider(client):
    _, api_key = _user_api(client)
    resp = client.get(
        "/api/runtime/access-state?provider=amex",
        headers={"X-Mighty-Key": api_key},
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["capabilities"]["verification"] is True
    assert body["capabilities"]["keepalive"] is True
    assert body["provider"]["provider_id"] == "amex"


def test_dashboard_renders_provider_manager_list(client):
    import app as mighty

    uid, api_key = _user_api(client)
    headers = {"X-Mighty-Key": api_key, "Content-Type": "application/json"}
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    client.post(
        "/api/runtime/access-state",
        json=_amex_payload(updated_at=now),
        headers=headers,
    )

    # Register a second provider for multi-provider dashboard behavior
    registry = get_provider_registry()
    registry.register(_delta_provider())

    with mighty.app.app_context():
        html = load_and_render_runtime_access_provider_list(
            mighty.get_db(), uid, escape=html_escape
        )
    assert 'data-provider-manager="1"' in html
    assert html.count('data-runtime-access="1"') == 2
    assert "American Express access" in html
    assert "Delta access" in html

    page = client.get("/dashboard")
    assert page.status_code == 200
    page_html = page.get_data(as_text=True)
    assert 'data-provider-manager="1"' in page_html
    assert 'data-provider="amex"' in page_html
    assert 'data-provider="delta"' in page_html
    assert "_pollRuntimeAccessState" in page_html
    assert "encodeURIComponent(provider)" in page_html


def test_load_provider_cards_from_db_for_each_registered(client):
    import app as mighty

    uid, api_key = _user_api(client)
    headers = {"X-Mighty-Key": api_key, "Content-Type": "application/json"}
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    client.post(
        "/api/runtime/access-state",
        json=_amex_payload(updated_at=now),
        headers=headers,
    )
    get_provider_registry().register(_delta_provider())

    with mighty.app.app_context():
        cards = load_runtime_access_provider_cards(mighty.get_db(), uid)
    assert [c.managed.provider_id for c in cards] == ["amex", "delta"]
    assert cards[0].presentation.provider == "amex"
    assert cards[0].presentation.status == "healthy"
    assert cards[1].presentation.status == "never_reported"
    assert cards[1].managed.capabilities.keepalive is False
