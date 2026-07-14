/**
 * Minimal DOM harness for extractAmexAccountDataPage (sourced from background.js).
 * Usage: node tests/js/amex_extract_harness.mjs <fixture-name>
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import vm from 'vm';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '../..');
const bg = fs.readFileSync(path.join(root, 'extension/background.js'), 'utf8');

const start = bg.indexOf('function extractAmexAccountDataPage()');
const end = bg.indexOf('/** @deprecated Alias — callers should use extractAmexAccountDataPage.');
if (start < 0 || end < 0) {
  console.error(JSON.stringify({ error: 'extractAmexAccountDataPage not found' }));
  process.exit(2);
}
const fnSrc = bg.slice(start, end);

function makeDom({ pathName = '/overview', bodyText = '', readyState = 'complete' } = {}) {
  const body = {
    innerText: bodyText,
    textContent: bodyText,
  };
  const doc = {
    body,
    readyState,
    querySelectorAll(sel) {
      // Minimal stub: headingWalk / selectors see no nodes unless bodyText drives regex.
      return [];
    },
  };
  return {
    location: {
      pathname: pathName,
      href: `https://global.americanexpress.com${pathName}`,
    },
    document: doc,
    console: { log() {}, warn() {} },
  };
}

const FIXTURES = {
  overview_with_mr: {
    pathName: '/overview',
    bodyText: 'Account Home Recent activity Membership Rewards 125,000 points Manage account',
  },
  overview_balances_only: {
    pathName: '/overview',
    bodyText: 'Account Home Statement Balance $1,234.56 Card ending in 1234 Manage account',
  },
  spa_not_hydrated: {
    pathName: '/overview',
    bodyText: ' ',
    readyState: 'loading',
  },
  rewards_page: {
    pathName: '/en-us/rewards',
    bodyText: 'Membership Rewards 88,500 Redeem points Account Home',
  },
  multiple_cards: {
    pathName: '/overview',
    bodyText: (
      'Account Home Card ending in 1111 Statement Balance $10.00 '
      + 'Card ending in 2222 Statement Balance $20.50 Membership Rewards 50,000'
    ),
  },
  zero_publishable: {
    pathName: '/overview',
    bodyText: 'Account Home Recent activity Manage account Welcome back',
  },
  marketing: {
    pathName: '/en-us/credit-cards',
    bodyText: 'Compare credit cards and apply today for great rewards',
  },
  login: {
    pathName: '/account/login',
    bodyText: 'Sign in to your account User ID Show password Forgot password',
  },
};

const name = process.argv[2] || 'overview_with_mr';
const fixture = FIXTURES[name];
if (!fixture) {
  console.error(JSON.stringify({ error: `unknown fixture ${name}`, known: Object.keys(FIXTURES) }));
  process.exit(2);
}

const sandbox = makeDom(fixture);
vm.createContext(sandbox);
vm.runInContext(`${fnSrc}\nthis.__result = extractAmexAccountDataPage();`, sandbox);
const result = sandbox.__result;
// Never print balances — only status/reason/keys.
console.log(JSON.stringify({
  status: result.status,
  reason: result.reason,
  publishable_fields: result.publishable_fields,
  field_count: (result.fields || []).length,
  loggedIn: result.loggedIn,
  diagnostics: result.diagnostics,
}));
