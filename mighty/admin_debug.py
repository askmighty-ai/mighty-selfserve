"""Internal admin-only debugging pages (HTML renderers)."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any

from flask import render_template_string

from mighty.prompt_eval import PromptVersionMetrics

ADMIN_TOOLS: list[tuple[str, str, str]] = [
    (
        "prompt-eval",
        "Prompt eval",
        "Compare prompt versions — latency, cost, success rate, and extraction completeness",
    ),
    ("account-json", "Account JSON", "Decrypted account_data blobs per source"),
    ("extracted-fields", "Extracted fields", "Synced items, provenance, and classification"),
    ("provider-schemas", "Provider schemas", "Category schemas, connectors, and extraction hints"),
    ("discovery-cache", "Discovery cache", "In-process field schema cache entries"),
    ("ai-cache", "AI cache", "Provider config and recent discovery calls"),
    ("sync-history", "Sync history", "Field changes, audit events, and sync metadata"),
    ("sync-timeline", "Sync timeline", "Live sync state and per-account sync timestamps"),
    ("replay-discovery", "Replay field discovery", "Run discovery synchronously with step-by-step output"),
    (
        "ai-playground",
        "AI Playground",
        "Inspect extraction pipeline, compare preprocessing, prompts, and models",
    ),
]


def _he(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def _json_block(data: Any) -> str:
    text = json.dumps(data, indent=2, sort_keys=True, default=str)
    return f'<pre class="json-block">{_he(text)}</pre>'


def _source_picker(sources: list[str], current: str | None, base_path: str, *, label: str = "Account") -> str:
    if not sources:
        return '<p class="muted">No connected accounts yet.</p>'
    options = "".join(
        f'<option value="{_he(src)}"{" selected" if src == current else ""}>{_he(src)}</option>'
        for src in sources
    )
    return f"""
<form class="source-picker" method="get" action="{_he(base_path)}">
  <label>{_he(label)}</label>
  <select name="source" onchange="this.form.submit()">{options}</select>
</form>
<p class="muted">Showing <strong>{_he(current or sources[0])}</strong></p>
"""


def _admin_shell(active: str, title: str, body_html: str) -> str:
    nav_items = ""
    for slug, nav_label, _desc in ADMIN_TOOLS:
        cls = "admin-nav-link active" if slug == active else "admin-nav-link"
        nav_items += f'<a class="{cls}" href="/admin/{slug}">{_he(nav_label)}</a>'

    return render_template_string(
        """<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }} — Admin Debug</title>
<style>
body{font-family:-apple-system,sans-serif;margin:0;background:#0f1117;color:#e5e7eb}
.admin-layout{display:flex;min-height:100vh}
.admin-sidebar{width:240px;background:#111827;border-right:1px solid #1f2937;padding:16px 12px}
.admin-sidebar h1{font-size:14px;margin:0 0 4px;color:#f9fafb}
.admin-sidebar p{font-size:11px;color:#6b7280;margin:0 0 16px}
.admin-nav{display:flex;flex-direction:column;gap:4px}
.admin-nav-link{display:block;padding:8px 10px;border-radius:8px;color:#9ca3af;text-decoration:none;font-size:12px}
.admin-nav-link:hover,.admin-nav-link.active{background:#1f2937;color:#f3f4f6}
.admin-main{flex:1;padding:24px}
.admin-main h2{font-size:20px;margin:0 0 6px;color:#f9fafb}
.lede{font-size:13px;color:#9ca3af;margin:0 0 20px}
.card{background:#111827;border:1px solid #1f2937;border-radius:10px;padding:16px;margin-bottom:16px}
.card h3{font-size:13px;color:#d1d5db;margin:0 0 10px}
.muted{font-size:12px;color:#6b7280}
.json-block{background:#0b0d12;border:1px solid #1f2937;border-radius:8px;padding:12px;overflow:auto;font-size:11px;max-height:70vh;white-space:pre-wrap}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{padding:8px 10px;border-bottom:1px solid #1f2937;text-align:left}
th{color:#9ca3af;background:#0b0d12}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:10px;font-weight:700}
.badge-ok{background:#064e3b;color:#6ee7b7}.badge-warn{background:#78350f;color:#fcd34d}.badge-err{background:#7f1d1d;color:#fca5a5}
.btn{padding:8px 12px;border-radius:8px;border:1px solid #374151;background:#1f2937;color:#f3f4f6;font-size:12px;cursor:pointer;text-decoration:none}
.grid-2{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.stat{background:#0b0d12;border:1px solid #1f2937;border-radius:8px;padding:12px}
.stat .num{font-size:22px;font-weight:700;color:#f9fafb}
.source-picker{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.source-picker select{background:#0f1117;color:#e5e7eb;border:1px solid #374151;border-radius:6px;padding:6px 8px}
</style></head><body><div class="admin-layout">
<aside class="admin-sidebar"><h1>Admin Debug</h1>
<p>Internal operator tools. Not linked from customer UI.</p>
<nav class="admin-nav">{{ nav_items|safe }}</nav>
<a href="/dashboard" style="display:block;margin-top:20px;color:#818cf8;font-size:12px">&larr; Dashboard</a>
</aside><main class="admin-main"><h2>{{ title }}</h2>{{ body_html|safe }}</main></div></body></html>""",
        title=title,
        nav_items=nav_items,
        body_html=body_html,
    )


def render_admin_index() -> str:
    cards = "".join(
        f'<a class="card btn" style="display:block" href="/admin/{slug}">'
        f'<div style="font-weight:600;margin-bottom:4px">{_he(label)}</div>'
        f'<div class="muted">{_he(desc)}</div></a>'
        for slug, label, desc in ADMIN_TOOLS
    )
    return _admin_shell("index", "Admin Debug Tools", f'<p class="lede">All pages require ADMIN_EMAIL.</p><div class="grid-2">{cards}</div>')


def render_account_json_page(sources, source, account_json, *, synced_at=None):
    picker = _source_picker(sources, source, "/admin/account-json")
    if not account_json:
        return _admin_shell("account-json", "Account JSON", picker + '<p class="muted">No account selected.</p>')
    display = dict(account_json)
    raw = display.get("raw_text") or ""
    if raw:
        display["raw_text"] = f"[{len(raw)} chars] {raw[:500]}{'…' if len(raw) > 500 else ''}"
    body = picker + f'<p class="muted">synced_at: {_he(synced_at or "—")}</p>' + _json_block(display)
    return _admin_shell("account-json", "Account JSON", body)


def render_extracted_fields_page(sources, source, synced_items, discovered_fields, *, synced_at=None):
    picker = _source_picker(sources, source, "/admin/extracted-fields")
    rows = "".join(
        f"<tr><td>{_he(i.get('key',''))}</td><td>{_he(i.get('label',''))}</td><td>{_he(i.get('value',''))}</td>"
        f"<td>{_he(i.get('_type') or i.get('type',''))}</td><td>{i.get('confidence','—')}</td></tr>"
        for i in synced_items
    ) or '<tr><td colspan="5" class="muted">No synced items</td></tr>'
    prov = "".join(
        f"<tr><td>{_he(f.get('key',''))}</td><td>{_he(f.get('label',''))}</td><td>{_he(f.get('value',''))}</td>"
        f"<td>{f.get('confidence','—')}</td><td class='muted'>{_he((f.get('source_snippet') or '')[:120])}</td></tr>"
        for f in discovered_fields
    ) or '<tr><td colspan="5" class="muted">No discovered_fields</td></tr>'
    body = picker + f'<div class="card"><h3>Synced items</h3><table><tbody>{rows}</tbody></table></div>'
    body += f'<div class="card"><h3>Discovered fields</h3><table><tbody>{prov}</tbody></table></div>'
    return _admin_shell("extracted-fields", "Extracted Fields", body)


def render_provider_schemas_page(*, category_schemas, expected_fields, site_connectors, extraction_hints, ai_provider_info):
    body = '<p class="lede">Static schemas plus SQLite extraction hints.</p>'
    body += '<div class="card"><h3>AI provider</h3>' + _json_block(ai_provider_info) + "</div>"
    body += '<div class="card"><h3>Category schemas</h3>' + _json_block(category_schemas) + "</div>"
    body += '<div class="card"><h3>Expected fields</h3>' + _json_block(expected_fields) + "</div>"
    body += '<div class="card"><h3>Site connectors</h3>' + _json_block(site_connectors) + "</div>"
    body += '<div class="card"><h3>Extraction hints</h3>' + _json_block(extraction_hints[:100]) + "</div>"
    return _admin_shell("provider-schemas", "Provider Schemas", body)


def _fmt_ts(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if ts else "—"


def render_discovery_cache_page(entries, *, ttl_success, ttl_failure):
    rows = "".join(
        f"<tr><td>{_he(e.get('source',''))}</td><td>{'hit' if e.get('success') else 'fail'}</td>"
        f"<td>{e.get('field_count',0)}</td><td>{e.get('age_seconds',0)}s</td><td>{e.get('expires_in_seconds',0)}s</td>"
        f"<td class='muted'>{_he(e.get('key',''))}</td></tr>" for e in entries
    ) or '<tr><td colspan="6" class="muted">Cache empty (in-process; cleared on restart)</td></tr>'
    body = f'<p class="lede">Success TTL {ttl_success}s · failure TTL {ttl_failure}s.</p>'
    body += f'<div class="card"><table><tbody>{rows}</tbody></table></div>'
    return _admin_shell("discovery-cache", "Discovery Cache", body)


def render_ai_cache_page(*, provider_info, env_settings, call_log):
    rows = "".join(
        f"<tr><td>{_fmt_ts(e.get('timestamp'))}</td><td>{_he(e.get('source',''))}</td>"
        f"<td>{'cache hit' if e.get('cache_hit') else 'call'}</td><td>{_he(e.get('provider',''))}</td>"
        f"<td>{_he(e.get('model',''))}</td><td>{e.get('field_count',0)}</td>"
        f"<td>{e.get('latency_ms','—')}</td><td>{_he(e.get('error') or '')}</td></tr>"
        for e in reversed(call_log)
    ) or '<tr><td colspan="8" class="muted">No calls logged yet</td></tr>'
    body = '<div class="card"><h3>Provider</h3>' + _json_block(provider_info) + "</div>"
    body += '<div class="card"><h3>Environment</h3>' + _json_block(env_settings) + "</div>"
    body += f'<div class="card"><h3>Recent calls</h3><table><tbody>{rows}</tbody></table></div>'
    return _admin_shell("ai-cache", "AI Cache", body)


def _fmt_iso(v):
    return _he(v[:19].replace("T", " ")) if v else "—"


def render_sync_history_page(sources, source, field_history, audit_events, account_meta):
    picker = _source_picker(sources, source, "/admin/sync-history", label="Filter source")
    fh = "".join(f"<tr><td>{_fmt_iso(r.get('changed_at'))}</td><td>{_he(r.get('source',''))}</td>"
                 f"<td>{_he(r.get('field_label',''))}</td><td>{_he(r.get('old_value',''))}</td>"
                 f"<td>{_he(r.get('new_value',''))}</td></tr>" for r in field_history) or '<tr><td colspan="5" class="muted">No field history</td></tr>'
    audit = "".join(f"<tr><td>{_fmt_iso(r.get('created_at'))}</td><td>{_he(r.get('event_type',''))}</td>"
                    f"<td>{_he(r.get('source',''))}</td><td>{_he(r.get('detail',''))}</td></tr>" for r in audit_events) or '<tr><td colspan="4" class="muted">No audit events</td></tr>'
    meta = "".join(f"<tr><td>{_he(r.get('source',''))}</td><td>{_fmt_iso(r.get('synced_at'))}</td>"
                   f"<td>{_he(r.get('sync_status',''))}</td><td>{_he(r.get('sync_failure_reason') or '')}</td></tr>"
                   for r in account_meta) or '<tr><td colspan="4" class="muted">No sync metadata</td></tr>'
    body = picker
    body += f'<div class="card"><h3>Field history</h3><table><tbody>{fh}</tbody></table></div>'
    body += f'<div class="card"><h3>Privacy audit log</h3><table><tbody>{audit}</tbody></table></div>'
    body += f'<div class="card"><h3>Account sync metadata</h3><table><tbody>{meta}</tbody></table></div>'
    return _admin_shell("sync-history", "Sync History", body)


def render_sync_timeline_page(*, live_status, user_flags, account_timeline):
    rows = "".join(
        f"<tr><td>{_he(r.get('source',''))}</td><td>{_fmt_iso(r.get('synced_at'))}</td>"
        f"<td>{_he(r.get('sync_status',''))}</td><td>{_he(r.get('connection_status',''))}</td>"
        f"<td>{_he(r.get('extraction_status',''))}</td><td>{_he(r.get('sync_failure_reason') or '')}</td></tr>"
        for r in sorted(account_timeline, key=lambda x: x.get('synced_at') or '', reverse=True)
    ) or '<tr><td colspan="6" class="muted">No accounts</td></tr>'
    body = '<div class="card"><h3>Live _sync_status</h3>' + _json_block(live_status) + "</div>"
    body += '<div class="card"><h3>User sync flags</h3>' + _json_block(user_flags) + "</div>"
    body += f'<div class="card"><h3>Account timeline</h3><table><tbody>{rows}</tbody></table></div>'
    return _admin_shell("sync-timeline", "Sync Timeline", body)


def render_replay_discovery_page(sources, source, *, csrf_token: str):
    picker = _source_picker(sources, source, "/admin/replay-discovery")
    btn = f'<button class="btn" id="run-replay" data-source="{_he(source)}">Run replay</button>' if source else ""
    script = """
<script>
document.getElementById('run-replay')?.addEventListener('click', async (ev) => {
  const btn = ev.currentTarget, src = btn.dataset.source;
  btn.disabled = true; btn.textContent = 'Running…';
  try {
    const r = await fetch('/api/admin/debug/replay-discovery/' + encodeURIComponent(src));
    const data = await r.json();
    let card = document.getElementById('replay-output');
    if (!card) { card = document.createElement('div'); card.className='card'; card.id='replay-output';
      card.innerHTML='<h3>Replay output</h3>'; btn.parentElement.after(card); }
    const pre = document.createElement('pre'); pre.className='json-block';
    pre.textContent = JSON.stringify(data, null, 2);
    card.querySelector('pre')?.remove(); card.appendChild(pre);
  } finally { btn.disabled=false; btn.textContent='Run replay'; }
});
</script>"""
    return _admin_shell("replay-discovery", "Replay Field Discovery", picker + btn + script)


def render_ai_playground_page(
    sources: list[str],
    source: str | None,
    *,
    provider_info: dict[str, Any],
) -> str:
    picker = _source_picker(sources, source, "/admin/ai-playground")
    openai_cfg = provider_info.get("openai") or {}
    provider_opts = (
        f'<option value="openai"{" selected" if provider_info.get("configured_provider") == "openai" else ""}>'
        f'OpenAI ({_he(openai_cfg.get("model") or "—")})</option>'
        f'<option value="gemini"{" selected" if provider_info.get("configured_provider") == "gemini" else ""}>'
        f'Gemini</option>'
    )
    controls = f"""
<div class="card">
  <h3>Controls</h3>
  <form class="source-picker" id="provider-form">
    <label>Provider</label>
    <select id="playground-provider" name="provider">{provider_opts}</select>
  </form>
  <div class="btn-row">
    <button type="button" class="btn" id="btn-load" data-source="{_he(source or '')}" disabled>Load snapshot</button>
    <button type="button" class="btn" id="btn-extract" data-mode="extract" disabled>Run extraction again</button>
    <button type="button" class="btn" id="btn-preprocess" data-mode="compare_preprocess" disabled>Compare preprocessing on/off</button>
    <button type="button" class="btn" id="btn-prompts" data-mode="compare_prompts" disabled>Compare prompt versions</button>
    <button type="button" class="btn" id="btn-models" data-mode="compare_models" disabled>Compare GPT-5.4-mini vs GPT-5.5</button>
  </div>
  <p class="muted">Dry-run only — does not save discovered fields. OpenAI calls require API keys on this server.</p>
</div>
<div class="card" id="playground-output"><h3>Output</h3><p class="muted">Select an account to load the pipeline snapshot.</p></div>
<style>
.btn-row{{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}}
.btn-row .btn{{margin:0}}
</style>
<script>
(function() {{
  const srcSelect = document.querySelector('.source-picker select[name="source"]');
  const providerSel = document.getElementById('playground-provider');
  const out = document.getElementById('playground-output');
  const btns = document.querySelectorAll('#btn-load,#btn-extract,#btn-preprocess,#btn-prompts,#btn-models');

  function currentSource() {{
    return srcSelect?.value || document.getElementById('btn-load')?.dataset.source || '';
  }}

  function setBusy(busy) {{
    btns.forEach(b => {{ b.disabled = busy || !currentSource(); }});
  }}

  function show(data) {{
    out.innerHTML = '<h3>Output</h3>';
    const pre = document.createElement('pre');
    pre.className = 'json-block';
    pre.textContent = JSON.stringify(data, null, 2);
    out.appendChild(pre);
  }}

  async function callApi(mode) {{
    const src = currentSource();
    if (!src) return;
    setBusy(true);
    try {{
      const r = await fetch('/api/admin/debug/ai-playground/' + encodeURIComponent(src), {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ mode, provider: providerSel.value }}),
      }});
      show(await r.json());
    }} catch (e) {{
      show({{ error: String(e) }});
    }} finally {{
      setBusy(false);
    }}
  }}

  async function loadSnapshot() {{
    const src = currentSource();
    if (!src) return;
    setBusy(true);
    try {{
      const q = new URLSearchParams({{ provider: providerSel.value }});
      const r = await fetch('/api/admin/debug/ai-playground/' + encodeURIComponent(src) + '?' + q);
      show(await r.json());
    }} catch (e) {{
      show({{ error: String(e) }});
    }} finally {{
      setBusy(false);
    }}
  }}

  btns.forEach(b => {{
    if (b.id === 'btn-load') b.addEventListener('click', loadSnapshot);
    else b.addEventListener('click', () => callApi(b.dataset.mode));
  }});
  srcSelect?.addEventListener('change', () => {{
    btns.forEach(b => {{ b.disabled = !currentSource(); }});
    document.getElementById('btn-load').dataset.source = currentSource();
  }});
  if (currentSource()) {{
    btns.forEach(b => {{ b.disabled = false; }});
    loadSnapshot();
  }}
}})();
</script>"""
    body = (
        '<p class="lede">Inspect the full field-discovery pipeline for a connected account. '
        'Compare preprocessing, prompt versions, and models without writing to production storage.</p>'
        + picker
        + controls
    )
    return _admin_shell("ai-playground", "AI Playground", body)


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}%"


def _fmt_ms(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f} ms"


def _fmt_cost(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value:.6f}"


def _fmt_completeness(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}%"


def _metric_badge(value: float | None, *, good_at: float = 90.0, warn_at: float = 70.0) -> str:
    if value is None:
        return '<span class="badge badge-muted">—</span>'
    if value >= good_at:
        cls = "badge-ok"
    elif value >= warn_at:
        cls = "badge-warn"
    else:
        cls = "badge-err"
    return f'<span class="badge {cls}">{_he(value)}</span>'


def render_prompt_eval_page(
    metrics: list[PromptVersionMetrics],
    *,
    days: int,
    fixture_ran: bool,
) -> str:
    if not metrics:
        body = (
            '<p class="lede">Side-by-side metrics for each field-discovery prompt version.</p>'
            '<div class="card"><p class="muted">No prompt versions found. Add prompts under '
            '<code>prompts/</code> or run field discovery to populate <code>ai_request_log</code>.</p></div>'
        )
        return _admin_shell("prompt-eval", "Prompt Evaluation", body)

    header = (
        f'<p class="lede">Compare field-discovery prompt versions side-by-side. '
        f'Production metrics cover the last <strong>{days}</strong> days from '
        f'<code>ai_request_log</code>.</p>'
    )
    if fixture_ran:
        header += (
            '<p class="muted">Fixture completeness reflects the latest live eval run against '
            '<code>tests/fixtures</code> + <code>tests/expected</code>.</p>'
        )
    else:
        header += (
            '<p class="muted">Run fixture eval to score extraction completeness against golden fixtures '
            '(requires configured AI provider).</p>'
        )

    rows = ""
    for m in metrics:
        rows += (
            f"<tr>"
            f"<td><strong>{_he(m.version_label)}</strong>"
            f"<div class='muted' style='font-size:11px;margin-top:4px'>{_he(m.description or '')}</div></td>"
            f"<td>{m.request_count}</td>"
            f"<td>{_he(_fmt_ms(m.avg_latency_ms))}</td>"
            f"<td>{_he(_fmt_cost(m.total_cost_usd))}<div class='muted' style='font-size:11px'>"
            f"avg {_he(_fmt_cost(m.avg_cost_usd))}</div></td>"
            f"<td>{_metric_badge(m.success_rate)} {_he(_fmt_pct(m.success_rate))}</td>"
            f"<td>{m.validation_failures}</td>"
            f"<td>{_metric_badge(m.extraction_completeness)} {_he(_fmt_completeness(m.extraction_completeness))}"
            f"<div class='muted' style='font-size:11px'>{m.fixture_count} fixture(s)</div></td>"
            f"</tr>"
        )

    controls = """
<div class="card">
  <h3>Actions</h3>
  <div class="btn-row">
    <button type="button" class="btn" id="btn-run-fixtures">Run fixture eval</button>
    <button type="button" class="btn" id="btn-refresh">Refresh production metrics</button>
  </div>
  <p class="muted">Fixture eval calls the configured provider once per fixture × prompt version.</p>
</div>
<div class="card" id="prompt-eval-output"></div>
<style>
.btn-row{display:flex;flex-wrap:wrap;gap:8px}
.compare-table td{vertical-align:top}
.badge-muted{background:#1f2937;color:#9ca3af}
</style>
<script>
(function() {
  const out = document.getElementById('prompt-eval-output');
  async function refresh() {
    const r = await fetch('/api/admin/prompt-eval?days=30');
    const data = await r.json();
    if (!r.ok) { out.innerHTML = '<p class="muted">' + (data.error || 'Request failed') + '</p>'; return; }
    out.innerHTML = '<h3>JSON</h3><pre class="json-block">' + JSON.stringify(data, null, 2) + '</pre>';
  }
  document.getElementById('btn-refresh')?.addEventListener('click', refresh);
  document.getElementById('btn-run-fixtures')?.addEventListener('click', async () => {
    const btn = document.getElementById('btn-run-fixtures');
    btn.disabled = true; btn.textContent = 'Running…';
    try {
      const r = await fetch('/api/admin/prompt-eval/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      });
      const data = await r.json();
      if (r.ok && data.redirect) { window.location.href = data.redirect; return; }
      out.innerHTML = '<h3>Fixture eval</h3><pre class="json-block">' + JSON.stringify(data, null, 2) + '</pre>';
    } finally { btn.disabled = false; btn.textContent = 'Run fixture eval'; }
  });
})();
</script>"""

    table = f"""
<div class="card">
  <h3>Prompt version comparison</h3>
  <table class="compare-table">
    <thead><tr>
      <th>Version</th>
      <th>Requests</th>
      <th>Avg latency</th>
      <th>Est. cost</th>
      <th>Success rate</th>
      <th>Validation failures</th>
      <th>Extraction completeness</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""

    return _admin_shell("prompt-eval", "Prompt Evaluation", header + controls + table)
