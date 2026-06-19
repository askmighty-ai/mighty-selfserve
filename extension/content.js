// Mighty content script — contextual benefit surfacing

let mightyPill = null;
let mightyPanel = null;
let hideTimer = null;

function removePill() {
  if (mightyPill) { mightyPill.remove(); mightyPill = null; }
  if (mightyPanel) { mightyPanel.remove(); mightyPanel = null; }
  if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
}

function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function explainWhy(why) {
  if (!why) return "Matched your account data.";

  var parts = [];

  // Intent (weight 0.4 — lead if high)
  if (why.intent_factor >= 0.9)
    parts.push("directly matches your current search");
  else if (why.intent_factor >= 0.4)
    parts.push("related to your current activity");
  else
    parts.push("available in your accounts");

  // Urgency (weight 0.2)
  if (why.urgency_factor >= 0.9)
    parts.push("expires very soon");
  else if (why.urgency_factor >= 0.7)
    parts.push("expires this month");
  else if (why.urgency_factor >= 0.4)
    parts.push("expires within 90 days");
  // else: omit — no urgency signal

  // Value (weight 0.3)
  if (why.value_factor >= 0.7)
    parts.push("high-value");
  else if (why.value_factor >= 0.3)
    parts.push("moderate value");
  // else: omit low value

  // Confidence (only mention if low)
  if (why.confidence_factor < 0.5)
    parts.push("data needs review");

  if (parts.length === 0) return "Matched your account data.";
  // Capitalize first, join the rest
  return parts[0].charAt(0).toUpperCase() + parts[0].slice(1) +
    (parts.length > 1 ? " · " + parts.slice(1).join(" · ") : "") + ".";
}

function showBenefits(context, benefits, count) {
  removePill(); // clear any existing

  const CONTEXT_LABELS = {
    flight: 'flight benefits',
    hotel: 'hotel benefits',
    car: 'rental benefits',
    shopping: 'purchase benefits',
    dining: 'dining benefits',
  };
  const contextLabel = CONTEXT_LABELS[context] || 'benefits';

  // Pill container
  mightyPill = document.createElement('div');
  mightyPill.id = 'mighty-pill';
  mightyPill.style.cssText = `
    position: fixed;
    bottom: 24px;
    right: 24px;
    z-index: 2147483647;
    background: #4f46e5;
    color: white;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 13px;
    font-weight: 500;
    padding: 10px 16px;
    border-radius: 24px;
    cursor: pointer;
    box-shadow: 0 4px 16px rgba(79,70,229,0.4);
    display: flex;
    align-items: center;
    gap: 8px;
    transition: transform 0.15s ease;
    user-select: none;
  `;
  mightyPill.innerHTML = `<span style="font-size:16px">✦</span> ${count} ${contextLabel} available`;

  // Panel (hidden initially)
  mightyPanel = document.createElement('div');
  mightyPanel.id = 'mighty-panel';
  mightyPanel.style.cssText = `
    position: fixed;
    bottom: 76px;
    right: 24px;
    z-index: 2147483647;
    background: white;
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.12);
    width: 300px;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    display: none;
    overflow: hidden;
  `;

  // Panel header
  const header = document.createElement('div');
  header.style.cssText = 'padding: 12px 16px; border-bottom: 1px solid #f3f4f6; display: flex; justify-content: space-between; align-items: center;';
  header.innerHTML = `
    <span style="font-size:13px;font-weight:600;color:#111">Relevant ${contextLabel}</span>
    <button id="mighty-close" style="background:none;border:none;cursor:pointer;color:#9ca3af;font-size:18px;line-height:1;padding:0">×</button>
  `;
  mightyPanel.appendChild(header);

  // Benefit list
  const list = document.createElement('div');
  list.style.cssText = 'padding: 8px 0; max-height: 280px; overflow-y: auto;';

  // Group by account
  const byAccount = {};
  for (const b of benefits) {
    if (!byAccount[b.account]) byAccount[b.account] = [];
    byAccount[b.account].push(b);
  }

  for (const [account, items] of Object.entries(byAccount)) {
    const acctDiv = document.createElement('div');
    acctDiv.style.cssText = 'padding: 8px 16px;';
    acctDiv.innerHTML = `<div style="font-size:11px;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">${account}</div>`;
    for (const item of items) {
      const row = document.createElement('div');
      row.setAttribute('data-mighty-benefit', '1');
      row.style.cssText = 'font-size:13px;color:#374151;padding:2px 0;display:flex;justify-content:space-between;align-items:baseline;gap:8px;';
      var derivedBadge = item.derived
        ? `<span style="font-size:10px;background:#ede9fe;color:#6d28d9;border-radius:3px;padding:1px 5px;margin-left:5px;flex-shrink:0">via ${escapeHtml(item.account)}</span>`
        : '';
      var displayVal = item.derived
        ? (item.detail ? item.detail.slice(0, 48) + (item.detail.length > 48 ? '…' : '') : item.benefit)
        : (item.value.length > 20 ? item.value.slice(0, 20) + '…' : item.value);
      var labelText = item.derived ? escapeHtml(item.program + ' — ' + item.benefit) : escapeHtml(item.label);
      var benefitHtml = `<span style="display:flex;align-items:center;gap:0;flex-wrap:wrap">• ${labelText}${derivedBadge}</span><span style="color:#6b7280;font-size:12px;flex-shrink:0">${escapeHtml(displayVal)}</span>`;
      if (item._why) {
        var whyId = 'mighty-why-' + Math.random().toString(36).slice(2);
        benefitHtml += '<span style="margin-left:6px;font-size:10px;color:#a5b4fc;cursor:pointer;' +
          'text-decoration:underline;text-underline-offset:2px" ' +
          'onclick="(function(el){' +
            'var t=document.getElementById(\'' + whyId + '\');' +
            't.style.display=t.style.display===\'none\'?\'block\':\'none\';' +
          '})(this)">Why?</span>' +
          '<div id="' + whyId + '" style="display:none;font-size:11px;color:#e0e7ff;' +
            'margin-top:3px;padding:4px 8px;background:rgba(255,255,255,0.08);border-radius:4px">' +
            escapeHtml(explainWhy(item._why)) +
          '</div>';
      }
      if (item.source && item.field_key) {
        benefitHtml += '<span style="margin-left:8px;font-size:10px;color:#a5b4fc;cursor:pointer;opacity:0.7" ' +
          'title="Remove this suggestion" ' +
          'onclick="mightyDismiss(this, \'' + escapeHtml(item.source) + '\', \'' + escapeHtml(item.field_key) + '\', \'' + escapeHtml(context) + '\')">&#x2715;</span>';
      }
      row.innerHTML = benefitHtml;
      acctDiv.appendChild(row);
    }
    list.appendChild(acctDiv);
  }
  mightyPanel.appendChild(list);

  // Footer
  const footer = document.createElement('div');
  footer.style.cssText = 'padding: 8px 16px; border-top: 1px solid #f3f4f6;';
  footer.innerHTML = `<a href="https://mighty-selfserve-production.up.railway.app/dashboard" target="_blank" style="font-size:11px;color:#6366f1;text-decoration:none">View all in Mighty →</a>`;
  mightyPanel.appendChild(footer);

  document.body.appendChild(mightyPanel);
  document.body.appendChild(mightyPill);

  // Toggle panel on pill click
  mightyPill.addEventListener('click', () => {
    const isOpen = mightyPanel.style.display !== 'none';
    mightyPanel.style.display = isOpen ? 'none' : 'block';
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
  });

  // Close button
  document.getElementById('mighty-close').addEventListener('click', (e) => {
    e.stopPropagation();
    removePill();
  });

  // Auto-hide pill after 12 seconds if panel never opened
  hideTimer = setTimeout(() => {
    if (mightyPanel && mightyPanel.style.display === 'none') {
      removePill();
    }
  }, 12000);
}

function mightyDismiss(el, source, fieldKey, context) {
  // Hide the row immediately
  var row = el.closest('[data-mighty-benefit]') || el.parentElement;
  row.style.opacity = '0.3';
  row.style.pointerEvents = 'none';

  // Send feedback to server via background script
  chrome.runtime.sendMessage({
    type: 'MIGHTY_FEEDBACK',
    source: source,
    field_key: fieldKey,
    feedback: 'not_relevant',
    context: context,
  });
}

// Listen for messages from background script
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'MIGHTY_BENEFITS' && msg.count > 0) {
    showBenefits(msg.context, msg.benefits, msg.count);
  }
});
