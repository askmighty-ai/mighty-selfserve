"""Golden / replay tests for WorkerSignal → system compiler (M4)."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    REASON_WORKER_MISSING,
    REASON_WORKER_UNREACHABLE,
    AttentionClass,
    AttentionCtaKey,
    AttentionSourceKind,
    AttentionUrgency,
)
from mighty.attention_compiler import (
    WorkerSignal,
    compile_attention_candidates,
    compile_worker_attention,
    worker_source_ref,
    worker_system_attention_id,
    worker_system_fingerprint,
)

USER_ID = "user-1"
SEEN = "2026-07-18T12:00:00+00:00"


def _signal(**overrides) -> WorkerSignal:
    payload = {
        "user_id": USER_ID,
        "installed": False,
        "reachable": False,
        "last_seen_at": None,
        "version": None,
        "update_required": False,
        "enrolled_account_count": 1,
    }
    payload.update(overrides)
    return WorkerSignal(**payload)


class TestWorkerCompiler:
    def test_missing_worker_golden(self):
        item = compile_worker_attention(_signal())
        assert item is not None
        assert item.to_dict() == {
            "schema_version": ATTENTION_ITEM_SCHEMA_VERSION,
            "attention_id": "att_user-1_system_worker",
            "user_id": USER_ID,
            "attention_class": AttentionClass.SYSTEM.value,
            "urgency": AttentionUrgency.BLOCKER.value,
            "provider": None,
            "fingerprint": "worker:setup",
            "reason": {"code": REASON_WORKER_MISSING},
            "cta_key": AttentionCtaKey.INSTALL_WORKER.value,
            "source_kind": AttentionSourceKind.WORKER.value,
            "source_ref": "worker:user-1",
            "observed_at": None,
            "becomes_stale_at": None,
            "interruption_expected": False,
        }

    def test_unreachable_worker(self):
        item = compile_worker_attention(
            _signal(installed=True, reachable=False, last_seen_at=SEEN, version="1.0.0")
        )
        assert item is not None
        assert item.reason.code == REASON_WORKER_UNREACHABLE
        assert item.fingerprint == worker_system_fingerprint()
        assert item.attention_id == worker_system_attention_id(USER_ID)
        assert item.source_ref == worker_source_ref(USER_ID)
        assert item.observed_at == SEEN

    def test_healthy_worker_does_not_emit(self):
        assert (
            compile_worker_attention(
                _signal(installed=True, reachable=True, version="1.2.3", last_seen_at=SEEN)
            )
            is None
        )

    def test_update_required_alone_does_not_emit(self):
        assert (
            compile_worker_attention(
                _signal(
                    installed=True,
                    reachable=True,
                    update_required=True,
                    version="0.1.0",
                    last_seen_at=SEEN,
                )
            )
            is None
        )

    def test_no_enrolled_accounts_does_not_emit(self):
        assert compile_worker_attention(_signal(enrolled_account_count=0)) is None


def test_gather_places_worker_before_data_gap():
    from dataclasses import dataclass

    from mighty.account_state import CONN_CONNECTED, DATA_NONE

    @dataclass
    class _Account:
        user_id: str = USER_ID
        provider: str = "amex"
        connection_state: str = CONN_CONNECTED
        data_status: str = DATA_NONE
        last_data_refresh: str | None = None
        updated_at: str = SEEN

    items = compile_attention_candidates(
        worker_signal=_signal(),
        account_states=[_Account()],
    )
    assert [i.attention_class for i in items] == [
        AttentionClass.SYSTEM,
        AttentionClass.DATA_GAP,
    ]
