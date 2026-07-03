"""Admin AI Playground — dry-run field discovery with full pipeline visibility."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from mighty.ai_metrics import estimate_tokens
from mighty.ai_platform import build_field_discovery_context
from mighty.ai_provider import (
    DiscoveryContext,
    DiscoveryError,
    DiscoveryValidationError,
    GeminiProvider,
    OpenAIProvider,
    get_field_discovery_provider,
    validate_discovered_fields,
)
from mighty.field_discovery import field_discovery_max_chars, truncate_discovery_input
from mighty.field_discovery_preprocess import prepare_discovery_input
from mighty.prompts import render_prompt

PLAYGROUND_PREVIEW_CHARS = 8000
COMPARE_MODELS = ("gpt-5.4-mini", "gpt-5.5")
PROMPT_VARIANTS = (
    ("field_discovery", "1.0.0"),
    ("field_discovery_v2", "2.0.0"),
)


def _preview(text: str, *, limit: int = PLAYGROUND_PREVIEW_CHARS) -> dict[str, Any]:
    raw = text or ""
    return {
        "text": raw[:limit],
        "chars": len(raw),
        "truncated": len(raw) > limit,
    }


def _site_name(source: str) -> str:
    from app import SUPPORTED_SITES

    return next(
        (name for key, name, *_ in SUPPORTED_SITES if key == source),
        source.replace("_", " ").title(),
    )


def _category_hint(source: str) -> str:
    from app import _get_category_schema

    schema = _get_category_schema(source or "")
    if not schema:
        return ""
    return (
        f"\nThis is a {schema['name']}. "
        f"Prioritise these field types:\n  {schema['priority_fields']}\n"
    )


def _hint_phrases(db, source: str) -> list[str]:
    try:
        rows = db.execute(
            "SELECT trigger_phrase FROM extraction_hints WHERE site=? "
            "ORDER BY success_count DESC, confidence DESC LIMIT 50",
            (source,),
        ).fetchall()
        return [r["trigger_phrase"] for r in rows]
    except Exception:
        return []


def _load_account(uid: str, source: str, *, decrypt_account_data, decrypt_cred, get_db) -> dict[str, Any]:
    db = get_db()
    row = db.execute(
        "SELECT data_enc, synced_at FROM account_data WHERE user_id=? AND source=?",
        (uid, source),
    ).fetchone()
    if not row:
        return {"error": f"no account_data for source {source!r}"}

    blob = decrypt_account_data(uid, row["data_enc"] or "")
    raw_text = blob.get("raw_text") or ""
    cred = db.execute(
        "SELECT extra_enc FROM account_credentials WHERE user_id=? AND source=?",
        (uid, source),
    ).fetchone()
    discovered: list[dict] = []
    if cred and cred["extra_enc"]:
        try:
            discovered = json.loads(decrypt_cred(uid, cred["extra_enc"])).get(
                "discovered_fields", []
            )
        except Exception:
            pass

    items = blob.get("ai_items") or blob.get("items") or []
    hints = _hint_phrases(db, source)
    site_name = _site_name(source)
    return {
        "source": source,
        "site_name": site_name,
        "raw_text": raw_text,
        "synced_at": row["synced_at"],
        "stored_synced_items": items,
        "stored_discovered_fields": discovered,
        "stored_account_fields": {
            "sync_status": blob.get("sync_status"),
            "extraction_status": blob.get("extraction_status"),
            "connection_status": blob.get("connection_status"),
            "sync_source": blob.get("sync_source"),
        },
        "hint_phrases": hints,
    }


def _prepared_text(raw_text: str, hints: list[str], *, use_preprocess: bool) -> tuple[str, dict[str, Any]]:
    max_chars = field_discovery_max_chars()
    bounded = truncate_discovery_input(raw_text, max_chars)
    if use_preprocess:
        result = prepare_discovery_input(bounded, hint_phrases=hints, max_chars=max_chars)
        return result.text, asdict(result.stats)
    return bounded, {"mode": "preprocess_off", "bounded_chars": len(bounded)}


def _build_context(
    *,
    site_name: str,
    source: str,
    snippets: str,
    today: str,
    category_hint: str,
    prompt_id: str = "field_discovery",
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


def _provider_for(name: str, *, model: str | None = None):
    provider = get_field_discovery_provider(name)
    if model and isinstance(provider, OpenAIProvider):
        provider.model = model
    return provider


def _serialize_openai_response(response: Any) -> dict[str, Any]:
    if response is None:
        return {}
    if hasattr(response, "model_dump"):
        try:
            data = response.model_dump(exclude_none=True)
            return {
                "id": data.get("id"),
                "model": data.get("model"),
                "created": data.get("created"),
                "system_fingerprint": data.get("system_fingerprint"),
                "usage": data.get("usage"),
                "choices": [
                    {
                        "index": c.get("index"),
                        "finish_reason": c.get("finish_reason"),
                        "message": {
                            "role": (c.get("message") or {}).get("role"),
                            "content_preview": ((c.get("message") or {}).get("content") or "")[:500],
                        },
                    }
                    for c in (data.get("choices") or [])
                ],
            }
        except Exception:
            pass
    return {"repr": repr(response)[:2000]}


def _run_openai_debug(
    provider: OpenAIProvider,
    source: str | None,
    content: str,
    context: DiscoveryContext,
) -> dict[str, Any]:
    if not provider.is_configured():
        return {"error": provider.unavailable_message()}

    request_meta = {
        "model": provider.model,
        "messages": [{"role": "user", "content_preview": context.prompt[:500]}],
        "response_format": "json_schema",
        "temperature": 0,
    }
    started = time.perf_counter()
    try:
        from mighty.ai_provider import FIELD_DISCOVERY_JSON_SCHEMA
        from mighty.ai_retry import call_with_retry

        response = call_with_retry(
            lambda: provider._client.chat.completions.create(
                model=provider.model,
                messages=[{"role": "user", "content": context.prompt}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "discovered_fields",
                        "strict": True,
                        "schema": FIELD_DISCOVERY_JSON_SCHEMA,
                    },
                },
                temperature=0,
            ),
        )
    except Exception as exc:
        return {
            "error": str(exc),
            "openai_request": request_meta,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    raw_text = (response.choices[0].message.content or "").strip()
    parsed: Any = None
    parse_error: str | None = None
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        parse_error = str(exc)

    validation: dict[str, Any] = {"ok": False, "field_count": 0, "error": parse_error}
    fields: list[dict] = []
    filtered: list[dict] = []
    if parsed is not None:
        try:
            fields = validate_discovered_fields(parsed)
            validation = {"ok": True, "field_count": len(fields), "error": None}
            from app import _post_filter_fields

            filtered = _post_filter_fields(fields, source=source or "")
        except DiscoveryValidationError as exc:
            validation = {"ok": False, "field_count": 0, "error": str(exc)}

    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    token_estimate = estimate_tokens(len(context.prompt)) + estimate_tokens(len(raw_text))
    usage = getattr(response, "usage", None)
    if usage is not None:
        token_estimate = (
            getattr(usage, "total_tokens", None)
            or (
                (getattr(usage, "prompt_tokens", 0) or 0)
                + (getattr(usage, "completion_tokens", 0) or 0)
            )
            or token_estimate
        )

    return {
        "provider": provider.provider_name,
        "model": provider.model,
        "latency_ms": latency_ms,
        "token_estimate": token_estimate,
        "prompt_id": context.prompt_id,
        "prompt_version": context.prompt_version,
        "rendered_prompt_preview": _preview(context.prompt),
        "openai_request": request_meta,
        "openai_response": _serialize_openai_response(response),
        "raw_response_text": raw_text,
        "parsed_json": parsed,
        "validation": validation,
        "fields_before_filter": fields,
        "fields_after_filter": filtered,
    }


def _run_provider_extraction(
    provider_name: str,
    source: str,
    content: str,
    context: DiscoveryContext,
    *,
    model: str | None = None,
) -> dict[str, Any]:
    provider = _provider_for(provider_name, model=model)
    if isinstance(provider, OpenAIProvider):
        return _run_openai_debug(provider, source, content, context)

    if not provider.is_configured():
        return {"error": provider.unavailable_message(), "provider": provider_name}

    started = time.perf_counter()
    try:
        result = provider.discover_fields(source, content, context)
    except DiscoveryError as exc:
        return {
            "error": str(exc),
            "provider": provider_name,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    from app import _post_filter_fields

    filtered = _post_filter_fields(result.fields, source=source or "")
    metrics = result.metrics
    token_estimate = (
        metrics.estimated_token_count() if metrics else estimate_tokens(len(context.prompt))
    )
    return {
        "provider": result.provider,
        "model": result.model,
        "latency_ms": metrics.latency_ms if metrics else round((time.perf_counter() - started) * 1000, 1),
        "token_estimate": token_estimate,
        "prompt_id": context.prompt_id,
        "prompt_version": context.prompt_version,
        "rendered_prompt_preview": _preview(context.prompt),
        "validation": {"ok": True, "field_count": len(result.fields), "error": None},
        "fields_before_filter": result.fields,
        "fields_after_filter": filtered,
        "metrics": asdict(metrics) if metrics else None,
    }


def _snapshot_payload(account: dict[str, Any], *, provider_name: str) -> dict[str, Any]:
    if account.get("error"):
        return account

    raw_text = account["raw_text"]
    if not raw_text:
        return {"error": "raw_text is empty", "source": account["source"]}

    hints = account["hint_phrases"]
    prepared, stats = _prepared_text(raw_text, hints, use_preprocess=True)
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    category_hint = _category_hint(account["source"])
    context = _build_context(
        site_name=account["site_name"],
        source=account["source"],
        snippets=prepared,
        today=today,
        category_hint=category_hint,
    )
    provider = _provider_for(provider_name)
    model = getattr(provider, "model", "") or (
        provider.models[0] if getattr(provider, "models", None) else ""
    )

    return {
        "source": account["source"],
        "site_name": account["site_name"],
        "provider": provider_name,
        "synced_at": account["synced_at"],
        "raw_captured_html": _preview(raw_text),
        "preprocessed_html": _preview(prepared),
        "preprocess_stats": stats,
        "rendered_prompt": _preview(context.prompt),
        "prompt_id": context.prompt_id,
        "prompt_version": context.prompt_version,
        "model": model,
        "token_estimate": estimate_tokens(len(context.prompt)),
        "stored_synced_items": account["stored_synced_items"],
        "stored_discovered_fields": account["stored_discovered_fields"],
        "stored_account_fields": account["stored_account_fields"],
    }


def admin_ai_playground_snapshot(
    uid: str,
    source: str,
    *,
    provider_name: str,
    decrypt_account_data,
    decrypt_cred,
    get_db,
) -> dict[str, Any]:
    account = _load_account(
        uid, source, decrypt_account_data=decrypt_account_data, decrypt_cred=decrypt_cred, get_db=get_db
    )
    return _snapshot_payload(account, provider_name=provider_name)


def admin_ai_playground_run(
    uid: str,
    source: str,
    body: dict[str, Any],
    *,
    decrypt_account_data,
    decrypt_cred,
    get_db,
) -> dict[str, Any]:
    mode = (body.get("mode") or "extract").strip()
    provider_name = (body.get("provider") or "openai").strip().lower()
    account = _load_account(
        uid, source, decrypt_account_data=decrypt_account_data, decrypt_cred=decrypt_cred, get_db=get_db
    )
    if account.get("error"):
        return account
    if not account["raw_text"]:
        return {"error": "raw_text is empty", "source": source}

    hints = account["hint_phrases"]
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    category_hint = _category_hint(source)
    site_name = account["site_name"]

    if mode == "extract":
        prepared, stats = _prepared_text(account["raw_text"], hints, use_preprocess=True)
        context = _build_context(
            site_name=site_name,
            source=source,
            snippets=prepared,
            today=today,
            category_hint=category_hint,
        )
        result = _run_provider_extraction(provider_name, source, prepared, context)
        result["mode"] = mode
        result["preprocess_stats"] = stats
        result["raw_captured_html"] = _preview(account["raw_text"])
        result["preprocessed_html"] = _preview(prepared)
        result["stored_account_fields"] = account["stored_account_fields"]
        result["stored_discovered_fields"] = account["stored_discovered_fields"]
        return result

    if mode == "compare_preprocess":
        on_text, on_stats = _prepared_text(account["raw_text"], hints, use_preprocess=True)
        off_text, off_stats = _prepared_text(account["raw_text"], hints, use_preprocess=False)
        ctx_on = _build_context(
            site_name=site_name, source=source, snippets=on_text,
            today=today, category_hint=category_hint,
        )
        ctx_off = _build_context(
            site_name=site_name, source=source, snippets=off_text,
            today=today, category_hint=category_hint,
        )
        return {
            "mode": mode,
            "provider": provider_name,
            "compare": {
                "preprocess_on": {
                    "preprocess_stats": on_stats,
                    "preprocessed_html": _preview(on_text),
                    "extraction": _run_provider_extraction(provider_name, source, on_text, ctx_on),
                },
                "preprocess_off": {
                    "preprocess_stats": off_stats,
                    "preprocessed_html": _preview(off_text),
                    "extraction": _run_provider_extraction(provider_name, source, off_text, ctx_off),
                },
            },
        }

    if mode == "compare_prompts":
        prepared, stats = _prepared_text(account["raw_text"], hints, use_preprocess=True)
        runs: dict[str, Any] = {}
        for prompt_id, version in PROMPT_VARIANTS:
            ctx = _build_context(
                site_name=site_name,
                source=source,
                snippets=prepared,
                today=today,
                category_hint=category_hint,
                prompt_id=prompt_id,
            )
            label = f"{prompt_id}@{version}"
            runs[label] = {
                "prompt_id": ctx.prompt_id,
                "prompt_version": ctx.prompt_version,
                "rendered_prompt_preview": _preview(ctx.prompt),
                "extraction": _run_provider_extraction(provider_name, source, prepared, ctx),
            }
        return {"mode": mode, "provider": provider_name, "preprocess_stats": stats, "compare": runs}

    if mode == "compare_models":
        if provider_name != "openai":
            return {"error": "compare_models requires provider=openai"}
        prepared, stats = _prepared_text(account["raw_text"], hints, use_preprocess=True)
        context = _build_context(
            site_name=site_name,
            source=source,
            snippets=prepared,
            today=today,
            category_hint=category_hint,
        )
        runs: dict[str, Any] = {}
        for model in COMPARE_MODELS:
            runs[model] = _run_provider_extraction(
                "openai", source, prepared, context, model=model,
            )
        return {
            "mode": mode,
            "preprocess_stats": stats,
            "compare": runs,
        }

    return {"error": f"unknown mode {mode!r}"}
