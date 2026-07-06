"""AccountState projector — shadow-mode canonical account model (see docs/ACCOUNT_STATE.md)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from mighty.connection_state import (
    CONNECTED as CONN_CONNECTED,
    CONNECTING,
    NEEDS_LOGIN as CONN_NEEDS_LOGIN,
    WAITING_FOR_EXTENSION as CONN_WAITING,
)
from mighty.observation_catalog import (
    expected_observations_for_provider,
    field_keys_to_observations,
)
from mighty.pipeline_stages import (
    FAIL_LOGIN_REQUIRED,
    FAIL_SESSION_EXPIRED,
    PipelineStageId,
    StageStatus,
)
from mighty.provider_account import (
    DATA_SOURCE_API,
    DATA_SOURCE_EMAIL,
    DATA_SOURCE_EXTENSION,
    DATA_SOURCE_MANUAL,
    DATA_SOURCE_RAILWAY,
    has_normalized_data,
    normalize_data_source,
)

ACCOUNT_STATE_VERSION = 1
COMPLETE_COVERAGE_THRESHOLD = 60

ACCESS_BROWSER_SESSION = "browser_session"
ACCESS_MIGHTY_LOGIN = "mighty_login"
ACCESS_API = "api"
ACCESS_MANUAL = "manual"

CONN_NOT_CONNECTED = "not_connected"
CONN_CONNECTING = "connecting"
CONN_CONNECTED = "connected"
CONN_NEEDS_LOGIN = "needs_login"

SESSION_HEALTHY = "healthy"
SESSION_EXPIRING = "expiring"
SESSION_EXPIRED = "expired"
SESSION_UNKNOWN = "unknown"

DATA_NONE = "none"
DATA_PARTIAL = "partial"
DATA_COMPLETE = "complete"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

ACTION_CONNECT = "connect"
ACTION_OPEN_PROVIDER = "open_provider"
ACTION_LOGIN = "login"
ACTION_WAIT = "wait"
ACTION_NONE = "none"
ACTION_REVIEW = "review"

URGENCY_BLOCKER = "blocker"
URGENCY_SOON = "soon"
URGENCY_OPTIONAL = "optional"

FINANCIAL_PROVIDERS = frozenset(
    {"amex", "chase", "apple_card", "citi", "capital_one", "discover", "wells_fargo", "sofi"}
)

SESSION_TTL_HOURS: dict[str, tuple[int, int]] = {
    "financial": (24, 24 * 7),
    "default": (24 * 7, 24 * 14),
}

_decrypt_fn: Callable[[str, str], dict] | None = None


def configure_account_state(*, decrypt_fn: Callable[[str, str], dict] | None) -> None:
    """Register decrypt callback from the Flask app (avoids circular imports)."""
    global _decrypt_fn
    _decrypt_fn = decrypt_fn


def _resolve_decrypt_fn(
    decrypt_fn: Callable[[str, str], dict] | None,
) -> Callable[[str, str], dict] | None:
    return decrypt_fn if decrypt_fn is not None else _decrypt_fn


@dataclass
class ConfidenceFactors:
    session: int = 0
    observation: int = 0
    validation: int = 0
    provider_prior: int = 0


@dataclass
class Confidence:
    level: str
    score: int
    factors: ConfidenceFactors = field(default_factory=ConfidenceFactors)


@dataclass
class RecommendedAction:
    kind: str
    label: str
    url: str | None = None
    urgency: str = URGENCY_OPTIONAL
    reason: str | None = None


@dataclass
class AccountState:
    user_id: str
    provider: str
    display_name: str
    category: str | None
    access_method: str
    connection_state: str
    session_health: str
    last_verified_at: str | None
    data_status: str
    last_data_refresh: str | None
    observations_available: list[str]
    field_count: int
    next_recommended_action: RecommendedAction | None
    confidence: Confidence
    status_line: str
    is_actionable: bool
    updated_at: str
    version: int = ACCOUNT_STATE_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if self.next_recommended_action:
            payload["next_recommended_action"] = asdict(self.next_recommended_action)
        payload["confidence"] = {
            "level": self.confidence.level,
            "score": self.confidence.score,
            "factors": asdict(self.confidence.factors),
        }
        return payload


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_account_state_tables(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS account_state (
            user_id                 TEXT NOT NULL,
            provider                TEXT NOT NULL,
            display_name            TEXT NOT NULL,
            category                TEXT,
            access_method           TEXT NOT NULL,
            connection_state        TEXT NOT NULL,
            session_health          TEXT NOT NULL,
            last_verified_at        TEXT,
            data_status             TEXT NOT NULL,
            last_data_refresh       TEXT,
            observations_json       TEXT NOT NULL DEFAULT '[]',
            action_json             TEXT,
            confidence_level        TEXT NOT NULL,
            confidence_score        INTEGER NOT NULL DEFAULT 0,
            confidence_factors_json TEXT,
            status_line             TEXT NOT NULL DEFAULT '',
            is_actionable           INTEGER NOT NULL DEFAULT 0,
            field_count             INTEGER NOT NULL DEFAULT 0,
            state_json              TEXT NOT NULL,
            updated_at              TEXT NOT NULL,
            version                 INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (user_id, provider)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_account_state_user ON account_state(user_id)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_account_state_conn "
        "ON account_state(user_id, connection_state)"
    )
    db.commit()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _meaningful_field_count(items: list | None) -> int:
    if not items:
        return 0
    count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        key = item.get("key") or item.get("field_key")
        val = str(item.get("value", "")).strip().lower()
        if key and val and val not in {"", "—", "–", "-", "n/a", "none", "0", "no data"}:
            count += 1
    return count


def _trusted_keys_from_items(items: list | None) -> list[str]:
    keys: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = item.get("key") or item.get("field_key")
        if key:
            keys.append(str(key))
    return keys


def access_method_from_data_source(data_source: str | None) -> str:
    src = normalize_data_source(data_source)
    if src == DATA_SOURCE_EXTENSION:
        return ACCESS_BROWSER_SESSION
    if src == DATA_SOURCE_RAILWAY:
        return ACCESS_MIGHTY_LOGIN
    if src == DATA_SOURCE_API:
        return ACCESS_API
    if src in (DATA_SOURCE_MANUAL, DATA_SOURCE_EMAIL):
        return ACCESS_MANUAL
    return ACCESS_BROWSER_SESSION


def _session_ttl_hours(provider: str) -> tuple[int, int]:
    if provider in FINANCIAL_PROVIDERS:
        return SESSION_TTL_HOURS["financial"]
    return SESSION_TTL_HOURS["default"]


def session_health_from_verified_at(
    *,
    provider: str,
    last_verified_at: str | None,
    login_required: bool,
    access_method: str,
) -> str:
    if login_required:
        return SESSION_EXPIRED
    if access_method == ACCESS_MANUAL:
        return SESSION_UNKNOWN
    if not last_verified_at:
        return SESSION_UNKNOWN
    verified = _parse_iso(last_verified_at)
    if not verified:
        return SESSION_UNKNOWN
    if verified.tzinfo is None:
        verified = verified.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - verified
    healthy_hours, expiring_hours = _session_ttl_hours(provider)
    if age <= timedelta(hours=healthy_hours):
        return SESSION_HEALTHY
    if age <= timedelta(hours=expiring_hours):
        return SESSION_EXPIRING
    return SESSION_EXPIRED


def _session_factor(session_health: str) -> int:
    return {
        SESSION_HEALTHY: 100,
        SESSION_EXPIRING: 70,
        SESSION_EXPIRED: 30,
        SESSION_UNKNOWN: 50,
    }.get(session_health, 50)


def confidence_level_from_score(score: int) -> str:
    if score >= 80:
        return CONFIDENCE_HIGH
    if score >= 50:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def compute_confidence(
    *,
    session_health: str,
    observation_pct: int | None,
    validation_pct: int | None,
    provider_prior: int = 50,
) -> Confidence:
    obs = observation_pct if observation_pct is not None else 0
    val = validation_pct if validation_pct is not None else 0
    factors = ConfidenceFactors(
        session=_session_factor(session_health),
        observation=obs,
        validation=val,
        provider_prior=provider_prior,
    )
    score = int(round(
        factors.session * 0.25
        + factors.observation * 0.35
        + factors.validation * 0.25
        + factors.provider_prior * 0.15
    ))
    score = max(0, min(100, score))
    return Confidence(level=confidence_level_from_score(score), score=score, factors=factors)


def data_status_from_signals(
    *,
    items: list | None,
    observation_pct: int | None,
    has_meaningful: bool | None = None,
) -> str:
    meaningful = has_meaningful if has_meaningful is not None else has_normalized_data(items)
    if not meaningful:
        return DATA_NONE
    if observation_pct is None:
        return DATA_PARTIAL
    if observation_pct >= COMPLETE_COVERAGE_THRESHOLD:
        return DATA_COMPLETE
    return DATA_PARTIAL


def _load_stage_row(db: Any, run_id: str, stage: str) -> dict[str, Any] | None:
    row = db.execute(
        """
        SELECT stage, status, failure_reason, started_at, finished_at, artifacts_json
        FROM pipeline_stages WHERE run_id=? AND stage=?
        """,
        (run_id, stage),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    artifacts: dict[str, Any] = {}
    if item.get("artifacts_json"):
        try:
            loaded = json.loads(item["artifacts_json"])
            if isinstance(loaded, dict):
                artifacts = loaded
        except (json.JSONDecodeError, TypeError):
            artifacts = {}
    item["artifacts"] = artifacts
    return item


def _latest_run_for_account(db: Any, user_id: str, provider: str) -> dict[str, Any] | None:
    row = db.execute(
        """
        SELECT run_id, created_at, finished_at, initiator, data_source, run_status,
               terminal_stage, terminal_reason
        FROM pipeline_runs
        WHERE user_id=? AND source=?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (user_id, provider),
    ).fetchone()
    return dict(row) if row else None


def _latest_successful_connection_verification(
    db: Any,
    user_id: str,
    provider: str,
) -> str | None:
    row = db.execute(
        """
        SELECT ps.finished_at
        FROM pipeline_stages ps
        JOIN pipeline_runs pr ON pr.run_id = ps.run_id
        WHERE pr.user_id=? AND pr.source=?
          AND ps.stage=? AND ps.status=?
        ORDER BY ps.finished_at DESC
        LIMIT 1
        """,
        (
            user_id,
            provider,
            PipelineStageId.CONNECTION.value,
            StageStatus.SUCCESS.value,
        ),
    ).fetchone()
    return row["finished_at"] if row else None


def _latest_trusted_observations(
    db: Any,
    user_id: str,
    provider: str,
) -> tuple[list[str], str | None]:
    row = db.execute(
        """
        SELECT ps.artifacts_json, ps.finished_at
        FROM pipeline_stages ps
        JOIN pipeline_runs pr ON pr.run_id = ps.run_id
        WHERE pr.user_id=? AND pr.source=?
          AND ps.stage=? AND ps.status=?
        ORDER BY ps.finished_at DESC
        LIMIT 1
        """,
        (
            user_id,
            provider,
            PipelineStageId.TRUSTED_OBSERVATIONS.value,
            StageStatus.SUCCESS.value,
        ),
    ).fetchone()
    if not row:
        return [], None
    keys: list[str] = []
    if row["artifacts_json"]:
        try:
            artifacts = json.loads(row["artifacts_json"])
            if isinstance(artifacts, dict):
                keys = list(artifacts.get("trusted_keys") or [])
        except (json.JSONDecodeError, TypeError):
            keys = []
    return keys, row["finished_at"]


def _latest_validation_ratio(db: Any, user_id: str, provider: str) -> int | None:
    row = db.execute(
        """
        SELECT ps.artifacts_json
        FROM pipeline_stages ps
        JOIN pipeline_runs pr ON pr.run_id = ps.run_id
        WHERE pr.user_id=? AND pr.source=?
          AND ps.stage=?
        ORDER BY ps.finished_at DESC
        LIMIT 1
        """,
        (user_id, provider, PipelineStageId.VALIDATION.value),
    ).fetchone()
    if not row or not row["artifacts_json"]:
        return None
    try:
        artifacts = json.loads(row["artifacts_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(artifacts, dict):
        return None
    fields_in = int(artifacts.get("fields_in") or 0)
    fields_out = int(artifacts.get("fields_out") or 0)
    if fields_in <= 0:
        return 100 if fields_out > 0 else 0
    return int(fields_out / fields_in * 100)


def _provider_prior_score(db: Any, provider: str) -> int:
    try:
        from mighty.provider_benchmark import compute_all_provider_benchmarks

        rows = compute_all_provider_benchmarks(
            db,
            [provider],
            {},
            display_names={provider: provider},
        )
        if rows:
            return rows[0].readiness_score
    except Exception:
        pass
    return 50


def _login_required_signals(
    *,
    sync_status: str,
    connection_status: str | None,
    connection_stage: dict[str, Any] | None,
) -> bool:
    if connection_status == CONN_CONNECTED:
        return False
    if sync_status == "login_required":
        return True
    if connection_status in {CONN_NEEDS_LOGIN, "login_required"}:
        return True
    if not connection_stage:
        return False
    if connection_stage.get("status") != StageStatus.FAILED.value:
        return False
    reason = connection_stage.get("failure_reason") or ""
    return reason in {FAIL_LOGIN_REQUIRED, FAIL_SESSION_EXPIRED, "login_wall"}


def _connection_state_from_signals(
    *,
    in_credentials: bool,
    sync_status: str,
    connection_status: str | None,
    login_required: bool,
    sync_running: bool,
    updating_source: str | None,
    provider: str,
    has_account_row: bool,
) -> str:
    if login_required:
        return CONN_NEEDS_LOGIN
    if sync_running and updating_source == provider:
        return CONN_CONNECTING
    if connection_status == CONN_CONNECTED:
        return CONN_CONNECTED
    if connection_status in {CONNECTING, CONN_WAITING} or sync_status == "needs_first_visit":
        return CONN_CONNECTING
    if in_credentials or has_account_row:
        if sync_status == "ok" and not login_required:
            return CONN_CONNECTED
        return CONN_CONNECTING
    return CONN_NOT_CONNECTED


def build_recommended_action(
    *,
    connection_state: str,
    data_status: str,
    confidence: Confidence,
    provider: str,
) -> RecommendedAction | None:
    if connection_state == CONN_NEEDS_LOGIN:
        return RecommendedAction(
            kind=ACTION_LOGIN,
            label="Sign in",
            url=f"/credentials?connect={provider}",
            urgency=URGENCY_BLOCKER,
            reason="session_expired",
        )
    if connection_state == CONN_NOT_CONNECTED:
        return RecommendedAction(
            kind=ACTION_CONNECT,
            label="Connect account",
            url=f"/credentials?connect={provider}",
            urgency=URGENCY_SOON,
            reason="not_connected",
        )
    if connection_state == CONN_CONNECTING:
        return RecommendedAction(
            kind=ACTION_OPEN_PROVIDER,
            label="Open provider in Chrome",
            url=f"/credentials?connect={provider}",
            urgency=URGENCY_SOON,
            reason="connecting",
        )
    if data_status == DATA_PARTIAL and confidence.level == CONFIDENCE_LOW:
        return RecommendedAction(
            kind=ACTION_REVIEW,
            label="Review account details",
            url=f"/account/{provider}",
            urgency=URGENCY_OPTIONAL,
            reason="partial_data",
        )
    if connection_state == CONN_CONNECTED and data_status in {DATA_COMPLETE, DATA_PARTIAL}:
        return RecommendedAction(
            kind=ACTION_NONE,
            label="",
            url=None,
            urgency=URGENCY_OPTIONAL,
            reason="healthy",
        )
    return None


def build_status_line(
    *,
    connection_state: str,
    data_status: str,
    session_health: str,
    last_data_refresh: str | None,
) -> str:
    from mighty.user_copy import (
        ACCOUNT_STATE_LABELS,
        ACCOUNT_STATE_NEEDS_SIGN_IN,
        ACCOUNT_STATE_NEEDS_ATTENTION,
        ACCOUNT_STATE_READY,
        ACCOUNT_STATE_UPDATING,
    )

    parts: list[str] = []
    if connection_state == CONN_NEEDS_LOGIN:
        parts.append(ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_SIGN_IN])
    elif connection_state == CONN_CONNECTING:
        parts.append(ACCOUNT_STATE_LABELS[ACCOUNT_STATE_UPDATING])
    elif connection_state == CONN_NOT_CONNECTED:
        parts.append(ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_SIGN_IN])
    elif data_status == DATA_COMPLETE:
        parts.append(ACCOUNT_STATE_LABELS[ACCOUNT_STATE_READY])
    elif data_status == DATA_PARTIAL:
        parts.append(ACCOUNT_STATE_LABELS[ACCOUNT_STATE_READY])
    elif data_status == DATA_NONE:
        if connection_state == CONN_CONNECTED:
            parts.append(ACCOUNT_STATE_LABELS[ACCOUNT_STATE_UPDATING])
        else:
            parts.append(ACCOUNT_STATE_LABELS[ACCOUNT_STATE_UPDATING])

    if last_data_refresh and data_status in {DATA_COMPLETE, DATA_PARTIAL}:
        verified = _parse_iso(last_data_refresh)
        if verified:
            if verified.tzinfo is None:
                verified = verified.replace(tzinfo=timezone.utc)
            days = (datetime.now(timezone.utc) - verified).days
            if days == 0:
                parts.append("Updated today")
            elif days == 1:
                parts.append("Updated yesterday")
            else:
                parts.append(f"Data from {verified.strftime('%b %d')}")
    elif session_health == SESSION_EXPIRING and connection_state == CONN_CONNECTED:
        parts.append("Session expiring soon")

    return " · ".join(parts) if parts else "Unknown"


def project_account_state(
    db: Any,
    user_id: str,
    provider: str,
    *,
    display_name: str | None = None,
    category: str | None = None,
    decrypt_fn: Callable[[str, str], dict] | None = None,
    display_names: dict[str, str] | None = None,
    category_map: dict[str, str] | None = None,
) -> AccountState:
    """Project AccountState from persisted account + pipeline signals."""
    ad_row = db.execute(
        """
        SELECT display_name, synced_at, connection_status, extraction_status,
               sync_status, data_enc
        FROM account_data WHERE user_id=? AND source=?
        """,
        (user_id, provider),
    ).fetchone()
    cred_row = db.execute(
        "SELECT 1 FROM account_credentials WHERE user_id=? AND source=? AND source != '_email'",
        (user_id, provider),
    ).fetchone()
    user_row = db.execute(
        "SELECT sync_running, sync_current_source FROM users WHERE id=?",
        (user_id,),
    ).fetchone()

    ad_data: dict[str, Any] = {}
    resolved_decrypt = _resolve_decrypt_fn(decrypt_fn)
    if ad_row and ad_row["data_enc"] and resolved_decrypt:
        ad_data = resolved_decrypt(user_id, ad_row["data_enc"] or "") or {}

    sync_status = (
        (ad_row["sync_status"] if ad_row and ad_row["sync_status"] else "")
        or ad_data.get("sync_status")
        or "ok"
    )
    connection_status = (
        (ad_row["connection_status"] if ad_row and ad_row["connection_status"] else "")
        or ad_data.get("connection_status")
        or None
    )
    data_source = normalize_data_source(
        ad_data.get("data_source") or ad_data.get("sync_source")
    )
    items = ad_data.get("items") or ad_data.get("ai_items") or []
    field_count = _meaningful_field_count(items)

    latest_run = _latest_run_for_account(db, user_id, provider)
    run_id = latest_run["run_id"] if latest_run else None
    connection_stage = _load_stage_row(db, run_id, PipelineStageId.CONNECTION.value) if run_id else None

    if latest_run and latest_run.get("data_source"):
        data_source = normalize_data_source(latest_run["data_source"]) or data_source

    access_method = access_method_from_data_source(data_source)
    login_required = _login_required_signals(
        sync_status=sync_status,
        connection_status=connection_status,
        connection_stage=connection_stage,
    )

    last_verified_at = _latest_successful_connection_verification(db, user_id, provider)
    if connection_stage and connection_stage.get("status") == StageStatus.SUCCESS.value:
        last_verified_at = connection_stage.get("finished_at") or last_verified_at

    session_health = session_health_from_verified_at(
        provider=provider,
        last_verified_at=last_verified_at,
        login_required=login_required,
        access_method=access_method,
    )

    sync_running = bool(user_row and user_row["sync_running"])
    updating_source = user_row["sync_current_source"] if user_row else None
    connection_state = _connection_state_from_signals(
        in_credentials=bool(cred_row),
        sync_status=sync_status,
        connection_status=connection_status,
        login_required=login_required,
        sync_running=sync_running,
        updating_source=updating_source,
        provider=provider,
        has_account_row=bool(ad_row),
    )

    trusted_keys, trusted_finished_at = _latest_trusted_observations(db, user_id, provider)
    if not trusted_keys:
        trusted_keys = _trusted_keys_from_items(items)
    observations_available = sorted(field_keys_to_observations(trusted_keys))

    if category_map and provider in category_map:
        category = category or category_map[provider]
    expected = expected_observations_for_provider(provider, category)
    observation_pct = None
    if expected:
        matched = len(set(observations_available) & set(expected))
        observation_pct = int(matched / len(expected) * 100)

    data_status = data_status_from_signals(
        items=items,
        observation_pct=observation_pct,
    )

    last_data_refresh = ad_row["synced_at"] if ad_row and ad_row["synced_at"] else None
    if not last_data_refresh:
        last_data_refresh = trusted_finished_at

    validation_pct = _latest_validation_ratio(db, user_id, provider)
    provider_prior = _provider_prior_score(db, provider)
    confidence = compute_confidence(
        session_health=session_health,
        observation_pct=observation_pct,
        validation_pct=validation_pct,
        provider_prior=provider_prior,
    )

    if connection_stage and connection_stage.get("status") == StageStatus.FAILED.value:
        if connection_stage.get("failure_reason") in {FAIL_LOGIN_REQUIRED, FAIL_SESSION_EXPIRED}:
            confidence = Confidence(
                level=CONFIDENCE_LOW,
                score=min(confidence.score, 49),
                factors=confidence.factors,
            )

    next_recommended_action = build_recommended_action(
        connection_state=connection_state,
        data_status=data_status,
        confidence=confidence,
        provider=provider,
    )
    status_line = build_status_line(
        connection_state=connection_state,
        data_status=data_status,
        session_health=session_health,
        last_data_refresh=last_data_refresh,
    )
    is_actionable = bool(
        next_recommended_action
        and next_recommended_action.kind not in {ACTION_NONE, ACTION_WAIT}
    )

    names = display_names or {}
    resolved_name = display_name or (ad_row["display_name"] if ad_row else None) or names.get(provider)
    if not resolved_name:
        resolved_name = provider.replace("_", " ").title()

    return AccountState(
        user_id=user_id,
        provider=provider,
        display_name=resolved_name,
        category=category,
        access_method=access_method,
        connection_state=connection_state,
        session_health=session_health,
        last_verified_at=last_verified_at,
        data_status=data_status,
        last_data_refresh=last_data_refresh,
        observations_available=observations_available,
        field_count=field_count,
        next_recommended_action=next_recommended_action,
        confidence=confidence,
        status_line=status_line,
        is_actionable=is_actionable,
        updated_at=utc_now_iso(),
        version=ACCOUNT_STATE_VERSION,
    )


def persist_account_state(db: Any, state: AccountState) -> None:
    ensure_account_state_tables(db)
    action_json = None
    if state.next_recommended_action:
        action_json = json.dumps(asdict(state.next_recommended_action), separators=(",", ":"))
    factors_json = json.dumps(asdict(state.confidence.factors), separators=(",", ":"))
    state_json = json.dumps(state.to_dict(), separators=(",", ":"), default=str)
    db.execute(
        """
        INSERT INTO account_state (
            user_id, provider, display_name, category, access_method,
            connection_state, session_health, last_verified_at, data_status,
            last_data_refresh, observations_json, action_json, confidence_level,
            confidence_score, confidence_factors_json, status_line, is_actionable,
            field_count, state_json, updated_at, version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, provider) DO UPDATE SET
            display_name=excluded.display_name,
            category=excluded.category,
            access_method=excluded.access_method,
            connection_state=excluded.connection_state,
            session_health=excluded.session_health,
            last_verified_at=excluded.last_verified_at,
            data_status=excluded.data_status,
            last_data_refresh=excluded.last_data_refresh,
            observations_json=excluded.observations_json,
            action_json=excluded.action_json,
            confidence_level=excluded.confidence_level,
            confidence_score=excluded.confidence_score,
            confidence_factors_json=excluded.confidence_factors_json,
            status_line=excluded.status_line,
            is_actionable=excluded.is_actionable,
            field_count=excluded.field_count,
            state_json=excluded.state_json,
            updated_at=excluded.updated_at,
            version=excluded.version
        """,
        (
            state.user_id,
            state.provider,
            state.display_name,
            state.category,
            state.access_method,
            state.connection_state,
            state.session_health,
            state.last_verified_at,
            state.data_status,
            state.last_data_refresh,
            json.dumps(state.observations_available, separators=(",", ":")),
            action_json,
            state.confidence.level,
            state.confidence.score,
            factors_json,
            state.status_line,
            1 if state.is_actionable else 0,
            state.field_count,
            state_json,
            state.updated_at,
            state.version,
        ),
    )
    db.commit()


def load_account_state(db: Any, user_id: str, provider: str) -> AccountState | None:
    row = db.execute(
        "SELECT state_json FROM account_state WHERE user_id=? AND provider=?",
        (user_id, provider),
    ).fetchone()
    if not row or not row["state_json"]:
        return None
    try:
        payload = json.loads(row["state_json"])
    except (json.JSONDecodeError, TypeError):
        return None
    factors = payload.get("confidence", {}).get("factors", {})
    action_payload = payload.get("next_recommended_action")
    action = None
    if action_payload:
        action = RecommendedAction(**action_payload)
    confidence = Confidence(
        level=payload.get("confidence", {}).get("level", CONFIDENCE_LOW),
        score=int(payload.get("confidence", {}).get("score", 0)),
        factors=ConfidenceFactors(
            session=int(factors.get("session", 0)),
            observation=int(factors.get("observation", 0)),
            validation=int(factors.get("validation", 0)),
            provider_prior=int(factors.get("provider_prior", 0)),
        ),
    )
    return AccountState(
        user_id=payload["user_id"],
        provider=payload["provider"],
        display_name=payload.get("display_name", provider),
        category=payload.get("category"),
        access_method=payload.get("access_method", ACCESS_BROWSER_SESSION),
        connection_state=payload.get("connection_state", CONN_NOT_CONNECTED),
        session_health=payload.get("session_health", SESSION_UNKNOWN),
        last_verified_at=payload.get("last_verified_at"),
        data_status=payload.get("data_status", DATA_NONE),
        last_data_refresh=payload.get("last_data_refresh"),
        observations_available=list(payload.get("observations_available") or []),
        field_count=int(payload.get("field_count", 0)),
        next_recommended_action=action,
        confidence=confidence,
        status_line=payload.get("status_line", ""),
        is_actionable=bool(payload.get("is_actionable")),
        updated_at=payload.get("updated_at", utc_now_iso()),
        version=int(payload.get("version", ACCOUNT_STATE_VERSION)),
    )


def list_account_states(
    db: Any,
    user_id: str,
    *,
    providers: list[str] | None = None,
) -> list[AccountState]:
    if providers is None:
        rows = db.execute(
            """
            SELECT provider FROM account_state WHERE user_id=? ORDER BY display_name COLLATE NOCASE
            """,
            (user_id,),
        ).fetchall()
        providers = [row["provider"] for row in rows]
    return [
        state
        for provider in providers
        if (state := load_account_state(db, user_id, provider)) is not None
    ]


def recompute_account_state(
    db: Any,
    user_id: str,
    provider: str,
    *,
    decrypt_fn: Callable[[str, str], dict] | None = None,
    display_names: dict[str, str] | None = None,
    category_map: dict[str, str] | None = None,
) -> AccountState:
    state = project_account_state(
        db,
        user_id,
        provider,
        decrypt_fn=_resolve_decrypt_fn(decrypt_fn),
        display_names=display_names,
        category_map=category_map,
    )
    persist_account_state(db, state)
    return state


def recompute_account_state_from_run(
    db: Any,
    run_id: str,
    *,
    decrypt_fn: Callable[[str, str], dict] | None = None,
    display_names: dict[str, str] | None = None,
    category_map: dict[str, str] | None = None,
) -> AccountState | None:
    row = db.execute(
        "SELECT user_id, source FROM pipeline_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if not row:
        return None
    return recompute_account_state(
        db,
        row["user_id"],
        row["source"],
        decrypt_fn=decrypt_fn,
        display_names=display_names,
        category_map=category_map,
    )


def safe_recompute_account_state(
    db: Any,
    user_id: str,
    provider: str,
) -> None:
    """Shadow-mode hook after sync save — never raise into request handlers."""
    try:
        recompute_account_state(db, user_id, provider)
    except Exception:
        pass


def safe_recompute_account_state_from_run(db: Any, run_id: str) -> None:
    """Shadow-mode hook: never raise into pipeline finalization."""
    try:
        recompute_account_state_from_run(db, run_id)
    except Exception:
        pass
