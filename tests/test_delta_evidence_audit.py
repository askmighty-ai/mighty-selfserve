"""Unit tests for Delta evidence audit diagnostics."""

import json
import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.delta_evidence_audit import (
    DELTA_OBSERVATIONS,
    build_delta_run_audit,
    parse_evidence_blocks,
    preview_connector_fields,
)
from mighty.pipeline_inspector import (
    ensure_pipeline_tables,
    finalize_run,
    record_stage,
    start_run,
)
from mighty.pipeline_stages import PipelineStageId, RunInitiator, RunStatus, StageStatus

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


def test_parse_evidence_blocks_delta_fixture():
    raw = load_fixture("delta_companion_cert.txt")
    blocks = parse_evidence_blocks(raw)

    assert len(blocks["page_url"]) >= 2
    assert any("wallet" in b.header.lower() or "wallet" in b.body.lower() for b in blocks["page_url"])
    assert any("45,320" in b.body for b in blocks["page_url"])


def test_parse_api_and_embedded_blocks():
    raw = (
        '\n\n=== API RESPONSE: https://www.delta.com/login/login/getDashBrdData ===\n'
        '{"smBalance":"45320","medallionMemberDesc":"Diamond Medallion"}\n'
        '\n\n=== EMBEDDED STATE: embedded:__NEXT_DATA__@https://www.delta.com/myprofile/ ===\n'
        '{"props":{"pageProps":{"member":{"medallionStatus":"Diamond"}}}}\n'
        '\n\n=== NETWORK JSON: https://www.delta.com/api/wallet ===\n'
        '{"wallet":{"totalEcreditValue":"250.00"}}\n'
        '\n\n=== GRAPHQL: https://www.delta.com/graphql ===\n'
        '{"data":{"trips":[{"confirmation":"ABC123"}]}}\n'
    )
    blocks = parse_evidence_blocks(raw)

    assert len(blocks["api_response"]) == 1
    assert len(blocks["embedded_state"]) == 1
    assert len(blocks["network_json"]) == 1
    assert len(blocks["graphql"]) == 1


def test_preview_connector_fields_from_api_response():
    payload = {
        "data": {
            "member": {
                "medallionStatus": "Diamond",
                "skymiles": "45320",
            }
        },
        "wallet": {"totalEcreditValue": "100.00"},
    }
    fields = preview_connector_fields(json.dumps(payload))
    keys = {f["key"] for f in fields}
    assert "elite_status" in keys
    assert "points_balance" in keys
    assert "ecredit_balance" in keys


def test_build_audit_finds_evidence_gaps_when_only_one_trusted():
    raw = load_fixture("delta_companion_cert.txt")
    run = {
        "run_id": "run-delta-1",
        "source": "delta",
        "run_status": RunStatus.COMPLETE.value,
        "created_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:00:05+00:00",
    }
    stages = [
        {
            "stage": PipelineStageId.STRUCTURED.value,
            "status": StageStatus.FAILED.value,
            "failure_reason": "connector_miss",
            "artifacts": {"field_keys": [], "source_label": "connector"},
        },
        {
            "stage": PipelineStageId.INTELLIGENT.value,
            "status": StageStatus.FAILED.value,
            "failure_reason": "llm_empty",
            "artifacts": {"raw_field_count": 0},
        },
        {
            "stage": PipelineStageId.VALIDATION.value,
            "status": StageStatus.FAILED.value,
            "failure_reason": "llm_empty",
            "artifacts": {"fields_in": 0, "fields_out": 0},
        },
        {
            "stage": PipelineStageId.TRUSTED_OBSERVATIONS.value,
            "status": StageStatus.SUCCESS.value,
            "artifacts": {"trusted_keys": ["elite_status"], "trusted_count": 1},
        },
    ]

    audit = build_delta_run_audit(
        run=run,
        stages=stages,
        raw_text=raw,
        raw_text_source="fixture",
        discovered_fields=[
            {"key": "elite_status", "label": "Medallion Status", "value": "Diamond Medallion"},
        ],
    )

    assert len(audit.trusted_observations) == 1
    assert audit.trusted_observations == ["elite_status"]

    in_evidence = {c.observation_id for c in audit.comparisons if c.in_evidence}
    assert "skymiles_balance" in in_evidence
    assert "companion_certificates" in in_evidence
    assert "mqds" in in_evidence
    assert "expiration_dates" in in_evidence

    skymiles = next(c for c in audit.comparisons if c.observation_id == "skymiles_balance")
    assert skymiles.in_evidence is True
    assert skymiles.extracted is False
    assert skymiles.trusted is False
    assert "intelligent" in (skymiles.recommended_extractor or "").lower()

    medallion = next(c for c in audit.comparisons if c.observation_id == "medallion_status")
    assert medallion.in_evidence is True
    assert medallion.extracted is True
    assert medallion.trusted is True


def test_list_successful_delta_runs(pipeline_db):
    run_id = start_run(
        pipeline_db,
        user_id="u1",
        source="delta",
        initiator=RunInitiator.EXTENSION_SYNC.value,
    )
    record_stage(
        pipeline_db,
        run_id,
        PipelineStageId.TRUSTED_OBSERVATIONS.value,
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00",
        status=StageStatus.SUCCESS.value,
        artifacts={"trusted_keys": ["elite_status"]},
    )
    finalize_run(
        pipeline_db,
        run_id,
        terminal_stage=PipelineStageId.TRUSTED_OBSERVATIONS.value,
        run_status=RunStatus.COMPLETE.value,
    )

    from mighty.delta_evidence_audit import list_successful_delta_runs

    runs = list_successful_delta_runs(pipeline_db)
    assert len(runs) == 1
    assert runs[0]["run_id"] == run_id


def test_all_delta_observations_defined():
    assert len(DELTA_OBSERVATIONS) >= 9
    ids = {o["id"] for o in DELTA_OBSERVATIONS}
    for required in (
        "skymiles_balance",
        "medallion_status",
        "mqds",
        "ecredits",
        "companion_certificates",
        "upcoming_trips",
        "flight_credits",
        "expiration_dates",
    ):
        assert required in ids


@pytest.fixture()
def pipeline_db(tmp_path):
    db_path = tmp_path / "delta_audit.db"
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_pipeline_tables(conn)
    yield conn
    conn.close()
