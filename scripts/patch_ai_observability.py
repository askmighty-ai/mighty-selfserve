#!/usr/bin/env python3
"""Apply AI observability patches to app.py."""
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "app.py"
text = path.read_text()

replacements = [
(
"""        try:
            db.execute("ALTER TABLE users ADD COLUMN sync_started_at TEXT")
            db.commit()
        except Exception:
            pass

init_db()
print(f"[Mighty] POSTMARK_API_KEY={'set' if POSTMARK_API_KEY else 'NOT SET'}", flush=True)""",
"""        try:
            db.execute("ALTER TABLE users ADD COLUMN sync_started_at TEXT")
            db.commit()
        except Exception:
            pass
        try:
            db.execute(\"\"\"
                CREATE TABLE IF NOT EXISTS ai_request_log (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at          TEXT NOT NULL,
                    provider            TEXT NOT NULL,
                    model               TEXT NOT NULL,
                    cache_hit           INTEGER NOT NULL DEFAULT 0,
                    latency_ms          REAL NOT NULL DEFAULT 0,
                    prompt_chars        INTEGER NOT NULL DEFAULT 0,
                    completion_chars    INTEGER NOT NULL DEFAULT 0,
                    estimated_tokens    INTEGER NOT NULL DEFAULT 0,
                    estimated_cost_usd  REAL,
                    failure_reason      TEXT,
                    prompt_id           TEXT,
                    prompt_version      TEXT,
                    source              TEXT
                )
            \"\"\")
            db.execute("CREATE INDEX IF NOT EXISTS idx_airl_created ON ai_request_log(created_at)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_airl_provider ON ai_request_log(provider)")
            db.commit()
        except Exception:
            pass

init_db()

from mighty.ai_observability import configure_db, get_daily_stats

configure_db(get_db)

print(f"[Mighty] POSTMARK_API_KEY={'set' if POSTMARK_API_KEY else 'NOT SET'}", flush=True)""",
"init_db"),
(
"""from mighty.field_discovery import (
    DiscoveryError,
    assert_field_discovery_available,
    field_discovery_max_chars,
    get_field_schema_cache,
    is_field_discovery_enabled,
    schema_cache_key,
    truncate_discovery_input,
)""",
"""from mighty.field_discovery import (
    DiscoveryError,
    assert_field_discovery_available,
    field_discovery_max_chars,
    get_field_schema_cache,
    is_field_discovery_enabled,
    schema_cache_key,
    truncate_discovery_input,
)
from mighty.ai_metrics import build_metrics, cache_hit_metrics
from mighty.ai_observability import observe_request""",
"imports"),
(
"""    if cached_fields is not None:
        return cached_fields""",
"""    if cached_fields is not None:
        from mighty.ai_provider import ai_provider_name, get_field_discovery_provider

        provider = get_field_discovery_provider()
        model = getattr(provider, "model", "") or (
            provider.models[0] if getattr(provider, "models", None) else ""
        )
        cache_hit_metrics(
            provider=ai_provider_name(),
            model=model or "cached",
            prompt_id="field_discovery",
            prompt_version="1.0.0",
            source=source,
        )
        return cached_fields""",
"cache hit"),
]

for old, new, name in replacements:
    if old not in text:
        raise SystemExit(f"Missing block: {name}")
    text = text.replace(old, new, 1)

old_ctx = """        context = DiscoveryContext(
            site_name=site_name,
            source=source,
            prompt=prompt,
            today=_today_str,
            category_hint=category_hint,
        )
        try:
            result = discover_fields_with_provider(source, snippets, context)
        except DiscoveryError as err:
            cache.record_failure(cache_key, err)
            raise
        print(
            f"[Mighty] Field discovery via {result.provider} ({result.model}), "
            f"{len(result.fields)} raw fields",
            flush=True,
        )"""
new_ctx = """        context = DiscoveryContext(
            site_name=site_name,
            source=source,
            prompt=prompt,
            prompt_id="field_discovery",
            prompt_version="1.0.0",
            today=_today_str,
            category_hint=category_hint,
        )
        try:
            result = discover_fields_with_provider(source, snippets, context)
        except DiscoveryError as err:
            cache.record_failure(cache_key, err)
            raise
        metrics_suffix = ""
        if result.metrics:
            m = result.metrics
            metrics_suffix = (
                f", prompt={m.prompt_id}@{m.prompt_version}, "
                f"latency={m.latency_ms:.0f}ms, cache_hit={m.cache_hit}"
            )
            if m.estimated_cost_usd is not None:
                metrics_suffix += f", est_cost=${m.estimated_cost_usd:.6f}"
        print(
            f"[Mighty] Field discovery via {result.provider} ({result.model}), "
            f"{len(result.fields)} raw fields{metrics_suffix}",
            flush=True,
        )"""
if old_ctx not in text:
    raise SystemExit("Missing context block")
text = text.replace(old_ctx, new_ctx, 1)

old_gemini = """                    try:
                        _gc = _gemini_client()
                        if _gc:
                            page_resp = _gc.models.generate_content(
                                model="gemini-2.0-flash",
                                contents=[{"role": "user", "parts": [{"text": page_prompt + "\\n\\n" + (bounded_text[:2000] if bounded_text else "")}]}],
                                config={"temperature": 0.3, "max_output_tokens": 200}
                            )
                            page_text = page_resp.text.strip() if page_resp.text else ""
                            if page_text and source:
                                _store_suggested_paths(source, page_text)
                    except Exception:
                        pass"""
new_gemini = """                    page_input = page_prompt + "\\n\\n" + (bounded_text[:2000] if bounded_text else "")
                    import time as _time
                    _page_started = _time.perf_counter()
                    try:
                        _gc = _gemini_client()
                        if _gc:
                            page_resp = _gc.models.generate_content(
                                model="gemini-2.0-flash",
                                contents=[{"role": "user", "parts": [{"text": page_input}]}],
                                config={"temperature": 0.3, "max_output_tokens": 200}
                            )
                            page_text = page_resp.text.strip() if page_resp.text else ""
                            _page_latency = (_time.perf_counter() - _page_started) * 1000
                            observe_request(
                                build_metrics(
                                    provider="gemini",
                                    model="gemini-2.0-flash",
                                    latency_ms=_page_latency,
                                    cache_hit=False,
                                    prompt_id="field_discovery_missing_pages",
                                    prompt_version="1.0.0",
                                    input_text=page_input,
                                    output_text=page_text,
                                ),
                                source=source,
                            )
                            if page_text and source:
                                _store_suggested_paths(source, page_text)
                    except Exception as exc:
                        _page_latency = (_time.perf_counter() - _page_started) * 1000
                        observe_request(
                            build_metrics(
                                provider="gemini",
                                model="gemini-2.0-flash",
                                latency_ms=_page_latency,
                                cache_hit=False,
                                prompt_id="field_discovery_missing_pages",
                                prompt_version="1.0.0",
                                input_text=page_input,
                                output_text="",
                            ),
                            failure_reason=str(exc),
                            source=source,
                        )"""
if old_gemini not in text:
    raise SystemExit("Missing gemini block")
text = text.replace(old_gemini, new_gemini, 1)

idx = text.find("def call_claude_for_prompt(description, api_key, url):")
end = text.find("\ndef build_mcp_config", idx)
if idx < 0 or end < 0:
    raise SystemExit("call_claude markers missing")

new_fn = '''def call_claude_for_prompt(description, api_key, url):
    """Call Claude Haiku to generate a tailored checkpoint prompt from an agent description."""
    import time as _time

    model = "claude-haiku-4-5-20251001"
    system = (
        "You generate concise system prompt instructions for AI agents that tell them when to call "
        "the Mighty authorization API. Given a description of what an agent does, produce checkpoint "
        "instructions that list the specific action types requiring authorization.\\n\\n"
        "Return a JSON object with exactly two fields:\\n"
        "- \\"prompt\\": string — the complete checkpoint instructions, concise and specific to this agent\\n"
        "- \\"warning\\": string or null — null if the description was specific enough; a short plain-English "
        "message (1 sentence) if the description was too vague to generate useful checkpoints\\n\\n"
        "The prompt must include:\\n"
        "1. A specific list of action types derived from the agent's description\\n"
        "2. The exact API call format using the provided api_key and url\\n"
        "3. Brief instructions for polling and handling approved/denied/timeout responses\\n\\n"
        "Keep the prompt under 120 words. Return JSON only, no markdown fences."
    )
    user_msg = (
        f"Agent description: {description}\\n"
        f"API key: {api_key}\\n"
        f"Mighty URL: {url}\\n\\n"
        f"API endpoints:\\n"
        f"  Authorize: POST {url}/api/authorize\\n"
        f"    body: {{\\"api_key\\":\\"{api_key}\\",\\"action_type\\":\\"<type>\\",\\"label\\":\\"<desc>\\",\\"fields\\":[[\\"Key\\",\\"Val\\"]]}}\\n"
        f"  Poll status: GET {url}/api/status/<request_id>  →  approved | denied | pending | timeout\\n"
        f"  Record (no approval): POST {url}/api/record\\n"
        f"    body: {{\\"api_key\\":\\"{api_key}\\",\\"action_type\\":\\"<type>\\",\\"label\\":\\"<desc>\\",\\"outcome\\":\\"completed\\"}}"
    )
    body = json.dumps({
        "model": model,
        "max_tokens": 512,
        "system": system,
        "messages": [{"role": "user", "content": user_msg}]
    }).encode()
    input_text = system + "\\n" + user_msg
    started = _time.perf_counter()
    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01"
            }
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
        text_out = result["content"][0]["text"].strip()
        if text_out.startswith("```"):
            text_out = "\\n".join(text_out.split("\\n")[1:])
            if text_out.endswith("```"):
                text_out = text_out[:-3].strip()
        latency_ms = (_time.perf_counter() - started) * 1000
        observe_request(
            build_metrics(
                provider="anthropic",
                model=model,
                latency_ms=latency_ms,
                cache_hit=False,
                prompt_id="onboarding_checkpoint",
                prompt_version="1.0.0",
                input_text=input_text,
                output_text=text_out,
            ),
        )
        return json.loads(text_out)
    except Exception as exc:
        latency_ms = (_time.perf_counter() - started) * 1000
        observe_request(
            build_metrics(
                provider="anthropic",
                model=model,
                latency_ms=latency_ms,
                cache_hit=False,
                prompt_id="onboarding_checkpoint",
                prompt_version="1.0.0",
                input_text=input_text,
                output_text="",
            ),
            failure_reason=str(exc),
        )
        raise

'''
text = text[:idx] + new_fn + text[end:]

admin_marker = '    return jsonify({"sites": result, "total_sites": len(result)})\n\n\n@app.route("/api/admin/site-url-health"'
if admin_marker not in text:
    raise SystemExit("admin marker missing")

admin_block = '''    return jsonify({"sites": result, "total_sites": len(result)})


@app.route("/api/admin/ai-observability")
@require_login
def api_admin_ai_observability():
    uid = session.get("user_id")
    user = get_db().execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()
    if not _is_admin_user(user):
        return jsonify({"error": "admin only"}), 403
    return jsonify(get_daily_stats(get_db()))


@app.route("/admin/ai-observability")
@require_login
def admin_ai_observability():
    uid = session.get("user_id")
    user = get_db().execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()
    if not _is_admin_user(user):
        return redirect("/dashboard")

    stats = get_daily_stats(get_db())
    provider_rows = ""
    total_providers = sum(p["count"] for p in stats["provider_distribution"]) or 1
    for item in stats["provider_distribution"]:
        pct = round(item["count"] / total_providers * 100, 1)
        provider_rows += (
            f'<tr><td style="padding:10px 12px;font-size:13px;color:#111">{he(item["provider"])}</td>'
            f'<td style="padding:10px 12px;font-size:13px;color:#374151;text-align:right">{item["count"]}</td>'
            f'<td style="padding:10px 12px;font-size:13px;color:#374151;text-align:right">{pct}%</td></tr>'
        )
    if not provider_rows:
        provider_rows = (
            '<tr><td colspan="3" style="padding:16px;text-align:center;color:#9ca3af;font-size:13px">'
            'No AI requests logged today</td></tr>'
        )

    def _stat_card(label: str, value: str, sub: str = "") -> str:
        sub_html = (
            f'<div style="font-size:11px;color:#9ca3af;margin-top:4px">{he(sub)}</div>'
            if sub else ""
        )
        return (
            f'<div style="background:#fff;border-radius:10px;padding:16px;'
            f'box-shadow:0 1px 3px rgba(0,0,0,.08)">'
            f'<div style="font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;'
            f'letter-spacing:.04em">{he(label)}</div>'
            f'<div style="font-size:24px;font-weight:700;color:#111;margin-top:6px">{he(value)}</div>'
            f'{sub_html}</div>'
        )

    cards = (
        _stat_card("Requests today", str(stats["requests_today"]), "UTC day")
        + _stat_card("Cache hit rate", f'{stats["cache_hit_pct"]}%')
        + _stat_card("Avg latency", f'{stats["avg_latency_ms"]} ms')
        + _stat_card("Avg cost", f'${stats["avg_cost_usd"]:.6f}')
        + _stat_card("Failures", str(stats["failures"]))
    )

    return render_template_string("""<!DOCTYPE html><html><head><title>AI Observability — Mighty Admin</title>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>body{font-family:-apple-system,sans-serif;margin:0;background:#f9fafb}
    .container{max-width:960px;margin:0 auto;padding:24px}</style></head>
    <body>
    <div class="container">
    <h2 style="font-size:20px;font-weight:700;color:#111;margin:0 0 4px">AI Observability</h2>
    <p style="font-size:13px;color:#6b7280;margin:0 0 20px">Production AI request metrics for today (UTC). Structured logs emit as JSON to stdout on every call.</p>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:24px">"""
    + cards + """</div>
    <h3 style="font-size:14px;font-weight:600;color:#374151;margin:0 0 10px">Provider distribution</h3>
    <table style="width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)">
    <thead><tr style="background:#f3f4f6">
    <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;font-weight:600">Provider</th>
    <th style="padding:10px 12px;text-align:right;font-size:12px;color:#6b7280;font-weight:600">Requests</th>
    <th style="padding:10px 12px;text-align:right;font-size:12px;color:#6b7280;font-weight:600">Share</th>
    </tr></thead><tbody>""" + provider_rows + """</tbody></table>
    <p style="font-size:12px;color:#9ca3af;margin-top:16px">JSON API: <code>/api/admin/ai-observability</code></p>
    </div></body></html>""")


@app.route("/api/admin/site-url-health"'''
text = text.replace(admin_marker, admin_block, 1)

path.write_text(text)
print("patched", path)
