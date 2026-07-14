// Amex Membership Rewards DOM extraction (runs on americanexpress.com account pages)
(function () {
  const LOG = '[Mighty Amex Extract]';

  function formatPoints(numStr) {
    const n = parseInt(String(numStr).replace(/[^\d]/g, ''), 10);
    if (!n || n <= 0) return null;
    return n.toLocaleString('en-US');
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

  function isLoginPage() {
    const path = location.pathname.toLowerCase();
    if (/\/account\/log-?in/.test(path)) return true;
    const sample = (document.body?.innerText || '').slice(0, 2500).toLowerCase();
    const loginHits = ['sign in to your account', 'user id', 'show password', 'forgot password']
      .filter(s => sample.includes(s)).length;
    const rewardsHit = sample.includes('membership rewards');
    return loginHits >= 2 && !rewardsHit;
  }

  function extractMembershipRewards() {
    console.log(LOG, 'extract attempt on', location.href);

    if (isLoginPage()) {
      console.log(LOG, 'login page detected — not logged in');
      return { loggedIn: false, value: null };
    }

    const strategies = [
      function selectors() {
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
              if (num) {
                console.log(LOG, 'selector hit', sel, num);
                return num;
              }
            }
          } catch (_) {}
        }
        return null;
      },
      function headingWalk() {
        const nodes = document.querySelectorAll('h1,h2,h3,h4,span,div,p,a,button');
        for (const node of nodes) {
          const t = (node.textContent || '').trim();
          if (!/membership rewards/i.test(t)) continue;
          let scope = node.closest('section,article,li,div') || node.parentElement;
          for (let depth = 0; depth < 4 && scope; depth++) {
            const num = findInRoot(scope);
            if (num) {
              console.log(LOG, 'heading walk found', num, 'near', t.slice(0, 40));
              return num;
            }
            scope = scope.parentElement;
          }
        }
        return null;
      },
      function bodyRegex() {
        const body = document.body?.innerText || '';
        // Value-bearing MR only — whitespace / points|balance|: between label and digits.
        const m = body.match(
          /Membership Rewards(?:®)?(?:\s*(?:points|balance|:))*\s*((?:[\d]{1,3}(?:,\d{3})+|(?!19\d{2}\b|20\d{2}\b)\d{4,7}))/i,
        );
        if (m) {
          console.log(LOG, 'body regex found', m[1]);
          return m[1];
        }
        return null;
      },
    ];

    for (let i = 0; i < strategies.length; i++) {
      const raw = strategies[i]();
      const formatted = formatPoints(raw);
      if (formatted) {
        console.log(LOG, `strategy ${i + 1} success →`, formatted);
        return { loggedIn: true, value: formatted, raw: raw };
      }
    }

    const bodyLower = (document.body?.innerText || '').toLowerCase();
    const loggedIn = bodyLower.includes('membership rewards')
      || bodyLower.includes('account home')
      || bodyLower.includes('recent activity');
    console.log(LOG, loggedIn ? 'logged in but balance not found' : 'not logged in');
    return { loggedIn, value: null };
  }

  function maybeReport() {
    const result = extractMembershipRewards();
    if (!result.loggedIn || !result.value) return;
    chrome.runtime.sendMessage({
      type: 'AMEX_MR_EXTRACTED',
      value: result.value,
      url: location.href,
    }).catch(() => {});
  }

  // Initial + delayed passes for SPA render
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

  // Expose for background executeScript
  window.__mightyExtractAmexMR = extractMembershipRewards;
})();
