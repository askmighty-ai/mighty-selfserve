# Alpha Readiness Audit

Audit date: 2026-07-02  
Scope: onboarding, account connection, extension install, login-required flow, error messages, loading states, recovery, settings, account removal, browser compatibility.

---

## Prioritized issue list

### P0 — Blockers (fixed in this PR)

| Issue | Area | Status |
|-------|------|--------|
| `/api/sync/failure` and `/api/sync/login-cleared` wrote Amex `connection_status` for every provider | Login required / sync | **Fixed** — only providers with formal session verification (Amex today) update `connection_status`; others rely on `sync_status` |
| Dashboard showed “Get Chrome Extension (coming soon)” with dead link | Extension install | **Fixed** — links to `/extension-setup` |
| Extension setup page showed false success after 3s when extension never connected | Extension install | **Fixed** — 8s timeout shows actionable install/reload guidance |
| Disconnect did not reset `email_suggestions.added` | Account removal | **Fixed** |
| Full account delete omitted `email_suggestions` rows | Account removal | **Fixed** |

### P1 — High (documented; not fixed — >1 hour or out of alpha scope)

| Issue | Area | Notes |
|-------|------|-------|
| Session verification is Amex-only | Account connection | `mighty/adapters/extension.py` — Delta/Marriott/etc. use `sync_status` heuristics only; extend adapter before multi-provider alpha |
| No Chrome Web Store install path | Extension install | Alpha assumes unpacked sideload; need store listing + install URL |
| Extension manifest hardcodes production Railway URL | Extension / dev | `extension/manifest.json` — local/staging dashboard relay won't fire without manual URL edit |
| Monolithic `app.py` (~20k lines) | Maintainability | Onboarding, settings, connect modal, and routes share one file — refactor risk for alpha |

### P2 — Medium (documented)

| Issue | Area | Notes |
|-------|------|-------|
| Dead `ONBOARDING_HTML` 3-step wizard | Onboarding | `/onboarding` redirects to dashboard; wizard template never rendered |
| Two onboarding UX paths | Onboarding | Dashboard privacy modal vs unused wizard — consolidate post-alpha |
| `openModal()` dead code on credentials page | Account connection | Redirects to `/email-scan` but nothing calls it |
| Mobile web is view-only with no in-browser sync | Browser compatibility | By design; mobile app is alternate path but not alpha-tested |
| Firefox/Safari/Edge unsupported | Browser compatibility | No extension; only mobile-userAgent gate on sync button — no explicit landing-page message |
| Server-side retry in Settings can fail silently on busy sync | Recovery | `SETTINGS_RETRY_FAILED` copy exists; no queue visibility |

### P3 — Low (documented)

| Issue | Area | Notes |
|-------|------|-------|
| No JS/extension E2E tests | Tests | Python pytest only; extension behavior verified manually |
| No mobile app automated tests | Tests | Expo app untested in CI |
| Popup links hardcoded to production URL | Extension | `extension/popup.html` |
| Push notification blocked state relies on generic browser copy | Settings | Acceptable for alpha |

---

## Area summaries

### Onboarding
- **Active:** first-visit dashboard modal (`onboarded=0`) + accounts empty-state card.
- **Fixed:** Chrome requirement called out on signup and onboarding modal.
- **Deferred:** remove or wire up dead `/onboarding` wizard template.

### Account connection flow
- **Active:** credentials connect modal → extension poll → lifecycle states.
- **Fixed:** sync failure/login-cleared no longer corrupt non-Amex `connection_status`.
- **Deferred:** formal connection state machine beyond Amex.

### Extension install
- **Active:** Settings → `/extension-setup` auto-config via meta tag.
- **Fixed:** dashboard install CTA + honest setup timeout messaging.

### Login required flow
- **Active:** extension reports failure; dashboard banner + lifecycle badges; `/api/sync/login-cleared` on re-auth.
- **Fixed:** provider-scoped connection status updates.

### Error messages
- **Good:** canonical copy in `mighty/user_copy.py` (`FAILURE_ACTIONS`).
- **Gap:** some dashboard JS still uses generic `alert()` — acceptable for alpha.

### Loading states
- **Good:** connect modal spinner, sync progress steps, extension-setup spinner, dashboard `is-syncing` cards.
- **Gap:** 12-minute sync poller give-up reloads without explicit user message.

### Recovery after failure
- **Good:** extension retry CTA, login-cleared path, Settings server retry fallback.
- **Fixed:** disconnect clears email suggestion “added” flag so account can be re-added cleanly.

### Settings
- **Good:** notifications, privacy, API key, extension setup link, danger zone with password confirm.
- **Fixed:** delete account now removes email suggestions.

### Account removal
- **Per-account:** `POST /credentials/delete/<source>` — cleans credentials, data, field tables.
- **Full account:** `POST /settings/delete-account` — password-gated, clears session.
- **Fixed:** email_suggestions handled in both paths.

### Browser compatibility
- **Chrome + extension:** primary alpha path (documented in ALPHA.md).
- **Mobile web:** sync disabled; toast nudges to app/desktop.
- **Fixed:** signup/onboarding state Chrome requirement explicitly.

---

## Regression tests

Added `tests/test_alpha_readiness.py` covering:
- Sync failure / login-cleared provider scoping
- Disconnect resets email suggestion
- Full delete removes email suggestions
- Extension setup page meta + timeout copy
- Dashboard extension link
- Signup/onboarding Chrome copy

Run: `pytest tests/test_alpha_readiness.py -v`
