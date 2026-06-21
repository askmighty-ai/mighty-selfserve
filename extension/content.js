// Mighty content script — contextual benefit surfacing

let mightyPill     = null;
let mightyPanel    = null;
let hideTimer      = null;
let mightyDashUrl  = null;  // set from background message

function removePill() {
  if (mightyPill)  { mightyPill.remove();  mightyPill  = null; }
  if (mightyPanel) { mightyPanel.remove(); mightyPanel = null; }
  if (hideTimer)   { clearTimeout(hideTimer); hideTimer = null; }
}

function esc(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// Activity label used in panel header
const ACTIVITY_LABELS = {
  flight:   'You\'re viewing flights',
  hotel:    'You\'re viewing hotels',
  car:      'You\'re renting a car',
  shopping: 'You\'re shopping',
  dining:   'You\'re browsing dining',
};

// Confidence color
function confColor(conf) {
  if (!conf || conf === 'high')   return '#6b7280';  // subtle — high conf is the norm
  if (conf === 'medium')          return '#d97706';  // amber
  return '#dc2626';                                   // red for low
}

// Evidence row — visible, always shown
function evidenceHtml(item) {
  var parts = [];
  if (item.derived && item.account) {
    parts.push('Via your <strong>' + esc(item.account) + '</strong>');
  } else if (item.account) {
    parts.push('Found in your <strong>' + esc(item.account) + '</strong>');
  }
  if (item.synced_ago) {
    var stale = item.synced_ago.includes('d') && parseInt(item.synced_ago) >= 2;
    parts.push('<span style="color:' + (stale ? '#f97316' : '#9ca3af') + '">' +
               esc(item.synced_ago) + '</span>');
  }
  if (item.confidence) {
    parts.push('<span style="color:' + confColor(item.confidence) + '">' +
               esc(item.confidence) + ' confidence</span>');
  }
  if (item.why_shown) {
    parts.push('<span style="color:#a5b4fc">' + esc(item.why_shown) + '</span>');
  }
  if (!parts.length) return '';
  return '<div style="font-size:10px;margin-top:4px;line-height:1.5;color:#9ca3af">' +
         parts.join(' · ') + '</div>';
}

function buildFeedbackButtons(item) {
  if (!item.source && !item.field_key) return '';
  var s = esc(item.source || ''), fk = esc(item.field_key || ''), ctx = esc(item._ctx || '');
  var btn = 'background:none;border:none;cursor:pointer;font-size:10px;color:#d1d5db;' +
            'padding:0;font-family:inherit;text-decoration:underline;text-underline-offset:2px';
  return '<div style="display:flex;gap:10px;margin-top:5px">' +
    '<button style="' + btn + '" onclick="mightyFeedback(\'useful\',\'' + s + '\',\'' + fk + '\',\'' + ctx + '\',this)">Useful</button>' +
    '<button style="' + btn + '" onclick="mightyFeedback(\'already_used\',\'' + s + '\',\'' + fk + '\',\'' + ctx + '\',this)">Already used</button>' +
    '<button style="' + btn + '" onclick="mightyFeedback(\'not_relevant\',\'' + s + '\',\'' + fk + '\',\'' + ctx + '\',this)">Not relevant</button>' +
    '<button style="' + btn + '" onclick="mightyFeedback(\'dont_show\',\'' + s + '\',\'' + fk + '\',\'' + ctx + '\',this)">Don\'t show</button>' +
  '</div>';
}

function buildExistingRow(item) {
  var row = document.createElement('div');
  row.setAttribute('data-mighty-benefit', '1');
  row.style.cssText = 'padding:10px 0;border-bottom:1px solid #f3f4f6';

  var label  = item.derived
    ? esc(item.program + ' — ' + item.benefit)
    : esc(item.label);
  var detail = item.derived && item.detail ? esc(item.detail) : '';
  var val    = !item.derived && item.value
    ? esc((item.value.length > 30 ? item.value.slice(0, 30) + '…' : item.value))
    : '';

  row.innerHTML =
    '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px">' +
      '<div style="font-size:13px;font-weight:500;color:#111;line-height:1.35">' + label + '</div>' +
      (val ? '<div style="font-size:12px;color:#6b7280;flex-shrink:0;margin-top:1px">' + val + '</div>' : '') +
    '</div>' +
    (detail ? '<div style="font-size:12px;color:#374151;margin-top:3px;line-height:1.4">' + detail + '</div>' : '') +
    evidenceHtml(item) +
    buildFeedbackButtons(item);

  return row;
}

function buildCardRecRow(item) {
  var row = document.createElement('div');
  row.style.cssText = 'padding:10px 0';

  var benefits = (item.benefits || []).slice(0, 2).map(function(b) {
    return '<li style="font-size:11px;color:#6b7280;margin-bottom:2px;line-height:1.4">' + esc(b) + '</li>';
  }).join('');
  var more = (item.benefits || []).length > 2
    ? '<li style="font-size:11px;color:#9ca3af;list-style:none">+ ' + ((item.benefits||[]).length - 2) + ' more</li>'
    : '';

  row.innerHTML =
    '<div style="font-size:10px;font-weight:600;color:#9ca3af;text-transform:uppercase;' +
    'letter-spacing:.04em;margin-bottom:4px">Worth knowing about</div>' +
    '<div style="font-size:12px;font-weight:500;color:#374151">' + esc(item.card_name) + '</div>' +
    '<ul style="margin:4px 0 0;padding:0 0 0 12px;list-style:disc">' + benefits + more + '</ul>' +
    (item.last_verified
      ? '<div style="font-size:10px;color:#d1d5db;margin-top:4px">Published card benefit · verified ' +
        esc(item.last_verified) + '</div>'
      : '');

  return row;
}

window.mightyFeedback = function(action, source, fieldKey, context, el) {
  var row = el.closest('[data-mighty-benefit]') || el.parentElement;
  if (action === 'dont_show' || action === 'not_relevant') {
    row.style.opacity = '0.25';
    row.style.pointerEvents = 'none';
  } else {
    el.style.color = '#34d399';
    el.textContent = '✓';
    el.style.pointerEvents = 'none';
  }
  chrome.runtime.sendMessage({
    type: 'MIGHTY_FEEDBACK',
    source: source,
    field_key: fieldKey,
    feedback: action,
    context: context,
  });
};

window.mightyDismiss = function(el, source, fieldKey, context) {
  window.mightyFeedback('dont_show', source, fieldKey, context, el);
};

function showBenefits(context, benefits, cardRecs, isCheckout) {
  removePill();

  var activityLabel = ACTIVITY_LABELS[context] || 'You may have relevant benefits';
  var totalCount    = benefits.length + (cardRecs || []).length;

  // ── Pill ──────────────────────────────────────────────────────────────────
  mightyPill = document.createElement('div');
  mightyPill.id = 'mighty-pill';

  var pillBg     = isCheckout ? '#4f46e5' : '#374151';
  var pillShadow = isCheckout
    ? '0 4px 16px rgba(79,70,229,0.35)'
    : '0 2px 8px rgba(0,0,0,0.18)';
  var pillOpacity = isCheckout ? '1' : '0.82';

  // Activity-centric framing: lead with what the user is doing
  var pillText = isCheckout
    ? activityLabel + '. You have ' + totalCount + ' relevant benefit' + (totalCount !== 1 ? 's' : '')
    : activityLabel + '. You may have something relevant';

  mightyPill.style.cssText =
    'position:fixed;bottom:24px;right:24px;z-index:2147483647;' +
    'background:' + pillBg + ';color:white;' +
    'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;' +
    'font-size:13px;font-weight:500;padding:9px 15px;border-radius:24px;cursor:pointer;' +
    'box-shadow:' + pillShadow + ';display:flex;align-items:center;gap:7px;' +
    'transition:transform 0.15s ease,opacity 0.15s ease;user-select:none;opacity:' + pillOpacity;

  mightyPill.innerHTML = '<span style="font-size:14px">✦</span> ' + pillText;

  // ── Panel ─────────────────────────────────────────────────────────────────
  mightyPanel = document.createElement('div');
  mightyPanel.id = 'mighty-panel';
  mightyPanel.style.cssText =
    'position:fixed;bottom:70px;right:24px;z-index:2147483647;' +
    'background:white;border:1px solid #e5e7eb;border-radius:12px;' +
    'box-shadow:0 8px 32px rgba(0,0,0,0.12);width:320px;' +
    'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;' +
    'display:none;overflow:hidden';

  // Panel header — activity-centric
  var subhead = isCheckout ? 'Worth checking before you book' : 'This may apply';
  var header = document.createElement('div');
  header.style.cssText = 'padding:12px 16px;border-bottom:1px solid #f3f4f6;' +
                         'display:flex;justify-content:space-between;align-items:flex-start';
  header.innerHTML =
    '<div>' +
      '<div style="font-size:13px;font-weight:700;color:#111">' + esc(activityLabel) + '</div>' +
      '<div style="font-size:10px;color:#9ca3af;margin-top:1px">' + esc(subhead) + '</div>' +
    '</div>' +
    '<button id="mighty-close" style="background:none;border:none;cursor:pointer;' +
    'color:#9ca3af;font-size:20px;line-height:1;padding:0;margin-left:8px">×</button>';
  mightyPanel.appendChild(header);

  // Body
  var list = document.createElement('div');
  list.style.cssText = 'padding:8px 16px;max-height:320px;overflow-y:auto';

  // Stamp context for feedback
  benefits.forEach(function(b) { b._ctx = context; });

  if (benefits.length) {
    var existHead = document.createElement('div');
    existHead.style.cssText =
      'font-size:10px;font-weight:700;color:#6b7280;text-transform:uppercase;' +
      'letter-spacing:.05em;margin-bottom:4px;padding-top:2px';
    existHead.textContent = 'You already have this';
    list.appendChild(existHead);
    benefits.forEach(function(b) { list.appendChild(buildExistingRow(b)); });
  }

  if (cardRecs && cardRecs.length) {
    var sep = document.createElement('div');
    sep.style.cssText = 'border-top:1px solid #f3f4f6;margin:8px 0';
    list.appendChild(sep);
    cardRecs.forEach(function(r) { list.appendChild(buildCardRecRow(r)); });
  }

  mightyPanel.appendChild(list);

  // Footer
  var footer = document.createElement('div');
  footer.style.cssText = 'padding:8px 16px;border-top:1px solid #f3f4f6';
  footer.innerHTML =
    '<a href="' + (mightyDashUrl || 'https://mighty-selfserve-production.up.railway.app/dashboard') + '" target="_blank" ' +
    'style="font-size:11px;color:#6366f1;text-decoration:none">View all in Mighty →</a>';
  mightyPanel.appendChild(footer);

  document.body.appendChild(mightyPanel);
  document.body.appendChild(mightyPill);

  mightyPill.addEventListener('click', function() {
    var isOpen = mightyPanel.style.display !== 'none';
    mightyPanel.style.display = isOpen ? 'none' : 'block';
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
  });

  document.getElementById('mighty-close').addEventListener('click', function(e) {
    e.stopPropagation();
    removePill();
  });

  hideTimer = setTimeout(function() {
    if (mightyPanel && mightyPanel.style.display === 'none') removePill();
  }, isCheckout ? 20000 : 8000);
}

chrome.runtime.onMessage.addListener(function(msg) {
  if (msg.type === 'MIGHTY_BENEFITS' && msg.count > 0) {
    if (msg.dashUrl) mightyDashUrl = msg.dashUrl;
    showBenefits(msg.context, msg.benefits || [], msg.cardRecs || [], msg.isCheckout || false);
  }
});
