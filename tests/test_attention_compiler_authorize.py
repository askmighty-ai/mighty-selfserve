"""Golden / replay tests for AuthorizeRow → AttentionItem compiler (PR 2F)."""

from __future__ import annotations

import json
import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    REASON_PENDING_AUTHORIZATION,
    AttentionClass,
    AttentionCtaKey,
    AttentionSourceKind,
    AttentionUrgency,
)
from mighty.attention_compiler import (
    AuthorizeRow,
    AuthorizeRowValidationError,
    authorize_attention_id,
    authorize_row_fingerprint,
    authorize_source_ref,
    compile_authorize_attention,
)

USER_ID = "user-1"
ACTION_ID = "42"
CREATED_AT = "2026-07-21T11:00:00+00:00"
EXPIRES_AT = "2026-07-21T12:00:00+00:00"


def _row(**overrides) -> AuthorizeRow:
    payload = {
        "action_id": ACTION_ID,
        "user_id": USER_ID,
        "status": "pending",
        "created_at": CREATED_AT,
        "expires_at": EXPIRES_AT,
        "provider": None,
    }
    payload.update(overrides)
    return AuthorizeRow(**payload)


class TestIdentityHelpers:
    def test_fingerprint_and_ids_match_part_xiv_scenario_5(self):
        assert authorize_row_fingerprint(ACTION_ID) == "authorize:row:42"
        assert (
            authorize_attention_id(USER_ID, ACTION_ID)
            == "att_user-1_agent_authorization_row42"
        )
        assert authorize_source_ref(ACTION_ID) == "authorize:42"


class TestCompileAuthorize:
    def test_pending_golden(self):
        item = compile_authorize_attention(_row())
        assert item is not None
        assert item.to_dict() == {
            "schema_version": ATTENTION_ITEM_SCHEMA_VERSION,
            "attention_id": "att_user-1_agent_authorization_row42",
            "user_id": USER_ID,
            "attention_class": AttentionClass.AGENT_AUTHORIZATION.value,
            "urgency": AttentionUrgency.BLOCKER.value,
            "provider": None,
            "fingerprint": "authorize:row:42",
            "reason": {"code": REASON_PENDING_AUTHORIZATION},
            "cta_key": AttentionCtaKey.OPEN_ACTIVITY_APPROVAL.value,
            "source_kind": AttentionSourceKind.AUTHORIZE.value,
            "source_ref": "authorize:42",
            "observed_at": CREATED_AT,
            "becomes_stale_at": EXPIRES_AT,
            "interruption_expected": False,
        }

    def test_pending_with_provider(self):
        item = compile_authorize_attention(_row(provider="Amex"))
        assert item is not None
        assert item.provider == "amex"

    def test_status_normalized_case(self):
        item = compile_authorize_attention(_row(status="PENDING"))
        assert item is not None
        assert item.attention_class == AttentionClass.AGENT_AUTHORIZATION

    @pytest.mark.parametrize(
        "status",
        ["approved", "denied", "expired", "cancelled", "complete"],
    )
    def test_terminal_statuses_emit_none(self, status):
        assert compile_authorize_attention(_row(status=status)) is None

    def test_replay_stability(self):
        row = _row()
        snapshots = [
            compile_authorize_attention(row).to_dict() for _ in range(5)
        ]
        assert all(snap == snapshots[0] for snap in snapshots)

    def test_json_round_trip_payload(self):
        item = compile_authorize_attention(_row())
        encoded = json.dumps(item.to_dict(), sort_keys=True)
        assert json.loads(encoded)["fingerprint"] == "authorize:row:42"

    def test_missing_action_id_rejected(self):
        with pytest.raises(AuthorizeRowValidationError):
            AuthorizeRow(action_id="", user_id=USER_ID, status="pending")

    def test_missing_user_id_rejected(self):
        with pytest.raises(AuthorizeRowValidationError):
            AuthorizeRow(action_id=ACTION_ID, user_id="  ", status="pending")

    def test_optional_timestamps_none(self):
        item = compile_authorize_attention(
            _row(created_at=None, expires_at=None)
        )
        assert item is not None
        assert item.observed_at is None
        assert item.becomes_stale_at is None
