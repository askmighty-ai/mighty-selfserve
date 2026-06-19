// Mighty content script — contextual benefit surfacing

let mightyPill   = null;
let mightyPanel  = null;
let hideTimer    = null;

function removePill() {
  if (mightyPill)  { mightyPill.remove();  mightyPill  = null; }
  if (mightyPanel) { mightyPanel.remove(); mightyPanel = null; }
  if (hideTimer)   { clearTimeout(hideTimer); hideTimer = null; }
}

function escapeHtml(s) {
  return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// Conservative confidence display
function confidenceBadge(conf) {
  if (!conf || conf === 'high') return '';
  var color = conf === 'low' ? '#fbbf24' : '#d1d5db';
  return `<span style="font-size:9px;color:${color};margin-left:4px;text-transform:uppercase;letter-spacing:.04em">${escapeHtml(conf)} confidence</span>`;
}

// "Found in your Delta account" vs "Published card benefit"
function proofLine(item) {
  var parts = [];
  if (item.derived && item.account) {
    parts.push('Via your ' + escapeHtml(item.account));
  } else if (item.account) {
    parts.push('Found in your ' + escapeHtml(item.account) + ' account');
  }
  if (item.last_verified) {
    parts.push('verified ' + escapeHtml(item.last_verified));
  }
  if (!parts.length) return '';
  return `<div style="font-size:10px;color:#9ca3af;margin-top:3px">${parts.join(' · ')}</div>`;
}

function buildExistingRow(item) {
  var row = document.createElement('div');
  row.setAttribute('data-mighty-benefit', '1');
  row.style.cssText = 'padding:8px 0;border-bottom:1px solid #f3f4f6';

  var label  = item.derived ? escapeHtml(item.program + ' — ' + item.benefit) : escapeHtml(item.label);
  var detail = item.derived && item.detail ? escapeHtml(item.detail) : '';
  var val    = !item.derived ? escapeHtml(
    (item.value || '').length > 28 ? (item.value || '').slice(0, 28) + '…' : (item.value || '')
  ) : '';

  row.innerHTML =
    `<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px">` +
      `<div style="font-size:13px;font-weight:500;color:#111;line-height:1.4">${label}${confidenceBadge(item.confidence)}</div>` +
      (val ? `<div style="font-size:12px;color:#6b7280;flex-shrink:0">${val}</div>` : '') +
    `</div>` +
    (detail ? `<div style="font-size:12px;color:#374151;margin-top:2px;line-height:1.4">${detail}</div>` : '') +
    proofLine(item) +
    buildFeedbackRow(item);

  return row;
}

function buildCardRecRow(item) {
  var row = document.createElement('div');
  row.style.cssText = 'padding:8px 0';

  var benefits = (item.benefits || []).slice(0, 3).map(function(b) {
    return `<li style="font-size:11px;color:#6b7280;margin-bottom:2px;line-height:1.4">${escapeHtml(b)}</li>`;
  }).join('');

  row.innerHTML =
    `<div style="font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px">Worth knowing about</div>` +
    `<div style="font-size:12px;font-weight:500;color:#374151">${escapeHtml(item.card_name)}</div>` +
    `<ul style="margin:4px 0 0;padding:0 0 0 12px;list-style:disc">${benefits}</ul>` +
    (item.last_verified
      ? `<div style="font-size:10px;color:#d1d5db;margin-top:4px">Published card benefit · verified ${escapeHtml(item.last_verified)}</div>`
      : '');

  return row;
}

function buildFeedbackRow(item) {
  if (!item.source && !item.field_key) return '';
  var src = escapeHtml(item.source || '');
  var fk  = escapeHtml(item.field_key || '');
  var ctx = escapeHtml(item._ctx || '');
  var btnStyle = 'background:none;border:none;cursor:pointer;font-size:10px;color:#9ca3af;' +
                 'padding:0;font-family:inherit;text-decoration:underline;text-underline-offset:2px';
  return `<div style="display:flex;gap:10px;margin-top:4px">` +
    `<button style="${btnStyle}" onclick="mightyFeedback('useful','${src}','${fk}','${ctx}',this)">Useful</button>` +
    `<button style="${btnStyle}" onclick="mightyFeedback('already_used','${src}','${fk}','${ctx}',this)">Already used</button>` +
    `<button style="${btnStyle}" onclick="mightyFeedback('not_relevant','${src}','${fk}','${ctx}',this)">Not relevant</button>` +
    `<button style="${btnStyle}" onclick="mightyFeedback('dont_show','${src}','${fk}','${ctx}',this)">Don't show again</button>` +
  `</div>`;
}

window.mightyFeedback = function(action, source, fieldKey, context, el) {
  var row = el.closest('[data-mighty-benefit]') || el.parentElement;
  if (action === 'dont_show' || action === 'not_relevant') {
    row.style.opacity = '0.3';
    row.style.pointerEvents = 'none';
  } else {
    el.style.color = '#34d399';
    el.textContent = action === 'useful' ? 'Noted ✓' : 'Got it ✓';
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

// Legacy dismiss — kept for backward compat
window.mightyDismiss = function(el, source, fieldKey, context) {
  window.mightyFeedback('dont_show', source, fieldKey, context, el);
};

function showBenefits(context, benefits, cardRecs, isCheckout) {
  removePill();

  var CONTEXT_LABELS = {
    flight: 'flight benefits',
    hotel:  'hotel benefits',
    car:    'rental benefits',
    shopping: 'purchase benefits',
    dining: 'dining benefits',
  };
  var contextLabel = CONTEXT_LABELS[context] || 'benefits';
  var totalCount   = benefits.length + (cardRecs || []).length;

  // ── Pill ──────────────────────────────────────────────────────────────────
  mightyPill = document.createElement('div');
  mightyPill.id = 'mighty-pill';

  // At checkout: prominent indigo. While browsing: subtle grey.
  var pillBg     = isCheckout ? '#4f46e5' : '#6b7280';
  var pillShadow = isCheckout ? '0 4px 16px rgba(79,70,229,0.35)' : '0 2px 8px rgba(0,0,0,0.15)';

  mightyPill.style.cssText = `
    position: fixed;
    bottom: 24px;
    right: 24px;
    z-index: 2147483647;
    background: ${pillBg};
    color: white;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 13px;
    font-weight: 500;
    padding: 9px 15px;
    border-radius: 24px;
    cursor: pointer;
    box-shadow: ${pillShadow};
    display: flex;
    align-items: center;
    gap: 7px;
    transition: transform 0.15s ease, opacity 0.15s ease;
    user-select: none;
    opacity: ${isCheckout ? '1' : '0.85'};
  `;

  var pillLabel = isCheckout
    ? `You have ${totalCount} ${contextLabel} that may apply`
    : `You may have a relevant ${contextLabel}`;
  mightyPill.innerHTML = `<span style="font-size:14px">✦</span> ${pillLabel}`;

  // ── Panel ─────────────────────────────────────────────────────────────────
  mightyPanel = document.createElement('div');
  mightyPanel.id = 'mighty-panel';
  mightyPanel.style.cssText = `
    position: fixed;
    bottom: 70px;
    right: 24px;
    z-index: 2147483647;
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.12);
    width: 310px;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    display: none;
    overflow: hidden;
  `;

  // Header
  var header = document.createElement('div');
  header.style.cssText = 'padding:12px 16px;border-bottom:1px solid #f3f4f6;display:flex;justify-content:space-between;align-items:center';
  header.innerHTML = `
    <div>
      <div style="font-size:13px;font-weight:600;color:#111">Relevant ${contextLabel}</div>
      <div style="font-size:10px;color:#9ca3af;margin-top:1px">${isCheckout ? 'Worth checking before you book' : 'This may apply'}</div>
    </div>
    <button id="mighty-close" style="background:none;border:none;cursor:pointer;color:#9ca3af;font-size:20px;line-height:1;padding:0">×</button>
  `;
  mightyPanel.appendChild(header);

  // Existing benefits section
  var list = document.createElement('div');
  list.style.cssText = 'padding:8px 16px;max-height:300px;overflow-y:auto';

  // Stamp context on each item for feedback
  benefits.forEach(function(b) { b._ctx = context; });

  if (benefits.length) {
    // "You already have this" heading
    var existingHead = document.createElement('div');
    existingHead.style.cssText = 'font-size:10px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;padding-top:2px';
    existingHead.textContent = 'You already have this';
    list.appendChild(existingHead);
    benefits.forEach(function(b) {
      list.appendChild(buildExistingRow(b));
    });
  }

  // Card recommendations — quieter, separated
  if (cardRecs && cardRecs.length) {
    var sep = document.createElement('div');
    sep.style.cssText = 'border-top:1px solid #f3f4f6;margin:8px 0';
    list.appendChild(sep);
    cardRecs.forEach(function(r) {
      list.appendChild(buildCardRecRow(r));
    });
  }

  mightyPanel.appendChild(list);

  // Footer
  var footer = document.createElement('div');
  footer.style.cssText = 'padding:8px 16px;border-top:1px solid #f3f4f6';
  footer.innerHTML = `<a href="https://mighty-selfserve-production.up.railway.app/dashboard" target="_blank" style="font-size:11px;color:#6366f1;text-decoration:none">View all in Mighty →</a>`;
  mightyPanel.appendChild(footer);

  document.body.appendChild(mightyPanel);
  document.body.appendChild(mightyPill);

  // Toggle panel
  mightyPill.addEventListener('click', function() {
    var isOpen = mightyPanel.style.display !== 'none';
    mightyPanel.style.display = isOpen ? 'none' : 'block';
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
  });

  document.getElementById('mighty-close').addEventListener('click', function(e) {
    e.stopPropagation();
    removePill();
  });

  // Auto-hide: shorter at browsing (8s), longer at checkout (20s)
  hideTimer = setTimeout(function() {
    if (mightyPanel && mightyPanel.style.display === 'none') removePill();
  }, isCheckout ? 20000 : 8000);
}

// Listen for messages from background script
chrome.runtime.onMessage.addListener(function(msg) {
  if (msg.type === 'MIGHTY_BENEFITS' && msg.count > 0) {
    showBenefits(msg.context, msg.benefits || [], msg.cardRecs || [], msg.isCheckout || false);
  }
});
