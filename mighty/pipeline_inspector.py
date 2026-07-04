"""Provider pipeline tracing: runs, stages, and structured stage events."""

from __future__ import annotations

import json
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

from mighty.pipeline_stages import (
    FAIL_ALL_FILTERED,
    FAIL_CONNECTOR_MISS,
    FAIL_DISCOVERY_DISABLED,
    FAIL_DISCOVERY_ERROR,
    FAIL_EXCEPTION,
    FAIL_DOMAIN_UNREACHABLE,
    FAIL_INVALID_NORMALIZED_VALUE,
    FAIL_LOGIN_REQUIRED,
    FAIL_LOGIN_WALL,
    FAIL_LOW_CONFIDENCE_ONLY,
    FAIL_LLM_EMPTY,
    FAIL_NAV_TIMEOUT,
    FAIL_NO_DATA,
    FAIL_NO_PAGES_VISITED,
    FAIL_NOT_ATTEMPTED_ON_SYNC_PATH,
    FAIL_NO_TRUSTED_OBSERVATIONS,
    FAIL_PARTIAL_TRUST,
    FAIL_PAYLOAD_TOO_SMALL,
    FAIL_QUALITY_GATE,
    FAIL_STALE_DATE_ONLY,
    FAIL_STORAGE_SPLIT,
    FAIL_WRITE_ERROR,
    STAGE_ORDER,
    STAGE_ORDER_INDEX,
    PipelineStageId,
    RunInitiator,
    RunStatus,
    StageStatus,
)

_URL_MARKER_RE = re.compile(
    r"(?:---|===)\s*(?:API RESPONSE|EMBEDDED STATE|https?://[^\s]+)\s*(?:===|---)?",
    re.IGNORECASE,
)
_HTTP_URL_RE = re.compile(r"https?://[^\s>\]]+", re.IGNORECASE)
_EMPTY_VALUES = frozenset({"", "—", "–", "-", "n/a", "none", "0", "no data", "tbd"})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id() -> str:
    return str(uuid.uuid4())


def ensure_pipeline_tables(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            run_id          TEXT PRIMARY KEY,
            created_at      TEXT NOT NULL,
            finished_at     TEXT,
            user_id         TEXT NOT NULL,
            source          TEXT NOT NULL,
            initiator       TEXT NOT NULL,
            data_source     TEXT,
            run_status      TEXT NOT NULL,
            terminal_stage  TEXT,
            terminal_reason TEXT
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_pr_source_created ON pipeline_runs(source, created_at)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_pr_user_source ON pipeline_runs(user_id, source, created_at)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_stages (
            run_id          TEXT NOT NULL,
            stage           TEXT NOT NULL,
            stage_order     INTEGER NOT NULL,
            started_at      TEXT NOT NULL,
            finished_at     TEXT,
            duration_ms     REAL,
            status          TEXT NOT NULL,
            failure_reason  TEXT,
            artifacts_json  TEXT,
            PRIMARY KEY (run_id, stage),
            FOREIGN KEY (run_id) REFERENCES pipeline_runs(run_id)
        )
        """
    )
    db.commit()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _log_stage_event(payload: dict[str, Any]) -> None:
    print(_json_dumps({"event": "pipeline_stage", **payload}), flush=True)


def start_run(
    db: Any,
    *,
    user_id: str,
    source: str,
    initiator: str,
    data_source: str | None = None,
    run_id: str | None = None,
) -> str:
    run_id = run_id or new_run_id()
    now = utc_now_iso()
    db.execute(
        """
        INSERT INTO pipeline_runs (
            run_id, created_at, finished_at, user_id, source,
            initiator, data_source, run_status, terminal_stage, terminal_reason
        ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, NULL, NULL)
        """,
        (run_id, now, user_id, source, initiator, data_source, RunStatus.RUNNING.value),
    )
    db.commit()
    _log_stage_event(
        {
            "run_id": run_id,
            "action": "start",
            "source": source,
            "initiator": initiator,
            "data_source": data_source,
        }
    )
    return run_id


def _run_is_active(db: Any, run_id: str) -> bool:
    row = db.execute(
        "SELECT run_status FROM pipeline_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    return bool(row and row["run_status"] == RunStatus.RUNNING.value)


def last_recorded_stage(db: Any, run_id: str) -> str | None:
    row = db.execute(
        "SELECT stage FROM pipeline_stages WHERE run_id=? ORDER BY stage_order DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    return row["stage"] if row else None


def finalize_run(
    db: Any,
    run_id: str,
    *,
    terminal_stage: str,
    terminal_reason: str | None = None,
    run_status: str | None = None,
) -> bool:
    if not _run_is_active(db, run_id):
        return False

    if run_status is None:
        stage_row = db.execute(
            "SELECT status FROM pipeline_stages WHERE run_id=? AND stage=?",
            (run_id, terminal_stage),
        ).fetchone()
        if stage_row and stage_row["status"] == StageStatus.SUCCESS.value:
            run_status = RunStatus.COMPLETE.value
        elif stage_row and stage_row["status"] == StageStatus.SKIPPED.value:
            run_status = RunStatus.COMPLETE.value
        else:
            run_status = RunStatus.FAILED.value

    finished_at = utc_now_iso()
    db.execute(
        """
        UPDATE pipeline_runs
        SET finished_at=?, run_status=?, terminal_stage=?, terminal_reason=?
        WHERE run_id=?
        """,
        (finished_at, run_status, terminal_stage, terminal_reason, run_id),
    )
    db.commit()
    _log_stage_event(
        {
            "run_id": run_id,
            "action": "finalize",
            "run_status": run_status,
            "terminal_stage": terminal_stage,
            "terminal_reason": terminal_reason,
        }
    )
    return True


def abort_pipeline_run(
    db: Any,
    run_id: str | None,
    *,
    terminal_stage: str | None = None,
    terminal_reason: str = FAIL_EXCEPTION,
    run_status: str = RunStatus.ABORTED.value,
) -> bool:
    """Mark a running pipeline aborted after an unhandled exception."""
    if not run_id or not _run_is_active(db, run_id):
        return False
    stage = terminal_stage or last_recorded_stage(db, run_id) or PipelineStageId.CONNECTION.value
    skip_remaining_stages(db, run_id, after_stage=stage, reason=terminal_reason)
    return finalize_run(
        db,
        run_id,
        terminal_stage=stage,
        terminal_reason=terminal_reason,
        run_status=run_status,
    )


@contextmanager
def pipeline_run_guard(
    db: Any,
    run_id: str | None,
    *,
    terminal_stage: str | None = None,
) -> Iterator[None]:
    """Finalize a running pipeline as aborted if an exception escapes the block."""
    try:
        yield
    except Exception:
        abort_pipeline_run(db, run_id, terminal_stage=terminal_stage)
        raise


def record_stage(
    db: Any,
    run_id: str,
    stage: str,
    *,
    started_at: str,
    finished_at: str | None,
    status: str,
    failure_reason: str | None = None,
    artifacts: dict[str, Any] | None = None,
) -> None:
    duration_ms = None
    if finished_at:
        try:
            start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            finish_dt = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
            duration_ms = max(0.0, (finish_dt - start_dt).total_seconds() * 1000)
        except Exception:
            duration_ms = None

    stage_order = STAGE_ORDER_INDEX.get(stage, 0)
    db.execute(
        """
        INSERT INTO pipeline_stages (
            run_id, stage, stage_order, started_at, finished_at, duration_ms,
            status, failure_reason, artifacts_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, stage) DO UPDATE SET
            stage_order = excluded.stage_order,
            started_at = excluded.started_at,
            finished_at = excluded.finished_at,
            duration_ms = excluded.duration_ms,
            status = excluded.status,
            failure_reason = excluded.failure_reason,
            artifacts_json = excluded.artifacts_json
        """,
        (
            run_id,
            stage,
            stage_order,
            started_at,
            finished_at,
            duration_ms,
            status,
            failure_reason,
            _json_dumps(artifacts or {}),
        ),
    )
    db.commit()
    _log_stage_event(
        {
            "run_id": run_id,
            "stage": stage,
            "status": status,
            "failure_reason": failure_reason,
            "duration_ms": duration_ms,
        }
    )


def skip_remaining_stages(
    db: Any,
    run_id: str,
    *,
    after_stage: str,
    reason: str | None = None,
) -> None:
    start_index = STAGE_ORDER_INDEX.get(after_stage, 0)
    now = utc_now_iso()
    for stage in STAGE_ORDER:
        if STAGE_ORDER_INDEX[stage.value] <= start_index:
            continue
        record_stage(
            db,
            run_id,
            stage.value,
            started_at=now,
            finished_at=now,
            status=StageStatus.SKIPPED.value,
            failure_reason=reason,
            artifacts={"skipped_after": after_stage},
        )


@dataclass
class StageRecorder:
    db: Any
    run_id: str
    stage: str
    started_at: str = field(default_factory=utc_now_iso)
    _artifacts: dict[str, Any] = field(default_factory=dict)
    _status: str = StageStatus.RUNNING.value
    _failure_reason: str | None = None
    _finished: bool = False

    def set_artifacts(self, artifacts: dict[str, Any]) -> None:
        self._artifacts.update(artifacts)

    def succeed(self, artifacts: dict[str, Any] | None = None) -> None:
        if artifacts:
            self.set_artifacts(artifacts)
        self._status = StageStatus.SUCCESS.value
        self._persist()

    def fail(self, reason: str, artifacts: dict[str, Any] | None = None) -> None:
        if artifacts:
            self.set_artifacts(artifacts)
        self._status = StageStatus.FAILED.value
        self._failure_reason = reason
        self._persist()

    def skip(self, reason: str | None = None, artifacts: dict[str, Any] | None = None) -> None:
        if artifacts:
            self.set_artifacts(artifacts)
        self._status = StageStatus.SKIPPED.value
        self._failure_reason = reason
        self._persist()

    def _persist(self) -> None:
        if self._finished:
            return
        finished_at = utc_now_iso()
        record_stage(
            self.db,
            self.run_id,
            self.stage,
            started_at=self.started_at,
            finished_at=finished_at,
            status=self._status,
            failure_reason=self._failure_reason,
            artifacts=self._artifacts,
        )
        self._finished = True


@contextmanager
def pipeline_stage(db: Any, run_id: str | None, stage: str) -> Iterator[StageRecorder | None]:
    if not run_id:
        yield None
        return
    recorder = StageRecorder(db=db, run_id=run_id, stage=stage)
    try:
        yield recorder
        if not recorder._finished:
            recorder.succeed()
    except Exception:
        if not recorder._finished:
            recorder.fail(FAIL_WRITE_ERROR)
        raise


def _extract_urls(raw_text: str) -> list[str]:
    urls: list[str] = []
    for match in _HTTP_URL_RE.findall(raw_text or ""):
        cleaned = match.rstrip(").,;")
        if cleaned not in urls:
            urls.append(cleaned)
    return urls


def _meaningful_item_keys(items: list[dict[str, Any]] | None) -> list[str]:
    keys: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        value = str(item.get("value") or "").strip().lower()
        if key and value and value not in _EMPTY_VALUES:
            keys.append(key)
    return keys


def record_inferred_client_stages(
    db: Any,
    run_id: str,
    *,
    sync_status: str | None,
    sync_failure_reason: str | None,
    connection_status: str | None,
    raw_text: str,
    items: list[dict[str, Any]] | None,
    json_payload_chars: int = 0,
) -> tuple[bool, str | None]:
    """Infer and record connection, navigation, and capture when client stages are absent.

    Returns (pipeline_may_continue, terminal_stage_if_aborted).
    """
    now = utc_now_iso()

    connection_reason = None
    connection_status_value = StageStatus.SUCCESS.value
    if sync_status == "login_required" or sync_failure_reason in {FAIL_LOGIN_WALL, FAIL_LOGIN_REQUIRED}:
        connection_status_value = StageStatus.FAILED.value
        connection_reason = FAIL_LOGIN_REQUIRED
    elif connection_status and connection_status.lower() in {"needs_login", "login_required"}:
        connection_status_value = StageStatus.FAILED.value
        connection_reason = FAIL_LOGIN_REQUIRED

    record_stage(
        db,
        run_id,
        PipelineStageId.CONNECTION.value,
        started_at=now,
        finished_at=now,
        status=connection_status_value,
        failure_reason=connection_reason,
        artifacts={
            "inferred": True,
            "connection_status": connection_status,
            "sync_status": sync_status,
        },
    )
    if connection_status_value == StageStatus.FAILED.value:
        skip_remaining_stages(db, run_id, after_stage=PipelineStageId.CONNECTION.value, reason=connection_reason)
        finalize_run(
            db,
            run_id,
            terminal_stage=PipelineStageId.CONNECTION.value,
            terminal_reason=connection_reason,
            run_status=RunStatus.FAILED.value,
        )
        return False, PipelineStageId.CONNECTION.value

    urls = _extract_urls(raw_text)
    navigation_status = StageStatus.SUCCESS.value
    navigation_reason = None
    if not urls and not raw_text.strip():
        navigation_status = StageStatus.FAILED.value
        navigation_reason = FAIL_NO_PAGES_VISITED

    record_stage(
        db,
        run_id,
        PipelineStageId.NAVIGATION.value,
        started_at=now,
        finished_at=now,
        status=navigation_status,
        failure_reason=navigation_reason,
        artifacts={
            "inferred": True,
            "pages_visited": max(1, len(urls)) if raw_text.strip() else len(urls),
            "urls": urls[:10],
        },
    )
    if navigation_status == StageStatus.FAILED.value:
        skip_remaining_stages(db, run_id, after_stage=PipelineStageId.NAVIGATION.value, reason=navigation_reason)
        finalize_run(
            db,
            run_id,
            terminal_stage=PipelineStageId.NAVIGATION.value,
            terminal_reason=navigation_reason,
            run_status=RunStatus.FAILED.value,
        )
        return False, PipelineStageId.NAVIGATION.value

    capture_status = StageStatus.SUCCESS.value
    capture_reason = None
    raw_len = len(raw_text or "")
    item_count = len(items or [])
    if sync_failure_reason == FAIL_QUALITY_GATE:
        capture_status = StageStatus.FAILED.value
        capture_reason = FAIL_QUALITY_GATE
    elif sync_failure_reason == FAIL_NO_DATA or (raw_len == 0 and item_count == 0):
        capture_status = StageStatus.FAILED.value
        capture_reason = FAIL_NO_DATA
    elif sync_failure_reason == FAIL_LOGIN_WALL:
        capture_status = StageStatus.FAILED.value
        capture_reason = FAIL_LOGIN_WALL
    elif raw_len < 80 and item_count == 0 and json_payload_chars == 0:
        capture_status = StageStatus.FAILED.value
        capture_reason = FAIL_PAYLOAD_TOO_SMALL

    record_stage(
        db,
        run_id,
        PipelineStageId.CAPTURE.value,
        started_at=now,
        finished_at=now,
        status=capture_status,
        failure_reason=capture_reason,
        artifacts={
            "inferred": True,
            "raw_text_chars": raw_len,
            "item_count": item_count,
            "json_payload_chars": json_payload_chars,
        },
    )
    if capture_status == StageStatus.FAILED.value:
        skip_remaining_stages(db, run_id, after_stage=PipelineStageId.CAPTURE.value, reason=capture_reason)
        finalize_run(
            db,
            run_id,
            terminal_stage=PipelineStageId.CAPTURE.value,
            terminal_reason=capture_reason,
            run_status=RunStatus.FAILED.value,
        )
        return False, PipelineStageId.CAPTURE.value

    return True, None


def record_structured_stage(
    db: Any,
    run_id: str | None,
    *,
    fields: list[dict[str, Any]] | None,
    has_extractor: bool,
    source_label: str = "connector",
    attempted: bool = True,
) -> None:
    if not run_id:
        return
    now = utc_now_iso()
    field_list = fields or []
    if field_list:
        record_stage(
            db,
            run_id,
            PipelineStageId.STRUCTURED.value,
            started_at=now,
            finished_at=now,
            status=StageStatus.SUCCESS.value,
            artifacts={
                "field_count": len(field_list),
                "field_keys": [f.get("key") for f in field_list if f.get("key")][:20],
                "source_label": source_label,
            },
        )
        return

    if not attempted:
        record_stage(
            db,
            run_id,
            PipelineStageId.STRUCTURED.value,
            started_at=now,
            finished_at=now,
            status=StageStatus.SKIPPED.value,
            failure_reason=FAIL_NOT_ATTEMPTED_ON_SYNC_PATH,
            artifacts={"reason": FAIL_NOT_ATTEMPTED_ON_SYNC_PATH},
        )
        return

    if not has_extractor:
        record_stage(
            db,
            run_id,
            PipelineStageId.STRUCTURED.value,
            started_at=now,
            finished_at=now,
            status=StageStatus.SKIPPED.value,
            artifacts={"reason": "no_structured_extractor"},
        )
        return

    record_stage(
        db,
        run_id,
        PipelineStageId.STRUCTURED.value,
        started_at=now,
        finished_at=now,
        status=StageStatus.FAILED.value,
        failure_reason=FAIL_CONNECTOR_MISS,
        artifacts={"source_label": source_label},
    )


def record_intelligent_stage(
    db: Any,
    run_id: str | None,
    *,
    enabled: bool,
    raw_field_count: int,
    provider: str | None = None,
    model: str | None = None,
    cache_hit: bool = False,
    error: str | None = None,
) -> None:
    if not run_id:
        return
    now = utc_now_iso()
    artifacts = {
        "raw_field_count": raw_field_count,
        "provider": provider,
        "model": model,
        "cache_hit": cache_hit,
    }
    if not enabled:
        record_stage(
            db,
            run_id,
            PipelineStageId.INTELLIGENT.value,
            started_at=now,
            finished_at=now,
            status=StageStatus.SKIPPED.value,
            failure_reason=FAIL_DISCOVERY_DISABLED,
            artifacts=artifacts,
        )
        return
    if error:
        record_stage(
            db,
            run_id,
            PipelineStageId.INTELLIGENT.value,
            started_at=now,
            finished_at=now,
            status=StageStatus.FAILED.value,
            failure_reason=FAIL_DISCOVERY_ERROR,
            artifacts={**artifacts, "error": error[:200]},
        )
        return
    if raw_field_count <= 0:
        record_stage(
            db,
            run_id,
            PipelineStageId.INTELLIGENT.value,
            started_at=now,
            finished_at=now,
            status=StageStatus.FAILED.value,
            failure_reason=FAIL_LLM_EMPTY,
            artifacts=artifacts,
        )
        return

    record_stage(
        db,
        run_id,
        PipelineStageId.INTELLIGENT.value,
        started_at=now,
        finished_at=now,
        status=StageStatus.SUCCESS.value,
        artifacts=artifacts,
    )


def record_validation_stage(
    db: Any,
    run_id: str | None,
    *,
    fields_in: int,
    fields_out: int,
    auto_enabled_count: int,
    failure_reason: str | None,
) -> None:
    if not run_id:
        return
    now = utc_now_iso()
    artifacts = {
        "fields_in": fields_in,
        "fields_out": fields_out,
        "auto_enabled_count": auto_enabled_count,
    }
    if fields_out <= 0:
        reason = failure_reason or FAIL_ALL_FILTERED
        if reason == "llm_empty":
            reason = FAIL_LLM_EMPTY
        elif reason == "low_confidence_only":
            reason = FAIL_LOW_CONFIDENCE_ONLY
        elif reason == "stale_date_only":
            reason = FAIL_STALE_DATE_ONLY
        record_stage(
            db,
            run_id,
            PipelineStageId.VALIDATION.value,
            started_at=now,
            finished_at=now,
            status=StageStatus.FAILED.value,
            failure_reason=reason,
            artifacts=artifacts,
        )
        return

    record_stage(
        db,
        run_id,
        PipelineStageId.VALIDATION.value,
        started_at=now,
        finished_at=now,
        status=StageStatus.SUCCESS.value,
        artifacts=artifacts,
    )


def record_trusted_observations_stage(
    db: Any,
    run_id: str | None,
    *,
    trusted_items: list[dict[str, Any]] | None,
    discovered_field_count: int,
    enabled_field_count: int,
    extraction_status: str | None,
    items_written: int,
) -> tuple[str, str | None]:
    """Record stage 7 and return (status, failure_reason)."""
    if not run_id:
        return StageStatus.SKIPPED.value, None

    now = utc_now_iso()
    trusted_keys = _meaningful_item_keys(trusted_items)
    storage_split = discovered_field_count > 0 and items_written == 0 and enabled_field_count > 0

    artifacts = {
        "trusted_count": len(trusted_keys),
        "trusted_keys": trusted_keys[:20],
        "canonical_store": "account_data.items" if items_written > 0 else "none",
        "extraction_status": extraction_status,
        "storage_split": storage_split,
        "persistence_detail": {
            "items_written": items_written,
            "discovered_fields_written": discovered_field_count,
        },
    }

    if storage_split:
        record_stage(
            db,
            run_id,
            PipelineStageId.TRUSTED_OBSERVATIONS.value,
            started_at=now,
            finished_at=now,
            status=StageStatus.FAILED.value,
            failure_reason=FAIL_STORAGE_SPLIT,
            artifacts=artifacts,
        )
        return StageStatus.FAILED.value, FAIL_STORAGE_SPLIT

    if trusted_keys:
        record_stage(
            db,
            run_id,
            PipelineStageId.TRUSTED_OBSERVATIONS.value,
            started_at=now,
            finished_at=now,
            status=StageStatus.SUCCESS.value,
            artifacts=artifacts,
        )
        return StageStatus.SUCCESS.value, None

    if extraction_status == "failed":
        record_stage(
            db,
            run_id,
            PipelineStageId.TRUSTED_OBSERVATIONS.value,
            started_at=now,
            finished_at=now,
            status=StageStatus.FAILED.value,
            failure_reason=FAIL_NO_TRUSTED_OBSERVATIONS,
            artifacts=artifacts,
        )
        return StageStatus.FAILED.value, FAIL_NO_TRUSTED_OBSERVATIONS

    record_stage(
        db,
        run_id,
        PipelineStageId.TRUSTED_OBSERVATIONS.value,
        started_at=now,
        finished_at=now,
        status=StageStatus.FAILED.value,
        failure_reason=FAIL_NO_TRUSTED_OBSERVATIONS,
        artifacts=artifacts,
    )
    return StageStatus.FAILED.value, FAIL_NO_TRUSTED_OBSERVATIONS


def finalize_pipeline_from_save(
    db: Any,
    run_id: str | None,
    *,
    trusted_status: str,
    trusted_reason: str | None,
    validation_failed: bool,
    validation_reason: str | None,
) -> None:
    if not run_id:
        return
    if trusted_status == StageStatus.SUCCESS.value:
        finalize_run(
            db,
            run_id,
            terminal_stage=PipelineStageId.TRUSTED_OBSERVATIONS.value,
            terminal_reason=None,
            run_status=RunStatus.COMPLETE.value,
        )
        return
    if validation_failed:
        skip_remaining_stages(
            db,
            run_id,
            after_stage=PipelineStageId.VALIDATION.value,
            reason=validation_reason,
        )
        finalize_run(
            db,
            run_id,
            terminal_stage=PipelineStageId.VALIDATION.value,
            terminal_reason=validation_reason,
            run_status=RunStatus.FAILED.value,
        )
        return
    finalize_run(
        db,
        run_id,
        terminal_stage=PipelineStageId.TRUSTED_OBSERVATIONS.value,
        terminal_reason=trusted_reason,
        run_status=RunStatus.FAILED.value,
    )


def finalize_pipeline_after_empty_discovery(
    db: Any,
    run_id: str | None,
    *,
    has_structured_extractor: bool,
    structured_fields: list[dict[str, Any]] | None = None,
    validation_reason: str | None = None,
) -> None:
    if not run_id:
        return
    record_structured_stage(
        db,
        run_id,
        fields=structured_fields or [],
        has_extractor=has_structured_extractor,
    )
    reason = validation_reason or FAIL_LLM_EMPTY
    record_validation_stage(
        db,
        run_id,
        fields_in=0,
        fields_out=0,
        auto_enabled_count=0,
        failure_reason=reason,
    )
    trusted_status, trusted_reason = record_trusted_observations_stage(
        db,
        run_id,
        trusted_items=[],
        discovered_field_count=0,
        enabled_field_count=0,
        extraction_status="failed",
        items_written=0,
    )
    finalize_pipeline_from_save(
        db,
        run_id,
        trusted_status=trusted_status,
        trusted_reason=trusted_reason,
        validation_failed=True,
        validation_reason=reason,
    )


def finalize_sync_without_discovery(
    db: Any,
    run_id: str | None,
    *,
    items: list[dict[str, Any]] | None,
    extraction_status: str | None,
    has_structured_extractor: bool,
    structured_fields: list[dict[str, Any]] | None = None,
) -> None:
    if not run_id:
        return
    record_structured_stage(
        db,
        run_id,
        fields=structured_fields or [],
        has_extractor=has_structured_extractor,
        attempted=False,
    )
    record_intelligent_stage(db, run_id, enabled=False, raw_field_count=0)
    record_validation_stage(
        db,
        run_id,
        fields_in=len(items or []),
        fields_out=len(_meaningful_item_keys(items)),
        auto_enabled_count=len(_meaningful_item_keys(items)),
        failure_reason=None if _meaningful_item_keys(items) else FAIL_NO_TRUSTED_OBSERVATIONS,
    )
    trusted_status, trusted_reason = record_trusted_observations_stage(
        db,
        run_id,
        trusted_items=items,
        discovered_field_count=0,
        enabled_field_count=len(_meaningful_item_keys(items)),
        extraction_status=extraction_status,
        items_written=len(_meaningful_item_keys(items)),
    )
    finalize_pipeline_from_save(
        db,
        run_id,
        trusted_status=trusted_status,
        trusted_reason=trusted_reason,
        validation_failed=not _meaningful_item_keys(items),
        validation_reason=FAIL_NO_TRUSTED_OBSERVATIONS if not _meaningful_item_keys(items) else None,
    )


def ingest_client_stages(
    db: Any,
    *,
    user_id: str,
    source: str,
    run_id: str,
    initiator: str,
    data_source: str | None,
    stages: list[dict[str, Any]],
) -> None:
    existing = db.execute(
        "SELECT run_id FROM pipeline_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    if not existing:
        start_run(
            db,
            user_id=user_id,
            source=source,
            initiator=initiator,
            data_source=data_source,
            run_id=run_id,
        )

    for stage_payload in stages:
        stage_name = str(stage_payload.get("stage") or "").strip()
        if stage_name not in STAGE_ORDER_INDEX:
            continue
        record_stage(
            db,
            run_id,
            stage_name,
            started_at=str(stage_payload.get("started_at") or utc_now_iso()),
            finished_at=stage_payload.get("finished_at"),
            status=str(stage_payload.get("status") or StageStatus.SUCCESS.value),
            failure_reason=stage_payload.get("failure_reason"),
            artifacts=stage_payload.get("artifacts") or {},
        )


def list_recent_runs(db: Any, *, limit: int = 100) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT * FROM pipeline_runs ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_run(db: Any, run_id: str) -> dict[str, Any] | None:
    row = db.execute("SELECT * FROM pipeline_runs WHERE run_id=?", (run_id,)).fetchone()
    return dict(row) if row else None


def get_run_stages(db: Any, run_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT * FROM pipeline_stages WHERE run_id=? ORDER BY stage_order",
        (run_id,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        if item.get("artifacts_json"):
            try:
                item["artifacts"] = json.loads(item["artifacts_json"])
            except Exception:
                item["artifacts"] = {}
        result.append(item)
    return result


def sync_initiator(sync_source: str) -> str:
    if sync_source == "railway":
        return RunInitiator.RAILWAY_SYNC.value
    return RunInitiator.EXTENSION_SYNC.value


def map_sync_failure_reason(reason: str) -> str:
    mapping = {
        "no_data": FAIL_NO_DATA,
        "no_content": FAIL_NO_DATA,
        "timeout": FAIL_NAV_TIMEOUT,
        "login_wall": FAIL_LOGIN_WALL,
        "login_required": FAIL_LOGIN_REQUIRED,
        "domain_unreachable": FAIL_DOMAIN_UNREACHABLE,
        "domain_moved": FAIL_DOMAIN_UNREACHABLE,
    }
    return mapping.get(reason, FAIL_NO_DATA)


def record_adapter_extraction_run(
    db: Any,
    *,
    user_id: str,
    source: str,
    data_source: str | None,
    structured_item: dict[str, Any] | None,
    extraction_status: str,
    invalid_value: bool = False,
) -> str:
    """Record a short adapter-only pipeline (structured → trusted observations)."""
    run_id = new_run_id()
    start_run(
        db,
        user_id=user_id,
        source=source,
        initiator=RunInitiator.ADAPTER.value,
        data_source=data_source,
        run_id=run_id,
    )
    now = utc_now_iso()
    for stage in (
        PipelineStageId.CONNECTION,
        PipelineStageId.NAVIGATION,
        PipelineStageId.CAPTURE,
    ):
        record_stage(
            db,
            run_id,
            stage.value,
            started_at=now,
            finished_at=now,
            status=StageStatus.SKIPPED.value,
            artifacts={"reason": "adapter_path"},
        )

    if invalid_value or not structured_item:
        record_structured_stage(
            db,
            run_id,
            fields=[],
            has_extractor=True,
            source_label="adapter",
        )
        record_intelligent_stage(db, run_id, enabled=False, raw_field_count=0)
        record_validation_stage(
            db,
            run_id,
            fields_in=0,
            fields_out=0,
            auto_enabled_count=0,
            failure_reason=FAIL_INVALID_NORMALIZED_VALUE,
        )
        trusted_status, trusted_reason = record_trusted_observations_stage(
            db,
            run_id,
            trusted_items=[],
            discovered_field_count=0,
            enabled_field_count=0,
            extraction_status="failed",
            items_written=0,
        )
        finalize_pipeline_from_save(
            db,
            run_id,
            trusted_status=trusted_status,
            trusted_reason=trusted_reason or FAIL_INVALID_NORMALIZED_VALUE,
            validation_failed=True,
            validation_reason=FAIL_INVALID_NORMALIZED_VALUE,
        )
        return run_id

    items = [structured_item]
    record_structured_stage(
        db,
        run_id,
        fields=items,
        has_extractor=True,
        source_label="adapter",
    )
    record_intelligent_stage(db, run_id, enabled=False, raw_field_count=0)
    record_validation_stage(
        db,
        run_id,
        fields_in=1,
        fields_out=1,
        auto_enabled_count=1,
        failure_reason=None,
    )
    trusted_status, trusted_reason = record_trusted_observations_stage(
        db,
        run_id,
        trusted_items=items,
        discovered_field_count=1,
        enabled_field_count=1,
        extraction_status=extraction_status,
        items_written=1,
    )
    finalize_pipeline_from_save(
        db,
        run_id,
        trusted_status=trusted_status,
        trusted_reason=trusted_reason,
        validation_failed=False,
        validation_reason=None,
    )
    return run_id
