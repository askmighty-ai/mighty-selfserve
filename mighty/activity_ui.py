"""Activity V1 UI — Home V1B-aligned chronological timeline."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

from mighty.activity_projection import (
    CATEGORY_COULD_NOT_COMPLETE,
    ActivityItem,
    ActivityProjection,
)

ACTIVITY_CSS = """
.activity-page{flex:1;overflow-y:auto;padding:28px 32px 64px;background:#eee9e2}
.activity-shell{max-width:640px;margin:0 auto}
.activity-header{margin:0 0 32px}
.activity-kicker{margin:0;font-size:13px;font-weight:400;color:#a8a29e;letter-spacing:0}
.activity-title{margin:8px 0 0;font-size:40px;font-weight:650;color:#1c1917;letter-spacing:-0.045em;line-height:1.12}
.activity-subtitle{margin:10px 0 0;font-size:16px;color:#78716c;line-height:1.55;max-width:34ch}
.activity-day{margin:0 0 32px}
.activity-day-label{margin:0 0 14px;font-size:11px;font-weight:600;color:#d6d3d1;text-transform:uppercase;letter-spacing:0.07em}
.activity-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:16px}
.activity-item{background:#fff;border:0.5px solid rgba(0,0,0,0.07);border-radius:12px;padding:18px 18px;box-shadow:0 1px 1px rgba(0,0,0,0.02)}
.activity-item[data-category="needs_approval"]{border-color:rgba(180,83,9,0.28);border-left:3px solid #b45309;background:linear-gradient(180deg,#fffbeb 0%,#ffffff 62%);box-shadow:0 1px 2px rgba(28,25,23,0.04)}
.activity-item[data-category="needs_approval"] .activity-item-title{font-weight:600;color:#1c1917}
.activity-item[data-category="completed"],
.activity-item[data-category="in_progress"],
.activity-item[data-category="could_not_complete"]{border-color:rgba(0,0,0,0.06)}
.activity-item-top{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.activity-item-title{margin:0;font-size:16px;font-weight:550;color:#1c1917;letter-spacing:-0.02em;line-height:1.35}
.activity-item-meta{margin:6px 0 0;font-size:13px;color:#a8a29e;line-height:1.4}
.activity-item-meta span+span::before{content:"·";margin:0 7px;color:#d6d3d1}
.activity-status{flex-shrink:0;font-size:11px;font-weight:600;color:#78716c;background:#f5f5f4;border-radius:999px;padding:4px 10px;white-space:nowrap}
.activity-status[data-category="needs_approval"]{color:#9a3412;background:#ffedd5}
.activity-status[data-category="completed"]{color:#57534e;background:#f5f5f4}
.activity-status[data-category="could_not_complete"]{color:#78716c;background:#f5f5f4}
.activity-status[data-category="in_progress"]{color:#57534e;background:#f5f5f4}
.activity-item-body{margin:10px 0 0;font-size:15px;color:#78716c;line-height:1.55;max-width:42ch}
.activity-actions{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-top:16px}
.activity-btn{appearance:none;border:none;border-radius:10px;padding:10px 16px;font-size:13px;font-weight:600;font-family:inherit;cursor:pointer}
.activity-btn-approve{background:#1c1917;color:#fff;padding:12px 22px;font-size:14px;font-weight:650;box-shadow:0 1px 2px rgba(28,25,23,0.2)}
.activity-btn-approve:hover{background:#292524}
.activity-btn-deny{background:#fff;color:#57534e;border:0.5px solid rgba(0,0,0,0.12)}
.activity-btn-deny:hover{background:#fafaf9}
.activity-details{margin-top:14px;border-top:0.5px solid rgba(0,0,0,0.05);padding-top:10px}
.activity-details summary{cursor:pointer;font-size:13px;font-weight:500;color:#a8a29e;list-style:none}
.activity-details summary::-webkit-details-marker{display:none}
.activity-details summary:hover{color:#78716c}
.activity-details[open] summary{color:#78716c;margin-bottom:10px}
.activity-detail-grid{display:flex;flex-direction:column;gap:8px}
.activity-detail-row{display:grid;grid-template-columns:120px minmax(0,1fr);gap:10px;font-size:13px;line-height:1.45}
.activity-detail-key{color:#a8a29e}
.activity-detail-val{color:#44403c}
.activity-attempt{margin-top:8px;padding:10px 12px;background:#fafaf9;border-radius:8px}
.activity-attempt-title{margin:0 0 4px;font-size:12px;font-weight:600;color:#78716c}
.activity-empty{padding:56px 8px 28px;max-width:34ch}
.activity-empty-title{margin:0;font-size:22px;font-weight:600;color:#1c1917;letter-spacing:-0.03em}
.activity-empty-body{margin:12px 0 0;font-size:15px;color:#a8a29e;line-height:1.6}
.activity-error{margin:0 0 18px;padding:12px 14px;border-radius:10px;background:#fff7ed;color:#9a3412;font-size:13px}
.activity-load-more{margin:8px 0 0;display:flex;justify-content:center}
.activity-load-more button{appearance:none;border:0.5px solid rgba(0,0,0,0.12);background:#fff;border-radius:10px;padding:10px 16px;font-size:13px;font-weight:600;color:#57534e;cursor:pointer;font-family:inherit}
.activity-load-more button:hover{background:#fafaf9}
.activity-loading{font-size:13px;color:#a8a29e;padding:8px 0}
@media(max-width:640px){
.activity-page{padding:20px 16px 48px}
.activity-title{font-size:32px}
.activity-detail-row{grid-template-columns:1fr}
}
"""


def render_activity_main(
    projection: ActivityProjection,
    *,
    escape: Callable[[Any], str],
    csrf_token: str,
    error: str | None = None,
) -> str:
    """Render Activity main column (no chrome)."""
    error_html = ""
    if error:
        error_html = f'<div class="activity-error" role="alert">{escape(error)}</div>'

    if not projection.items and not projection.next_cursor:
        body = (
            f'<div class="activity-empty">'
            f'<h2 class="activity-empty-title">All quiet</h2>'
            f'<p class="activity-empty-body">'
            f"Approvals and completed work will show up here."
            f"</p></div>"
        )
    else:
        body = _render_groups(projection.items, escape=escape)
        if projection.next_cursor:
            body += (
                f'<div class="activity-load-more">'
                f'<button type="button" id="activity-load-more" '
                f'data-cursor="{escape(projection.next_cursor)}">Load more</button>'
                f'</div>'
                f'<div class="activity-loading" id="activity-loading" hidden>Loading…</div>'
            )

    return (
        f'<div class="activity-page">'
        f'<div class="activity-shell">'
        f'{error_html}'
        f'<header class="activity-header">'
        f'<p class="activity-kicker">Activity</p>'
        f'<h1 class="activity-title">Activity</h1>'
        f'<p class="activity-subtitle">Approvals and completed work.</p>'
        f'</header>'
        f'<div id="activity-timeline" data-csrf="{escape(csrf_token)}">'
        f"{body}"
        f"</div>"
        f"</div></div>"
        f"{_activity_script()}"
    )


def _render_groups(items: tuple[ActivityItem, ...] | list[ActivityItem], *, escape: Callable[[Any], str]) -> str:
    groups: dict[str, list[ActivityItem]] = defaultdict(list)
    order: list[str] = []
    for item in items:
        label = _date_group_label(item.occurred_at)
        if label not in groups:
            order.append(label)
        groups[label].append(item)
    parts: list[str] = []
    for label in order:
        rows = "".join(_render_item(i, escape=escape) for i in groups[label])
        parts.append(
            f'<section class="activity-day">'
            f'<h2 class="activity-day-label">{escape(label)}</h2>'
            f'<ul class="activity-list">{rows}</ul>'
            f"</section>"
        )
    return "".join(parts)


def _render_item(item: ActivityItem, *, escape: Callable[[Any], str]) -> str:
    meta_bits = [escape(_rel_or_time(item.occurred_at))]
    if item.provider_display_name:
        meta_bits.append(escape(item.provider_display_name))
    meta = "".join(f"<span>{b}</span>" for b in meta_bits)

    actions = ""
    if item.user_action == "approve_deny":
        aid = escape(item.action_id)
        actions = (
            f'<div class="activity-actions">'
            f'<button type="button" class="activity-btn activity-btn-approve" '
            f"data-decide=\"{aid}\" data-decision=\"approve\">Approve</button>"
            f'<button type="button" class="activity-btn activity-btn-deny" '
            f"data-decide=\"{aid}\" data-decision=\"deny\">Deny</button>"
            f"</div>"
        )

    details = _render_details(item, escape=escape)
    return (
        f'<li class="activity-item" data-category="{escape(item.category)}" '
        f'data-activity-id="{escape(item.activity_id)}">'
        f'<div class="activity-item-top">'
        f"<div>"
        f'<h3 class="activity-item-title">{escape(item.title)}</h3>'
        f'<div class="activity-item-meta">{meta}</div>'
        f"</div>"
        f'<span class="activity-status" data-category="{escape(item.category)}">'
        f"{escape(item.status_label)}</span>"
        f"</div>"
        f'<p class="activity-item-body">{escape(item.explanation)}</p>'
        f"{actions}"
        f"{details}"
        f"</li>"
    )


def _render_details(item: ActivityItem, *, escape: Callable[[Any], str]) -> str:
    d = item.detail
    rows: list[str] = []
    rows.append(_detail_row("Attempted", d.attempted, escape))
    rows.append(_detail_row("What happened", d.happened, escape))
    if d.why:
        rows.append(_detail_row("Why", d.why, escape))
    if d.provider_display_name:
        rows.append(_detail_row("Account", d.provider_display_name, escape))
    rows.append(_detail_row("Requested", _fmt_time(d.requested_at), escape))
    if d.decided_at:
        rows.append(_detail_row("Decided", _fmt_time(d.decided_at), escape))
    if d.outcome_detail and item.category == CATEGORY_COULD_NOT_COMPLETE:
        # Precise underlying outcome without implying system failure for deny/cancel
        rows.append(_detail_row("Outcome", d.outcome_detail, escape))
    for key, value in d.fields:
        rows.append(_detail_row(key, value, escape))

    attempts = ""
    if d.receipt_history:
        blocks = []
        for summary in d.receipt_history:
            why = f'<div class="activity-detail-val">{escape(summary.why)}</div>' if summary.why else ""
            auth = (
                f'<div class="activity-detail-val">{escape(summary.authorization)}</div>'
                if summary.authorization
                else ""
            )
            blocks.append(
                f'<div class="activity-attempt">'
                f'<p class="activity-attempt-title">Attempt {int(summary.attempt)}</p>'
                f'<div class="activity-detail-val">{escape(summary.happened)}'
                f" · {escape(_fmt_time(summary.occurred_at))}</div>"
                f"{auth}{why}"
                f"</div>"
            )
        attempts = "".join(blocks)

    return (
        f'<details class="activity-details">'
        f"<summary>View details</summary>"
        f'<div class="activity-detail-grid">{"".join(rows)}{attempts}</div>'
        f"</details>"
    )


def _detail_row(key: str, value: str, escape: Callable[[Any], str]) -> str:
    return (
        f'<div class="activity-detail-row">'
        f'<div class="activity-detail-key">{escape(key)}</div>'
        f'<div class="activity-detail-val">{escape(value)}</div>'
        f"</div>"
    )


def _date_group_label(iso_str: str) -> str:
    dt = _parse(iso_str)
    if dt is None:
        return "Earlier"
    now = datetime.now(timezone.utc)
    local_today = now.astimezone().date()
    day = dt.astimezone().date()
    delta = (local_today - day).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    return dt.astimezone().strftime("%b %-d, %Y")


def _rel_or_time(iso_str: str) -> str:
    dt = _parse(iso_str)
    if dt is None:
        return iso_str or ""
    now = datetime.now(timezone.utc)
    seconds = int((now - dt).total_seconds())
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return _fmt_time(iso_str)


def _fmt_time(iso_str: str | None) -> str:
    dt = _parse(iso_str or "")
    if dt is None:
        return iso_str or "—"
    local = dt.astimezone()
    return local.strftime("%-I:%M %p · %b %-d").lstrip("0")


def _parse(iso_str: str) -> datetime | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _activity_script() -> str:
    return """
<script>
(function(){
  var root = document.getElementById('activity-timeline');
  if(!root) return;
  var csrf = root.getAttribute('data-csrf') || '';

  function toast(msg){
    var el = document.getElementById('mighty-toast');
    if(!el){
      el = document.createElement('div');
      el.id = 'mighty-toast';
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.className = 'show';
    setTimeout(function(){ el.className = 'hide'; }, 1800);
  }

  root.addEventListener('click', function(ev){
    var btn = ev.target.closest('[data-decide]');
    if(!btn) return;
    var id = btn.getAttribute('data-decide');
    var decision = btn.getAttribute('data-decision');
    btn.disabled = true;
    fetch('/dashboard/decide/' + encodeURIComponent(id), {
      method: 'POST',
      headers: {'Content-Type':'application/json','X-CSRF-Token': csrf},
      body: JSON.stringify({decision: decision})
    }).then(function(r){ return r.json().then(function(d){ return {ok:r.ok, d:d}; }); })
      .then(function(res){
        if(res.ok){
          toast(decision === 'approve' ? 'Approved' : 'Denied');
          window.location.reload();
        } else {
          btn.disabled = false;
          toast((res.d && res.d.error) || 'Could not save decision');
        }
      }).catch(function(){
        btn.disabled = false;
        toast('Could not save decision');
      });
  });

  var more = document.getElementById('activity-load-more');
  if(more){
    more.addEventListener('click', function(){
      var cursor = more.getAttribute('data-cursor') || '';
      window.location.href = '/activity?cursor=' + encodeURIComponent(cursor);
    });
  }
})();
</script>
"""
