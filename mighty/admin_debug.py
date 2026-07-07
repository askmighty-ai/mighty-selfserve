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
        "provider-access-probe",
        "Provider Access Probe",
        "Latest account access probe per provider (Phase 1 reliability)",
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
            f'<p class="muted">Started: {_he(started_display)} · Finished: {_he(finished_display)}'
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


def render_provider_access_probe_page(
    rows: list[dict[str, Any]],
    *,
    manual_state: dict[str, Any] | None = None,
    automatic_probes_disabled: bool = False,
) -> str:
    manual_state = manual_state or {}
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
  refreshStatus();
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
        '<p class="muted" style="font-size:11px">JSON API: '
        '<code>/api/admin/provider-access-probe</code></p>'
        f"{run_controls}"
        '<div class="card"><table><thead><tr>'
        "<th>Provider</th><th>Status</th><th>Auth state</th><th>Final URL</th>"
        "<th>Page title</th><th>Form signals</th><th>Matched rules</th>"
        "<th>Evidence snippet</th><th>Probed at</th><th>Failure reason</th>"
        "<th>Page diagnostics</th>"
        f"</tr></thead><tbody>{table}</tbody></table></div>"
        f"{script}"
    )
    return _admin_shell("provider-access-probe", "Provider Access Probe", body)
