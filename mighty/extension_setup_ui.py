"""Extension-setup page — means to Visit Amex, not the product.

Customer path: add Mighty → Visit American Express.
No heartbeat, connection status, or diagnostics on the primary path.
Auth and API key remain owned by app.py.
"""

from __future__ import annotations

import json
from html import escape

from mighty import user_copy

# Default Amex entry used when caller omits visit_href (login → overview).
_DEFAULT_VISIT_HREF = "https://www.americanexpress.com/en-us/account/login"


def render_extension_setup_page(
    *,
    api_key: str,
    home_href: str = "/",
    visit_href: str | None = None,
    visit_label: str | None = None,
    diagnostics: bool = False,
) -> str:
    """Render /extension-setup for anticipation → Visit Amex.

    Diagnostics stay off the customer path; enable only for founder/debug.
    """
    key = escape(api_key or "")
    home = escape(home_href or "/")
    visit = escape((visit_href or _DEFAULT_VISIT_HREF).strip() or _DEFAULT_VISIT_HREF)
    visit_cta = escape(
        (visit_label or user_copy.EXT_SETUP_GO_HOME).strip()
        or user_copy.home_visit_provider_cta("American Express")
    )
    key_prefix = escape(
        ((api_key or "")[:10] + "…")
        if api_key and len(api_key) > 10
        else (api_key or "—")
    )
    storage_key = f"mighty_setup_done:{(api_key or '')[:24]}"
    install_steps = "".join(
        f"<li>{escape(step)}</li>" for step in user_copy.EXT_SETUP_INSTALL_STEPS
    )
    diag_block = ""
    if diagnostics:
        diag_block = f"""
  <details class="ext-diag" id="diag-panel">
    <summary>Setup diagnostics (internal)</summary>
    <p class="ext-diag__lede">
      Internal only — not part of the customer product.
    </p>
    <p class="ext-diag__meta">Page API key prefix: <code>{key_prefix}</code>
      · Origin: <code id="diag-origin"></code></p>
    <p class="ext-diag__hint" id="diag-first-fail">Loading…</p>
    <ol class="ext-diag__stages" id="diag-stages"></ol>
    <pre class="ext-diag__events" id="diag-events">…</pre>
    <button type="button" class="ext-btn-secondary" id="diag-refresh">Refresh</button>
  </details>
"""
    copy = {
        "verifying": user_copy.EXT_SETUP_VERIFYING,
        "success": user_copy.EXT_SETUP_SUCCESS,
        "notDetected": user_copy.EXT_SETUP_NOT_DETECTED,
        "failChrome": user_copy.EXT_SETUP_FAIL_CHROME,
        "failExtension": user_copy.EXT_SETUP_FAIL_EXTENSION,
        "failMighty": user_copy.EXT_SETUP_FAIL_MIGHTY,
        "verifyCta": user_copy.EXT_SETUP_VERIFY_CTA,
        "tryAgain": user_copy.EXT_SETUP_TRY_AGAIN,
        "visitHint": user_copy.EXT_SETUP_VISIT_HINT,
        "idle": user_copy.EXT_SETUP_VERIFY_IDLE,
        "contextBlocked": user_copy.EXT_SETUP_CONTEXT_BLOCKED,
        "contextNotChrome": user_copy.EXT_SETUP_CONTEXT_NOT_CHROME,
        "storageKey": storage_key,
    }
    copy_json = json.dumps(copy)
    diag_script = ""
    if diagnostics:
        diag_script = """
  var diagOrigin = document.getElementById('diag-origin');
  var diagFirst = document.getElementById('diag-first-fail');
  var diagStages = document.getElementById('diag-stages');
  var diagEvents = document.getElementById('diag-events');
  var diagRefresh = document.getElementById('diag-refresh');
  if (diagOrigin) diagOrigin.textContent = location.origin;

  function renderDiagnostics(payload) {
    if (!diagStages || !payload) return;
    var stages = payload.stages || [];
    diagStages.innerHTML = stages.map(function (s) {
      var cls = 'st-' + (s.state || 'unknown');
      var mark = s.state === 'pass' ? 'PASS' : (s.state === 'fail' ? 'FAIL' : '····');
      var detail = (s.event && s.event.detail) ? (' — ' + s.event.detail) : '';
      return '<li class="' + cls + '"><strong>' + mark + '</strong> ' + s.label + detail + '</li>';
    }).join('');
    if (diagFirst) {
      if (payload.connected) {
        diagFirst.textContent = 'Ready (internal).';
        diagFirst.className = 'ext-diag__hint is-ok';
      } else if (payload.first_failure_stage) {
        diagFirst.textContent = 'First incomplete stage: ' + payload.first_failure_stage;
        diagFirst.className = 'ext-diag__hint';
      } else {
        diagFirst.textContent = 'No handshake progress yet.';
        diagFirst.className = 'ext-diag__hint';
      }
    }
    if (diagEvents) {
      var lines = (payload.recent_events || []).map(function (e) {
        return (e.created_at || '') + ' [' + (e.ok ? 'ok' : 'FAIL') + '] ' +
          e.stage + ' · ' + (e.source || '') + ' · ' + (e.detail || '');
      });
      diagEvents.textContent = lines.length ? lines.join('\\n') : 'No events yet.';
    }
  }

  function refreshDiagnostics() {
    if (!diagStages) return Promise.resolve();
    return fetch('/api/extension/setup-diagnostics?meta=1', {
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' }
    }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (payload) {
        if (!payload) return;
        if (extPresentSignal) {
          payload.stages = (payload.stages || []).map(function (s) {
            if (s.id === 'service_worker_alive' && s.state === 'unknown') {
              return Object.assign({}, s, {
                state: 'pass',
                event: { ok: true, detail: 'page signal', source: 'page' }
              });
            }
            return s;
          });
        }
        renderDiagnostics(payload);
      }).catch(function () {});
  }

  window.addEventListener('message', function (event) {
    if (event.source !== window) return;
    if (event.origin !== location.origin) return;
    if (!event.data || event.data.type !== '__mighty_setup_ext__') return;
    extPresentSignal = true;
    refreshDiagnostics();
  });

  fetch('/api/extension/setup-handshake', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
    body: JSON.stringify({
      stage: 'page_meta_present',
      ok: !!document.querySelector('meta[name="mighty-api-key"]'),
      detail: 'setup page self-check',
      source: 'setup-page'
    })
  }).catch(function () {});

  if (diagRefresh) diagRefresh.addEventListener('click', refreshDiagnostics);
  refreshDiagnostics();
  setInterval(refreshDiagnostics, 2000);
"""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<meta name="mighty-api-key" content="{key}">
<title>{escape(user_copy.EXT_SETUP_TITLE)} — Mighty</title>
<link rel="stylesheet" href="/static/design-system/mighty-ds.css">
<style>
  body.ext-setup-page {{
    margin: 0;
    min-height: 100vh;
    background:
      radial-gradient(1200px 600px at 50% -10%, rgba(31, 92, 79, 0.08), transparent 55%),
      linear-gradient(180deg, var(--mds-bg) 0%, var(--mds-bg-deep) 100%);
    color: var(--mds-ink);
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 24px 16px;
    box-sizing: border-box;
    font-family: var(--mds-font-sans, "Source Sans 3", system-ui, sans-serif);
  }}
  .ext-card {{
    background: var(--mds-surface);
    border: 1px solid var(--mds-line);
    border-radius: var(--mds-radius);
    padding: 2rem 1.75rem 1.75rem;
    max-width: 540px;
    width: 100%;
    box-shadow: var(--mds-shadow-sm);
    display: grid;
    gap: 1.1rem;
  }}
  .ext-card h1 {{
    margin: 0;
    font-size: 1.45rem;
    font-weight: var(--mds-weight-semibold, 600);
    color: var(--mds-pine-ink);
    text-align: center;
    letter-spacing: -0.02em;
  }}
  .ext-lede {{
    margin: 0;
    font-size: 1.02rem;
    line-height: 1.55;
    color: var(--mds-muted);
    text-align: center;
  }}
  .ext-context {{
    display: none;
    gap: 0.65rem;
    padding: 1rem 1.05rem;
    border-radius: var(--mds-radius-sm);
    border: 1px solid var(--mds-line);
    background: var(--mds-surface-soft);
  }}
  .ext-context.is-visible {{ display: grid; }}
  .ext-context.is-warn {{
    background: var(--mds-waiting-soft);
    border-color: #e8d5a8;
  }}
  .ext-context h2 {{
    margin: 0;
    font-size: 0.75rem;
    font-weight: 700;
    color: var(--mds-pine-ink);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  .ext-context p {{
    margin: 0;
    font-size: 0.9rem;
    line-height: 1.5;
    color: var(--mds-ink);
  }}
  .ext-context__alert {{
    margin: 0;
    font-size: 0.88rem;
    line-height: 1.45;
    color: var(--mds-waiting);
    font-weight: 600;
  }}
  .ext-context__alert[hidden] {{ display: none; }}
  .ext-install-block[hidden],
  .ext-ready[hidden] {{ display: none !important; }}
  .ext-dl {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    box-sizing: border-box;
    padding: 0.75rem 1rem;
    border-radius: var(--mds-radius-sm);
    border: 1px solid var(--mds-line);
    background: var(--mds-surface);
    color: var(--mds-pine-ink);
    font-size: 0.94rem;
    font-weight: var(--mds-weight-semibold, 600);
    text-decoration: none;
  }}
  .ext-dl:hover {{ background: var(--mds-bg-deep); color: var(--mds-pine-ink); text-decoration: none; }}
  .ext-card h2.ext-section {{
    margin: 0;
    font-size: 0.75rem;
    font-weight: 700;
    color: var(--mds-pine-ink);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  ol.ext-install {{
    margin: 0;
    padding-left: 1.25rem;
    font-size: 0.92rem;
    color: var(--mds-ink);
    line-height: 1.55;
  }}
  ol.ext-install li {{ margin-bottom: 0.45rem; }}
  .ext-hint {{
    margin: 0;
    font-size: 0.82rem;
    line-height: 1.45;
    color: var(--mds-muted);
    text-align: center;
  }}
  .ext-status {{
    font-size: 0.92rem;
    font-weight: 600;
    padding: 0.7rem 0.95rem;
    border-radius: var(--mds-radius-sm);
    background: var(--mds-surface-soft);
    border: 1px solid var(--mds-line);
    color: var(--mds-muted);
    text-align: center;
    line-height: 1.45;
  }}
  .ext-status.is-progress {{
    background: var(--mds-waiting-soft);
    border-color: #e8d5a8;
    color: var(--mds-waiting);
  }}
  .ext-status.is-warn {{
    background: var(--mds-waiting-soft);
    border-color: #e8d5a8;
    color: var(--mds-ink);
    text-align: left;
    font-weight: 500;
  }}
  .ext-status.is-ok {{
    background: var(--mds-success-soft);
    border-color: #c5dfd0;
    color: var(--mds-pine-ink);
  }}
  .ext-fail-list {{
    margin: 0.55rem 0 0;
    padding-left: 1.1rem;
    font-size: 0.84rem;
    font-weight: 500;
    color: var(--mds-ink-soft);
    line-height: 1.45;
  }}
  .ext-fail-list li {{ margin-bottom: 0.35rem; }}
  .ext-spinner {{
    display: inline-block;
    width: 14px;
    height: 14px;
    border: 2px solid var(--mds-line);
    border-top-color: var(--mds-pine);
    border-radius: 50%;
    animation: ext-spin 0.7s linear infinite;
    margin-right: 8px;
    vertical-align: middle;
  }}
  @keyframes ext-spin {{ to {{ transform: rotate(360deg); }} }}
  .ext-actions {{ display: grid; gap: 0.55rem; }}
  .ext-btn-primary {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    box-sizing: border-box;
    padding: 0.85rem 1rem;
    border-radius: var(--mds-radius-sm);
    border: none;
    background: var(--mds-pine);
    color: #fff;
    font-size: 0.98rem;
    font-weight: var(--mds-weight-semibold, 600);
    cursor: pointer;
    font-family: inherit;
    text-decoration: none;
  }}
  .ext-btn-primary:hover {{ background: var(--mds-pine-hover); color: #fff; text-decoration: none; }}
  .ext-btn-primary:disabled {{ opacity: 0.65; cursor: wait; }}
  .ext-btn-primary.is-hidden {{ display: none; }}
  .ext-btn-secondary {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    box-sizing: border-box;
    padding: 0.75rem 1rem;
    border-radius: var(--mds-radius-sm);
    border: 1px solid var(--mds-line);
    background: var(--mds-surface);
    color: var(--mds-pine-ink);
    font-size: 0.94rem;
    font-weight: 600;
    text-decoration: none;
    cursor: pointer;
    font-family: inherit;
  }}
  .ext-btn-secondary:hover {{ background: var(--mds-surface-soft); }}
  .ext-continue {{
    display: grid;
    gap: 0.35rem;
    justify-items: center;
  }}
  .ext-continue a {{
    width: auto;
    padding: 0.35rem 0.5rem;
    border: none;
    background: transparent;
    color: var(--mds-muted);
    font-size: 0.82rem;
    font-weight: 500;
    text-decoration: underline;
    text-underline-offset: 2px;
  }}
  .ext-continue a:hover {{
    background: transparent;
    color: var(--mds-pine-ink);
  }}
  .ext-diag {{
    border: 1px dashed var(--mds-line-strong);
    border-radius: var(--mds-radius-sm);
    padding: 0.75rem 0.9rem 0.9rem;
    background: #faf7f2;
  }}
  .ext-diag summary {{
    cursor: pointer;
    font-size: 0.82rem;
    font-weight: 700;
    color: var(--mds-pine-ink);
    letter-spacing: 0.02em;
  }}
  .ext-diag__lede {{
    margin: 0.55rem 0 0.4rem;
    font-size: 0.8rem;
    line-height: 1.45;
    color: var(--mds-muted);
  }}
  .ext-diag__meta {{
    margin: 0 0 0.45rem;
    font-size: 0.78rem;
    color: var(--mds-ink-soft);
  }}
  .ext-diag__meta code {{
    font-size: 0.74rem;
    background: var(--mds-surface);
    padding: 0.1rem 0.3rem;
    border-radius: 4px;
  }}
  .ext-diag__hint {{
    margin: 0 0 0.5rem;
    font-size: 0.84rem;
    font-weight: 700;
    color: var(--mds-waiting);
  }}
  .ext-diag__hint.is-ok {{ color: var(--mds-success); }}
  .ext-diag__stages {{
    margin: 0 0 0.55rem;
    padding-left: 1.1rem;
    font-size: 0.8rem;
    line-height: 1.45;
    color: var(--mds-ink);
  }}
  .ext-diag__stages li {{ margin-bottom: 0.25rem; }}
  .ext-diag__stages .st-pass {{ color: var(--mds-success); }}
  .ext-diag__stages .st-fail {{ color: var(--mds-danger); }}
  .ext-diag__stages .st-unknown {{ color: var(--mds-muted); }}
  .ext-diag__events {{
    margin: 0 0 0.55rem;
    max-height: 140px;
    overflow: auto;
    font-size: 0.68rem;
    line-height: 1.35;
    background: var(--mds-surface);
    border: 1px solid var(--mds-line);
    border-radius: 6px;
    padding: 0.45rem 0.55rem;
    white-space: pre-wrap;
    word-break: break-word;
  }}
</style>
</head>
<body class="mds ext-setup-page">
<div class="ext-card">
  <h1>{escape(user_copy.EXT_SETUP_TITLE)}</h1>
  <p class="ext-lede">{escape(user_copy.EXT_SETUP_BODY, quote=False)}</p>

  <div class="ext-context" id="context-panel">
    <h2>{escape(user_copy.EXT_SETUP_CONTEXT_HEADING)}</h2>
    <p>{escape(user_copy.EXT_SETUP_CONTEXT_LEDE, quote=False)}</p>
    <p class="ext-context__alert" id="context-alert" hidden></p>
  </div>

  <div class="ext-install-block" id="install-block" hidden>
    <a class="ext-dl" href="/download/mighty-in-chrome.zip">{escape(user_copy.EXT_SETUP_DOWNLOAD_LABEL)}</a>
    <h2 class="ext-section">{escape(user_copy.EXT_SETUP_INSTALL_HEADING)}</h2>
    <ol class="ext-install">{install_steps}</ol>
    <p class="ext-hint">{escape(user_copy.EXT_SETUP_RELOAD_HINT, quote=False)}</p>
    <div class="ext-actions" style="margin-top:0.35rem">
      <button type="button" class="ext-btn-primary" id="verify-btn">{escape(user_copy.EXT_SETUP_VERIFY_CTA)}</button>
      <button type="button" class="ext-btn-secondary" id="retry-btn" hidden>{escape(user_copy.EXT_SETUP_TRY_AGAIN)}</button>
    </div>
    <div class="ext-status" id="status" role="status" hidden></div>
  </div>

  <div class="ext-ready" id="ready-block" hidden>
    <div class="ext-status is-ok" id="ready-status" role="status">
      {escape(user_copy.EXT_SETUP_SUCCESS, quote=False)}
    </div>
    <div class="ext-actions">
      <a class="ext-btn-primary" href="{visit}" target="_blank" rel="noopener" id="visit-cta">{visit_cta}</a>
    </div>
    <p class="ext-hint">{escape(user_copy.EXT_SETUP_VISIT_HINT, quote=False)}</p>
    <div class="ext-continue">
      <a href="{home}" id="continue-home">Back to Mighty</a>
    </div>
  </div>

  <div class="ext-continue" id="continue-wrap">
    <a href="{home}" id="skip-home">{escape(user_copy.EXT_SETUP_CONTINUE)}</a>
  </div>
  {diag_block}
</div>
<script>
(function () {{
  var C = {copy_json};
  var ready = false;
  var verifying = false;
  var contextOk = false;
  var extPresentSignal = false;

  var contextPanel = document.getElementById('context-panel');
  var contextAlert = document.getElementById('context-alert');
  var installBlock = document.getElementById('install-block');
  var readyBlock = document.getElementById('ready-block');
  var statusEl = document.getElementById('status');
  var verifyBtn = document.getElementById('verify-btn');
  var retryBtn = document.getElementById('retry-btn');
  var continueWrap = document.getElementById('continue-wrap');

  function isChromeDesktop() {{
    var ua = navigator.userAgent || '';
    var isChromium = /Chrome\\//.test(ua) || /CriOS\\//.test(ua);
    var isEdge = /Edg\\//.test(ua);
    var isOpera = /OPR\\//.test(ua);
    var isMobile = /Mobile|Android|iPhone|iPad/i.test(ua);
    return isChromium && !isEdge && !isOpera && !isMobile;
  }}

  function probeLikelyIncognito() {{
    return new Promise(function (resolve) {{
      var settled = false;
      function done(v) {{
        if (settled) return;
        settled = true;
        resolve(v);
      }}
      try {{
        if (navigator.storage && navigator.storage.estimate) {{
          navigator.storage.estimate().then(function (est) {{
            var quota = (est && est.quota) || 0;
            if (quota > 0 && quota < 120 * 1024 * 1024) done(true);
            else done(false);
          }}).catch(function () {{ done(null); }});
          setTimeout(function () {{ done(null); }}, 600);
          return;
        }}
      }} catch (e) {{}}
      try {{
        var fs = window.webkitRequestFileSystem || window.requestFileSystem;
        if (fs) {{
          fs(window.TEMPORARY, 1, function () {{ done(false); }}, function () {{ done(true); }});
          setTimeout(function () {{ done(null); }}, 600);
          return;
        }}
      }} catch (e2) {{}}
      done(null);
    }});
  }}

  function setInstallVisible(unlocked) {{
    contextOk = !!unlocked;
    installBlock.hidden = !unlocked || ready;
  }}

  function showContextWarning(msg) {{
    contextPanel.classList.add('is-visible', 'is-warn');
    contextAlert.hidden = false;
    contextAlert.textContent = msg;
    setInstallVisible(false);
  }}

  function clearContextWarning() {{
    contextPanel.classList.remove('is-warn');
    contextAlert.hidden = true;
    contextAlert.textContent = '';
    if (!contextPanel.classList.contains('is-visible')) {{
      // keep hidden on happy path
    }}
  }}

  function markReady() {{
    ready = true;
    verifying = false;
    try {{ sessionStorage.setItem(C.storageKey, '1'); }} catch (e) {{}}
    contextPanel.classList.remove('is-visible', 'is-warn');
    contextAlert.hidden = true;
    installBlock.hidden = true;
    readyBlock.hidden = false;
    continueWrap.style.display = 'none';
    if (statusEl) statusEl.hidden = true;
  }}

  function markVerifying() {{
    verifying = true;
    if (statusEl) {{
      statusEl.hidden = false;
      statusEl.className = 'ext-status is-progress';
      statusEl.innerHTML = '<span class="ext-spinner"></span> ' + C.verifying;
    }}
    verifyBtn.disabled = true;
    verifyBtn.textContent = C.verifying;
    retryBtn.hidden = true;
  }}

  function markNotDetected() {{
    verifying = false;
    if (statusEl) {{
      statusEl.hidden = false;
      statusEl.className = 'ext-status is-warn';
      statusEl.innerHTML =
        '<div>' + C.notDetected + '</div>' +
        '<ol class="ext-fail-list">' +
        '<li>' + C.failChrome + '</li>' +
        '<li>' + C.failExtension + '</li>' +
        '<li>' + C.failMighty + '</li>' +
        '</ol>';
    }}
    verifyBtn.classList.add('is-hidden');
    retryBtn.hidden = false;
    retryBtn.textContent = C.tryAgain;
  }}

  function fetchStatus() {{
    return fetch('/api/extension/setup-status', {{
      credentials: 'same-origin',
      headers: {{ 'Accept': 'application/json' }}
    }}).then(function (r) {{
      if (!r.ok) return null;
      return r.json();
    }}).catch(function () {{ return null; }});
  }}

  function pollOnce() {{
    return fetchStatus().then(function (payload) {{
      var ok = !!(payload && payload.connected);
      if (ok && !ready) markReady();
      return ok;
    }});
  }}

  function startVerify() {{
    if (ready || verifying) return;
    if (!contextOk) {{
      showContextWarning(C.contextBlocked);
      return;
    }}
    // Optimistic unlock: don't make the user watch connection theater.
    // Brief status poll; if already present, great — otherwise still advance
    // so the next true action is Visit American Express.
    markVerifying();
    var deadline = Date.now() + 2500;
    function tick() {{
      fetchStatus().then(function (payload) {{
        if (payload && payload.connected) {{
          markReady();
          return;
        }}
        if (Date.now() >= deadline) {{
          markReady();
          return;
        }}
        setTimeout(tick, 400);
      }});
    }}
    tick();
  }}

  verifyBtn.addEventListener('click', startVerify);
  retryBtn.addEventListener('click', startVerify);

  if (!isChromeDesktop()) {{
    showContextWarning(C.contextNotChrome);
  }} else {{
    probeLikelyIncognito().then(function (likely) {{
      if (likely === true) {{
        showContextWarning(C.contextBlocked);
        return;
      }}
      if (!ready) {{
        clearContextWarning();
        setInstallVisible(true);
      }}
    }});
  }}

  // Silent readiness — never shown as a connection panel.
  pollOnce().then(function (ok) {{
    if (!ok) {{
      try {{
        if (sessionStorage.getItem(C.storageKey) === '1') {{
          sessionStorage.removeItem(C.storageKey);
        }}
      }} catch (e) {{}}
    }}
  }});
  setInterval(function () {{
    if (ready) return;
    pollOnce();
  }}, 2000);
{diag_script}
}})();
</script>
</body>
</html>"""
