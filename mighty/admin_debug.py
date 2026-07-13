"""Internal admin-only debugging pages (HTML renderers)."""

from __future__ import annotations

import html
import json
from typing import Any

from flask import render_template_string

from mighty.admin_local_time import format_admin_local_time, timezone_note_html

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
    ("coverage", "Observation coverage", "Expected vs observed observation types per provider"),
    (
        "recommendation-unlocks",
        "Recommendation unlocks",
        "Which recommendations are possible given observed extraction data",
    ),
    (
        "delta-evidence-audit",
        "Delta evidence audit",
        "Compare captured Delta evidence vs extraction per pipeline run",
    ),
    (
        "capture-capability",
        "Capture Capability",
        "Needed vs present capture evidence per provider",
    ),
    (
        "provider-benchmark",
        "Provider Benchmark",
        "Combined readiness score from login, capture, coverage, and unlocks",
    ),
    (
        "provider-reliability-scorecard",
        "Provider Reliability Scorecard",
        "Reliability percentages, failure reasons, and engineering attention queue",
    ),
    (
        "account-state",
        "Account State (shadow)",
        "Canonical AccountState projection — internal preview only",
    ),
    (
        "account-snapshots",
        "Account Snapshots",
        "Immutable normalized snapshots — customer UI source of truth",
    ),
    (
        "provider-access-probe",
        "Provider Access Probe",
        "Latest account access probe per provider (Phase 1 reliability)",
    ),
    (
        "login-truth",
        "Login Truth",
        "Current account access vs cached private data",
    ),
    (
        "session-evidence",
        "Session Evidence Timeline",
        "Why Mighty believes each provider is connected, signed out, or unknown",
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
time.mighty-local-time{display:inline-flex;flex-direction:column;gap:1px;line-height:1.35;vertical-align:top}
time.mighty-local-time .mighty-rel{color:#e5e7eb;font-size:12px}
time.mighty-local-time .mighty-exact{color:#6b7280;font-size:10px}
.mighty-tz-note{font-size:11px;margin:0 0 14px}
</style>
<script src="/static/admin_local_time.js" defer></script>
</head><body><div class="admin-layout">
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
    body = picker + f'<p class="muted">synced_at: {format_admin_local_time(synced_at)}</p>' + _json_block(display)
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
    """Format epoch timestamps via the shared local-time helper."""
    return format_admin_local_time(ts)


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
    """Format ISO/admin timestamps via the shared local-time helper."""
    return format_admin_local_time(v)


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
    body = picker + timezone_note_html()
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
    body = timezone_note_html()
    body += '<div class="card"><h3>Live _sync_status</h3>' + _json_block(live_status) + "</div>"
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
    body = (
        '<p class="lede">Most recent pipeline runs, newest first.</p>'
        f"{timezone_note_html()}"
        '<div class="card"><table><thead><tr>'
        "<th>Run</th><th>Source</th><th>Initiator</th><th>Data source</th>"
        "<th>Status</th><th>Terminal stage</th><th>Terminal reason</th>"
        "<th>Created</th><th>Finished</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
    )
    return _admin_shell("pipeline-runs", "Pipeline Runs", body)


def render_pipeline_run_detail_page(run: dict[str, Any], stages: list[dict[str, Any]]) -> str:
    meta_items = [
        ("Run ID", run.get("run_id"), False),
        ("Source", run.get("source"), False),
        ("Initiator", run.get("initiator"), False),
        ("Data source", run.get("data_source") or "—", False),
        ("Status", None, False),
        ("Terminal stage", run.get("terminal_stage") or "—", False),
        ("Terminal reason", run.get("terminal_reason") or "—", False),
        ("Created", _fmt_iso(run.get("created_at")), True),
        ("Finished", _fmt_iso(run.get("finished_at")), True),
    ]
    meta_html = ""
    for label, val, is_html in meta_items:
        if label == "Status":
            display = _run_status_badge(run.get("run_status"))
        elif label == "Run ID":
            display = f'<span class="run-id">{_he(val)}</span>'
        elif is_html:
            display = val
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
        started_display = _fmt_iso(s.get("started_at"))
        finished_display = _fmt_iso(s.get("finished_at"))
        inferred = bool((artifacts or {}).get("inferred"))
        source_badge = (
            '<span class="badge badge-muted">inferred</span>'
            if inferred
            else '<span class="badge badge-ok">measured</span>'
        )
        stage_name = str(s.get("stage") or "").replace("_", " ")
        card_cls = _stage_card_class(s.get("status"))
        stage_cards += (
            f'<div class="{card_cls}">'
            f'<div class="stage-header"><h4>{_he(stage_name)}</h4>{_stage_status_badge(s.get("status"))}</div>'
            f'<p class="muted">Started: {started_display} · Finished: {finished_display}'
            f" · Duration: {_he(duration_display)} · {source_badge}"
            + (f' · Failure: <strong style="color:#fca5a5">{_he(s.get("failure_reason"))}</strong>' if s.get("failure_reason") else "")
            + "</p>"
            f"<div>{artifacts_html}</div>"
            f"</div>"
        )
    if not stage_cards:
        stage_cards = '<p class="muted">No stages recorded for this run.</p>'

    body = (
        f'<p><a href="/admin/pipeline-runs" class="btn">&larr; All runs</a></p>'
        f"{timezone_note_html()}"
        f'<div class="run-meta">{meta_html}</div>'
        f"<h3 style=\"font-size:14px;color:#d1d5db;margin:0 0 12px\">Stages</h3>{stage_cards}"
    )
    return _admin_shell("pipeline-runs", "Pipeline Run Detail", body)


def _coverage_pct_badge(pct: int | None) -> str:
    if pct is None:
        return '<span class="badge badge-muted">n/a</span>'
    if pct >= 80:
        return f'<span class="badge badge-ok">{pct}%</span>'
    if pct >= 50:
        return f'<span class="badge badge-warn">{pct}%</span>'
    return f'<span class="badge badge-err">{pct}%</span>'


def _observation_list(obs_ids: list[str], *, empty_msg: str = "None") -> str:
    if not obs_ids:
        return f'<p class="muted">{_he(empty_msg)}</p>'
    from mighty.observation_catalog import observation_label

    items = "".join(
        f'<li><code>{_he(obs)}</code> — {_he(observation_label(obs))}</li>'
        for obs in obs_ids
    )
    return f'<ul style="margin:0;padding-left:18px;font-size:12px">{items}</ul>'


def render_coverage_page(rows: list[Any]) -> str:
    table_rows = "".join(
        f"<tr>"
        f'<td><a href="/admin/coverage/{_he(r.source)}">{_he(r.display_name)}</a>'
        f'<div class="muted" style="font-size:10px">{_he(r.source)}</div></td>'
        f"<td>{len(r.expected)}</td>"
        f"<td>{len(r.observed)}</td>"
        f"<td>{_coverage_pct_badge(r.coverage_pct)}</td>"
        f"</tr>"
        for r in rows
    ) or '<tr><td colspan="4" class="muted">No providers configured</td></tr>'

    body = (
        '<p class="lede">How much of each provider do we actually understand? '
        "Observed types come from successful pipeline <code>trusted_observations</code> stages.</p>"
        f"{timezone_note_html()}"
        '<div class="card"><table><thead><tr>'
        "<th>Provider</th><th>Expected</th><th>Observed</th><th>Coverage</th>"
        f"</tr></thead><tbody>{table_rows}</tbody></table></div>"
    )
    return _admin_shell("coverage", "Observation Coverage", body)


def render_coverage_detail_page(
    row: Any,
    *,
    raw_field_keys: list[str] | None = None,
) -> str:
    pct_display = f"{row.coverage_pct}%" if row.coverage_pct is not None else "n/a"
    meta_html = (
        f'<div class="run-meta">'
        f'<div class="stat"><div class="label">Provider</div><div class="val">{_he(row.display_name)}</div></div>'
        f'<div class="stat"><div class="label">Source key</div><div class="val"><code>{_he(row.source)}</code></div></div>'
        f'<div class="stat"><div class="label">Coverage</div><div class="val">{_coverage_pct_badge(row.coverage_pct)} ({_he(pct_display)})</div></div>'
        f"</div>"
    )

    grid = (
        '<div class="grid-2">'
        f'<div class="card"><h3>Expected observations ({len(row.expected)})</h3>'
        f"{_observation_list(row.expected, empty_msg='No expected observations defined')}</div>"
        f'<div class="card"><h3>Observed observations ({len(row.observed)})</h3>'
        f"{_observation_list(row.observed, empty_msg='None observed in pipeline runs')}</div>"
        f'<div class="card"><h3>Missing observations ({len(row.missing)})</h3>'
        f"{_observation_list(row.missing, empty_msg='Full coverage — nothing missing')}</div>"
    )
    if raw_field_keys:
        keys_html = ", ".join(f"<code>{_he(k)}</code>" for k in raw_field_keys)
        grid += (
            f'<div class="card"><h3>Raw trusted field keys ({len(raw_field_keys)})</h3>'
            f'<p class="muted" style="font-size:11px;margin:0 0 8px">From pipeline trusted_observations artifacts</p>'
            f"<p style=\"font-size:11px;margin:0\">{keys_html or '—'}</p></div>"
        )
    grid += "</div>"

    body = (
        f'<p><a href="/admin/coverage" class="btn">&larr; All providers</a></p>'
        f"{meta_html}{grid}"
    )
    return _admin_shell("coverage", f"Coverage — {row.display_name}", body)


def render_recommendation_unlocks_page(rows: list[Any]) -> str:
    table_rows = "".join(
        f"<tr>"
        f'<td><a href="/admin/recommendation-unlocks/{_he(r.source)}">{_he(r.display_name)}</a>'
        f'<div class="muted" style="font-size:10px">{_he(r.source)}</div></td>'
        f"<td>{len(r.observed)}</td>"
        f"<td>{len(r.unlocked)}</td>"
        f"<td>{len(r.blocked)}</td>"
        f"</tr>"
        for r in rows
    ) or '<tr><td colspan="4" class="muted">No providers configured</td></tr>'

    body = (
        '<p class="lede">Which recommendations could we generate if we turned on the engine today? '
        "Observed types come from successful pipeline <code>trusted_observations</code> stages. "
        "This is an engineering diagnostic — no recommendations are generated here.</p>"
        '<div class="card"><table><thead><tr>'
        "<th>Provider</th><th>Observed</th><th>Unlocked</th><th>Blocked</th>"
        f"</tr></thead><tbody>{table_rows}</tbody></table></div>"
    )
    return _admin_shell("recommendation-unlocks", "Recommendation Unlocks", body)


def render_recommendation_unlocks_detail_page(row: Any) -> str:
    meta_html = (
        f'<div class="run-meta">'
        f'<div class="stat"><div class="label">Provider</div><div class="val">{_he(row.display_name)}</div></div>'
        f'<div class="stat"><div class="label">Source key</div><div class="val"><code>{_he(row.source)}</code></div></div>'
        f'<div class="stat"><div class="label">Unlocked</div><div class="val">{len(row.unlocked)}</div></div>'
        f'<div class="stat"><div class="label">Blocked</div><div class="val">{len(row.blocked)}</div></div>'
        f"</div>"
    )

    if row.unlocked:
        from mighty.recommendation_unlock_catalog import RECOMMENDATION_TYPES

        unlocked_items = "".join(
            f"<li><strong>{_he(RECOMMENDATION_TYPES[rid].title)}</strong> "
            f'<span class="muted">({_he(rid)})</span></li>'
            for rid in row.unlocked
            if rid in RECOMMENDATION_TYPES
        )
        unlocked_html = (
            f'<ul style="margin:0;padding-left:18px;font-size:12px">{unlocked_items}</ul>'
            if unlocked_items
            else '<p class="muted">None</p>'
        )
    else:
        unlocked_html = '<p class="muted">No recommendations unlocked — missing required observations</p>'

    blocked_rows = "".join(
        f"<tr>"
        f"<td><strong>{_he(b.title)}</strong>"
        f'<div class="muted" style="font-size:10px">{_he(b.recommendation_id)}</div></td>'
        f"<td>{_observation_list(b.missing_observations, empty_msg='—')}</td>"
        f"</tr>"
        for b in row.blocked
    ) or '<tr><td colspan="2" class="muted">All catalog recommendations unlocked</td></tr>'

    body = (
        f'<p><a href="/admin/recommendation-unlocks" class="btn">&larr; All providers</a></p>'
        f"{meta_html}"
        '<div class="grid-2">'
        f'<div class="card"><h3>Observed observations ({len(row.observed)})</h3>'
        f"{_observation_list(row.observed, empty_msg='None observed in pipeline runs')}</div>"
        f'<div class="card"><h3>Unlocked recommendations ({len(row.unlocked)})</h3>'
        f"{unlocked_html}</div>"
        "</div>"
        f'<div class="card" style="margin-top:16px"><h3>Blocked recommendations ({len(row.blocked)})</h3>'
        '<p class="muted" style="font-size:11px;margin:0 0 8px">Missing observations per blocked recommendation</p>'
        '<table><thead><tr><th>Recommendation</th><th>Missing observations</th></tr></thead>'
        f"<tbody>{blocked_rows}</tbody></table></div>"
    )
    return _admin_shell(
        "recommendation-unlocks",
        f"Recommendation Unlocks — {row.display_name}",
        body,
    )


def _evidence_block_list(blocks: list[Any], *, empty_msg: str) -> str:
    if not blocks:
        return f'<p class="muted">{_he(empty_msg)}</p>'
    items = ""
    for block in blocks:
        preview = (block.body or "")[:400]
        if len(block.body or "") > 400:
            preview += "…"
        items += (
            f'<details style="margin-bottom:8px">'
            f'<summary style="font-size:11px;cursor:pointer;color:#d1d5db">'
            f'{_he(block.header)} <span class="muted">({block.char_count} chars)</span></summary>'
            f'<pre class="json-block" style="max-height:240px;margin-top:6px">{_he(preview)}</pre>'
            f"</details>"
        )
    return items


def render_delta_evidence_audit_page(runs: list[dict[str, Any]]) -> str:
    rows = "".join(
        f"<tr>"
        f'<td class="run-id"><a href="/admin/delta-evidence-audit/{_he(r.get("run_id",""))}">'
        f'{_he((r.get("run_id") or "")[:8])}…</a></td>'
        f'<td>{_fmt_iso(r.get("created_at"))}</td>'
        f'<td>{_he(r.get("initiator") or "—")}</td>'
        f"<td>{r.get('trusted_count', 0)}</td>"
        f'<td class="muted"><a href="/admin/delta-evidence-audit/{_he(r.get("run_id",""))}">Open audit</a></td>'
        f"</tr>"
        for r in runs
    ) or '<tr><td colspan="5" class="muted">No successful Delta pipeline runs yet</td></tr>'

    body = (
        '<p class="lede">Diagnostic for Delta full-provider support. '
        "Compares captured evidence blocks in <code>raw_text</code> against pipeline "
        "extraction stages — does not change extraction logic.</p>"
        f"{timezone_note_html()}"
        '<div class="card"><table><thead><tr>'
        "<th>Run</th><th>Created</th><th>Initiator</th>"
        "<th>Trusted obs</th><th>Detail</th>"
        f"</tr></thead><tbody>{rows}</tbody></table></div>"
    )
    return _admin_shell("delta-evidence-audit", "Delta Evidence Audit", body)


def render_delta_evidence_audit_detail_page(audit: Any) -> str:
    meta_html = (
        f'<div class="run-meta">'
        f'<div class="stat"><div class="label">Run ID</div>'
        f'<div class="val run-id">{_he(audit.run_id)}</div></div>'
        f'<div class="stat"><div class="label">Status</div><div class="val">{_he(audit.run_status)}</div></div>'
        f'<div class="stat"><div class="label">Raw text</div>'
        f'<div class="val">{audit.raw_text_chars:,} chars</div></div>'
        f'<div class="stat"><div class="label">Trusted observations</div>'
        f'<div class="val">{len(audit.trusted_observations)}</div></div>'
        f"</div>"
    )
    if audit.raw_text_source:
        meta_html += f'<p class="muted" style="margin:-8px 0 16px">Evidence source: {_he(audit.raw_text_source)}</p>'

    stage = audit.stage_summary or {}
    stage_html = (
        f'<div class="card"><h3>Pipeline stages</h3>'
        f'<p class="muted" style="font-size:11px">structured={_he(stage.get("structured_status"))} '
        f'({ _he(stage.get("structured_failure") or "—") }) · '
        f'intelligent={_he(stage.get("intelligent_status"))} '
        f'({ _he(stage.get("intelligent_failure") or "—") }) · '
        f'validation={_he(stage.get("validation_status"))} '
        f'({ _he(stage.get("validation_failure") or "—") })</p></div>'
    )

    evidence_grid = (
        '<div class="grid-2">'
        f'<div class="card"><h3>API RESPONSE ({len(audit.api_response_blocks)})</h3>'
        f"{_evidence_block_list(audit.api_response_blocks, empty_msg='None')}</div>"
        f'<div class="card"><h3>NETWORK JSON ({len(audit.network_json_blocks)})</h3>'
        f"{_evidence_block_list(audit.network_json_blocks, empty_msg='None')}</div>"
        f'<div class="card"><h3>GRAPHQL ({len(audit.graphql_blocks)})</h3>'
        f"{_evidence_block_list(audit.graphql_blocks, empty_msg='None')}</div>"
        f'<div class="card"><h3>EMBEDDED STATE ({len(audit.embedded_state_blocks)})</h3>'
        f"{_evidence_block_list(audit.embedded_state_blocks, empty_msg='None')}</div>"
        "</div>"
    )
    evidence_grid += (
        f'<div class="card"><h3>Page / URL blocks ({len(audit.page_blocks)})</h3>'
        f"{_evidence_block_list(audit.page_blocks, empty_msg='None')}</div>"
    )

    extracted_rows = "".join(
        f"<tr><td><code>{_he(f.key)}</code></td><td>{_he(f.label)}</td>"
        f"<td>{_he(f.value)}</td><td>{_he(f.source)}</td>"
        f"<td>{'yes' if f.trusted else 'no'}</td></tr>"
        for f in audit.extracted_fields
    ) or '<tr><td colspan="5" class="muted">No extracted fields recorded</td></tr>'

    comp_rows = "".join(
        f"<tr>"
        f"<td><strong>{_he(c.label)}</strong>"
        f'<div class="muted" style="font-size:10px">{_he(c.observation_id)}</div></td>'
        f"<td>{'yes' if c.in_evidence else 'no'}</td>"
        f"<td>{'yes' if c.extracted else 'no'}</td>"
        f"<td>{'yes' if c.trusted else 'no'}</td>"
        f"<td>{_he(c.recommended_extractor or '—')}</td>"
        f"<td class='muted' style='font-size:11px'>{_he(c.diagnosis)}</td>"
        f"</tr>"
        for c in audit.comparisons
    )

    connector_rows = "".join(
        f"<tr><td><code>{_he(r.get('key',''))}</code></td>"
        f"<td>{_he(r.get('label',''))}</td><td>{_he(r.get('value',''))}</td>"
        f"<td><code>{_he(r.get('connector_path',''))}</code></td>"
        f"<td class='muted'>{_he((r.get('evidence_header') or '')[:80])}</td></tr>"
        for r in audit.connector_preview
    ) or '<tr><td colspan="5" class="muted">Connector paths did not match JSON evidence</td></tr>'

    body = (
        f'<p><a href="/admin/delta-evidence-audit" class="btn">&larr; All Delta runs</a></p>'
        f"{meta_html}{stage_html}{evidence_grid}"
        f'<div class="card"><h3>Extracted fields ({len(audit.extracted_fields)})</h3>'
        f'<table><thead><tr><th>Key</th><th>Label</th><th>Value</th>'
        f"<th>Source</th><th>Trusted</th></tr></thead><tbody>{extracted_rows}</tbody></table></div>"
        f'<div class="card"><h3>Connector preview (would extract from JSON)</h3>'
        f'<table><thead><tr><th>Key</th><th>Label</th><th>Value</th>'
        f"<th>Path</th><th>Evidence</th></tr></thead><tbody>{connector_rows}</tbody></table></div>"
        f'<div class="card"><h3>Observation comparison</h3>'
        f'<p class="muted" style="font-size:11px">For each important Delta observation: '
        f"was it in evidence, was it extracted, and which extractor should have found it?</p>"
        f'<table><thead><tr><th>Observation</th><th>In evidence</th><th>Extracted</th>'
        f"<th>Trusted</th><th>Should use</th><th>Diagnosis</th></tr></thead>"
        f"<tbody>{comp_rows}</tbody></table></div>"
    )
    return _admin_shell("delta-evidence-audit", "Delta Evidence Audit Detail", body)


def _capability_badge(present: bool, confidence: str) -> str:
    if present:
        return '<span class="badge badge-ok">present</span>'
    if confidence == "never observed":
        return '<span class="badge badge-muted">never observed</span>'
    return '<span class="badge badge-err">gap</span>'


def render_capture_capability_page(rows: list[Any]) -> str:
    table_rows = "".join(
        f"<tr>"
        f'<td><a href="/admin/capture-capability/{_he(r.source)}">{_he(r.display_name)}</a>'
        f'<div class="muted" style="font-size:10px">{_he(r.source)}</div></td>'
        f"<td>{r.needed_count}</td>"
        f"<td>{r.present_count}</td>"
        f"<td>{r.missing_count}</td>"
        f"<td>{_fmt_iso(r.last_seen)}</td>"
        f"</tr>"
        for r in rows
    ) or '<tr><td colspan="5" class="muted">No providers configured</td></tr>'

    body = (
        '<p class="lede">What capture inputs does each provider need vs what we actually record? '
        "Engineering workflow: <strong>Capture Capability</strong> → "
        "<a href=\"/admin/pipeline-runs\" style=\"color:#818cf8\">Pipeline Inspector</a> → "
        "<a href=\"/admin/coverage\" style=\"color:#818cf8\">Coverage</a> → "
        "<a href=\"/admin/recommendation-unlocks\" style=\"color:#818cf8\">Recommendation Unlocks</a>.</p>"
        f"{timezone_note_html()}"
        '<div class="card"><table><thead><tr>'
        "<th>Provider</th><th>Needed</th><th>Present</th><th>Missing</th><th>Last seen</th>"
        f"</tr></thead><tbody>{table_rows}</tbody></table></div>"
    )
    return _admin_shell("capture-capability", "Capture Capability", body)


def render_capture_capability_detail_page(row: Any, *, admin_sample: dict[str, Any] | None = None) -> str:
    from mighty.capture_capability import capability_label

    meta_html = (
        f'<div class="run-meta">'
        f'<div class="stat"><div class="label">Provider</div><div class="val">{_he(row.display_name)}</div></div>'
        f'<div class="stat"><div class="label">Source key</div><div class="val"><code>{_he(row.source)}</code></div></div>'
        f'<div class="stat"><div class="label">Needed</div><div class="val">{row.needed_count}</div></div>'
        f'<div class="stat"><div class="label">Present</div><div class="val">{row.present_count}</div></div>'
        f'<div class="stat"><div class="label">Missing</div><div class="val">{row.missing_count}</div></div>'
        f"</div>"
    )

    pipeline_link = ""
    if row.latest_successful_capture_run_id:
        rid = row.latest_successful_capture_run_id
        pipeline_link = (
            f'<div class="card" style="border-color:#4338ca;background:#1e1b4b">'
            f'<a href="/admin/pipeline-runs/{_he(rid)}" class="btn" '
            f'style="background:#4338ca;border-color:#6366f1;font-weight:700">'
            f"View latest successful capture →</a>"
            f'<p class="muted" style="margin:8px 0 0;font-size:11px">Run '
            f'<code class="run-id">{_he(rid[:8])}…</code> · Pipeline Inspector</p></div>'
        )

    improvement_html = (
        f'<div class="card"><h3>Next Best Improvement</h3>'
        f'<p style="font-size:14px;margin:0;color:#f3f4f6">{_he(row.next_best_improvement)}</p>'
        f'<p class="muted" style="font-size:11px;margin:8px 0 0">Deterministic recommendation from capture gap.</p></div>'
    )

    matrix_rows = "".join(
        f"<tr>"
        f"<td><strong>{_he(cap.label)}</strong>"
        f'<div class="muted" style="font-size:10px">{_he(cap.capability_id)}</div></td>'
        f'<td class="muted" style="font-size:11px;max-width:220px">{_he(cap.why_needed)}</td>'
        f"<td>{'Yes' if cap.needed else '—'}</td>"
        f"<td>{_capability_badge(cap.present, cap.confidence)}</td>"
        f"<td>{'Yes' if cap.gap else '—'}</td>"
        f'<td class="muted">{_he(cap.source_detail)}</td>'
        f"</tr>"
        for cap in row.rows
    )

    needed_list = "".join(
        f"<li><code>{_he(c)}</code> — {_he(capability_label(c))}</li>"
        for c in row.needed
    )
    present_list = "".join(
        f"<li><code>{_he(c)}</code> — {_he(capability_label(c))}</li>"
        for c in row.present
    ) or '<p class="muted">None observed in pipeline history</p>'
    gap_list = "".join(
        f"<li><code>{_he(c)}</code> — {_he(capability_label(c))}</li>"
        for c in row.missing
    ) or '<p class="muted">Full capture capability — no gaps</p>'

    initiator_rows = "".join(
        f"<tr><td>{_he(k)}</td><td>{v}</td></tr>"
        for k, v in sorted(row.initiator_counts.items())
    ) or '<tr><td colspan="2" class="muted">No pipeline runs</td></tr>'

    sample_html = ""
    if admin_sample:
        sample_html = (
            f'<div class="card"><h3>Your account sample</h3>'
            f'<p class="muted" style="font-size:11px">Admin connected account — not fleet-wide.</p>'
            f'<p class="muted">synced_at: {_he(admin_sample.get("synced_at") or "—")} · '
            f'raw_text: {admin_sample.get("raw_text_len", 0)} chars</p>'
            f'<pre class="json-block" style="max-height:120px">{_he(admin_sample.get("preview") or "")}</pre></div>'
        )

    cross_links = (
        f'<p style="margin-top:16px">'
        f'<a href="/admin/coverage/{_he(row.source)}" class="btn">Coverage →</a> '
        f'<a href="/admin/recommendation-unlocks/{_he(row.source)}" class="btn">Recommendation Unlocks →</a>'
        f"</p>"
    )

    present_display = present_list if row.present else '<p class="muted">None observed</p>'
    gap_display = gap_list if row.missing else '<p class="muted">None</p>'

    body = (
        f'<p><a href="/admin/capture-capability" class="btn">&larr; All providers</a></p>'
        f"{meta_html}"
        f"{pipeline_link}"
        f"{improvement_html}"
        '<div class="grid-2" style="margin-top:16px">'
        f'<div class="card"><h3>Needed evidence ({row.needed_count})</h3><ul style="margin:0;padding-left:18px;font-size:12px">{needed_list}</ul></div>'
        f'<div class="card"><h3>Captured evidence ({row.present_count})</h3>{present_display}</div>'
        f'<div class="card"><h3>Gap ({row.missing_count})</h3>{gap_display}</div>'
        "</div>"
        f'<div class="card" style="margin-top:16px"><h3>Capability matrix</h3>'
        '<table><thead><tr>'
        "<th>Capability</th><th>Why needed</th><th>Needed</th><th>Captured</th><th>Gap</th><th>Source</th>"
        f"</tr></thead><tbody>{matrix_rows}</tbody></table></div>"
        f'<div class="card"><h3>Initiator breakdown</h3>'
        f'<table><tbody>{initiator_rows}</tbody></table></div>'
        f"{sample_html}{cross_links}"
    )
    return _admin_shell("capture-capability", f"Capture Capability — {row.display_name}", body)


def _readiness_badge(score: int) -> str:
    return _coverage_pct_badge(score)


def _trend_badge(delta: int | None) -> str:
    if delta is None:
        return '<span class="badge badge-muted">new</span>'
    if delta > 0:
        return f'<span class="badge badge-ok">+{delta}</span>'
    if delta < 0:
        return f'<span class="badge badge-err">{delta}</span>'
    return '<span class="badge badge-muted">0</span>'


def render_provider_benchmark_page(rows: list[Any], *, trend_days: int = 14) -> str:
    from mighty.provider_benchmark import SCORE_WEIGHTS, attention_priority

    if not rows:
        main_table = '<tr><td colspan="8" class="muted">No providers configured</td></tr>'
    else:
        main_table = "".join(
            f"<tr>"
            f"<td><strong>{_he(r.display_name)}</strong>"
            f'<div class="muted" style="font-size:10px">{_he(r.source)}</div></td>'
            f"<td>{_readiness_badge(r.readiness_score)}</td>"
            f"<td>{_readiness_badge(r.login_score)}</td>"
            f"<td>{_readiness_badge(r.capture_score)}</td>"
            f"<td>{_readiness_badge(r.observation_score)}</td>"
            f"<td>{_readiness_badge(r.recommendation_score)}</td>"
            f"<td>{_trend_badge(r.trend_delta)}</td>"
            f"</tr>"
            for r in rows
        )

    attention_rows = sorted(rows, key=attention_priority)[:5]
    attention_table = "".join(
        f"<tr>"
        f"<td><strong>{_he(r.display_name)}</strong></td>"
        f"<td>{_readiness_badge(r.readiness_score)}</td>"
        f"<td>{_trend_badge(r.trend_delta)}</td>"
        f"<td class='muted'>{r.connection_success}/{r.connection_total} login · "
        f"{r.capture_present}/{r.capture_needed} capture · "
        f"{r.observation_observed}/{r.observation_expected} obs · "
        f"{r.recommendations_unlocked}/{r.recommendations_total} recs</td>"
        f"</tr>"
        for r in attention_rows
    ) or '<tr><td colspan="4" class="muted">No providers</td></tr>'

    improved = [r for r in rows if r.trend_delta is not None and r.trend_delta > 0]
    improved.sort(key=lambda r: -r.trend_delta)
    declining = [r for r in rows if r.trend_delta is not None and r.trend_delta < 0]
    declining.sort(key=lambda r: r.trend_delta)

    def _mini_list(items: list[Any], empty: str) -> str:
        if not items:
            return f'<p class="muted">{_he(empty)}</p>'
        lis = "".join(
            f"<li>{_he(r.display_name)} ({_trend_badge(r.trend_delta)})</li>"
            for r in items[:8]
        )
        return f'<ul style="margin:0;padding-left:18px;font-size:12px">{lis}</ul>'

    formula = (
        f"<code>readiness = "
        f"{int(SCORE_WEIGHTS['login'] * 100)}% × login + "
        f"{int(SCORE_WEIGHTS['capture'] * 100)}% × capture + "
        f"{int(SCORE_WEIGHTS['observation'] * 100)}% × observation + "
        f"{int(SCORE_WEIGHTS['recommendation'] * 100)}% × recommendation</code>"
    )

    body = (
        '<p class="lede">Engineering readiness benchmark combining '
        '<a href="/admin/pipeline-runs" style="color:#818cf8">Pipeline Inspector</a> (login), '
        '<a href="/admin/capture-capability" style="color:#818cf8">Capture Capability</a>, '
        '<a href="/admin/coverage" style="color:#818cf8">Observation Coverage</a>, and '
        '<a href="/admin/recommendation-unlocks" style="color:#818cf8">Recommendation Unlocks</a>. '
        f"Scores use the last {trend_days} days. Trend = recent readiness − prior readiness "
        f"(prior = all pipeline runs before the recent window).</p>"
        f'<div class="card"><h3>Scoring formula</h3><p style="font-size:12px;margin:0">{formula}</p>'
        '<ul style="font-size:11px;color:#9ca3af;margin:8px 0 0;padding-left:18px">'
        "<li><strong>Login</strong> — connection stage success rate</li>"
        "<li><strong>Capture</strong> — present / needed capture capabilities</li>"
        "<li><strong>Observation</strong> — expected observation coverage %</li>"
        "<li><strong>Recommendation</strong> — unlocked / catalog recommendations</li>"
        "</ul></div>"
        '<div class="grid-2">'
        f'<div class="card"><h3>Improved ({len(improved)})</h3>{_mini_list(improved, "No providers improved in trend window")}</div>'
        f'<div class="card"><h3>Declining ({len(declining)})</h3>{_mini_list(declining, "No providers declining in trend window")}</div>'
        "</div>"
        f'<div class="card"><h3>Needs attention first</h3>'
        '<p class="muted" style="font-size:11px;margin:0 0 10px">Lowest readiness, penalized for negative trend.</p>'
        '<table><thead><tr>'
        "<th>Provider</th><th>Readiness</th><th>Trend</th><th>Breakdown</th>"
        f"</tr></thead><tbody>{attention_table}</tbody></table></div>"
        '<div class="card"><h3>All providers</h3>'
        '<table><thead><tr>'
        "<th>Provider</th><th>Readiness</th><th>Login</th><th>Capture</th>"
        "<th>Observation</th><th>Recommendation</th><th>Trend</th>"
        f"</tr></thead><tbody>{main_table}</tbody></table></div>"
    )
    return _admin_shell("provider-benchmark", "Provider Benchmark", body)


def _failure_reason_list(reasons: list[Any], *, empty_msg: str) -> str:
    if not reasons:
        return f'<p class="muted">{_he(empty_msg)}</p>'
    rows = "".join(
        f"<tr><td><code>{_he(r.reason)}</code></td>"
        f"<td>{_he(r.label)}</td><td>{r.count}</td></tr>"
        for r in reasons
    )
    return (
        '<table><thead><tr><th>Reason</th><th>Label</th><th>Count</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )


def _missing_observation_list(items: list[Any], *, empty_msg: str) -> str:
    if not items:
        return f'<p class="muted">{_he(empty_msg)}</p>'
    rows = "".join(
        f"<tr><td><code>{_he(item.observation_id)}</code></td>"
        f"<td>{_he(item.label)}</td><td>{item.provider_count}</td></tr>"
        for item in items
    )
    return (
        '<table><thead><tr><th>Observation</th><th>Label</th><th>Providers missing</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
    )


def render_provider_reliability_scorecard_page(scorecard: Any, *, trend_days: int = 14) -> str:
    from mighty.provider_benchmark import SCORE_WEIGHTS

    if not scorecard.providers:
        provider_table = '<tr><td colspan="7" class="muted">No providers configured</td></tr>'
    else:
        provider_table = "".join(
            f"<tr>"
            f"<td><strong>{_he(r.display_name)}</strong>"
            f'<div class="muted" style="font-size:10px">{_he(r.source)}</div></td>'
            f"<td>{_readiness_badge(r.reliability_score)}</td>"
            f"<td>{_readiness_badge(r.login_success_pct)}</td>"
            f"<td>{_readiness_badge(r.capture_success_pct)}</td>"
            f"<td>{_readiness_badge(r.observation_success_pct)}</td>"
            f"<td>{_readiness_badge(r.recommendation_success_pct)}</td>"
            f"</tr>"
            for r in scorecard.providers
        )

    attention_table = "".join(
        f"<tr>"
        f"<td>#{r.attention_rank}</td>"
        f"<td><strong>{_he(r.display_name)}</strong></td>"
        f"<td>{_readiness_badge(r.reliability_score)}</td>"
        f"<td>{_readiness_badge(r.login_success_pct)}</td>"
        f"<td>{_readiness_badge(r.capture_success_pct)}</td>"
        f"<td>{_readiness_badge(r.observation_success_pct)}</td>"
        f"<td>{_readiness_badge(r.recommendation_success_pct)}</td>"
        f"</tr>"
        for r in scorecard.needs_attention
    ) or '<tr><td colspan="7" class="muted">No providers</td></tr>'

    formula = (
        f"<code>reliability = "
        f"{int(SCORE_WEIGHTS['login'] * 100)}% × login + "
        f"{int(SCORE_WEIGHTS['capture'] * 100)}% × capture + "
        f"{int(SCORE_WEIGHTS['observation'] * 100)}% × observation + "
        f"{int(SCORE_WEIGHTS['recommendation'] * 100)}% × recommendation</code>"
    )

    body = (
        '<p class="lede">Provider Reliability Scorecard combines '
        '<a href="/admin/pipeline-runs" style="color:#818cf8">Pipeline Inspector</a>, '
        '<a href="/admin/capture-capability" style="color:#818cf8">Capture Capability</a>, '
        '<a href="/admin/coverage" style="color:#818cf8">Observation Coverage</a>, and '
        '<a href="/admin/provider-benchmark" style="color:#818cf8">Provider Benchmark</a>. '
        f"Percentages use the last {trend_days} days of pipeline runs.</p>"
        f'<div class="card"><h3>Scoring formula</h3><p style="font-size:12px;margin:0">{formula}</p></div>'
        f'<div class="card"><h3>Needs engineering attention (top 5)</h3>'
        '<p class="muted" style="font-size:11px;margin:0 0 10px">Lowest reliability score, '
        "penalized for negative trend (same ranking as Provider Benchmark).</p>"
        '<table><thead><tr>'
        "<th>Rank</th><th>Provider</th><th>Reliability</th><th>Login</th>"
        "<th>Capture</th><th>Observation</th><th>Recommendation</th>"
        f"</tr></thead><tbody>{attention_table}</tbody></table></div>"
        '<div class="grid-2">'
        f'<div class="card"><h3>Top login failure reasons</h3>'
        f"{_failure_reason_list(scorecard.top_login_failure_reasons, empty_msg='No connection failures in window')}</div>"
        f'<div class="card"><h3>Top capture failure reasons</h3>'
        f"{_failure_reason_list(scorecard.top_capture_failure_reasons, empty_msg='No capture failures in window')}</div>"
        "</div>"
        f'<div class="card"><h3>Most commonly missing observations</h3>'
        f"{_missing_observation_list(scorecard.most_missing_observations, empty_msg='No missing observations across providers')}</div>"
        '<div class="card"><h3>All providers</h3>'
        '<table><thead><tr>'
        "<th>Provider</th><th>Reliability</th><th>Login success</th><th>Capture success</th>"
        "<th>Observation success</th><th>Recommendation success</th>"
        f"</tr></thead><tbody>{provider_table}</tbody></table></div>"
    )
    return _admin_shell("provider-reliability-scorecard", "Provider Reliability Scorecard", body)


def _state_badge(value: str, *, ok_values: set[str] | None = None) -> str:
    ok_values = ok_values or set()
    if value in ok_values:
        cls = "badge-ok"
    elif value in {"needs_login", "expired", "none", "low"}:
        cls = "badge-err"
    elif value in {"connecting", "partial", "expiring", "medium"}:
        cls = "badge-warn"
    else:
        cls = "badge-muted"
    return f'<span class="badge {cls}">{_he(value.replace("_", " "))}</span>'


def render_account_state_page(states: list[Any]) -> str:
    from mighty.account_presentation import attach_presentation_debug, resolve_account_presentation_with_debug

    if not states:
        table = '<tr><td colspan="15" class="muted">No account state rows yet.</td></tr>'
    else:
        rows = []
        for state in states:
            _presentation, debug = resolve_account_presentation_with_debug(state)
            attach_presentation_debug(state, debug)
            action = state.next_recommended_action
            action_text = "—"
            if action and action.kind != "none":
                action_text = f"{action.kind}: {action.label}"
            obs = ", ".join(state.observations_available[:6])
            if len(state.observations_available) > 6:
                obs += f" (+{len(state.observations_available) - 6})"
            ignored = ", ".join(state.ignored_stale_signals) if state.ignored_stale_signals else "—"
            rows.append(
                f"<tr>"
                f"<td><strong>{_he(state.display_name)}</strong>"
                f'<div class="muted" style="font-size:10px">{_he(state.provider)}</div></td>'
                f"<td>{_state_badge(state.access_method)}</td>"
                f"<td>{_state_badge(state.connection_state, ok_values={'connected'})}</td>"
                f"<td>{_state_badge(state.session_health, ok_values={'healthy'})}</td>"
                f"<td class='muted'>{_he(state.last_verified_at or '—')}</td>"
                f"<td>{_state_badge(state.data_status, ok_values={'complete'})}</td>"
                f"<td class='muted'>{_he(state.last_data_refresh or '—')}</td>"
                f"<td class='muted' style='max-width:220px'>{_he(obs or '—')}</td>"
                f"<td class='muted'>{_he(action_text)}</td>"
                f"<td>{_state_badge(state.confidence.level, ok_values={'high'})}"
                f'<div class="muted" style="font-size:10px">{state.confidence.score}/100</div></td>'
                f"<td class='muted'>{_he(state.status_line)}</td>"
                f"<td class='muted' style='font-size:11px'>{_he(state.why_state or '—')}</td>"
                f"<td class='muted' style='font-size:11px'>{_he(state.winning_signal or '—')}</td>"
                f"<td class='muted' style='font-size:11px'>{_he(ignored)}</td>"
                f"<td class='muted' style='font-size:10px'>{_he(state.updated_at)}</td>"
                f"</tr>"
            )
        table = "".join(rows)

    body = (
        '<p class="lede">Shadow-mode preview of the canonical <code>AccountState</code> model '
        "(see <code>docs/ACCOUNT_STATE.md</code>). Presentation debug columns explain "
        "<code>resolve_account_presentation</code> decisions.</p>"
        '<div class="card"><h3>Per-account projection</h3>'
        '<table><thead><tr>'
        "<th>Provider</th><th>Access</th><th>Connection</th><th>Session</th>"
        "<th>Last verified</th><th>Data</th><th>Last refresh</th>"
        "<th>Observations</th><th>Next action</th><th>Confidence</th>"
        "<th>Status line</th><th>why_state</th><th>winning_signal</th>"
        "<th>ignored_stale_signals</th><th>Updated</th>"
        f"</tr></thead><tbody>{table}</tbody></table></div>"
    )
    return _admin_shell("account-state", "Account State (shadow)", body)


def render_account_snapshots_page(
    sources: list[str],
    source: str | None,
    snapshots: list[Any],
    *,
    active: Any | None = None,
) -> str:
    picker = _source_picker(sources, source, "/admin/account-snapshots")
    if not source:
        body = (
            '<p class="lede">Select a provider to inspect immutable Account Snapshots. '
            "Customer UI renders from the latest successful snapshot only.</p>"
            f"{picker}"
        )
        return _admin_shell("account-snapshots", "Account Snapshots", body)

    rows = []
    for snap in snapshots:
        is_active = active is not None and snap.snapshot_id == active.snapshot_id
        badge = (
            '<span style="color:#065f46;font-weight:600">active</span>'
            if is_active
            else '<span class="muted">history</span>'
        )
        rows.append(
            f"<tr>"
            f'<td><a href="/admin/account-snapshots?source={_he(source)}'
            f'&snapshot_id={_he(snap.snapshot_id)}">{_he(snap.snapshot_id[:8])}…</a></td>'
            f"<td>{badge}</td>"
            f'<td class="muted">{_he(format_admin_local_time(snap.verified_at))}</td>'
            f'<td class="muted">{_he(format_admin_local_time(snap.created_at))}</td>'
            f"<td>v{_he(snap.schema_version)}</td>"
            f'<td class="muted">{_he(snap.provider_version)}</td>'
            f'<td class="muted">{_he(snap.confidence if snap.confidence is not None else "—")}</td>'
            f"<td>{_he(snap.field_count)}</td>"
            f'<td class="muted" style="font-size:10px">{_he(snap.access_cycle_id or "—")}</td>'
            f"</tr>"
        )
    table = "".join(rows) or (
        '<tr><td colspan="9" class="muted">No successful snapshots yet.</td></tr>'
    )

    detail = ""
    if active is not None:
        detail = (
            '<div class="card" style="margin-top:16px"><h3>Snapshot detail</h3>'
            f'<p><strong>Provider</strong> {_he(active.provider)} · '
            f'<strong>Verified</strong> {_he(format_admin_local_time(active.verified_at))} · '
            f'<strong>Snapshot ID</strong> <code>{_he(active.snapshot_id)}</code> · '
            f'<strong>Schema</strong> v{_he(active.schema_version)}</p>'
            f'<p class="muted">correlation_id={_he(active.correlation_id or "—")} · '
            f'access_cycle_id={_he(active.access_cycle_id or "—")} · '
            f'confidence={_he(active.confidence if active.confidence is not None else "—")}</p>'
            "<h4>Normalized fields</h4>"
            f"{_json_block(list(active.normalized_fields))}"
            "<h4>Buckets</h4>"
            f"{_json_block({k: getattr(active, k) for k in ('accounts','benefits','rewards','credits','offers','travel','warnings')})}"
            "<h4>Evidence references</h4>"
            f"{_json_block([ref.to_dict() for ref in active.evidence_refs])}"
            "<h4>Metadata</h4>"
            f"{_json_block(active.metadata)}"
            "</div>"
        )

    body = (
        '<p class="lede">Immutable normalized Account Snapshots. '
        "Newest successful snapshot is active; older rows remain queryable. "
        "Evidence refs point at extraction artifacts — raw payloads are not duplicated.</p>"
        f"{picker}"
        '<div class="card"><h3>History</h3>'
        '<table><thead><tr>'
        "<th>Snapshot</th><th>Role</th><th>Verified</th><th>Created</th>"
        "<th>Schema</th><th>Provider ver</th><th>Confidence</th>"
        "<th>Fields</th><th>Access cycle</th>"
        f"</tr></thead><tbody>{table}</tbody></table></div>"
        f"{detail}"
    )
    return _admin_shell("account-snapshots", "Account Snapshots", body)


def _probe_status_badge(status: str) -> str:
    colors = {
        "signed_in_data_seen": ("#065f46", "#d1fae5"),
        "signed_in_no_data_seen": ("#92400e", "#fef3c7"),
        "needs_sign_in": ("#991b1b", "#fee2e2"),
        "blocked": ("#7c2d12", "#ffedd5"),
        "error": ("#581c87", "#f3e8ff"),
        "not_started": ("#374151", "#f3f4f6"),
    }
    fg, bg = colors.get(status, ("#374151", "#f3f4f6"))
    label = status.replace("_", " ")
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:6px;'
        f'font-size:11px;font-weight:600;color:{fg};background:{bg}">{_he(label)}</span>'
    )


def _auth_state_badge(auth_state: str | None) -> str:
    colors = {
        "private_data_visible": ("#065f46", "#d1fae5"),
        "authenticated_no_private_data": ("#92400e", "#fef3c7"),
        "marketing": ("#1e40af", "#dbeafe"),
        "login_page": ("#991b1b", "#fee2e2"),
        "login_submitted": ("#9a3412", "#ffedd5"),
        "mfa_required": ("#7c2d12", "#fed7aa"),
        "bot_blocked": ("#581c87", "#f3e8ff"),
        "session_expired": ("#b45309", "#fef3c7"),
        "error": ("#7f1d1d", "#fecaca"),
        "unknown": ("#374151", "#f3f4f6"),
    }
    state = auth_state or "unknown"
    fg, bg = colors.get(state, ("#374151", "#f3f4f6"))
    label = state.replace("_", " ")
    return (
        f'<span style="display:inline-block;padding:2px 8px;border-radius:6px;'
        f'font-size:11px;font-weight:600;color:{fg};background:{bg}">{_he(label)}</span>'
    )


def _probe_form_signals(row: dict[str, Any]) -> str:
    flags = (
        ("login", row.get("login_form_present")),
        ("user", row.get("username_field_present")),
        ("pass", row.get("password_field_present")),
        ("mfa", row.get("mfa_signal_present")),
        ("bot", row.get("bot_block_signal_present")),
        ("expired", row.get("session_expired_signal_present")),
    )
    parts = [
        f"{name}:{'yes' if val else 'no'}"
        for name, val in flags
    ]
    return ", ".join(parts)


def _probe_matched_rules(row: dict[str, Any]) -> str:
    groups = []
    for key, label in (
        ("matched_login_rules", "login"),
        ("matched_private_data_rules", "private"),
        ("matched_blocking_rules", "block"),
    ):
        rules = row.get(key) or []
        if rules:
            groups.append(f"{label}={','.join(rules)}")
    return "; ".join(groups) or "—"


def _probe_page_diagnostics(row: dict[str, Any]) -> str:
    auth_state = row.get("auth_state") or ""
    failure = row.get("failure_reason") or ""
    if auth_state != "unknown" and failure != "blank_or_unloaded_page":
        return "—"
    diag = row.get("page_diagnostics") or {}
    if not diag:
        return "—"
    parts = []
    for key in (
        "ready_state",
        "body_exists",
        "body_text_length",
        "iframe_count",
        "input_count",
        "button_count",
        "password_input_count",
        "dom_wait_ms",
        "classifier_started_at",
        "content_script_error",
    ):
        if key in diag and diag.get(key) is not None and diag.get(key) != "":
            parts.append(f"{key}={diag.get(key)}")
    preview = diag.get("visible_text_preview")
    if preview:
        parts.append(f"preview={str(preview)[:120]}")
    return "; ".join(parts) or "—"


def _probe_deep_inspect_section(rows: list[dict[str, Any]]) -> str:
    """Render deep inspect diagnostics for the latest Amex probe run."""
    amex_row = next((r for r in rows if r.get("provider") == "amex"), None)
    if not amex_row:
        return ""
    deep = amex_row.get("deep_inspect") or {}
    if not deep:
        return (
            '<div class="card" style="margin-bottom:16px">'
            "<h3>Amex deep inspect</h3>"
            '<p class="muted" style="font-size:12px;margin:0">'
            "No deep inspect data yet. Run <strong>Run Probe — Amex</strong> to capture DOM diagnostics.</p>"
            "</div>"
        )

    def _fmt_list(label: str, items: list[Any] | None, limit: int = 20) -> str:
        if not items:
            return f"<dt>{_he(label)}</dt><dd class=\"muted\">—</dd>"
        shown = items[:limit]
        extra = f" (+{len(items) - limit} more)" if len(items) > limit else ""
        return (
            f"<dt>{_he(label)}{_he(extra)}</dt>"
            f"<dd><code style=\"font-size:10px;word-break:break-all\">"
            f"{_he(', '.join(str(i) for i in shown))}</code></dd>"
        )

    iframes = deep.get("iframes") or []
    iframe_lines = []
    for frame in iframes[:10]:
        if not isinstance(frame, dict):
            continue
        iframe_lines.append(
            f"#{frame.get('index', '?')}: src={frame.get('src') or '—'} "
            f"id={frame.get('id') or '—'} name={frame.get('name') or '—'} "
            f"sandbox={frame.get('sandbox') or '—'}"
        )

    nav = deep.get("navigation_timing") or {}
    nav_parts = [
        f"{k}={nav[k]}" for k in (
            "response_start_ms", "dom_content_loaded_ms", "load_event_ms", "duration_ms",
        )
        if nav.get(k) is not None
    ]

    js_errors = deep.get("js_errors") or []
    error_lines = []
    for err in js_errors[:10]:
        if isinstance(err, dict):
            error_lines.append(err.get("message") or str(err))
        else:
            error_lines.append(str(err))

    spa_lines = []
    for root in deep.get("spa_roots") or []:
        if not isinstance(root, dict):
            continue
        key = root.get("key") or "?"
        if not root.get("exists"):
            spa_lines.append(f"{key}: missing")
        else:
            spa_lines.append(
                f"{key}: children={root.get('child_element_count')} "
                f"innerHTML={root.get('inner_html_length')} text={root.get('text_length')}"
            )

    mutation = deep.get("mutation_timeline") or {}
    mutation_line = (
        f"count={mutation.get('total_count')} first={mutation.get('first_mutation_ms')}ms "
        f"last={mutation.get('last_mutation_ms')}ms activity={mutation.get('mutation_activity')}"
        if mutation else "—"
    )

    console_diag = deep.get("console_diagnostics") or []
    console_lines = [
        f"{c.get('level')}: {c.get('message')}" for c in console_diag[:10] if isinstance(c, dict)
    ]

    resources = deep.get("resource_diagnostics") or {}
    resource_line = (
        f"js={resources.get('js_count')} css={resources.get('css_count')} "
        f"fetch/xhr={resources.get('fetch_xhr_count')} "
        f"failed={len(resources.get('failed_loads') or [])} "
        f"slow={len(resources.get('slow_loads') or [])}"
        if resources else "—"
    )
    failed_lines = [
        f"{r.get('name')} ({r.get('response_status') or '?'})"
        for r in (resources.get("failed_loads") or [])[:5]
        if isinstance(r, dict)
    ]
    slow_lines = [
        f"{r.get('name')} ({r.get('duration_ms')}ms)"
        for r in (resources.get("slow_loads") or [])[:5]
        if isinstance(r, dict)
    ]

    frameworks = deep.get("framework_detection") or []
    obs = deep.get("observation_window") or {}
    obs_line = (
        f"{obs.get('observation_ms')}ms window; DOM {obs.get('start_dom_size')}→{obs.get('end_dom_size')} "
        f"(Δ{obs.get('dom_size_delta')}); text {obs.get('start_visible_text_length')}→"
        f"{obs.get('end_visible_text_length')} (Δ{obs.get('visible_text_length_delta')})"
        if obs else "—"
    )

    details = (
        f"<dt>outer_html_length</dt><dd>{_he(deep.get('outer_html_length'))}</dd>"
        f"<dt>outer_html_preview</dt>"
        f"<dd><pre style=\"font-size:10px;max-height:200px;overflow:auto;"
        f"white-space:pre-wrap;word-break:break-all;margin:0\">"
        f"{_he(str(deep.get('outer_html_preview') or '')[:2000])}</pre></dd>"
        f"<dt>iframe_count</dt><dd>{_he(deep.get('iframe_count'))}</dd>"
        f"<dt>iframes</dt><dd><code style=\"font-size:10px\">"
        f"{_he('; '.join(iframe_lines) or '—')}</code></dd>"
        f"<dt>shadow_root_count</dt><dd>{_he(deep.get('shadow_root_count'))}</dd>"
        f"<dt>script_count</dt><dd>{_he(deep.get('script_count'))}</dd>"
        f"{_fmt_list('script_srcs', deep.get('script_srcs'))}"
        f"{_fmt_list('stylesheet_hrefs', deep.get('stylesheet_hrefs'))}"
        f"<dt>navigation_timing</dt><dd class=\"muted\">{_he('; '.join(nav_parts) or '—')}</dd>"
        f"{_fmt_list('cookie_names', deep.get('cookie_names'))}"
        f"{_fmt_list('local_storage_keys', deep.get('local_storage_keys'))}"
        f"{_fmt_list('session_storage_keys', deep.get('session_storage_keys'))}"
        f"<dt>js_errors</dt><dd><code style=\"font-size:10px\">"
        f"{_he('; '.join(error_lines) or '—')}</code></dd>"
        f"<dt>content_script_injection_succeeded</dt>"
        f"<dd>{_he(deep.get('content_script_injection_succeeded'))}</dd>"
        f"<dt>final_url</dt><dd class=\"muted\" style=\"word-break:break-all\">"
        f"{_he(deep.get('final_url') or '—')}</dd>"
        f"<dt>page_title</dt><dd>{_he(deep.get('page_title') or '—')}</dd>"
        f"<dt>ready_state</dt><dd>{_he(deep.get('ready_state') or '—')}</dd>"
        f"<dt>visible_text_preview</dt>"
        f"<dd class=\"muted\">{_he(str(deep.get('visible_text_preview') or '')[:500])}</dd>"
        f"<dt>SPA roots</dt><dd><code style=\"font-size:10px\">"
        f"{_he('; '.join(spa_lines) or '—')}</code></dd>"
        f"<dt>mutation_timeline</dt><dd class=\"muted\">{_he(mutation_line)}</dd>"
        f"<dt>console_diagnostics</dt><dd><code style=\"font-size:10px\">"
        f"{_he('; '.join(console_lines) or '—')}</code></dd>"
        f"<dt>resource_diagnostics</dt><dd class=\"muted\">{_he(resource_line)}</dd>"
        f"<dt>failed_loads</dt><dd><code style=\"font-size:10px\">"
        f"{_he('; '.join(failed_lines) or '—')}</code></dd>"
        f"<dt>slow_loads</dt><dd><code style=\"font-size:10px\">"
        f"{_he('; '.join(slow_lines) or '—')}</code></dd>"
        f"<dt>framework_detection</dt><dd>{_he(', '.join(frameworks) or '—')}</dd>"
        f"<dt>observation_window</dt><dd class=\"muted\">{_he(obs_line)}</dd>"
        f"<dt>end_visible_text_preview</dt>"
        f"<dd class=\"muted\">{_he(str(obs.get('end_visible_text_preview') or '')[:500])}</dd>"
    )

    probed = _fmt_iso(amex_row.get("probed_at") or amex_row.get("timestamp"))
    auth_trace_section = _probe_auth_network_trace_section(deep)
    return (
        '<div class="card" style="margin-bottom:16px">'
        "<h3>Amex deep inspect</h3>"
        f'<p class="muted" style="font-size:12px;margin:0 0 12px">'
        f"Latest Amex manual probe diagnostics (probed {probed}). "
        "Cookie and storage values are never captured — names/keys only.</p>"
        f'<dl style="display:grid;grid-template-columns:180px 1fr;gap:6px 12px;'
        f'font-size:11px;margin:0">{details}</dl>'
        f"{auth_trace_section}"
        "</div>"
    )


def _probe_auth_network_trace_section(deep: dict[str, Any]) -> str:
    trace = deep.get("auth_network_trace") or {}
    if not trace:
        return (
            '<div style="margin-top:16px;padding-top:12px;border-top:1px solid #e5e7eb">'
            "<h4 style=\"margin:0 0 8px;font-size:13px\">Amex authentication network trace</h4>"
            '<p class="muted" style="font-size:11px;margin:0">'
            "No network trace captured yet.</p></div>"
        )

    status_counts = trace.get("status_counts") or {}
    status_line = ", ".join(f"{k}:{v}" for k, v in sorted(status_counts.items())) or "—"

    def _fmt_req(req: dict[str, Any]) -> str:
        if not isinstance(req, dict):
            return str(req)
        parts = [
            f"{req.get('method') or 'GET'} {req.get('url') or '—'}",
            f"status={req.get('status_code') or '?'}",
        ]
        if req.get("duration_ms") is not None:
            parts.append(f"{req.get('duration_ms')}ms")
        if req.get("with_credentials") is True or req.get("credentials") == "include":
            parts.append("credentials=include")
        if req.get("cors_error"):
            parts.append("cors_error")
        if req.get("network_error"):
            parts.append("network_error")
        header_names = req.get("response_header_names") or []
        if header_names:
            parts.append(f"headers=[{','.join(header_names[:8])}]")
        return " | ".join(parts)

    highlighted = trace.get("highlighted_requests") or []
    auth_session = trace.get("auth_session_requests") or []
    status_401 = trace.get("status_401_requests") or []
    status_403 = trace.get("status_403_requests") or []
    diagnostic = trace.get("diagnostic_summary") or "—"

    highlighted_lines = [_fmt_req(r) for r in highlighted[:10]]
    auth_lines = [_fmt_req(r) for r in auth_session[:10]]
    lines_401 = [_fmt_req(r) for r in status_401[:10]]
    lines_403 = [_fmt_req(r) for r in status_403[:10]]

    return (
        '<div style="margin-top:16px;padding-top:12px;border-top:1px solid #e5e7eb">'
        "<h4 style=\"margin:0 0 8px;font-size:13px\">Amex authentication network trace</h4>"
        f'<p class="muted" style="font-size:11px;margin:0 0 8px">'
        "Safe request metadata only — no cookie values, auth tokens, or response bodies.</p>"
        f'<p style="font-size:11px;margin:0 0 12px"><strong>Diagnostic:</strong> '
        f"{_he(diagnostic)}</p>"
        f'<dl style="display:grid;grid-template-columns:180px 1fr;gap:6px 12px;'
        f'font-size:11px;margin:0">'
        f"<dt>request_count</dt><dd>{_he(trace.get('request_count'))}</dd>"
        f"<dt>status_counts</dt><dd class=\"muted\">{_he(status_line)}</dd>"
        f"<dt>highlighted auth/session</dt><dd><code style=\"font-size:10px;word-break:break-all\">"
        f"{_he('; '.join(highlighted_lines) or '—')}</code></dd>"
        f"<dt>auth/session keyword matches</dt><dd><code style=\"font-size:10px;word-break:break-all\">"
        f"{_he('; '.join(auth_lines) or '—')}</code></dd>"
        f"<dt>401 failures</dt><dd><code style=\"font-size:10px;word-break:break-all\">"
        f"{_he('; '.join(lines_401) or '—')}</code></dd>"
        f"<dt>403 failures</dt><dd><code style=\"font-size:10px;word-break:break-all\">"
        f"{_he('; '.join(lines_403) or '—')}</code></dd>"
        f"</dl></div>"
    )


def _fmt_bootstrap_nav_event(event: dict[str, Any]) -> str:
    if not isinstance(event, dict):
        return str(event)
    parts = [
        f"{event.get('observed_at_ms', '?')}ms",
        event.get("source") or event.get("type") or "nav",
        event.get("url") or event.get("href") or "—",
    ]
    if event.get("status"):
        parts.append(f"status={event.get('status')}")
    if event.get("transition_type"):
        parts.append(f"transition={event.get('transition_type')}")
    return " | ".join(parts)


def _fmt_bootstrap_request(req: dict[str, Any]) -> str:
    if not isinstance(req, dict):
        return str(req)
    parts = [
        f"{req.get('start_time_ms', '?')}ms",
        req.get("url") or "—",
        f"status={req.get('status_code') or '?'}",
    ]
    if req.get("duration_ms") is not None:
        parts.append(f"{req.get('duration_ms')}ms")
    if req.get("redirect_count"):
        parts.append(f"redirects={req.get('redirect_count')}")
    header_names = req.get("response_header_names") or []
    if header_names:
        parts.append(f"headers=[{','.join(header_names[:8])}]")
    return " | ".join(parts)


def _fmt_live_session_diff_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, list):
        if not value:
            return "[]"
        if all(isinstance(v, dict) for v in value):
            parts = []
            for item in value[:5]:
                if not isinstance(item, dict):
                    continue
                bits = [f"{k}={item.get(k)!r}" for k in ("url", "status_code", "method", "with_credentials") if item.get(k) is not None]
                if bits:
                    parts.append("; ".join(bits))
            return " | ".join(parts) if parts else f"[{len(value)} request(s)]"
        return ", ".join(str(v) for v in value[:20])
    if isinstance(value, dict):
        return ", ".join(f"{k}={v!r}" for k, v in list(value.items())[:8])
    return str(value)


def _probe_live_session_comparison_section(
    live_session_comparison: dict[str, Any] | None,
    live_session_entry_urls: list[str],
) -> str:
    live_session_comparison = live_session_comparison or {}
    comparison = live_session_comparison.get("comparison") or {}
    lifecycle = live_session_comparison.get("lifecycle") or "idle"
    lc_colors = {
        "idle": ("#374151", "#f3f4f6"),
        "running": ("#92400e", "#fef3c7"),
        "done": ("#065f46", "#d1fae5"),
        "error": ("#991b1b", "#fee2e2"),
    }
    lc_fg, lc_bg = lc_colors.get(lifecycle, ("#374151", "#f3f4f6"))
    lifecycle_badge = (
        f'<span id="live-session-comparison-lifecycle" style="display:inline-block;padding:4px 10px;'
        f'border-radius:6px;font-size:12px;font-weight:600;color:{lc_fg};background:{lc_bg}">'
        f'{_he(lifecycle)}</span>'
    )
    default_entry = live_session_entry_urls[0] if live_session_entry_urls else ""
    entry_buttons = "".join(
        f'<button class="btn live-session-comparison-btn" data-entry-url="{_he(url)}" '
        f'style="font-size:11px;padding:6px 10px">Compare — {_he(url.split("//")[-1][:40])}</button>'
        for url in live_session_entry_urls
    )

    diagnostic = comparison.get("diagnostic_summary") or live_session_comparison.get("diagnostic_summary") or "—"
    field_diffs = comparison.get("field_diffs") or []
    logged_in = comparison.get("logged_in_tab") or {}
    if logged_in.get("found"):
        logged_in_status = f"found — {_he(logged_in.get('final_url') or '—')}"
    elif logged_in.get("network_trace_limitation") == "snapshot_failed":
        logged_in_status = "snapshot_failed"
    elif logged_in.get("network_trace_limitation") == "no_logged_in_amex_tab":
        logged_in_status = "not found"
    else:
        logged_in_status = _he(logged_in.get("network_trace_limitation") or "not found")

    diff_rows = ""
    for diff in field_diffs[:40]:
        if not isinstance(diff, dict):
            continue
        field = diff.get("field") or "unknown"
        left = _fmt_live_session_diff_value(diff.get("logged_in_tab"))
        right = _fmt_live_session_diff_value(diff.get("bootstrap_probe_tab"))
        diff_rows += (
            f'<tr style="background:#fff7ed">'
            f'<td style="font-size:11px;font-weight:600;vertical-align:top">{_he(field)}</td>'
            f'<td style="font-size:11px;word-break:break-word;vertical-align:top">{_he(left)}</td>'
            f'<td style="font-size:11px;word-break:break-word;vertical-align:top">{_he(right)}</td>'
            f"</tr>"
        )

    diff_rows_body = diff_rows or (
        '<tr><td colspan="3" class="muted" style="font-size:11px">'
        "No differences recorded yet.</td></tr>"
    )

    diff_table = (
        '<table style="width:100%;border-collapse:collapse;margin-top:12px">'
        '<thead><tr style="background:#f9fafb">'
        "<th style=\"font-size:11px;text-align:left;padding:6px\">Field</th>"
        "<th style=\"font-size:11px;text-align:left;padding:6px\">Logged-in tab</th>"
        "<th style=\"font-size:11px;text-align:left;padding:6px\">Bootstrap probe</th>"
        "</tr></thead><tbody>"
        f"{diff_rows_body}"
        "</tbody></table>"
    )

    return (
        '<div class="card" style="margin-bottom:16px">'
        "<h3>Amex Live Session Comparator</h3>"
        '<p class="muted" style="font-size:11px;margin:0 0 12px">'
        "Compare a known-good logged-in Amex tab against a fresh bootstrap probe tab. "
        "Collects metadata only — never cookie values, tokens, or response bodies.</p>"
        f'<p style="margin:0 0 12px">Lifecycle: {lifecycle_badge}'
        f' <span id="live-session-comparison-entry-label" class="muted" style="font-size:12px;margin-left:8px">'
        f'{_he(live_session_comparison.get("entry_url") or default_entry)}</span></p>'
        f'<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">{entry_buttons}</div>'
        f'<p style="font-size:11px;margin:0 0 12px"><strong>Diagnostic:</strong> {_he(diagnostic)}</p>'
        f'<p style="font-size:11px;margin:0 0 12px"><strong>Logged-in tab:</strong> {logged_in_status}</p>'
        "<h4 style=\"font-size:12px;margin:0 0 6px\">Differences only</h4>"
        f"{diff_table}"
        "</div>"
    )


def _probe_bootstrap_trace_section(
    bootstrap_traces: dict[str, dict[str, Any]],
    bootstrap_entry_urls: list[str],
) -> str:
    entry_buttons = "".join(
        f'<button class="btn bootstrap-trace-btn" data-entry-url="{_he(url)}" '
        f'style="font-size:11px;padding:6px 10px">Trace — {_he(url.split("//")[-1][:48])}</button>'
        for url in bootstrap_entry_urls
    )
    running = next(
        (t for t in bootstrap_traces.values() if t.get("lifecycle") == "running"),
        None,
    )
    lc = running.get("lifecycle") if running else "idle"
    lc_colors = {
        "idle": ("#374151", "#f3f4f6"),
        "running": ("#92400e", "#fef3c7"),
        "done": ("#065f46", "#d1fae5"),
        "error": ("#991b1b", "#fee2e2"),
    }
    lc_fg, lc_bg = lc_colors.get(lc, lc_colors["idle"])
    badge = (
        f'<span id="bootstrap-lifecycle-badge" style="display:inline-block;padding:4px 10px;'
        f'border-radius:6px;font-size:12px;font-weight:600;color:{lc_fg};background:{lc_bg}">'
        f'{_he(lc)}</span>'
    )

    trace_blocks = []
    for entry_url in bootstrap_entry_urls:
        state = bootstrap_traces.get(entry_url) or {}
        trace = state.get("trace") or {}
        nav = trace.get("navigation_timeline") or {}
        nav_events = nav.get("events") or []
        requests = trace.get("bootstrap_requests") or []
        nav_lines = [_fmt_bootstrap_nav_event(e) for e in nav_events[:15]]
        req_lines = [_fmt_bootstrap_request(r) for r in requests[:15]]
        first_401 = trace.get("first_401_url") or "—"
        first_401_ms = trace.get("first_401_at_ms")
        diagnostic = state.get("diagnostic_summary") or trace.get("diagnostic_summary") or "—"
        lifecycle_label = state.get("lifecycle") or "not run"
        trace_blocks.append(
            f'<div style="margin-top:12px;padding-top:12px;border-top:1px solid #e5e7eb">'
            f'<p style="font-size:11px;margin:0 0 6px"><strong>Entry:</strong> '
            f'<code>{_he(entry_url)}</code> '
            f'<span class="muted">({_he(lifecycle_label)})</span></p>'
            f'<dl style="display:grid;grid-template-columns:140px 1fr;gap:4px 10px;'
            f'font-size:11px;margin:0 0 8px">'
            f"<dt>final URL</dt><dd class=\"muted\" style=\"word-break:break-all\">"
            f"{_he(nav.get('final_url') or '—')}</dd>"
            f"<dt>first 401</dt><dd class=\"muted\" style=\"word-break:break-all\">"
            f"{_he(str(first_401_ms) + 'ms — ' + str(first_401) if first_401_ms else first_401)}</dd>"
            f"<dt>diagnostic</dt><dd>{_he(diagnostic)}</dd>"
            f"<dt>navigation</dt><dd><code style=\"font-size:10px;word-break:break-all\">"
            f"{_he('; '.join(nav_lines) or '—')}</code></dd>"
            f"<dt>bootstrap/session</dt><dd><code style=\"font-size:10px;word-break:break-all\">"
            f"{_he('; '.join(req_lines) or '—')}</code></dd>"
            f"</dl></div>"
        )

    trace_blocks_html = (
        "".join(trace_blocks)
        if trace_blocks
        else '<p class="muted" style="font-size:11px">No bootstrap traces yet.</p>'
    )

    return (
        '<div class="card" style="margin-bottom:16px">'
        "<h3>Amex Bootstrap Trace</h3>"
        '<p class="muted" style="font-size:12px;margin:0 0 12px">'
        "Diagnostic-only: opens one Amex tab at a chosen entry URL, observes 20 seconds of "
        "navigation and bootstrap/session network activity. No cookie values, tokens, or bodies.</p>"
        f'<p style="margin:0 0 12px">Lifecycle: {badge}'
        f' <span id="bootstrap-entry-label" class="muted" style="font-size:12px;margin-left:8px">'
        f'{_he((running or {}).get("entry_url") or "")}</span></p>'
        f'<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">{entry_buttons}</div>'
        f"{trace_blocks_html}"
        "</div>"
    )


def render_provider_access_probe_page(
    rows: list[dict[str, Any]],
    *,
    manual_state: dict[str, Any] | None = None,
    automatic_probes_disabled: bool = False,
    bootstrap_traces: dict[str, dict[str, Any]] | None = None,
    bootstrap_entry_urls: list[str] | None = None,
    live_session_entry_urls: list[str] | None = None,
    live_session_comparison: dict[str, Any] | None = None,
) -> str:
    manual_state = manual_state or {}
    bootstrap_traces = bootstrap_traces or {}
    bootstrap_entry_urls = bootstrap_entry_urls or []
    live_session_entry_urls = live_session_entry_urls or bootstrap_entry_urls
    live_session_comparison = live_session_comparison or {}
    lifecycle = manual_state.get("lifecycle") or "idle"
    lifecycle_colors = {
        "idle": ("#374151", "#f3f4f6"),
        "running": ("#92400e", "#fef3c7"),
        "done": ("#065f46", "#d1fae5"),
        "error": ("#991b1b", "#fee2e2"),
    }
    lc_fg, lc_bg = lifecycle_colors.get(lifecycle, ("#374151", "#f3f4f6"))
    lifecycle_badge = (
        f'<span id="probe-lifecycle-badge" style="display:inline-block;padding:4px 10px;'
        f'border-radius:6px;font-size:12px;font-weight:600;color:{lc_fg};background:{lc_bg}">'
        f'{_he(lifecycle)}</span>'
    )
    auto_note = (
        '<p class="muted" style="font-size:11px;margin:8px 0 0">'
        "Automatic probes are <strong>disabled</strong> in development/admin-test mode. "
        "Use Run Probe below.</p>"
        if automatic_probes_disabled
        else ""
    )
    run_controls = (
        '<div class="card" style="margin-bottom:16px">'
        "<h3>Manual probe runner</h3>"
        '<p class="muted" style="font-size:12px;margin:0 0 12px">'
        "Run exactly one provider at a time. Opens a single background tab, waits for page "
        "stability, classifies auth state, and records an immutable probe run.</p>"
        f'<p style="margin:0 0 12px">Lifecycle: {lifecycle_badge}'
        f' <span id="probe-lifecycle-provider" class="muted" style="font-size:12px;margin-left:8px">'
        f'{_he(manual_state.get("provider") or "")}</span></p>'
        '<div style="display:flex;gap:8px;flex-wrap:wrap">'
        '<button class="btn probe-run-btn" data-provider="amex" id="run-probe-amex">'
        "Run Probe — Amex</button>"
        '<button class="btn probe-run-btn" data-provider="delta" id="run-probe-delta">'
        "Run Probe — Delta</button>"
        "</div>"
        f"{auto_note}"
        "</div>"
    )
    script = """
<script>
(function () {
  const badge = document.getElementById('probe-lifecycle-badge');
  const providerEl = document.getElementById('probe-lifecycle-provider');
  const buttons = Array.from(document.querySelectorAll('.probe-run-btn'));
  let pollTimer = null;

  function setLifecycle(data) {
    const lc = data.lifecycle || 'idle';
    badge.textContent = lc;
    providerEl.textContent = data.provider ? ('(' + data.provider + ')') : '';
    const colors = {
      idle: ['#374151', '#f3f4f6'],
      running: ['#92400e', '#fef3c7'],
      done: ['#065f46', '#d1fae5'],
      error: ['#991b1b', '#fee2e2'],
    };
    const [fg, bg] = colors[lc] || colors.idle;
    badge.style.color = fg;
    badge.style.background = bg;
    const busy = lc === 'running';
    buttons.forEach(b => { b.disabled = busy; });
    if (busy && !pollTimer) {
      pollTimer = setInterval(refreshStatus, 2000);
    } else if (!busy && pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
      if (lc === 'done' || lc === 'error') {
        setTimeout(() => location.reload(), 800);
      }
    }
  }

  async function refreshStatus() {
    try {
      const r = await fetch('/api/admin/provider-access-probe/run-status');
      if (r.ok) setLifecycle(await r.json());
    } catch (e) {}
  }

  async function runProbe(provider) {
    buttons.forEach(b => { b.disabled = true; });
    badge.textContent = 'running';
    try {
      const r = await fetch('/api/admin/provider-access-probe/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider }),
      });
      const data = await r.json();
      if (!r.ok) {
        alert(data.error || 'Probe run failed');
        await refreshStatus();
        return;
      }
      setLifecycle(data);
      window.postMessage({
        type: '__mighty_dashboard__',
        action: 'run_manual_probe',
        provider: provider,
        manual_run_id: data.manual_run_id,
      }, '*');
      pollTimer = pollTimer || setInterval(refreshStatus, 2000);
    } catch (e) {
      alert('Probe run failed: ' + e.message);
      await refreshStatus();
    }
  }

  buttons.forEach(btn => {
    btn.addEventListener('click', () => runProbe(btn.dataset.provider));
  });

  const bootstrapBadge = document.getElementById('bootstrap-lifecycle-badge');
  const bootstrapEntryEl = document.getElementById('bootstrap-entry-label');
  const bootstrapButtons = Array.from(document.querySelectorAll('.bootstrap-trace-btn'));
  let bootstrapPollTimer = null;

  function setBootstrapLifecycle(data) {
    if (!bootstrapBadge) return;
    const lc = data.lifecycle || 'idle';
    bootstrapBadge.textContent = lc;
    if (bootstrapEntryEl) {
      bootstrapEntryEl.textContent = data.entry_url ? ('(' + data.entry_url + ')') : '';
    }
    const colors = {
      idle: ['#374151', '#f3f4f6'],
      running: ['#92400e', '#fef3c7'],
      done: ['#065f46', '#d1fae5'],
      error: ['#991b1b', '#fee2e2'],
    };
    const [fg, bg] = colors[lc] || colors.idle;
    bootstrapBadge.style.color = fg;
    bootstrapBadge.style.background = bg;
    const busy = lc === 'running';
    bootstrapButtons.forEach(b => { b.disabled = busy; });
    if (busy && !bootstrapPollTimer) {
      bootstrapPollTimer = setInterval(refreshBootstrapStatus, 2000);
    } else if (!busy && bootstrapPollTimer) {
      clearInterval(bootstrapPollTimer);
      bootstrapPollTimer = null;
      if (lc === 'done' || lc === 'error') {
        setTimeout(() => location.reload(), 800);
      }
    }
  }

  async function refreshBootstrapStatus() {
    try {
      const r = await fetch('/api/admin/provider-access-probe/bootstrap-trace-status');
      if (r.ok) setBootstrapLifecycle(await r.json());
    } catch (e) {}
  }

  async function runBootstrapTrace(entryUrl) {
    bootstrapButtons.forEach(b => { b.disabled = true; });
    if (bootstrapBadge) bootstrapBadge.textContent = 'running';
    try {
      const r = await fetch('/api/admin/provider-access-probe/bootstrap-trace', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entry_url: entryUrl }),
      });
      const data = await r.json();
      if (!r.ok) {
        alert(data.error || 'Bootstrap trace failed');
        await refreshBootstrapStatus();
        return;
      }
      setBootstrapLifecycle(data);
      window.postMessage({
        type: '__mighty_dashboard__',
        action: 'run_bootstrap_trace',
        entry_url: entryUrl,
        trace_run_id: data.trace_run_id,
      }, '*');
      bootstrapPollTimer = bootstrapPollTimer || setInterval(refreshBootstrapStatus, 2000);
    } catch (e) {
      alert('Bootstrap trace failed: ' + e.message);
      await refreshBootstrapStatus();
    }
  }

  bootstrapButtons.forEach(btn => {
    btn.addEventListener('click', () => runBootstrapTrace(btn.dataset.entryUrl));
  });

  const liveSessionBadge = document.getElementById('live-session-comparison-lifecycle');
  const liveSessionEntryEl = document.getElementById('live-session-comparison-entry-label');
  const liveSessionButtons = Array.from(document.querySelectorAll('.live-session-comparison-btn'));
  let liveSessionPollTimer = null;

  function setLiveSessionLifecycle(data) {
    if (!liveSessionBadge) return;
    const lc = data.lifecycle || 'idle';
    liveSessionBadge.textContent = lc;
    if (liveSessionEntryEl) {
      liveSessionEntryEl.textContent = data.entry_url ? ('(' + data.entry_url + ')') : '';
    }
    const colors = {
      idle: ['#374151', '#f3f4f6'],
      running: ['#92400e', '#fef3c7'],
      done: ['#065f46', '#d1fae5'],
      error: ['#991b1b', '#fee2e2'],
    };
    const [fg, bg] = colors[lc] || colors.idle;
    liveSessionBadge.style.color = fg;
    liveSessionBadge.style.background = bg;
    const busy = lc === 'running';
    liveSessionButtons.forEach(b => { b.disabled = busy; });
    if (busy && !liveSessionPollTimer) {
      liveSessionPollTimer = setInterval(refreshLiveSessionStatus, 2000);
    } else if (!busy && liveSessionPollTimer) {
      clearInterval(liveSessionPollTimer);
      liveSessionPollTimer = null;
      if (lc === 'done' || lc === 'error') {
        setTimeout(() => location.reload(), 800);
      }
    }
  }

  async function refreshLiveSessionStatus() {
    try {
      const r = await fetch('/api/admin/provider-access-probe/live-session-comparison-status');
      if (r.ok) setLiveSessionLifecycle(await r.json());
    } catch (e) {}
  }

  async function runLiveSessionComparison(entryUrl) {
    liveSessionButtons.forEach(b => { b.disabled = true; });
    if (liveSessionBadge) liveSessionBadge.textContent = 'running';
    try {
      const r = await fetch('/api/admin/provider-access-probe/live-session-comparison', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entry_url: entryUrl }),
      });
      const data = await r.json();
      if (!r.ok) {
        alert(data.error || 'Live session comparison failed');
        await refreshLiveSessionStatus();
        return;
      }
      setLiveSessionLifecycle(data);
      window.postMessage({
        type: '__mighty_dashboard__',
        action: 'run_live_session_comparison',
        entry_url: entryUrl,
        comparison_run_id: data.comparison_run_id,
      }, '*');
      liveSessionPollTimer = liveSessionPollTimer || setInterval(refreshLiveSessionStatus, 2000);
    } catch (e) {
      alert('Live session comparison failed: ' + e.message);
      await refreshLiveSessionStatus();
    }
  }

  liveSessionButtons.forEach(btn => {
    btn.addEventListener('click', () => runLiveSessionComparison(btn.dataset.entryUrl));
  });

  refreshStatus();
  refreshBootstrapStatus();
  refreshLiveSessionStatus();
})();
</script>"""

    table = "".join(
        f"<tr>"
        f"<td><strong>{_he(r.get('provider', ''))}</strong></td>"
        f"<td>{_probe_status_badge(r.get('status') or 'not_started')}</td>"
        f"<td>{_auth_state_badge(r.get('auth_state'))}</td>"
        f"<td class=\"muted\" style=\"font-size:11px;max-width:200px;word-break:break-all\">"
        f"{_he(r.get('final_url') or r.get('url_visited') or '—')}</td>"
        f"<td class=\"muted\" style=\"font-size:11px;max-width:160px\">"
        f"{_he(r.get('page_title') or '—')}</td>"
        f"<td style=\"font-size:11px\">{_he(_probe_form_signals(r))}</td>"
        f"<td class=\"muted\" style=\"font-size:11px;max-width:220px\">"
        f"{_he(_probe_matched_rules(r))}</td>"
        f"<td class=\"muted\" style=\"font-size:11px;max-width:220px\">"
        f"{_he((r.get('evidence_snippet') or '—')[:120])}</td>"
        f"<td>{_fmt_iso(r.get('probed_at') or r.get('timestamp'))}</td>"
        f"<td class=\"muted\">{_he(r.get('failure_reason') or '—')}</td>"
        f"<td class=\"muted\" style=\"font-size:11px;max-width:320px;word-break:break-word\">"
        f"{_he(_probe_page_diagnostics(r))}</td>"
        f"</tr>"
        for r in rows
    ) or '<tr><td colspan="11" class="muted">No probe runs yet</td></tr>'

    body = (
        '<p class="lede">Phase 1 account reliability diagnostic. Probes verify whether the '
        "extension can open each provider, detect login state, and capture at least one piece "
        "of private account-specific evidence. Does not drive user-facing account status.</p>"
        f"{timezone_note_html()}"
        '<p class="muted" style="font-size:11px">JSON API: '
        '<code>/api/admin/provider-access-probe</code></p>'
        f"{run_controls}"
        f"{_probe_live_session_comparison_section(live_session_comparison, live_session_entry_urls)}"
        f"{_probe_bootstrap_trace_section(bootstrap_traces, bootstrap_entry_urls)}"
        f"{_probe_deep_inspect_section(rows)}"
        '<div class="card"><table><thead><tr>'
        "<th>Provider</th><th>Status</th><th>Auth state</th><th>Final URL</th>"
        "<th>Page title</th><th>Form signals</th><th>Matched rules</th>"
        "<th>Evidence snippet</th><th>Probed at</th><th>Failure reason</th>"
        "<th>Page diagnostics</th>"
        f"</tr></thead><tbody>{table}</tbody></table></div>"
        f"{script}"
    )
    return _admin_shell("provider-access-probe", "Provider Access Probe", body)


def _current_access_badge(access_label: str, *, current_access: str) -> str:
    colors = {
        "connected_now": ("#065f46", "#d1fae5"),
        "signed_out": ("#991b1b", "#fee2e2"),
        "checking": ("#1e3a5f", "#dbeafe"),
        "unknown": ("#374151", "#f3f4f6"),
        "error": ("#9a3412", "#ffedd5"),
    }
    fg, bg = colors.get(current_access, colors["unknown"])
    return (
        f'<span style="display:inline-block;padding:3px 10px;border-radius:6px;'
        f'font-size:12px;font-weight:700;color:{fg};background:{bg}">{_he(access_label)}</span>'
    )


def _cached_data_badge(cached_label: str, *, cached_data_state: str) -> str:
    colors = {
        "fresh": ("#065f46", "#d1fae5"),
        "stale": ("#92400e", "#fef3c7"),
        "none": ("#374151", "#f3f4f6"),
    }
    fg, bg = colors.get(cached_data_state, colors["none"])
    return (
        f'<span style="display:inline-block;padding:3px 10px;border-radius:6px;'
        f'font-size:12px;font-weight:700;color:{fg};background:{bg}">{_he(cached_label)}</span>'
    )


def _current_access_source_detail(
    evidence: str,
    source_label: str,
    source_internal: str | None,
) -> str:
    if not source_internal or source_internal == source_label:
        return ""
    return (
        f'<details style="margin-top:4px">'
        f'<summary class="muted" style="font-size:10px;cursor:pointer">Technical details</summary>'
        f'<div class="muted" style="font-size:10px;margin-top:2px">{_he(evidence)}</div>'
        f'<div class="muted" style="font-size:10px;margin-top:2px">{_he(source_label)}</div>'
        f"</details>"
    )


def render_login_truth_page(rows: list[Any]) -> str:
    from mighty.login_truth import (
        current_account_access_summary,
        format_current_account_access_display_row,
        sort_current_account_access_rows,
    )

    sorted_rows = sort_current_account_access_rows(rows)
    summary = current_account_access_summary(sorted_rows)
    display_rows = [format_current_account_access_display_row(row) for row in sorted_rows]

    def _verification_note(r: Any) -> str:
        if r.current_access != "checking":
            return ""
        lifecycle = r.verification_lifecycle or "requested"
        return (
            f'<div class="muted" style="font-size:11px;margin-top:4px">'
            f"Verification status: {_he(lifecycle.replace('_', ' '))}</div>"
        )

    table = "".join(
        f"<tr>"
        f"<td><strong>{_he(r.provider)}</strong></td>"
        f"<td>{_current_access_badge(r.current_access_label, current_access=r.current_access)}"
        f"{_verification_note(r)}"
        f"{_current_access_source_detail(r.evidence, r.source_label, r.source_internal)}</td>"
        f"<td>{_cached_data_badge(r.cached_data_label, cached_data_state=r.cached_data_state)}</td>"
        f"<td class='muted'>{_fmt_iso(r.last_verified)}</td>"
        f"<td>{_he(r.next_action_text)}</td>"
        f"</tr>"
        for r in display_rows
    ) or '<tr><td colspan="5" class="muted">No providers configured</td></tr>'

    summary_card = (
        '<div class="card" style="margin-bottom:16px">'
        '<div style="display:flex;gap:24px;flex-wrap:wrap">'
        f'<div><div class="muted" style="font-size:11px">Connected now</div>'
        f'<div style="font-size:24px;font-weight:700;color:#6ee7b7">{summary["connected_now"]}</div></div>'
        f'<div><div class="muted" style="font-size:11px">Checking</div>'
        f'<div style="font-size:24px;font-weight:700;color:#93c5fd">{summary["checking"]}</div></div>'
        f'<div><div class="muted" style="font-size:11px">Signed out</div>'
        f'<div style="font-size:24px;font-weight:700;color:#fca5a5">{summary["signed_out"]}</div></div>'
        f'<div><div class="muted" style="font-size:11px">Unknown</div>'
        f'<div style="font-size:24px;font-weight:700;color:#9ca3af">{summary["unknown"]}</div></div>'
        "</div></div>"
    )

    body = (
        '<p class="lede">Current Access means recently verified session evidence '
        "within a short live-session freshness window — not historical connected "
        "state. Cached Data is independent: Mighty may still hold a fresh Membership "
        "Rewards balance even when the user is signed out or access is being checked.</p>"
        f"{timezone_note_html()}"
        f"{summary_card}"
        '<div class="card"><table><thead><tr>'
        "<th>Provider</th><th>Current access</th><th>Cached data</th>"
        "<th>Last verified</th><th>Next action</th>"
        f"</tr></thead><tbody>{table}</tbody></table></div>"
        '<p class="muted" style="font-size:12px;margin-top:16px">'
        "<strong>Why this matters:</strong> Current Access and Cached Data answer different "
        "questions. Stale connected evidence is re-verified automatically; it is never "
        "shown as Connected now indefinitely.</p>"
    )
    return _admin_shell("login-truth", "Current Account Access", body)


def _session_result_badge(result: str, *, category: str) -> str:
    if category == "cached_data":
        return (
            '<span style="display:inline-block;padding:3px 10px;border-radius:6px;'
            'font-size:12px;font-weight:700;color:#1e3a5f;background:#dbeafe">'
            "Cached data</span>"
        )
    if category == "legacy":
        from mighty.login_truth import format_session_evidence_result_label

        label = format_session_evidence_result_label(result, category="legacy")  # type: ignore[arg-type]
        return (
            '<span style="display:inline-block;padding:3px 10px;border-radius:6px;'
            'font-size:12px;font-weight:600;color:#6b7280;background:#e5e7eb">'
            f"{_he(label)}</span>"
        )
    colors = {
        "connected": ("#065f46", "#d1fae5"),
        "signed_out": ("#7f1d1d", "#fee2e2"),
        "unknown": ("#374151", "#f3f4f6"),
        "error": ("#7f1d1d", "#fecaca"),
    }
    from mighty.login_truth import format_session_evidence_result_label

    label = format_session_evidence_result_label(result, category=category)  # type: ignore[arg-type]
    fg, bg = colors.get(result, colors["unknown"])
    return (
        f'<span style="display:inline-block;padding:3px 10px;border-radius:6px;'
        f'font-size:12px;font-weight:700;color:{fg};background:{bg}">{_he(label)}</span>'
    )


def _evidence_precedence_card() -> str:
    return """
<div class="card" style="margin-bottom:16px">
  <div class="muted" style="font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:8px">
    Evidence precedence
  </div>
  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;font-size:13px">
    <div>
      <div style="font-weight:700;margin-bottom:4px;color:#6ee7b7">High</div>
      <ul style="margin:0;padding-left:18px;color:#d1d5db">
        <li>verified session</li>
        <li>session API 200</li>
        <li>session API 401</li>
        <li>explicit login page</li>
      </ul>
    </div>
    <div>
      <div style="font-weight:700;margin-bottom:4px;color:#93c5fd">Medium</div>
      <ul style="margin:0;padding-left:18px;color:#d1d5db">
        <li>authenticated page</li>
      </ul>
    </div>
    <div>
      <div style="font-weight:700;margin-bottom:4px;color:#fcd34d">Low</div>
      <ul style="margin:0;padding-left:18px;color:#d1d5db">
        <li>cached private data</li>
      </ul>
    </div>
    <div>
      <div style="font-weight:700;margin-bottom:4px;color:#9ca3af">Legacy</div>
      <ul style="margin:0;padding-left:18px;color:#9ca3af">
        <li>connection_status</li>
        <li>sync_status</li>
      </ul>
    </div>
  </div>
</div>
"""


def _session_evidence_filters(
    *,
    providers: list[str],
    selected_provider: str | None,
    include_cached_data: bool,
    include_legacy: bool,
) -> str:
    provider_options = '<option value="">All providers</option>' + "".join(
        f'<option value="{_he(p)}"{" selected" if p == selected_provider else ""}>{_he(p)}</option>'
        for p in providers
    )
    cached_checked = " checked" if include_cached_data else ""
    legacy_checked = " checked" if include_legacy else ""
    return f"""
<form class="source-picker" method="get" action="/admin/session-evidence" style="flex-wrap:wrap;align-items:center">
  <label>Provider</label>
  <select name="provider" onchange="this.form.submit()">{provider_options}</select>
  <label style="display:inline-flex;align-items:center;gap:6px;cursor:pointer">
    <input type="checkbox" name="include_cached" value="1"{cached_checked}
           onchange="this.form.submit()" />
    Include cached data
  </label>
  <label style="display:inline-flex;align-items:center;gap:6px;cursor:pointer;color:#9ca3af">
    <input type="checkbox" name="include_legacy" value="1"{legacy_checked}
           onchange="this.form.submit()" />
    Show legacy compatibility events
  </label>
</form>
"""


def _render_winner_explanation(section: Any) -> str:
    explanation = getattr(section, "winner_explanation", None)
    if explanation is None:
        from mighty.login_truth import format_current_winner_line

        return (
            f'<p style="font-size:13px;color:#e5e7eb;margin:0 0 12px">'
            f"{_he(format_current_winner_line(section))}</p>"
        )

    reason_body = ""
    if explanation.evidence_type:
        raw_observed = None
        current = getattr(section, "current", None)
        if current is not None:
            raw_observed = getattr(current, "observed_at", None)
        observed_html = (
            _fmt_iso(raw_observed)
            if raw_observed
            else _fmt_iso(explanation.observed_at)
        )
        reason_body = (
            f'<div style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'
            f'font-size:13px;color:#e5e7eb">{_he(explanation.evidence_type)}</div>'
            f'<div class="muted" style="font-size:12px;margin-top:2px">'
            f"{observed_html}</div>"
        )
    else:
        reason_body = (
            f'<div class="muted" style="font-size:13px">'
            f"{_he(explanation.reason_headline)}</div>"
        )

    ignored_html = ""
    if explanation.ignored:
        items = "".join(
            f'<div style="margin-bottom:10px">'
            f'<div style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'
            f'font-size:12px;color:#9ca3af">{_he(item.label)}</div>'
            f'<div class="muted" style="font-size:11px;margin-top:2px">'
            f"Ignored because: {_he(item.reason)}</div>"
            f"</div>"
            for item in explanation.ignored
        )
        ignored_html = (
            '<div style="margin-top:12px;padding-top:12px;border-top:1px solid #374151">'
            '<div class="muted" style="font-size:11px;text-transform:uppercase;'
            'letter-spacing:.04em;margin-bottom:8px">Ignored evidence</div>'
            f"{items}</div>"
        )

    reason_header = (
        f'<div class="muted" style="font-size:11px;margin-bottom:4px">'
        f"Reason</div>"
        f'<div style="font-size:12px;color:#d1d5db;margin-bottom:4px">'
        f"{_he(explanation.reason_headline) if explanation.evidence_type else ''}</div>"
    )
    if not explanation.evidence_type:
        reason_header = (
            '<div class="muted" style="font-size:11px;margin-bottom:4px">Reason</div>'
        )

    return (
        '<div style="margin-bottom:14px;padding:12px 14px;border:1px solid #374151;'
        'border-radius:8px;background:#111827">'
        '<div class="muted" style="font-size:11px;text-transform:uppercase;'
        'letter-spacing:.04em;margin-bottom:6px">Current winner</div>'
        f'<div style="font-size:18px;font-weight:700;color:#f9fafb;margin-bottom:10px">'
        f"{_he(explanation.state_label)}</div>"
        f"{reason_header}{reason_body}{ignored_html}"
        "</div>"
    )


def render_session_evidence_timeline_page(
    sections: list[Any],
    *,
    providers: list[str],
    selected_provider: str | None = None,
    include_cached_data: bool = False,
    include_legacy: bool = False,
) -> str:
    from mighty.login_truth import friendly_source_label

    filters = _session_evidence_filters(
        providers=providers,
        selected_provider=selected_provider,
        include_cached_data=include_cached_data,
        include_legacy=include_legacy,
    )

    body = (
        '<p class="lede">Session evidence explains why Mighty currently treats a provider as '
        "connected, signed out, or unknown. Canonical session evidence determines Current Access. "
        "Cached data and legacy compatibility signals are optional and never count as login proof.</p>"
        f"{timezone_note_html()}"
        f"{_evidence_precedence_card()}"
        f"{filters}"
    )

    if not sections:
        body += '<p class="muted">No providers to show.</p>'
        return _admin_shell("session-evidence", "Session Evidence Timeline", body)

    for section in sections:
        current = section.current
        winner_block = _render_winner_explanation(section)

        if current is None:
            current_table = (
                '<p class="muted">No provider_session_state row yet.</p>'
            )
        else:
            source_label, _internal = friendly_source_label(current.source)
            current_table = (
                "<table><thead><tr>"
                "<th>Provider</th><th>State</th><th>Evidence type</th>"
                "<th>Evidence summary</th><th>Observed at</th><th>Source</th><th>Confidence</th>"
                "</tr></thead><tbody><tr>"
                f"<td><strong>{_he(current.provider)}</strong></td>"
                f"<td>{_session_result_badge(current.state, category='session')}</td>"
                f"<td>{_he(current.evidence_type)}</td>"
                f"<td>{_he(current.evidence_summary)}</td>"
                f"<td class='muted'>{_fmt_iso(current.observed_at)}</td>"
                f"<td>{_he(source_label)}</td>"
                f"<td>{_he(current.confidence)}</td>"
                "</tr></tbody></table>"
            )

        def _event_row(ev: Any) -> str:
            row_style = ""
            type_badge = ""
            if ev.category == "cached_data":
                type_badge = ' <span class="badge badge-muted">cached</span>'
            elif ev.category == "legacy":
                type_badge = ' <span class="badge badge-muted">legacy</span>'
                row_style = ' style="opacity:0.55;color:#9ca3af"'
            return (
                f"<tr{row_style}>"
                f"<td class='muted'>{_fmt_iso(ev.observed_at.isoformat())}</td>"
                f"<td>{_he(ev.provider)}</td>"
                f"<td>{_he(ev.evidence_type)}{type_badge}</td>"
                f"<td>{_session_result_badge(ev.result, category=ev.category)}</td>"
                f"<td>{_he(friendly_source_label(ev.source)[0])}</td>"
                f"<td>{_he(ev.summary)}</td>"
                "</tr>"
            )

        event_rows = "".join(
            _event_row(ev) for ev in section.events
        ) or '<tr><td colspan="6" class="muted">No evidence events</td></tr>'

        body += (
            f'<div class="card">'
            f"<h3 style=\"text-transform:capitalize;margin:0 0 8px\">{_he(section.provider)}</h3>"
            f"{winner_block}"
            f'<div style="margin-bottom:14px"><div class="muted" style="font-size:11px;'
            f'text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px">Current state</div>'
            f"{current_table}</div>"
            f'<div class="muted" style="font-size:11px;text-transform:uppercase;'
            f'letter-spacing:.04em;margin-bottom:6px">Evidence timeline</div>'
            f"<table><thead><tr>"
            "<th>Time</th><th>Provider</th><th>Evidence type</th>"
            "<th>Result</th><th>Source</th><th>Summary</th>"
            f"</tr></thead><tbody>{event_rows}</tbody></table>"
            f"</div>"
        )

    return _admin_shell("session-evidence", "Session Evidence Timeline", body)
