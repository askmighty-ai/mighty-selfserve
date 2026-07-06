"""Tests for deterministic extraction (Never Waste Captured Evidence)."""

import json
import os
import secrets
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.deterministic_extraction import (
    enrich_sync_items_from_evidence,
    extract_deterministic_fields,
    iter_evidence_json_blocks,
)
from mighty.observation_catalog import field_keys_to_observations
from mighty.observation_coverage import compute_provider_coverage
from mighty.pipeline_inspector import (
    ensure_pipeline_tables,
    finalize_sync_without_discovery,
    get_run,
    get_run_stages,
    record_inferred_client_stages,
    start_run,
)
from mighty.pipeline_stages import PipelineStageId, RunInitiator, RunStatus, StageStatus

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, f"{name}.txt")) as f:
        return f.read()


@pytest.fixture()
def connector_and_filter():
    from app import _post_filter_fields, try_connector_paths

    return try_connector_paths, _post_filter_fields


class TestEvidenceBlocks:
    def test_iter_api_response_blocks(self):
        raw = "=== API RESPONSE: /dash ===\n{\"smBalance\":\"75000\"}\n\n=== https://x.com ===\nhello"
        blocks = iter_evidence_json_blocks(raw)
        assert len(blocks) == 1
        assert "smBalance" in blocks[0]


class TestDeterministicExtraction:
    def test_skips_when_items_already_present(self, connector_and_filter):
        connector_fn, post_filter_fn = connector_and_filter
        items = [{"key": "points_balance", "label": "Points", "value": "1,000"}]
        raw = _load_fixture("chase_statement")
        result = extract_deterministic_fields(
            "chase",
            raw,
            existing_items=items,
            connector_fn=connector_fn,
            post_filter_fn=post_filter_fn,
        )
        assert result.attempted is False
        assert result.items == items
        assert len(result.items) == 1
        assert result.items[0]["value"] == "1,000"

    def test_marketing_page_does_not_produce_trusted_fields(self, connector_and_filter):
        connector_fn, post_filter_fn = connector_and_filter
        marketing = (
            "=== https://www.chase.com/personal/credit-cards/education ===\n"
            "Learn More: Apply today for great rewards\n"
            "Terms and Conditions: See offer details\n"
            "Contact Us: 1-800-935-9935\n"
        )
        result = extract_deterministic_fields(
            "chase",
            marketing,
            existing_items=[],
            connector_fn=connector_fn,
            post_filter_fn=post_filter_fn,
        )
        assert result.attempted is True
        assert result.items == []
        assert result.label_value_fields == []
        assert result.regex_fields == []

    def test_field_provenance_maps_extractor_per_key(self, connector_and_filter):
        connector_fn, post_filter_fn = connector_and_filter
        raw = _load_fixture("chase_statement")
        result = extract_deterministic_fields(
            "chase",
            raw,
            existing_items=[],
            connector_fn=connector_fn,
            post_filter_fn=post_filter_fn,
        )
        provenance = result.field_provenance
        assert provenance
        assert "statement_balance" in provenance
        assert provenance["statement_balance"] in {"label_value", "regex"}
        artifacts = result.stage_artifacts
        assert artifacts["field_provenance"]["statement_balance"] == provenance["statement_balance"]

    def test_amex_fixture_produces_trusted_fields(self, connector_and_filter):
        connector_fn, post_filter_fn = connector_and_filter
        raw = _load_fixture("amex_credit")
        result = extract_deterministic_fields(
            "amex",
            raw,
            existing_items=[],
            connector_fn=connector_fn,
            post_filter_fn=post_filter_fn,
        )
        assert result.attempted is True
        assert result.items
        keys = {item["key"] for item in result.items}
        assert "points_balance" in keys

    def test_chase_fixture_produces_payment_and_balance_fields(self, connector_and_filter):
        connector_fn, post_filter_fn = connector_and_filter
        raw = _load_fixture("chase_statement")
        result = extract_deterministic_fields(
            "chase",
            raw,
            existing_items=[],
            connector_fn=connector_fn,
            post_filter_fn=post_filter_fn,
        )
        keys = {item["key"] for item in result.items}
        assert "statement_balance" in keys
        assert "payment_due_date" in keys
        assert "points_balance" in keys
        assert "credit_limit" in keys

    def test_delta_api_json_produces_tier_and_points(self, connector_and_filter):
        connector_fn, post_filter_fn = connector_and_filter
        payload = json.dumps({"smBalance": "75000", "medallionMemberDesc": "Diamond Medallion Member"})
        raw = f"=== API RESPONSE: getDashBrdData ===\n{payload}\n"
        result = extract_deterministic_fields(
            "delta",
            raw,
            existing_items=[],
            connector_fn=connector_fn,
            post_filter_fn=post_filter_fn,
        )
        keys = {item["key"] for item in result.items}
        assert "points_balance" in keys
        assert "elite_status" in keys
        assert result.connector_fields
        assert result.adapter_fields

    def test_amex_chase_delta_observation_coverage_improves(self, connector_and_filter):
        connector_fn, post_filter_fn = connector_and_filter
        cases = [
            ("amex", "amex_credit", "credit_card"),
            ("chase", "chase_statement", "credit_card"),
            (
                "delta",
                "=== API RESPONSE: getDashBrdData ===\n"
                + json.dumps({"smBalance": "75000", "medallionMemberDesc": "Diamond Medallion Member"}),
                "travel_loyalty",
            ),
        ]
        for source, raw, category in cases:
            if not raw.startswith("==="):
                raw = _load_fixture(raw)
            result = extract_deterministic_fields(
                source,
                raw,
                existing_items=[],
                connector_fn=connector_fn,
                post_filter_fn=post_filter_fn,
            )
            observed = field_keys_to_observations([item["key"] for item in result.items])
            coverage = compute_provider_coverage(source, category=category, observed_observations=observed)
            assert coverage.coverage_pct is not None
            assert coverage.coverage_pct > 0, f"{source} should have observation coverage"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_det.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)

    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    monkeypatch.setattr(mighty, "_claude", None)
    monkeypatch.setattr(mighty, "is_field_discovery_enabled", lambda: False)

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
            "email": f"det_{secrets.token_hex(4)}@test.local",
            "password": "pass12345",
            "_csrf": csrf,
        },
    )
    return c


def test_sync_preserves_incoming_items(client):
    import app as mighty

    incoming = [{"key": "points_balance", "label": "Points", "value": "99,999"}]
    raw_text = _load_fixture("chase_statement")

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        db = mighty.get_db()
        now = mighty.iso()
        stub = mighty.encrypt_account_data(uid, {"items": incoming, "sync_status": "ok"})
        db.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, "chase", "", "", "", now, now),
        )
        db.execute(
            "INSERT INTO account_data (user_id, source, display_name, icon, color, data_enc, synced_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, "chase", "Chase", "?", "#000", stub, now),
        )
        db.commit()
        api_key = db.execute("SELECT api_key FROM users WHERE id=?", (uid,)).fetchone()["api_key"]

    resp = client.post(
        "/api/data/sync",
        headers={"X-Mighty-Key": api_key, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "source": "chase",
                "sync_source": "extension",
                "data": {
                    "name": "Chase",
                    "items": incoming,
                    "raw_text": raw_text,
                    "sync_status": "ok",
                },
            }
        ),
    )
    assert resp.status_code == 200

    with mighty.app.app_context():
        row = mighty.get_db().execute(
            "SELECT data_enc FROM account_data WHERE user_id=? AND source=?",
            (uid, "chase"),
        ).fetchone()
        ad = mighty.decrypt_account_data(uid, row["data_enc"])
        assert len(ad["items"]) == 1
        assert ad["items"][0]["value"] == "99,999"


@pytest.mark.parametrize(
    "source,fixture_name,raw_override",
    [
        ("amex", "amex_credit", None),
        ("chase", "chase_statement", None),
        (
            "delta",
            None,
            "=== https://www.delta.com/account ===\n"
            "=== API RESPONSE: getDashBrdData ===\n"
            + json.dumps({"smBalance": "75000", "medallionMemberDesc": "Diamond Medallion Member"}),
        ),
    ],
)
def test_sync_empty_items_with_evidence_reaches_trusted_observations(
    client, source, fixture_name, raw_override,
):
    import app as mighty

    raw_text = raw_override or _load_fixture(fixture_name)

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        db = mighty.get_db()
        now = mighty.iso()
        stub = mighty.encrypt_account_data(uid, {"items": [], "sync_status": "ok"})
        db.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, source, "", "", "", now, now),
        )
        db.execute(
            "INSERT INTO account_data (user_id, source, display_name, icon, color, data_enc, synced_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, source, source.title(), "?", "#000", stub, now),
        )
        db.commit()
        api_key = db.execute("SELECT api_key FROM users WHERE id=?", (uid,)).fetchone()["api_key"]

    resp = client.post(
        "/api/data/sync",
        headers={"X-Mighty-Key": api_key, "Content-Type": "application/json"},
        data=json.dumps(
            {
                "source": source,
                "sync_source": "extension",
                "data": {
                    "name": source.title(),
                    "items": [],
                    "raw_text": raw_text,
                    "sync_status": "ok",
                },
            }
        ),
    )
    assert resp.status_code == 200
    body = resp.get_json()
    run_id = body.get("pipeline_run_id")
    assert run_id

    with mighty.app.app_context():
        run = get_run(mighty.get_db(), run_id)
        assert run["run_status"] == RunStatus.COMPLETE.value
        assert run["terminal_stage"] == PipelineStageId.TRUSTED_OBSERVATIONS.value

        stages = {s["stage"]: s for s in get_run_stages(mighty.get_db(), run_id)}
        assert stages[PipelineStageId.STRUCTURED.value]["status"] == StageStatus.SUCCESS.value
        assert stages[PipelineStageId.TRUSTED_OBSERVATIONS.value]["status"] == StageStatus.SUCCESS.value

        row = mighty.get_db().execute(
            "SELECT data_enc FROM account_data WHERE user_id=? AND source=?",
            (uid, source),
        ).fetchone()
        ad = mighty.decrypt_account_data(uid, row["data_enc"])
        assert ad.get("items"), f"{source} should persist extracted items"


@pytest.fixture()
def pipeline_db(tmp_path):
    db_path = tmp_path / "pipeline_det.db"
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_pipeline_tables(conn)
    yield conn
    conn.close()


def test_pipeline_finalize_records_deterministic_artifacts(pipeline_db):
    from app import _post_filter_fields, try_connector_paths

    run_id = start_run(
        pipeline_db,
        user_id="user-1",
        source="chase",
        initiator=RunInitiator.EXTENSION_SYNC.value,
        data_source="extension",
    )
    raw = _load_fixture("chase_statement")
    record_inferred_client_stages(
        pipeline_db,
        run_id,
        sync_status="ok",
        sync_failure_reason=None,
        connection_status="connected",
        raw_text=raw,
        items=[],
    )
    result = enrich_sync_items_from_evidence(
        "chase",
        raw,
        [],
        connector_fn=try_connector_paths,
        post_filter_fn=_post_filter_fields,
    )
    finalize_sync_without_discovery(
        pipeline_db,
        run_id,
        items=result.items,
        extraction_status="complete",
        has_structured_extractor=True,
        structured_fields=result.normalized_fields,
        extraction_attempted=True,
        extraction_artifacts=result.stage_artifacts,
    )
    structured = next(
        s for s in get_run_stages(pipeline_db, run_id)
        if s["stage"] == PipelineStageId.STRUCTURED.value
    )
    artifacts = json.loads(structured["artifacts_json"])
    assert artifacts["source_label"] == "deterministic"
    assert artifacts["label_value_count"] >= 1
    assert "field_provenance" in artifacts
    assert artifacts["field_provenance"]
