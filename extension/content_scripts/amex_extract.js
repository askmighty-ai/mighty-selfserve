// Amex account-data DOM extraction (runs on americanexpress.com account pages).
// Single source of truth for: success / no account data / not ready / failed.
(function () {
  const LOG = '[Mighty Amex Extract]';

  const ExtractionStatus = {
    EXTRACTION_SUCCESS: 'EXTRACTION_SUCCESS',
    NO_ACCOUNT_DATA: 'NO_ACCOUNT_DATA',
    NOT_READY: 'NOT_READY',
    EXTRACTION_FAILED: 'EXTRACTION_FAILED',
  };

  function formatPoints(numStr) {
    const n = parseInt(String(numStr).replace(/[^\d]/g, ''), 10);
    if (!n || n <= 0) return null;
    return n.toLocaleString('en-US');
  }

  function formatMoney(numStr) {
    const cleaned = String(numStr).replace(/[^\d.]/g, '');
    const n = parseFloat(cleaned);
    if (!Number.isFinite(n) || n < 0) return null;
    return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function pickBestNumber(text) {
    if (!text) return null;
    const matches = String(text).match(/\b[\d]{1,3}(?:,\d{3})+\b|\b[\d]{4,}\b/g);
    if (!matches) return null;
    let best = null;
    let bestN = 0;
    for (const m of matches) {
      const n = parseInt(m.replace(/,/g, ''), 10);
      if (n > bestN && n < 100_000_000) {
        bestN = n;
        best = m;
      }
    }
    return best;
  }

  function findInRoot(root) {
    if (!root) return null;
    const text = root.innerText || root.textContent || '';
    return pickBestNumber(text);
  }

  function buildResult(status, reason, opts = {}) {
    const fields = opts.fields || [];
    const publishable = fields.map((f) => f.key).filter(Boolean);
    const primary = fields.find((f) => f.key === 'points_balance') || fields[0] || null;
    return {
      status,
      reason,
      publishable_fields: publishable,
      diagnostics: { labels: opts.labels || [reason] },
      fields,
      value: primary ? primary.value : null,
      loggedIn: opts.loggedIn !== undefined ? !!opts.loggedIn : status !== ExtractionStatus.EXTRACTION_FAILED,
    };
  }

  function pageSurface() {
    const path = (location.pathname || '').toLowerCase();
    const bodyText = (document.body && (document.body.innerText || document.body.textContent)) || '';
    const sample = bodyText.slice(0, 2500).toLowerCase();
    const bodyLen = bodyText.trim().length;
    const loginHits = ['sign in to your account', 'user id', 'show password', 'forgot password']
      .filter((s) => sample.includes(s)).length;
    const marketingPath = /\/en-us\/(?:credit-cards|business|prepaid|gift-cards|benefits|offers)(?:\/|$)/i.test(path);
    const loginPath = /\/account\/log-?in/.test(path);
    const signedInChrome = (
      sample.includes('membership rewards')
      || sample.includes('account home')
      || sample.includes('recent activity')
      || sample.includes('manage account')
      || sample.includes('statement balance')
      || sample.includes('card ending')
    );
    return {
      path,
      bodyText,
      sample,
      bodyLen,
      loginHits,
      marketingPath,
      loginPath,
      signedInChrome,
      readyState: document.readyState || '',
      hasBody: !!document.body,
    };
  }

  function extractMembershipRewards(bodyText) {
    const sels = [
      '[data-testid*="membership-rewards" i]',
      '[data-testid*="rewards-balance" i]',
      '[data-automation-id*="rewards" i]',
      '[class*="membership-rewards" i]',
      '[class*="rewards-balance" i]',
      'a[href*="/rewards"]',
    ];
    for (const sel of sels) {
      try {
        const nodes = document.querySelectorAll(sel);
        for (const node of nodes) {
          const num = findInRoot(node);
          const formatted = formatPoints(num);
          if (formatted) {
            console.log(LOG, 'selector hit', sel);
            return formatted;
          }
        }
      } catch (_) {}
    }

    const nodes = document.querySelectorAll('h1,h2,h3,h4,span,div,p,a,button');
    for (const node of nodes) {
      const t = (node.textContent || '').trim();
      if (!/membership rewards/i.test(t)) continue;
      let scope = node.closest('section,article,li,div') || node.parentElement;
      for (let depth = 0; depth < 4 && scope; depth++) {
        const formatted = formatPoints(findInRoot(scope));
        if (formatted) {
          console.log(LOG, 'heading walk found near Membership Rewards');
          return formatted;
        }
        scope = scope.parentElement;
      }
    }

    const m = bodyText.match(/Membership Rewards[^0-9\n]{0,120}([\d][\d,]*)/i);
    if (m) {
      const formatted = formatPoints(m[1]);
      if (formatted) {
        console.log(LOG, 'body regex found Membership Rewards');
        return formatted;
      }
    }
    return null;
  }

  function extractStatementBalances(bodyText) {
    const fields = [];
    const re = /statement\s+balance[^$\d]{0,40}\$?([\d][\d,]*(?:\.\d{2})?)/gi;
    let m;
    let idx = 0;
    while ((m = re.exec(bodyText)) !== null) {
      const formatted = formatMoney(m[1]);
      if (!formatted) continue;
      idx += 1;
      fields.push({
        key: idx === 1 ? 'statement_balance' : `statement_balance_${idx}`,
        label: idx === 1 ? 'Statement Balance' : `Statement Balance ${idx}`,
        value: formatted,
        _type: 'currency',
      });
      if (idx >= 8) break;
    }
    return fields;
  }

  function extractCardEndings(bodyText) {
    const fields = [];
    const re = /card\s+ending\s+(?:in\s+)?([\d*]{4,})/gi;
    let m;
    let idx = 0;
    const seen = new Set();
    while ((m = re.exec(bodyText)) !== null) {
      const ending = String(m[1] || '').replace(/[^\d*]/g, '');
      if (!ending || seen.has(ending)) continue;
      seen.add(ending);
      idx += 1;
      fields.push({
        key: idx === 1 ? 'card_ending' : `card_ending_${idx}`,
        label: idx === 1 ? 'Card Ending' : `Card Ending ${idx}`,
        value: ending.slice(-4),
        _type: 'card_ending',
      });
      if (idx >= 8) break;
    }
    return fields;
  }

  function extractGenericPoints(bodyText) {
    // Only when Membership Rewards label is absent — avoid double-counting.
    if (/membership rewards/i.test(bodyText)) return null;
    const m = bodyText.match(/(?:points|rewards)\s*(?:balance|:)?\s*([\d][\d,]*)/i);
    if (!m) return null;
    const formatted = formatPoints(m[1]);
    if (!formatted) return null;
    return {
      key: 'points_balance',
      label: 'Rewards Points',
      value: formatted,
      _type: 'points_balance',
    };
  }

  function extractAmexAccountData() {
    console.log(LOG, 'extract attempt on', location.href);
    try {
      const surface = pageSurface();

      if (surface.loginPath || (surface.loginHits >= 2 && !surface.sample.includes('membership rewards'))) {
        console.log(LOG, 'login page detected');
        return buildResult(ExtractionStatus.EXTRACTION_FAILED, 'login_page', {
          loggedIn: false,
          labels: ['login_page'],
        });
      }

      if (surface.marketingPath && !surface.signedInChrome) {
        console.log(LOG, 'marketing page detected');
        return buildResult(ExtractionStatus.EXTRACTION_FAILED, 'marketing_page', {
          loggedIn: false,
          labels: ['marketing_page'],
        });
      }

      // SPA not hydrated / blank shell — caller may retry once.
      if (!surface.hasBody || surface.bodyLen < 40 || surface.readyState === 'loading') {
        console.log(LOG, 'page not ready');
        return buildResult(ExtractionStatus.NOT_READY, 'spa_not_hydrated', {
          loggedIn: false,
          labels: ['spa_not_hydrated'],
        });
      }

      const fields = [];
      const mr = extractMembershipRewards(surface.bodyText);
      if (mr) {
        fields.push({
          key: 'points_balance',
          label: 'Membership Rewards Points',
          value: mr,
          _type: 'points_balance',
        });
      }

      for (const bal of extractStatementBalances(surface.bodyText)) {
        fields.push(bal);
      }
      for (const card of extractCardEndings(surface.bodyText)) {
        fields.push(card);
      }
      if (!mr) {
        const pts = extractGenericPoints(surface.bodyText);
        if (pts) fields.push(pts);
      }

      if (fields.length) {
        const reason = fields[0].key === 'points_balance' && mr
          ? 'membership_rewards_found'
          : (fields[0].key.startsWith('statement_balance')
            ? 'statement_balance_found'
            : (fields[0].key.startsWith('card_ending')
              ? 'card_ending_found'
              : 'publishable_fields_found'));
        console.log(LOG, 'extraction success', reason, 'fields=', fields.map((f) => f.key).join(','));
        return buildResult(ExtractionStatus.EXTRACTION_SUCCESS, reason, {
          fields,
          loggedIn: true,
          labels: [reason, ...fields.map((f) => f.key)],
        });
      }

      if (surface.signedInChrome) {
        console.log(LOG, 'authenticated but no publishable widgets');
        return buildResult(ExtractionStatus.NO_ACCOUNT_DATA, 'no_publishable_widgets', {
          loggedIn: true,
          labels: ['no_publishable_widgets'],
        });
      }

      // Authenticated-looking URL but empty widgets — treat as not ready once.
      if (/\/overview|\/account|\/rewards/i.test(surface.path) && surface.bodyLen < 200) {
        return buildResult(ExtractionStatus.NOT_READY, 'spa_not_hydrated', {
          loggedIn: false,
          labels: ['spa_not_hydrated'],
        });
      }

      console.log(LOG, 'no account data');
      return buildResult(ExtractionStatus.NO_ACCOUNT_DATA, 'no_publishable_widgets', {
        loggedIn: surface.signedInChrome,
        labels: ['no_publishable_widgets'],
      });
    } catch (e) {
      console.warn(LOG, 'extraction fatal', e && e.message);
      return buildResult(ExtractionStatus.EXTRACTION_FAILED, 'dom_changed', {
        loggedIn: false,
        labels: ['dom_changed'],
      });
    }
  }

  // Backward-compatible alias used by older callers / messaging.
  function extractMembershipRewardsLegacy() {
    const r = extractAmexAccountData();
    return {
      loggedIn: !!r.loggedIn,
      value: r.value || null,
      raw: r.value || null,
      status: r.status,
      reason: r.reason,
    };
  }

  function maybeReport() {
    const result = extractAmexAccountData();
    if (result.status !== ExtractionStatus.EXTRACTION_SUCCESS || !result.value) return;
    chrome.runtime.sendMessage({
      type: 'AMEX_MR_EXTRACTED',
      value: result.value,
      url: location.href,
      extraction_status: result.status,
      extraction_reason: result.reason,
      publishable_fields: result.publishable_fields,
    }).catch(() => {});
  }

  // Initial + delayed passes for SPA render (content-script path only).
  maybeReport();
  setTimeout(maybeReport, 2500);
  setTimeout(maybeReport, 6000);

  let debounce;
  const obs = new MutationObserver(function () {
    clearTimeout(debounce);
    debounce = setTimeout(maybeReport, 1500);
  });
  if (document.body) {
    obs.observe(document.body, { childList: true, subtree: true, characterData: true });
  }

  window.__mightyExtractAmexMR = extractMembershipRewardsLegacy;
  window.__mightyExtractAmexAccountData = extractAmexAccountData;
  window.__MightyAmexExtractionStatus = ExtractionStatus;
})();
