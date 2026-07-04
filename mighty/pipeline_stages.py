"""Pipeline stage identifiers, ordering, and normalized failure reasons."""

from __future__ import annotations

from enum import Enum


class PipelineStageId(str, Enum):
    CONNECTION = "connection"
    NAVIGATION = "navigation"
    CAPTURE = "capture"
    STRUCTURED = "structured"
    INTELLIGENT = "intelligent"
    VALIDATION = "validation"
    TRUSTED_OBSERVATIONS = "trusted_observations"


STAGE_ORDER: tuple[PipelineStageId, ...] = (
    PipelineStageId.CONNECTION,
    PipelineStageId.NAVIGATION,
    PipelineStageId.CAPTURE,
    PipelineStageId.STRUCTURED,
    PipelineStageId.INTELLIGENT,
    PipelineStageId.VALIDATION,
    PipelineStageId.TRUSTED_OBSERVATIONS,
)

STAGE_ORDER_INDEX: dict[str, int] = {
    stage.value: index for index, stage in enumerate(STAGE_ORDER, start=1)
}


class StageStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class RunStatus(str, Enum):
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    ABORTED = "aborted"


class RunInitiator(str, Enum):
    EXTENSION_SYNC = "extension_sync"
    RAILWAY_SYNC = "railway_sync"
    INTERCEPT = "intercept"
    MANUAL = "manual"
    ADAPTER = "adapter"
    SYNC_FAILURE = "sync_failure"


# Connection
FAIL_LOGIN_REQUIRED = "login_required"
FAIL_SESSION_EXPIRED = "session_expired"
FAIL_NEEDS_FIRST_VISIT = "needs_first_visit"

# Navigation
FAIL_WRONG_URL = "wrong_url"
FAIL_NAV_TIMEOUT = "timeout"
FAIL_DOMAIN_UNREACHABLE = "domain_unreachable"
FAIL_NO_PAGES_VISITED = "no_pages_visited"

# Capture
FAIL_NO_DATA = "no_data"
FAIL_LOGIN_WALL = "login_wall"
FAIL_QUALITY_GATE = "quality_gate"
FAIL_PAYLOAD_TOO_SMALL = "payload_too_small"

# Structured extraction
FAIL_NOT_ATTEMPTED_ON_SYNC_PATH = "not_attempted_on_sync_path"
FAIL_CONNECTOR_MISS = "connector_miss"
FAIL_JSON_PARSE_ERROR = "json_parse_error"
FAIL_INVALID_NORMALIZED_VALUE = "invalid_normalized_value"

# Intelligent extraction
FAIL_LLM_EMPTY = "llm_empty"
FAIL_DISCOVERY_ERROR = "discovery_error"
FAIL_DISCOVERY_DISABLED = "discovery_disabled"

# Validation
FAIL_LOW_CONFIDENCE_ONLY = "low_confidence_only"
FAIL_STALE_DATE_ONLY = "stale_date_only"
FAIL_ALL_FILTERED = "all_filtered"

# Trusted observations
FAIL_NO_TRUSTED_OBSERVATIONS = "no_trusted_observations"
FAIL_STORAGE_SPLIT = "storage_split"
FAIL_PARTIAL_TRUST = "partial_trust"
FAIL_WRITE_ERROR = "write_error"

# Pipeline abort (unhandled exception mid-run)
FAIL_EXCEPTION = "exception"
