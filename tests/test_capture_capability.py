"""Unit tests for capture capability computation."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.capture_capability import (
    compute_provider_capability,
    next_best_improvement,
    parse_raw_text_evidence_markers,
    present_from_markers,
)
from mighty.pipeline_inspector import (
    ensure_pipeline_tables,
    finalize_run,
    record_inferred_client_stages,
    start_run,
)
from mighty.pipeline_stages import PipelineStageId, RunInitiator, RunStatus, StageStatus


@pytest.fixture()
def pipeline_db(tmp_path):
    import sqlite3

    db_path = tmp_path / "capture_cap.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_pipeline_tables(conn)
    yield conn
    conn.close()


class TestParseMarkers:
    def test_api_and_embedded_blocks(self):
        raw = (
            "\n\n=== API RESPONSE: https://api.example.com/account ===\n"
            '{"balance": 100}\n\n'
            "=== EMBEDDED STATE: embedded:__NEXT_DATA__@https://example.com ===\n"
            '{"props": {"miles": 5000}}\n\n'
            "--- https://example.com/account ---\n"
            "Diamond Medallion\n75,000 miles"
        )
        markers = parse_raw_text_evidence_markers(raw)
        assert markers["api_response_blocks"] == 1
        assert markers["embedded_state_blocks"] == 1
        assert markers["url_section_count"] >= 1
        assert markers["visible_text_chars"] > 0

    def test_page_meta_and_json_ld_blocks(self):
        raw = (
            "\n\n=== PAGE META: https://example.com/account ===\n"
            '{"title":"My Account","canonical":"https://example.com/account"}\n\n'
            "=== JSON-LD: https://example.com/account ===\n"
            '{"@type":"Person","name":"Member"}\n\n'
            "--- https://example.com/account ---\n"
            "Gold status\n12,000 points"
        )
        markers = parse_raw_text_evidence_markers(raw)
        assert markers["page_metadata_blocks"] == 1
        assert markers["json_ld_blocks"] == 1
        present = present_from_markers(markers)
        assert "page_metadata" in present
        assert "dom_html" not in present
        assert "embedded_json" in present
        assert "visible_text" in present

    def test_visible_text_chars_excludes_evidence_blocks(self):
        raw = (
            "\n\n=== PAGE META: https://example.com/account ===\n"
            '{"title":"My Account"}\n\n'
            "=== JSON-LD: https://example.com/account ===\n"
            '{"@type":"Person","name":"Member"}\n\n'
            "--- https://example.com/account ---\n"
            "Visible only"
        )
        markers = parse_raw_text_evidence_markers(raw)
        assert markers["visible_text_chars"] == len("Visible only")
        assert markers["visible_text_chars"] < len(raw.strip())

    def test_present_from_markers(self):
        markers = {
            "visible_text_chars": 500,
            "api_response_blocks": 2,
            "embedded_state_blocks": 1,
            "url_section_count": 1,
        }
        present = present_from_markers(markers)
        assert "visible_text" in present
        assert "network_json" in present
        assert "embedded_json" in present
        assert "navigation_urls" in present
        assert "dom_html" not in present


class TestNextBestImprovement:
    def test_first_missing_in_priority_order(self):
        assert next_best_improvement(["dom_html", "network_json"], has_runs=True) == (
            "Capture network JSON"
        )

    def test_no_runs_defaults_to_visible_text(self):
        assert next_best_improvement(["visible_text"], has_runs=False) == (
            "Capture visible text from account pages"
        )

    def test_full_capability(self):
        assert next_best_improvement([], has_runs=True) == (
            "Full capture capability — no gaps in checklist"
        )


class TestPipelineIntegration:
    def test_intercept_run_marks_network_json(self, pipeline_db):
        from mighty.capture_capability import collect_signals_from_pipeline

        run_id = start_run(
            pipeline_db,
            user_id="u1",
            source="delta",
            initiator=RunInitiator.INTERCEPT.value,
            data_source="extension",
        )
        raw = (
            "\n\n=== API RESPONSE: https://api.delta.com/member ===\n"
            '{"skymiles": {"balance": 75000}}'
        )
        record_inferred_client_stages(
            pipeline_db,
            run_id,
            sync_status="ok",
            sync_failure_reason=None,
            connection_status="connected",
            raw_text=raw,
            items=[],
            json_payload_chars=len(raw),
        )
        finalize_run(
            pipeline_db,
            run_id,
            terminal_stage=PipelineStageId.CAPTURE.value,
            run_status=RunStatus.COMPLETE.value,
        )

        signals = collect_signals_from_pipeline(pipeline_db)["delta"]
        cap = compute_provider_capability("delta", signals=signals)
        assert "network_json" in cap.present
        assert "visible_text" not in cap.present
        assert cap.latest_successful_capture_run_id == run_id
        assert "dom_html" in cap.missing
        assert cap.next_best_improvement == "Capture embedded framework state"

    def test_extension_sync_visible_text_only(self, pipeline_db):
        from mighty.capture_capability import collect_signals_from_pipeline

        run_id = start_run(
            pipeline_db,
            user_id="u1",
            source="hilton",
            initiator=RunInitiator.EXTENSION_SYNC.value,
            data_source="extension",
        )
        raw = (
            "--- https://www.hilton.com/account ---\n"
            "Gold Member\n143996 points\n"
            + "Loyalty balance and tier status for Hilton Honors member account page.\n" * 3
        )
        record_inferred_client_stages(
            pipeline_db,
            run_id,
            sync_status="ok",
            sync_failure_reason=None,
            connection_status="connected",
            raw_text=raw,
            items=[],
        )
        finalize_run(
            pipeline_db,
            run_id,
            terminal_stage=PipelineStageId.CAPTURE.value,
            run_status=RunStatus.COMPLETE.value,
        )

        cap = compute_provider_capability(
            "hilton",
            signals=collect_signals_from_pipeline(pipeline_db)["hilton"],
        )
        assert "visible_text" in cap.present
        assert "navigation_urls" in cap.present
        assert "network_json" in cap.missing
        assert cap.next_best_improvement == "Capture network JSON"
