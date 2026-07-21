"""Golden / replay tests for AccountState → data_gap compiler (M4)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_state import (
    CONN_CONNECTED,
    CONN_NOT_CONNECTED,
    DATA_COMPLETE,
    DATA_NONE,
    DATA_PARTIAL,
)
from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    REASON_DATA_GAP,
    AttentionClass,
    AttentionCtaKey,
    AttentionSourceKind,
    AttentionUrgency,
)
from mighty.attention_compiler import (
    account_state_source_ref,
    compile_attention_candidates,
    compile_data_gap_attention,
    data_gap_attention_id,
    data_gap_fingerprint,
)

USER_ID = "user-1"
PROVIDER = "amex"
UPDATED = "2026-07-21T12:00:00+00:00"


@dataclass
class _Account:
    user_id: str = USER_ID
    provider: str = PROVIDER
    connection_state: str = CONN_CONNECTED
    data_status: str = DATA_NONE
    last_data_refresh: str | None = None
    updated_at: str = UPDATED


class TestDataGapCompiler:
    def test_connected_none_golden(self):
        item = compile_data_gap_attention(_Account())
        assert item is not None
        assert item.to_dict() == {
            "schema_version": ATTENTION_ITEM_SCHEMA_VERSION,
            "attention_id": "att_user-1_data_gap_amex",
            "user_id": USER_ID,
            "attention_class": AttentionClass.DATA_GAP.value,
            "urgency": AttentionUrgency.INFORMATIONAL.value,
            "provider": PROVIDER,
            "fingerprint": "account_data:amex:data_gap",
            "reason": {"code": REASON_DATA_GAP},
            "cta_key": AttentionCtaKey.OPEN_PROVIDER_SURFACE.value,
            "source_kind": AttentionSourceKind.ACCOUNT_DATA.value,
            "source_ref": "account_state:user-1:amex",
            "observed_at": UPDATED,
            "becomes_stale_at": None,
            "interruption_expected": False,
        }

    def test_connected_partial_emits(self):
        item = compile_data_gap_attention(_Account(data_status=DATA_PARTIAL))
        assert item is not None
        assert item.attention_class is AttentionClass.DATA_GAP
        assert item.fingerprint == data_gap_fingerprint(PROVIDER)
        assert item.attention_id == data_gap_attention_id(USER_ID, PROVIDER)
        assert item.source_ref == account_state_source_ref(USER_ID, PROVIDER)

    def test_prefers_last_data_refresh_for_observed_at(self):
        item = compile_data_gap_attention(
            _Account(last_data_refresh="2026-07-20T08:00:00+00:00")
        )
        assert item is not None
        assert item.observed_at == "2026-07-20T08:00:00+00:00"

    def test_complete_does_not_emit(self):
        assert compile_data_gap_attention(_Account(data_status=DATA_COMPLETE)) is None

    def test_not_connected_does_not_emit(self):
        assert (
            compile_data_gap_attention(
                _Account(connection_state=CONN_NOT_CONNECTED, data_status=DATA_NONE)
            )
            is None
        )

    def test_missing_identity_does_not_emit(self):
        assert compile_data_gap_attention(_Account(user_id="")) is None
        assert compile_data_gap_attention(_Account(provider="")) is None


def test_gather_includes_data_gap_after_authorize():
    from mighty.attention_compiler import AuthorizeRow

    items = compile_attention_candidates(
        authorize_rows=[
            AuthorizeRow(action_id="7", user_id=USER_ID, status="pending"),
        ],
        account_states=[_Account(provider="amex"), _Account(provider="chase")],
    )
    classes = [item.attention_class for item in items]
    assert classes == [
        AttentionClass.AGENT_AUTHORIZATION,
        AttentionClass.DATA_GAP,
        AttentionClass.DATA_GAP,
    ]
    assert [item.provider for item in items[1:]] == ["amex", "chase"]
