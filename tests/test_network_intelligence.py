"""Tests for Network Intelligence (Phase 2)."""

import json
import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.capture_capability import parse_raw_text_evidence_markers, present_from_markers
from mighty.network_intelligence import (
    MAX_NETWORK_BLOCK_CHARS,
    MAX_RAW_TEXT_CHARS,
    MAX_SYNC_NETWORK_BUFFER_CHARS,
    SENSITIVE_JSON_KEYS,
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
        assert should_skip_network_url("https://example.com/telemetry/v1/collect")
        assert should_skip_network_url("https://example.com/pixel.gif")
        assert should_skip_network_url("https://example.com/uploads/statement.pdf")
        assert not should_skip_network_url("https://api.example.com/v1/member/profile")

    def test_skip_auth_and_token_urls(self):
        assert should_skip_network_url("https://auth.example.com/oauth/token")
        assert should_skip_network_url("https://api.example.com/api/auth/refresh")
        assert should_skip_network_url("https://login.example.com/signin/token")
        assert not should_skip_network_url("https://api.example.com/v1/member/rewards")

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
        assert not looks_like_account_json(json.dumps({
            "featureFlags": {"beta": True, "enabled": False, "rollout": "full rollout mode"},
        }))

    def test_graphql_payload_detection_conservative(self):
        rest_wrapper = json.dumps({"data": {"member": {"points": 5000, "tier": "Gold", "accountStatus": "active"}}})
        assert not is_graphql_payload(rest_wrapper)
        assert is_graphql_payload(rest_wrapper, content_type="application/graphql+json")
        assert is_graphql_payload(json.dumps({
            "data": {"member": {"points": 5000}},
            "extensions": {"tracing": {"version": 1}},
        }))
        assert is_graphql_payload(json.dumps({"errors": [{"message": "Unauthorized"}]}))

    @pytest.mark.parametrize("key", SENSITIVE_JSON_KEYS)
    def test_redact_top_level_sensitive_keys(self, key):
        payload = json.dumps({key: "leak-value", "balance": 100, "memberTier": "Gold status account"})
        redacted = redact_sensitive_json(payload)
        parsed = json.loads(redacted)
        assert parsed[key] == "[REDACTED]"
        assert parsed["balance"] == 100

    def test_redact_nested_objects_and_arrays(self):
        payload = json.dumps({
            "member": {
                "points": 5000,
                "credentials": {
                    "password": "secret123",
                    "session_id": "abc-session",
                },
            },
            "sessions": [
                {"access_token": "nested-token", "tier": "Gold"},
                {"refresh_token": "refresh-me", "balance": 10},
            ],
            "headers": {
                "authorization": "Bearer xyz",
                "cookie": "sid=123",
                "set-cookie": "csrf=abc",
            },
        })
        redacted = redact_sensitive_json(payload)
        parsed = json.loads(redacted)
        assert parsed["member"]["credentials"]["password"] == "[REDACTED]"
        assert parsed["member"]["credentials"]["session_id"] == "[REDACTED]"
        assert parsed["sessions"][0]["access_token"] == "[REDACTED]"
        assert parsed["sessions"][1]["refresh_token"] == "[REDACTED]"
        assert parsed["headers"]["authorization"] == "[REDACTED]"
        assert parsed["headers"]["cookie"] == "[REDACTED]"
        assert parsed["headers"]["set-cookie"] == "[REDACTED]"
        assert parsed["member"]["points"] == 5000


class TestNetworkSizeLimits:
    def test_documented_limits(self):
        assert MAX_NETWORK_BLOCK_CHARS == 120_000
        assert MAX_SYNC_NETWORK_BUFFER_CHARS == 80_000
        assert MAX_RAW_TEXT_CHARS == 40_000

    def test_format_network_block_truncates_oversized_payload(self):
        huge = json.dumps({"balance": 1, "notes": "x" * (MAX_NETWORK_BLOCK_CHARS + 5000)})
        block = format_network_block("https://api.example.com/account", huge, sync=True)
        payload = block.split("===", 2)[-1].strip()
        assert len(payload) <= MAX_NETWORK_BLOCK_CHARS

    def test_merge_network_blocks_caps_total_raw_text(self):
        blocks = [
            format_network_block(
                f"https://api.example.com/account/{i}",
                json.dumps({"balance": i, "memberTier": "Gold account status"}),
                sync=True,
            )
            for i in range(30)
        ]
        existing = "".join(blocks)
        visible = "\n\n--- https://example.com/account ---\nVisible text only"
        merged = merge_network_blocks(existing, visible)
        assert len(merged) <= MAX_RAW_TEXT_CHARS


class TestNetworkMarkers:
    def test_marker_counts(self):
        raw = (
            "\n\n=== NETWORK JSON: https://api.example.com/account ===\n"
            '{"balance": 100}\n\n'
            "=== GRAPHQL: https://api.example.com/graphql ===\n"
            '{"data":{"member":{"points":5000}},"extensions":{}}\n\n'
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

    def test_merge_preserves_visible_when_no_network_blocks(self):
        visible = "--- https://example.com/account ---\nGold Member\n12000 points"
        assert merge_network_blocks("", visible) == visible

    def test_graphql_block_uses_graphql_marker_only_when_appropriate(self):
        graphql_block = format_network_block(
            "https://api.example.com/graphql",
            json.dumps({"data": {"member": {"points": 1}}, "extensions": {}}),
            graphql=True,
        )
        rest_block = format_network_block(
            "https://api.example.com/member",
            json.dumps({"data": {"member": {"points": 1}}}),
            graphql=False,
            sync=True,
        )
        assert "=== GRAPHQL:" in graphql_block
        assert "=== NETWORK JSON:" in rest_block
        assert "=== GRAPHQL:" not in rest_block


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
