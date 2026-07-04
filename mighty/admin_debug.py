"""Internal admin-only debugging pages (HTML renderers)."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any

from flask import render_template_string

ADMIN_TOOLS: list[tuple[str, str, str]] = [
    ("account-json", "Account JSON", "Decrypted account_data blobs per source"),
    ("extracted-fields", "Extracted fields", "Synced items, provenance, and classification"),
    ("provider-schemas", "Provider schemas", "Category schemas, connectors, and extraction hints"),
    ("discovery-cache", "Discovery cache", "In-process field schema cache entries"),
    ("ai-cache", "AI cache", "Provider config and recent discovery calls"),
    ("sync-history", "Sync history", "Field changes, audit events, and sync metadata"),
    ("sync-timeline", "Sync timeline", "Live sync state and per-account sync timestamps"),
    ("replay-discovery", "Replay field discovery", "Run discovery synchronously with step-by-step output"),
    ("pipeline-runs", "Pipeline runs", "Recent provider pipeline runs and stage traces"),
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
.badge-ok{background:#064e3b;color:#6ee7b7}.badge-warn{background:#78350f;color:#fcd34d}.badge-err{background:#7f1d1d;color:#fca5a5}.badge-muted{background:#1f2937;color:#6b7280}
.stage-card{background:#111827;border:1px solid #1f2937;border-radius:10px;padding:14px;margin-bottom:10px}
.stage-card.stage-skipped{opacity:.45;border-color:#1f2937;background:#0b0d12}
.stage-card.stage-failed{border-color:#dc2626;background:#1c0a0a;box-shadow:0 0 0 1px #7f1d1d}
.stage-card.stage-success{border-color:#065f46}
.stage-header{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.stage-header h4{margin:0;font-size:13px;color:#f3f4f6;text-transform:capitalize}
.run-meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-bottom:20px}
.run-meta .stat .label{font-size:10px;color:#6b7280;text-transform:uppercase;letter-spacing:.04em}
.run-meta .stat .val{font-size:13px;color:#f3f4f6;word-break:break-all}
.run-id{font-family:ui-monospace,monospace;font-size:11px}
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


def _run_status_badge(status: str | None) -> str:
    if status == "complete":
        return '<span class="badge badge-ok">complete</span>'
    if status == "failed":
        return '<span class="badge badge-err">failed</span>'
    if status == "aborted":
        return '<span class="badge badge-warn">aborted</span>'
    if status == "running":
        return '<span class="badge badge-warn">running</span>'
    return f'<span class="badge badge-muted">{_he(status or "—")}</span>'


def _stage_status_badge(status: str | None) -> str:
    if status == "success":
        return '<span class="badge badge-ok">success</span>'
    if status == "failed":
        return '<span class="badge badge-err">failed</span>'
    if status == "skipped":
        return '<span class="badge badge-muted">skipped</span>'
    if status == "running":
        return '<span class="badge badge-warn">running</span>'
    return f'<span class="badge badge-muted">{_he(status or "—")}</span>'


def _stage_card_class(status: str | None) -> str:
    if status == "failed":
        return "stage-card stage-failed"
    if status == "skipped":
        return "stage-card stage-skipped"
    if status == "success":
        return "stage-card stage-success"
    return "stage-card"


def render_pipeline_runs_page(runs: list[dict[str, Any]]) -> str:
    rows = "".join(
        f"<tr>"
        f'<td class="run-id"><a href="/admin/pipeline-runs/{_he(r.get("run_id",""))}">{_he((r.get("run_id") or "")[:8])}…</a></td>'
        f'<td>{_he(r.get("source",""))}</td>'
        f'<td>{_he(r.get("initiator",""))}</td>'
        f'<td>{_he(r.get("data_source") or "—")}</td>'
        f"<td>{_run_status_badge(r.get('run_status'))}</td>"
        f'<td>{_he(r.get("terminal_stage") or "—")}</td>'
        f'<td class="muted">{_he(r.get("terminal_reason") or "—")}</td>'
        f'<td>{_fmt_iso(r.get("created_at"))}</td>'
        f'<td>{_fmt_iso(r.get("finished_at"))}</td>'
        f"</tr>"
        for r in runs
    ) or '<tr><td colspan="9" class="muted">No pipeline runs yet</td></tr>'
    body = '<p class="lede">Most recent pipeline runs, newest first.</p>'
    body += (
        '<div class="card"><table><thead><tr>'
        "<th>Run</th><th>Source</th><th>Initiator</th><th>Data source</th>"
        "<th>Status</th><th>Terminal stage</th><th>Terminal reason</th>"
        "<th>Created</th><th>Finished</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
    )
    return _admin_shell("pipeline-runs", "Pipeline Runs", body)


def render_pipeline_run_detail_page(run: dict[str, Any], stages: list[dict[str, Any]]) -> str:
    meta_items = [
        ("Run ID", run.get("run_id")),
        ("Source", run.get("source")),
        ("Initiator", run.get("initiator")),
        ("Data source", run.get("data_source") or "—"),
        ("Status", None),
        ("Terminal stage", run.get("terminal_stage") or "—"),
        ("Terminal reason", run.get("terminal_reason") or "—"),
        ("Created", _fmt_iso(run.get("created_at"))),
        ("Finished", _fmt_iso(run.get("finished_at"))),
    ]
    meta_html = ""
    for label, val in meta_items:
        if label == "Status":
            display = _run_status_badge(run.get("run_status"))
        elif label == "Run ID":
            display = f'<span class="run-id">{_he(val)}</span>'
        else:
            display = _he(val)
        meta_html += (
            f'<div class="stat"><div class="label">{_he(label)}</div><div class="val">{display}</div></div>'
        )

    stage_cards = ""
    for s in stages:
        artifacts = s.get("artifacts")
        if artifacts is None and s.get("artifacts_json"):
            try:
                artifacts = json.loads(s["artifacts_json"])
            except Exception:
                artifacts = {}
        artifacts_html = _json_block(artifacts or {}) if artifacts else '<p class="muted">No artifacts</p>'
        duration = s.get("duration_ms")
        duration_display = f"{duration:.0f} ms" if duration is not None else "—"
        stage_name = str(s.get("stage") or "").replace("_", " ")
        card_cls = _stage_card_class(s.get("status"))
        stage_cards += (
            f'<div class="{card_cls}">'
            f'<div class="stage-header"><h4>{_he(stage_name)}</h4>{_stage_status_badge(s.get("status"))}</div>'
            f'<p class="muted">Duration: {_he(duration_display)}'
            + (f' · Failure: <strong style="color:#fca5a5">{_he(s.get("failure_reason"))}</strong>' if s.get("failure_reason") else "")
            + "</p>"
            f"<div>{artifacts_html}</div>"
            f"</div>"
        )
    if not stage_cards:
        stage_cards = '<p class="muted">No stages recorded for this run.</p>'

    body = (
        f'<p><a href="/admin/pipeline-runs" class="btn">&larr; All runs</a></p>'
        f'<div class="run-meta">{meta_html}</div>'
        f"<h3 style=\"font-size:14px;color:#d1d5db;margin:0 0 12px\">Stages</h3>{stage_cards}"
    )
    return _admin_shell("pipeline-runs", "Pipeline Run Detail", body)
