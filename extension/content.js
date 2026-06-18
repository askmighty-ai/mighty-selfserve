// Mighty content script — contextual benefit surfacing

let mightyPill = null;
let mightyPanel = null;
let hideTimer = null;

function removePill() {
  if (mightyPill) { mightyPill.remove(); mightyPill = null; }
  if (mightyPanel) { mightyPanel.remove(); mightyPanel = null; }
  if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
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
      row.style.cssText = 'font-size:13px;color:#374151;padding:2px 0;display:flex;justify-content:space-between;align-items:baseline;gap:8px;';
      row.innerHTML = `<span>• ${item.label}</span><span style="color:#6b7280;font-size:12px;flex-shrink:0">${item.value.length > 20 ? item.value.slice(0,20)+'…' : item.value}</span>`;
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

// Listen for messages from background script
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'MIGHTY_BENEFITS' && msg.count > 0) {
    showBenefits(msg.context, msg.benefits, msg.count);
  }
});
