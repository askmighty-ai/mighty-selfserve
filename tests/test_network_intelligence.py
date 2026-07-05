"""Tests for Network Intelligence (Phase 2)."""

import json
import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.capture_capability import parse_raw_text_evidence_markers, present_from_markers
from mighty.network_intelligence import (
    format_network_block,
    is_graphql_payload,
    looks_like_account_json,
    merge_network_blocks,
    network_marker_counts,
    redact_sensitive_json,
    should_skip_network_url,
)


class TestNetworkFiltering:
    def test_skip_static_and_analytics_urls(self):
        assert should_skip_network_url("https://cdn.example.com/static/app.js")
        assert should_skip_network_url("https://example.com/analytics/beacon")
        assert should_skip_network_url("https://example.com/assets/logo.png")
        assert not should_skip_network_url("https://api.example.com/v1/member/profile")

    def test_account_json_keyword_gate(self):
        payload = json.dumps({
            "member": {
                "pointsBalance": 12000,
                "tier": "Gold",
                "accountStatus": "active",
                "rewardsProgram": "Honors",
            }
        })
        assert looks_like_account_json(payload)
        assert not looks_like_account_json(json.dumps({"featureFlags": {"beta": True, "enabled": False, "rollout": "full"}}))

    def test_graphql_payload_detection(self):
        payload = json.dumps({"data": {"member": {"points": 5000}}})
        assert is_graphql_payload(payload)

    def test_redact_sensitive_keys(self):
        payload = json.dumps({"balance": 100, "access_token": "secret-token"})
        redacted = redact_sensitive_json(payload)
        parsed = json.loads(redacted)
        assert parsed["balance"] == 100
        assert parsed["access_token"] == "[REDACTED]"


class TestNetworkMarkers:
    def test_marker_counts(self):
        raw = (
            "\n\n=== NETWORK JSON: https://api.example.com/account ===\n"
            '{"balance": 100}\n\n'
            "=== GRAPHQL: https://api.example.com/graphql ===\n"
            '{"data":{"member":{"points":5000}}}\n\n'
            "=== API RESPONSE: https://api.example.com/legacy ===\n"
            '{"miles": 75000}'
        )
        counts = network_marker_counts(raw)
        assert counts["network_json_blocks"] == 1
        assert counts["graphql_blocks"] == 1
        assert counts["api_response_blocks"] == 1

    def test_parse_raw_text_evidence_markers(self):
        raw = format_network_block(
            "https://api.hilton.com/member",
            json.dumps({"pointsBalance": 143996, "tier": "Gold"}),
            sync=True,
        )
        markers = parse_raw_text_evidence_markers(raw)
        assert markers["network_json_blocks"] == 1
        assert "network_json" in present_from_markers(markers)

    def test_merge_network_blocks_dedupes(self):
        block = format_network_block(
            "https://api.delta.com/member",
            json.dumps({"skymiles": {"balance": 75000}}),
            sync=True,
        )
        existing = block + "\n\n--- https://delta.com/account ---\nDiamond"
        new_sync = block + "\n\n--- https://delta.com/account ---\nUpdated visible"
        merged = merge_network_blocks(existing, new_sync)
        assert merged.count("=== NETWORK JSON:") == 1
        assert "Updated visible" in merged


class TestCaptureCapabilityIntegration:
    def test_extension_sync_with_network_json_blocks(self):
        raw = (
            "\n\n=== NETWORK JSON: https://api.hilton.com/member ===\n"
            '{"pointsBalance":143996,"tier":"Gold"}\n\n'
            "--- https://www.hilton.com/account ---\n"
            "Gold Member\n143996 points"
        )
        markers = parse_raw_text_evidence_markers(raw)
        present = present_from_markers(markers, initiator="extension_sync")
        assert "network_json" in present
        assert "visible_text" in present
