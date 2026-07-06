"""Delta Evidence Audit — compare captured sync evidence vs extraction for pipeline runs.

Diagnostic only: does not change extraction logic. Intended to explain why Delta may
capture extensive evidence but persist only one trusted observation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from mighty.pipeline_inspector import get_run, get_run_stages
from mighty.pipeline_stages import PipelineStageId, RunStatus, StageStatus

# ── Evidence block headers (extension + intercept + fixtures) ───────────────

_EVIDENCE_BLOCK_RE = re.compile(
    r"(?:^|\n)(===\s*(?:API RESPONSE|EMBEDDED STATE|NETWORK JSON|GRAPHQL|URL)"
    r"[^\n]*===|---\s*https?://[^\s]+\s*---)\n([\s\S]*?)(?=\n(?:===|---\s*https?://)|\Z)",
    re.MULTILINE,
)

_BLOCK_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api_response", re.compile(r"^===\s*API RESPONSE", re.I)),
    ("embedded_state", re.compile(r"^===\s*EMBEDDED STATE", re.I)),
    ("network_json", re.compile(r"^===\s*NETWORK JSON", re.I)),
    ("graphql", re.compile(r"^===\s*GRAPHQL", re.I)),
    ("page_url", re.compile(r"^===\s*URL:|^---\s*https?://", re.I)),
)

_JSON_BLOCK_TYPES = frozenset({"api_response", "embedded_state", "network_json", "graphql"})

# Delta connector paths (mirrors SITE_CONNECTORS["delta"] in app.py)
_DELTA_CONNECTOR_SPECS: tuple[dict[str, Any], ...] = (
    {
        "key": "elite_status",
        "label": "Medallion Status",
        "paths": (
            "data.member.medallionStatus",
            "member.medallionStatus",
            "loyalty.tier",
            "account.tier",
        ),
        "observations": ("medallion_status",),
    },
    {
        "key": "points_balance",
        "label": "SkyMiles Balance",
        "paths": (
            "data.member.skymiles",
            "data.member.miles",
            "data.member.smBalance",
            "member.skymiles",
            "member.miles",
            "smBalance",
            "loyalty.miles",
            "account.miles",
        ),
        "observations": ("skymiles_balance",),
    },
    {
        "key": "ecredit_balance",
        "label": "eCredit Balance",
        "paths": (
            "data.wallet.totalEcreditValue",
            "wallet.totalEcreditValue",
            "ecredits.totalValue",
        ),
        "observations": ("ecredits", "flight_credits"),
    },
)

# Important Delta observations for full-provider support
DELTA_OBSERVATIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "skymiles_balance",
        "label": "SkyMiles balance",
        "field_keys": ("points_balance", "miles_balance", "skymiles"),
        "text_patterns": (
            r"skymiles\s+balance",
            r"\b\d[\d,]*\s*miles\b",
            r"smbalance",
            r"\"skymiles\"",
            r"\"miles\"",
        ),
    },
    {
        "id": "medallion_status",
        "label": "Medallion status",
        "field_keys": ("elite_status", "medallion_status", "tier_status"),
        "text_patterns": (
            r"medallion\s+status",
            r"\b(diamond|platinum|gold|silver)\s+medallion\b",
            r"medallionmemberdesc",
            r"medallionstatus",
        ),
    },
    {
        "id": "mqds",
        "label": "MQDs",
        "field_keys": ("mqd", "mqds", "qualification_dollars"),
        "text_patterns": (
            r"\bmqds?\b",
            r"medallion\s+qualification\s+dollars?",
            r"\"mqd",
        ),
    },
    {
        "id": "mqm_mqs",
        "label": "MQM / MQS",
        "field_keys": ("mqm", "mqs", "qualification_miles"),
        "text_patterns": (
            r"\bmqms?\b",
            r"medallion\s+qualification\s+miles?",
            r"\"mqm",
        ),
    },
    {
        "id": "ecredits",
        "label": "eCredits",
        "field_keys": ("ecredit_balance", "ecredits", "travel_credits"),
        "text_patterns": (
            r"\becredits?\b",
            r"e-credit",
            r"totalecredit",
            r"wallet",
        ),
    },
    {
        "id": "companion_certificates",
        "label": "Companion certificates",
        "field_keys": ("certificates", "companion_cert", "companion_certificate"),
        "text_patterns": (
            r"companion\s+cert",
            r"companion\s+certificate",
            r"bring one companion",
        ),
    },
    {
        "id": "upcoming_trips",
        "label": "Upcoming trips",
        "field_keys": ("upcoming_trips", "next_trip", "reservations"),
        "text_patterns": (
            r"upcoming\s+trip",
            r"my\s+trips",
            r"next\s+trip",
            r"confirmation\s+#",
        ),
    },
    {
        "id": "flight_credits",
        "label": "Flight credits",
        "field_keys": ("travel_credits", "flight_credits", "ecredit_balance"),
        "text_patterns": (
            r"flight\s+credit",
            r"travel\s+fund",
            r"travel\s+credit",
            r"unused\s+ticket",
        ),
    },
    {
        "id": "expiration_dates",
        "label": "Expiration dates",
        "field_keys": ("expiry_date", "expiration_date", "certificates", "upgrades"),
        "text_patterns": (
            r"\bexpir",
            r"valid\s+through",
            r"valid\s+until",
            r"book\s+and\s+fly\s+by",
            r"qualification\s+year\s+ends",
        ),
    },
)

_FIELD_KEY_TO_OBS: dict[str, str] = {}
for _obs in DELTA_OBSERVATIONS:
    for _key in _obs["field_keys"]:
        _FIELD_KEY_TO_OBS[_key] = _obs["id"]


@dataclass(frozen=True)
class EvidenceBlock:
    block_type: str
    header: str
    body: str

    @property
    def char_count(self) -> int:
        return len(self.body)


@dataclass
class ExtractedFieldRow:
    key: str
    label: str
    value: str
    source: str
    trusted: bool


@dataclass
class ObservationComparison:
    observation_id: str
    label: str
    in_evidence: bool
    evidence_blocks: list[str]
    extracted: bool
    extracted_keys: list[str]
    trusted: bool
    recommended_extractor: str | None
    diagnosis: str


@dataclass
class DeltaRunEvidenceAudit:
    run_id: str
    source: str
    run_status: str
    created_at: str
    finished_at: str | None
    raw_text_chars: int
    raw_text_source: str
    api_response_blocks: list[EvidenceBlock] = field(default_factory=list)
    network_json_blocks: list[EvidenceBlock] = field(default_factory=list)
    graphql_blocks: list[EvidenceBlock] = field(default_factory=list)
    embedded_state_blocks: list[EvidenceBlock] = field(default_factory=list)
    page_blocks: list[EvidenceBlock] = field(default_factory=list)
    extracted_fields: list[ExtractedFieldRow] = field(default_factory=list)
    trusted_observations: list[str] = field(default_factory=list)
    connector_preview: list[dict[str, Any]] = field(default_factory=list)
    comparisons: list[ObservationComparison] = field(default_factory=list)
    stage_summary: dict[str, Any] = field(default_factory=dict)


def _block_type(header: str) -> str:
    for name, pattern in _BLOCK_TYPE_PATTERNS:
        if pattern.search(header.strip()):
            return name
    return "page_url"


def parse_evidence_blocks(raw_text: str) -> dict[str, list[EvidenceBlock]]:
    """Split raw_text into typed evidence blocks."""
    grouped: dict[str, list[EvidenceBlock]] = {
        "api_response": [],
        "network_json": [],
        "graphql": [],
        "embedded_state": [],
        "page_url": [],
    }
    if not raw_text:
        return grouped

    for match in _EVIDENCE_BLOCK_RE.finditer(raw_text):
        header = match.group(1).strip()
        body = match.group(2).strip()
        block_type = _block_type(header)
        grouped[block_type].append(EvidenceBlock(block_type=block_type, header=header, body=body))

    # Residual prose not captured by markers (leading entry page text)
    covered = sum(len(m.group(0)) for m in _EVIDENCE_BLOCK_RE.finditer(raw_text))
    if raw_text.strip() and covered < len(raw_text.strip()):
        remainder = raw_text.strip()
        if grouped["page_url"]:
            remainder = _EVIDENCE_BLOCK_RE.sub("", remainder).strip()
        if remainder and len(remainder) > 80:
            grouped["page_url"].insert(
                0,
                EvidenceBlock(
                    block_type="page_url",
                    header="[unmarked page text]",
                    body=remainder,
                ),
            )

    return grouped


def _resolve_json_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            cur = cur[idx] if idx < len(cur) else None
        else:
            return None
    return cur


def preview_connector_fields(json_text: str) -> list[dict[str, Any]]:
    """Show what SITE_CONNECTORS-style paths would extract from JSON evidence."""
    try:
        obj = json.loads(json_text)
    except (ValueError, TypeError):
        return []

    empty = frozenset({"", "0", "-", "–", "—", "null", "none", "unknown"})
    found: list[dict[str, Any]] = []
    for spec in _DELTA_CONNECTOR_SPECS:
        for path in spec["paths"]:
            val = _resolve_json_path(obj, path)
            if val is None:
                continue
            val_str = str(val).strip()
            if val_str.lower() in empty:
                continue
            found.append(
                {
                    "key": spec["key"],
                    "label": spec["label"],
                    "value": val_str,
                    "connector_path": path,
                }
            )
            break
    return found


def _observation_in_text(obs: dict[str, Any], text: str) -> bool:
    lower = text.lower()
    for pattern in obs["text_patterns"]:
        if re.search(pattern, lower, re.I):
            return True
    return False


def _observation_in_blocks(obs: dict[str, Any], blocks: dict[str, list[EvidenceBlock]]) -> list[str]:
    hits: list[str] = []
    for block_list in blocks.values():
        for block in block_list:
            if _observation_in_text(obs, block.header + "\n" + block.body):
                hits.append(block.header)
    return hits


def _recommended_extractor(
    obs_id: str,
    evidence_headers: list[str],
    blocks: dict[str, list[EvidenceBlock]],
) -> str | None:
    if not evidence_headers:
        return None

    block_types = {
        b.block_type
        for blist in blocks.values()
        for b in blist
        if b.header in evidence_headers
    }

    connector_obs = {
        obs
        for spec in _DELTA_CONNECTOR_SPECS
        for obs in spec.get("observations", ())
    }
    if block_types & _JSON_BLOCK_TYPES:
        if obs_id in connector_obs or obs_id in {"skymiles_balance", "medallion_status", "ecredits"}:
            return "connector (SITE_CONNECTORS delta paths)"
        return "intelligent (Gemini field discovery on JSON blocks)"

    if "page_url" in block_types or block_types:
        return "intelligent (Gemini field discovery on page text)"

    return "intelligent (Gemini field discovery)"


def _diagnosis(
    *,
    in_evidence: bool,
    extracted: bool,
    trusted: bool,
    recommended: str | None,
    stage_summary: dict[str, Any],
) -> str:
    if not in_evidence:
        return "Not present in captured raw_text for this run."
    if trusted:
        return "Present in evidence and persisted as a trusted observation."
    if extracted:
        return "Extracted but not trusted — check validation thresholds or enabled_fields."
    if stage_summary.get("structured_status") == StageStatus.FAILED.value:
        reason = stage_summary.get("structured_failure") or "connector_miss"
        return f"In evidence but connector stage failed ({reason})."
    if stage_summary.get("intelligent_status") == StageStatus.FAILED.value:
        reason = stage_summary.get("intelligent_failure") or "llm_empty"
        return f"In evidence but intelligent discovery failed ({reason})."
    if stage_summary.get("validation_status") == StageStatus.FAILED.value:
        reason = stage_summary.get("validation_failure") or "filtered"
        return f"In evidence but validation rejected fields ({reason})."
    if recommended:
        return f"In evidence but not extracted — {recommended} should surface this."
    return "In evidence but not extracted — investigate pipeline stage artifacts."


def _stage_artifacts(stages: list[dict[str, Any]], stage_name: str) -> dict[str, Any]:
    for stage in stages:
        if stage.get("stage") == stage_name:
            arts = stage.get("artifacts")
            if isinstance(arts, dict):
                return arts
            raw = stage.get("artifacts_json")
            if raw:
                try:
                    return json.loads(raw)
                except Exception:
                    pass
    return {}


def _stage_meta(stages: list[dict[str, Any]], stage_name: str) -> tuple[str | None, str | None, str | None]:
    for stage in stages:
        if stage.get("stage") == stage_name:
            return (
                stage.get("status"),
                stage.get("failure_reason"),
                stage.get("status"),
            )
    return None, None, None


def _collect_extracted_fields(
    stages: list[dict[str, Any]],
    discovered_fields: list[dict[str, Any]] | None,
    trusted_keys: set[str],
) -> list[ExtractedFieldRow]:
    rows: list[ExtractedFieldRow] = []
    seen: set[str] = set()

    structured = _stage_artifacts(stages, PipelineStageId.STRUCTURED.value)
    structured_keys = set(structured.get("field_keys") or [])
    source_label = structured.get("source_label") or "connector"

    if discovered_fields:
        for f in discovered_fields:
            key = str(f.get("key") or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            src = source_label if key in structured_keys or f.get("from_connector") else "intelligent"
            if f.get("from_connector"):
                src = "connector"
            rows.append(
                ExtractedFieldRow(
                    key=key,
                    label=str(f.get("label") or key),
                    value=str(f.get("value") or ""),
                    source=src,
                    trusted=key in trusted_keys,
                )
            )
        return rows

    for key in structured.get("field_keys") or []:
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            ExtractedFieldRow(
                key=key,
                label=key.replace("_", " ").title(),
                value="",
                source=source_label,
                trusted=key in trusted_keys,
            )
        )

    trusted_arts = _stage_artifacts(stages, PipelineStageId.TRUSTED_OBSERVATIONS.value)
    for key in trusted_arts.get("trusted_keys") or []:
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            ExtractedFieldRow(
                key=key,
                label=key.replace("_", " ").title(),
                value="",
                source="trusted",
                trusted=True,
            )
        )

    return rows


def _observation_extracted(
    obs_id: str,
    field_keys: set[str],
) -> tuple[bool, list[str]]:
    matched = [
        key
        for key in field_keys
        if _FIELD_KEY_TO_OBS.get(key) == obs_id or obs_id in key
    ]
    if matched:
        return True, matched

    obs = next(o for o in DELTA_OBSERVATIONS if o["id"] == obs_id)
    for key in field_keys:
        if key in obs["field_keys"]:
            matched.append(key)
    return bool(matched), matched


def build_delta_run_audit(
    *,
    run: dict[str, Any],
    stages: list[dict[str, Any]],
    raw_text: str,
    raw_text_source: str,
    discovered_fields: list[dict[str, Any]] | None = None,
) -> DeltaRunEvidenceAudit:
    blocks = parse_evidence_blocks(raw_text)
    trusted_arts = _stage_artifacts(stages, PipelineStageId.TRUSTED_OBSERVATIONS.value)
    trusted_keys = set(trusted_arts.get("trusted_keys") or [])

    extracted_rows = _collect_extracted_fields(stages, discovered_fields, trusted_keys)
    field_keys = {r.key for r in extracted_rows}

    structured_status, structured_failure, _ = _stage_meta(
        stages, PipelineStageId.STRUCTURED.value
    )
    intelligent_status, intelligent_failure, _ = _stage_meta(
        stages, PipelineStageId.INTELLIGENT.value
    )
    validation_status, validation_failure, _ = _stage_meta(
        stages, PipelineStageId.VALIDATION.value
    )

    stage_summary = {
        "structured_status": structured_status,
        "structured_failure": structured_failure,
        "intelligent_status": intelligent_status,
        "intelligent_failure": intelligent_failure,
        "validation_status": validation_status,
        "validation_failure": validation_failure,
        "trusted_count": len(trusted_keys),
    }

    connector_preview: list[dict[str, Any]] = []
    for block_type in _JSON_BLOCK_TYPES:
        for block in blocks.get(block_type, []):
            connector_preview.extend(
                {**row, "evidence_header": block.header[:120]}
                for row in preview_connector_fields(block.body)
            )

    comparisons: list[ObservationComparison] = []
    for obs in DELTA_OBSERVATIONS:
        evidence_hits = _observation_in_blocks(obs, blocks)
        extracted, matched_keys = _observation_extracted(obs["id"], field_keys)
        trusted = any(k in trusted_keys for k in matched_keys)
        recommended = _recommended_extractor(obs["id"], evidence_hits, blocks)
        comparisons.append(
            ObservationComparison(
                observation_id=obs["id"],
                label=obs["label"],
                in_evidence=bool(evidence_hits),
                evidence_blocks=evidence_hits[:5],
                extracted=extracted,
                extracted_keys=matched_keys,
                trusted=trusted,
                recommended_extractor=recommended if evidence_hits and not extracted else None,
                diagnosis=_diagnosis(
                    in_evidence=bool(evidence_hits),
                    extracted=extracted,
                    trusted=trusted,
                    recommended=recommended,
                    stage_summary=stage_summary,
                ),
            )
        )

    return DeltaRunEvidenceAudit(
        run_id=run["run_id"],
        source=run.get("source") or "delta",
        run_status=run.get("run_status") or "",
        created_at=run.get("created_at") or "",
        finished_at=run.get("finished_at"),
        raw_text_chars=len(raw_text or ""),
        raw_text_source=raw_text_source,
        api_response_blocks=blocks["api_response"],
        network_json_blocks=blocks["network_json"],
        graphql_blocks=blocks["graphql"],
        embedded_state_blocks=blocks["embedded_state"],
        page_blocks=blocks["page_url"],
        extracted_fields=extracted_rows,
        trusted_observations=sorted(trusted_keys),
        connector_preview=connector_preview,
        comparisons=comparisons,
        stage_summary=stage_summary,
    )


def list_successful_delta_runs(db: Any, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT run_id, created_at, finished_at, user_id, initiator, data_source,
               run_status, terminal_stage, terminal_reason
        FROM pipeline_runs
        WHERE source = 'delta' AND run_status = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (RunStatus.COMPLETE.value, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def load_raw_text_for_run(
    db: Any,
    run: dict[str, Any],
    *,
    decrypt_fn: Any | None = None,
) -> tuple[str, str]:
    """Return (raw_text, source_description) for auditing a pipeline run."""
    user_id = run.get("user_id")
    if not user_id:
        return "", "missing user_id"

    row = db.execute(
        "SELECT data_enc FROM account_data WHERE user_id=? AND source='delta'",
        (user_id,),
    ).fetchone()
    if not row or not row["data_enc"]:
        return "", "no account_data row"

    if decrypt_fn is None:
        return "", "decrypt_fn required"

    try:
        data = decrypt_fn(user_id, row["data_enc"])
    except Exception:
        return "", "decrypt failed"

    raw = data.get("raw_text") or ""
    return raw, "account_data.raw_text (current snapshot for run user)"


def load_discovered_fields_for_run(
    db: Any,
    run: dict[str, Any],
    *,
    decrypt_cred_fn: Any | None = None,
) -> list[dict[str, Any]]:
    user_id = run.get("user_id")
    if not user_id or not decrypt_cred_fn:
        return []

    row = db.execute(
        "SELECT extra_enc FROM account_credentials WHERE user_id=? AND source='delta'",
        (user_id,),
    ).fetchone()
    if not row or not row["extra_enc"]:
        return []

    try:
        extra = json.loads(decrypt_cred_fn(user_id, row["extra_enc"]))
    except Exception:
        return []

    fields = extra.get("discovered_fields") or []
    return [f for f in fields if isinstance(f, dict)]


def audit_delta_pipeline_run(
    db: Any,
    run_id: str,
    *,
    decrypt_account_fn: Any | None = None,
    decrypt_cred_fn: Any | None = None,
) -> DeltaRunEvidenceAudit | None:
    run = get_run(db, run_id)
    if not run or run.get("source") != "delta":
        return None

    stages = get_run_stages(db, run_id)
    raw_text, raw_source = load_raw_text_for_run(
        db, run, decrypt_fn=decrypt_account_fn
    )
    discovered = load_discovered_fields_for_run(
        db, run, decrypt_cred_fn=decrypt_cred_fn
    )

    return build_delta_run_audit(
        run=run,
        stages=stages,
        raw_text=raw_text,
        raw_text_source=raw_source,
        discovered_fields=discovered or None,
    )
