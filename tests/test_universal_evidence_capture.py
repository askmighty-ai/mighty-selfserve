"""Tests for universal evidence capture block formatting."""

import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.capture_capability import parse_raw_text_evidence_markers, present_from_markers
from mighty.field_discovery_preprocess import prepare_discovery_input


def test_universal_evidence_blocks_detected_by_capture_capability():
    raw = (
        "\n\n--- https://www.example.com/account ---\n"
        "Gold Member\n12,000 points\n\n"
        "=== PAGE META: https://www.example.com/account ===\n"
        '{"title":"My Account","canonical":"https://www.example.com/account","og:title":"Rewards"}\n\n'
        "=== JSON-LD: https://www.example.com/account ===\n"
        '{"@type":"Person","name":"Member","memberOf":{"name":"Loyalty Program"}}\n\n'
        "=== EMBEDDED STATE: embedded:__NEXT_DATA__@https://www.example.com/account ===\n"
        '{"props":{"pageProps":{"pointsBalance":12000}}}'
    )
    markers = parse_raw_text_evidence_markers(raw)
    present = present_from_markers(markers)

    assert markers["page_metadata_blocks"] == 1
    assert markers["json_ld_blocks"] == 1
    assert markers["embedded_state_blocks"] == 1
    assert markers["url_section_count"] >= 1
    assert markers["visible_text_chars"] == len("Gold Member\n12,000 points")
    assert "visible_text" in present
    assert "embedded_json" in present
    assert "page_metadata" in present
    assert "dom_html" not in present


def test_preprocess_preserves_universal_evidence_blocks():
    raw = (
        "=== PAGE META: https://example.com/account ===\n"
        '{"title":"Account"}\n\n'
        "=== JSON-LD: https://example.com/account ===\n"
        '{"@type":"Person","name":"Member"}\n\n'
        "--- https://example.com/account ---\n"
        "Points 5,000"
    )
    result = prepare_discovery_input(raw, max_chars=10_000)
    assert "PAGE META" in result.text
    assert "JSON-LD" in result.text
    assert "Points 5,000" in result.text
