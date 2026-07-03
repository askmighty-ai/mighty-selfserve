"""
mighty.prompt_eval
──────────────────
Admin-side metrics and fixture evaluation for versioned prompts.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mighty.ai_metrics import estimate_cost_usd
from mighty.ai_platform import build_field_discovery_context
from mighty.ai_provider import (
    DiscoveryContext,
    DiscoveryError,
    DiscoveryValidationError,
    get_field_discovery_provider,
    validate_discovered_fields,
)
from mighty.prompts import get_prompt, list_prompts, render_prompt

_VALIDATION_FAILURE_HINTS = (
    "field at index",
    "invalid json",
    "provider json",
    "missing required",
    "invalid confidence",
    "confidence out of range",
    "must be an array",
)

_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
_EXPECTED_DIR = Path(__file__).resolve().parents[1] / "tests" / "expected"


@dataclass
class PromptVersionMetrics:
    prompt_id: str
    prompt_version: str
    description: str = ""
    request_count: int = 0
    avg_latency_ms: float | None = None
    total_cost_usd: float | None = None
    avg_cost_usd: float | None = None
    success_rate: float | None = None
    validation_failures: int = 0
    total_failures: int = 0
    extraction_completeness: float | None = None
    fixture_count: int = 0
    fixture_validation_failures: int = 0
    registered: bool = True

    @property
    def version_label(self) -> str:
        return f"{self.prompt_id}@{self.prompt_version}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FixtureEvalResult:
    fixture: str
    source: str
    prompt_id: str
    prompt_version: str
    completeness_pct: float
    validation_failed: bool
    validation_error: str | None = None
    field_count: int = 0
    issues: list[str] = field(default_factory=list)
    latency_ms: float | None = None
    estimated_cost_usd: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_validation_failure(reason: str | None) -> bool:
    if not reason:
        return False
    lowered = reason.lower()
    return any(hint in lowered for hint in _VALIDATION_FAILURE_HINTS)


def score_extraction_completeness(fields: list[dict[str, Any]], spec: dict[str, Any]) -> tuple[float, list[str]]:
    """Score extracted fields against an expected spec. Returns (0-100 pct, issues)."""
    issues: list[str] = []
    checks = 0

    if "min_fields" in spec:
        checks += 1
        if len(fields) < spec["min_fields"]:
            issues.append(f"Too few fields: got {len(fields)}, expected >={spec['min_fields']}")

    if "max_fields" in spec:
        checks += 1
        if len(fields) > spec["max_fields"]:
            issues.append(f"Too many fields: got {len(fields)}, expected <={spec['max_fields']}")

    all_values = " ".join(str(f.get("value", "")).lower() for f in fields)
    all_keys = {str(f.get("key", "")) for f in fields}

    for req_key in spec.get("required_keys", []):
        checks += 1
        if not any(req_key in k for k in all_keys) and not any(
            req_key in str(f.get("label", "")).lower() for f in fields
        ):
            issues.append(f"Missing required key containing: {req_key!r}")

    for field_hint, fragments in spec.get("required_value_fragments", {}).items():
        matching = [
            f for f in fields
            if field_hint in str(f.get("key", ""))
            or field_hint in str(f.get("label", "")).lower()
        ]
        for frag in fragments:
            checks += 1
            if not any(str(frag).lower() in str(f.get("value", "")).lower() for f in matching):
                issues.append(
                    f"Field {field_hint!r} missing fragment {frag!r}. "
                    f"Got: {[f.get('value') for f in matching]}"
                )

    for frag in spec.get("must_contain_values", []):
        checks += 1
        if str(frag).lower() not in all_values:
            issues.append(f"No field contains required fragment: {frag!r}")

    for forbidden in spec.get("forbidden_values", []):
        checks += 1
        hits = [f for f in fields if str(forbidden).lower() in str(f.get("value", "")).lower()]
        if hits:
            issues.append(f"Forbidden value {forbidden!r} in: {[f.get('value') for f in hits]}")

    if checks == 0:
        return (100.0 if not issues else 0.0), issues

    passed = checks - len(issues)
    return round(max(0.0, passed / checks * 100.0), 1), issues


def load_fixture_cases() -> list[tuple[str, str, dict[str, Any]]]:
    """Return (fixture_filename, fixture_text, expected_spec) for eval fixtures."""
    if not _EXPECTED_DIR.is_dir():
        return []

    cases: list[tuple[str, str, dict[str, Any]]] = []
    for expected_path in sorted(_EXPECTED_DIR.glob("*.json")):
        spec = json.loads(expected_path.read_text(encoding="utf-8"))
        stem = expected_path.stem
        fixture_path = _FIXTURES_DIR / f"{stem}.txt"
        if not fixture_path.is_file():
            fixture_path = _FIXTURES_DIR / f"{stem}.html"
        if not fixture_path.is_file():
            continue
        cases.append((fixture_path.name, fixture_path.read_text(encoding="utf-8"), spec))
    return cases


def _build_discovery_context(
    *,
    prompt_id: str,
    site_name: str,
    source: str,
    snippets: str,
    today: str,
    category_hint: str,
) -> DiscoveryContext:
    if prompt_id == "field_discovery":
        return build_field_discovery_context(
            site_name=site_name,
            source=source,
            snippets=snippets,
            today=today,
            category_hint=category_hint,
        )
    rendered = render_prompt(
        prompt_id,
        site=site_name,
        text=snippets,
        today=today,
        category_hint=category_hint,
    )
    return DiscoveryContext(
        site_name=site_name,
        source=source,
        prompt=rendered.text,
        prompt_id=rendered.prompt_id,
        prompt_version=rendered.version,
        today=today,
        category_hint=category_hint,
    )


def _prepare_fixture_snippets(raw_text: str) -> str:
    from app import _extract_candidate_snippets

    return _extract_candidate_snippets(raw_text)


def _post_filter_fields(fields: list[dict[str, Any]], *, source: str) -> list[dict[str, Any]]:
    from app import _post_filter_fields as post_filter

    return post_filter(fields, source=source)


def _category_hint(source: str) -> str:
    from app import _get_category_schema

    schema = _get_category_schema(source or "")
    if not schema:
        return ""
    return (
        f"\nThis is a {schema['name']}. "
        f"Prioritise these field types:\n  {schema['priority_fields']}\n"
    )


def _site_name(source: str) -> str:
    from app import SUPPORTED_SITES

    return next(
        (name for key, name, *_ in SUPPORTED_SITES if key == source),
        source.replace("_", " ").title(),
    )


def evaluate_prompt_on_fixture(
    *,
    prompt_id: str,
    fixture_name: str,
    fixture_text: str,
    spec: dict[str, Any],
    provider_name: str | None = None,
    today: str | None = None,
) -> FixtureEvalResult:
    """Run one prompt version against a fixture and score extraction completeness."""
    source = str(spec.get("source") or "unknown")
    site_name = _site_name(source)
    snippets = _prepare_fixture_snippets(fixture_text)
    category_hint = _category_hint(source)
    today_str = today or datetime.now(timezone.utc).strftime("%B %d, %Y")
    context = _build_discovery_context(
        prompt_id=prompt_id,
        site_name=site_name,
        source=source,
        snippets=snippets,
        today=today_str,
        category_hint=category_hint,
    )

    provider = get_field_discovery_provider(provider_name) if provider_name else get_field_discovery_provider()
    if not provider.is_configured():
        return FixtureEvalResult(
            fixture=fixture_name,
            source=source,
            prompt_id=context.prompt_id,
            prompt_version=context.prompt_version,
            completeness_pct=0.0,
            validation_failed=True,
            validation_error=provider.unavailable_message(),
        )

    started = time.perf_counter()
    validation_error: str | None = None
    fields: list[dict[str, Any]] = []
    validation_failed = False
    estimated_cost: float | None = None

    try:
        result = provider.discover_fields(source, snippets, context)
        fields = result.fields
        if result.metrics and result.metrics.estimated_cost_usd is not None:
            estimated_cost = result.metrics.estimated_cost_usd
        latency_ms = result.metrics.latency_ms if result.metrics else round((time.perf_counter() - started) * 1000, 1)
    except DiscoveryValidationError as exc:
        validation_failed = True
        validation_error = str(exc)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        if estimated_cost is None:
            estimated_cost = estimate_cost_usd(
                provider.provider_name,
                getattr(provider, "model", "") or "",
                input_chars=len(context.prompt),
                output_chars=0,
            )
        return FixtureEvalResult(
            fixture=fixture_name,
            source=source,
            prompt_id=context.prompt_id,
            prompt_version=context.prompt_version,
            completeness_pct=0.0,
            validation_failed=True,
            validation_error=validation_error,
            latency_ms=latency_ms,
            estimated_cost_usd=estimated_cost,
        )
    except DiscoveryError as exc:
        validation_failed = True
        validation_error = str(exc)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return FixtureEvalResult(
            fixture=fixture_name,
            source=source,
            prompt_id=context.prompt_id,
            prompt_version=context.prompt_version,
            completeness_pct=0.0,
            validation_failed=True,
            validation_error=validation_error,
            latency_ms=latency_ms,
        )

    filtered = _post_filter_fields(fields, source=source)
    completeness, issues = score_extraction_completeness(filtered, spec)
    if estimated_cost is None:
        estimated_cost = estimate_cost_usd(
            provider.provider_name,
            getattr(provider, "model", "") or "",
            input_chars=len(context.prompt),
            output_chars=len(json.dumps(filtered)),
        )

    return FixtureEvalResult(
        fixture=fixture_name,
        source=source,
        prompt_id=context.prompt_id,
        prompt_version=context.prompt_version,
        completeness_pct=completeness,
        validation_failed=validation_failed,
        validation_error=validation_error,
        field_count=len(filtered),
        issues=issues,
        latency_ms=latency_ms,
        estimated_cost_usd=estimated_cost,
    )


def run_fixture_evaluations(
    prompt_ids: list[str] | None = None,
    *,
    provider_name: str | None = None,
) -> dict[str, list[FixtureEvalResult]]:
    """Run all fixture cases for each prompt id. Requires configured AI provider."""
    ids = prompt_ids or _discovery_prompt_ids()
    cases = load_fixture_cases()
    results: dict[str, list[FixtureEvalResult]] = {}

    for prompt_id in ids:
        prompt_results: list[FixtureEvalResult] = []
        for fixture_name, fixture_text, spec in cases:
            prompt_results.append(
                evaluate_prompt_on_fixture(
                    prompt_id=prompt_id,
                    fixture_name=fixture_name,
                    fixture_text=fixture_text,
                    spec=spec,
                    provider_name=provider_name,
                )
            )
        results[prompt_id] = prompt_results
    return results


def _discovery_prompt_ids() -> list[str]:
    return [
        p.prompt_id
        for p in list_prompts()
        if p.prompt_id.startswith("field_discovery") and p.prompt_id != "field_discovery_missing_pages"
    ]


def aggregate_fixture_metrics(results: list[FixtureEvalResult]) -> tuple[float | None, int, int]:
    if not results:
        return None, 0, 0
    avg_completeness = round(sum(r.completeness_pct for r in results) / len(results), 1)
    validation_failures = sum(1 for r in results if r.validation_failed)
    return avg_completeness, len(results), validation_failures


def get_production_metrics(
    db: Any,
    *,
    days: int | None = 30,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Aggregate ai_request_log rows by prompt_id and prompt_version."""
    params: list[Any] = []
    where = "prompt_id IS NOT NULL AND prompt_version IS NOT NULL"
    if days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        where += " AND created_at >= ?"
        params.append(cutoff)

    rows = db.execute(
        f"""
        SELECT prompt_id, prompt_version,
               COUNT(*) AS request_count,
               AVG(latency_ms) AS avg_latency_ms,
               SUM(estimated_cost_usd) AS total_cost_usd,
               AVG(estimated_cost_usd) AS avg_cost_usd,
               SUM(CASE WHEN failure_reason IS NULL THEN 1 ELSE 0 END) AS successes,
               SUM(CASE WHEN failure_reason IS NOT NULL THEN 1 ELSE 0 END) AS total_failures
        FROM ai_request_log
        WHERE {where}
        GROUP BY prompt_id, prompt_version
        """,
        params,
    ).fetchall()

    metrics: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["prompt_id"], row["prompt_version"])
        request_count = int(row["request_count"] or 0)
        successes = int(row["successes"] or 0)
        metrics[key] = {
            "request_count": request_count,
            "avg_latency_ms": round(float(row["avg_latency_ms"] or 0), 1) if request_count else None,
            "total_cost_usd": round(float(row["total_cost_usd"] or 0), 8) if row["total_cost_usd"] is not None else None,
            "avg_cost_usd": round(float(row["avg_cost_usd"] or 0), 8) if row["avg_cost_usd"] is not None else None,
            "success_rate": round(successes / request_count * 100.0, 1) if request_count else None,
            "total_failures": int(row["total_failures"] or 0),
            "validation_failures": 0,
        }

    failure_rows = db.execute(
        f"""
        SELECT prompt_id, prompt_version, failure_reason
        FROM ai_request_log
        WHERE {where} AND failure_reason IS NOT NULL
        """,
        params,
    ).fetchall()
    for row in failure_rows:
        key = (row["prompt_id"], row["prompt_version"])
        if key not in metrics:
            continue
        if is_validation_failure(row["failure_reason"]):
            metrics[key]["validation_failures"] += 1

    return metrics


def collect_prompt_version_metrics(
    db: Any,
    *,
    days: int | None = 30,
    fixture_results: dict[str, list[FixtureEvalResult]] | None = None,
) -> list[PromptVersionMetrics]:
    """Merge registered prompts, production logs, and optional fixture eval results."""
    production = get_production_metrics(db, days=days)
    seen: set[tuple[str, str]] = set()
    rows: list[PromptVersionMetrics] = []

    for prompt in list_prompts():
        if not prompt.prompt_id.startswith("field_discovery"):
            continue
        if prompt.prompt_id == "field_discovery_missing_pages":
            continue
        key = (prompt.prompt_id, prompt.version)
        seen.add(key)
        prod = production.get(key, {})
        fixture_list = (fixture_results or {}).get(prompt.prompt_id, [])
        completeness, fixture_count, fixture_validation = aggregate_fixture_metrics(fixture_list)
        rows.append(
            PromptVersionMetrics(
                prompt_id=prompt.prompt_id,
                prompt_version=prompt.version,
                description=prompt.description,
                request_count=int(prod.get("request_count") or 0),
                avg_latency_ms=prod.get("avg_latency_ms"),
                total_cost_usd=prod.get("total_cost_usd"),
                avg_cost_usd=prod.get("avg_cost_usd"),
                success_rate=prod.get("success_rate"),
                validation_failures=int(prod.get("validation_failures") or 0),
                total_failures=int(prod.get("total_failures") or 0),
                extraction_completeness=completeness,
                fixture_count=fixture_count,
                fixture_validation_failures=fixture_validation,
                registered=True,
            )
        )

    for key, prod in production.items():
        if key in seen:
            continue
        prompt_id, prompt_version = key
        rows.append(
            PromptVersionMetrics(
                prompt_id=prompt_id,
                prompt_version=prompt_version,
                description="(historical — prompt file removed or renamed)",
                request_count=int(prod.get("request_count") or 0),
                avg_latency_ms=prod.get("avg_latency_ms"),
                total_cost_usd=prod.get("total_cost_usd"),
                avg_cost_usd=prod.get("avg_cost_usd"),
                success_rate=prod.get("success_rate"),
                validation_failures=int(prod.get("validation_failures") or 0),
                total_failures=int(prod.get("total_failures") or 0),
                registered=False,
            )
        )

    rows.sort(key=lambda r: (r.prompt_id, r.prompt_version))
    return rows


def compare_prompt_versions(
    db: Any,
    *,
    days: int | None = 30,
    fixture_results: dict[str, list[FixtureEvalResult]] | None = None,
) -> list[PromptVersionMetrics]:
    """Return side-by-side metrics for every known prompt version."""
    return collect_prompt_version_metrics(db, days=days, fixture_results=fixture_results)


def summarize_fixture_run(
    fixture_results: dict[str, list[FixtureEvalResult]],
) -> list[PromptVersionMetrics]:
    """Build metrics rows from fixture eval only (no production DB)."""
    rows: list[PromptVersionMetrics] = []
    for prompt_id, results in fixture_results.items():
        if not results:
            continue
        first = results[0]
        completeness, fixture_count, fixture_validation = aggregate_fixture_metrics(results)
        latencies = [r.latency_ms for r in results if r.latency_ms is not None]
        costs = [r.estimated_cost_usd for r in results if r.estimated_cost_usd is not None]
        successes = sum(1 for r in results if not r.validation_failed)
        try:
            description = get_prompt(prompt_id).description
        except Exception:
            description = ""
        rows.append(
            PromptVersionMetrics(
                prompt_id=first.prompt_id,
                prompt_version=first.prompt_version,
                description=description,
                request_count=len(results),
                avg_latency_ms=round(sum(latencies) / len(latencies), 1) if latencies else None,
                total_cost_usd=round(sum(costs), 8) if costs else None,
                avg_cost_usd=round(sum(costs) / len(costs), 8) if costs else None,
                success_rate=round(successes / len(results) * 100.0, 1),
                validation_failures=fixture_validation,
                total_failures=sum(1 for r in results if r.validation_failed),
                extraction_completeness=completeness,
                fixture_count=fixture_count,
                fixture_validation_failures=fixture_validation,
            )
        )
    rows.sort(key=lambda r: (r.prompt_id, r.prompt_version))
    return rows
