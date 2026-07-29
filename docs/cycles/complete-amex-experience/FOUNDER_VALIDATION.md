# Founder Validation — Complete American Express Experience

**Date:** 2026-07-29 20:42 UTC
**Commit:** `32386cdc` (Complete Amex Experience)
**Session user:** `founder_at00_46f39ba0@test.local`
**Environment:** Local fresh DB (`.tmp-founder-validate-complete-amex.db`); no deploy
**Method:** Continuous single-user session; Gmail OAuth and live Amex/Chrome where unavailable were recorded as Unexpected and simulated only via product APIs the extension would call

## 1. Acceptance Test Summary

| AT | Verdict | Notes |
|----|---------|-------|
| AT-00 | **Fail** | Fresh Install path exercised through Confirm → Enable Monitoring → Home; blocked before steady watching without real Chrome/Amex |
| AT-13 | **Pass** | Compiler: Chrome SYSTEM present, Amex AUTH suppressed when worker missing |
| AT-01 | **Fail** | API lifecycle honest but Home does not present unsupported-data outcome |
| AT-05 | **Pass** | Unsupported-data API/Accounts honesty; Home hero gap tracked under AT-08 |
| AT-08 | **Fail** | Home vs Accounts consistency |
| AT-11 | **Pass** | Intent-only compose beat=intent tier=intent |
| AT-12 | **Pass** | repeat_ask beat; why-previous visible=True |
| AT-03 | **Pass** | Logged-out / needs-user story present; no false success calm |
| AT-02 | **Pass** | With verification_progress observation, narrative advances to observed_progress |
| AT-04 | **Partial** | Could not fully simulate mid-flow expiry; compose=progress |
| AT-06 | **Pass** | Reload preserves non-upgraded tier=intent |
| AT-07 | **Pass** | Reopen Home reflects current Amex lifecycle; no wizard restart |
| AT-09 | **Partial** | No false Permission to Leave in hero while unsupported-data active (correct restraint); true all-clear beat not reached this session |
| AT-14 | **Partial** | Steady clear return unexercised; no false calm regression observed |
| AT-10 | **Pass** | Home alone provides enough signal to answer whether Mighty is working / what it needs |

### Detail ledger

### AT-00 — Fail
- Fresh Install path exercised through Confirm → Enable Monitoring → Home; blocked before steady watching without real Chrome/Amex
- Landing HTTP 200; signup → 302 loc=/email-scan
- Post-signup surface contains email-scan/discover cues: True
- Confirm Amex → 302 /enable-monitoring
- Enable monitoring: outcome-first=True; chrome CTA=True
- Extension setup page HTTP 200; never-dead-end cues=True
- Home after enable/skip path: Amex in API=True; Visit/Sign-in cues=True
- **Unexpected:** Gmail OAuth cannot be completed in automated Founder Validation; seeding Amex discovery facts as the post-Gmail system would, then continuing the same user session (not a full environment reset).
- **Contradiction:** Could not reach steady-state quiet watching of Amex without real Chrome extension + Amex session — AT-00 incomplete

### AT-13 — Pass
- Compiler: Chrome SYSTEM present, Amex AUTH suppressed when worker missing
- Home chrome cues=True

### AT-01 — Fail
- API lifecycle honest but Home does not present unsupported-data outcome
- home_unsupported=False first_ask=True invented=False intent_ok=True
- **Unexpected:** Amex terminal via extension API simulation (no live Amex browser login)
- **Contradiction:** Home does not present unsupported-data outcome after terminal (still first-manage/Visit or missing knows/does-not-know copy) while API/Accounts say Logged in — no account data

### AT-05 — Pass
- Unsupported-data API/Accounts honesty; Home hero gap tracked under AT-08
- meaning='Mighty can tell you are signed in, but no account details were available yet.' bg='No account data'

### AT-08 — Fail
- Home vs Accounts consistency
- **Contradiction:** Home hero contradicts Accounts/API unsupported-data (still first-manage/Visit)

### AT-11 — Pass
- Intent-only compose beat=intent tier=intent

### AT-12 — Pass
- repeat_ask beat; why-previous visible=True

### AT-03 — Pass
- Logged-out / needs-user story present; no false success calm
- **Unexpected:** Session signed-out simulated via journey observations + prior needs-login; not a live Amex logout

### AT-02 — Pass
- With verification_progress observation, narrative advances to observed_progress
- **Unexpected:** Live 'already signed into Amex' browser session not available; used system observation as product would after extension sees session

### AT-04 — Partial
- Could not fully simulate mid-flow expiry; compose=progress

### AT-06 — Pass
- Reload preserves non-upgraded tier=intent

### AT-07 — Pass
- Reopen Home reflects current Amex lifecycle; no wizard restart
- plc=unsupported-data
- **Unexpected:** 30-minute wall-clock wait not literally elapsed; validated reopen/current-evidence behavior in-session

### AT-09 — Partial
- No false Permission to Leave in hero while unsupported-data active (correct restraint); true all-clear beat not reached this session
- **Unexpected:** Could not produce success-with-data Amex terminal without inventing balances — AT-09 all-clear path unexercised

### AT-14 — Partial
- Steady clear return unexercised; no false calm regression observed

### AT-10 — Pass
- Home alone provides enough signal to answer whether Mighty is working / what it needs

## 2. Blocking failures

- **AT-00:** Fresh Install path exercised through Confirm → Enable Monitoring → Home; blocked before steady watching without real Chrome/Amex
  - Could not reach steady-state quiet watching of Amex without real Chrome extension + Amex session — AT-00 incomplete
- **AT-01:** API lifecycle honest but Home does not present unsupported-data outcome
  - Home does not present unsupported-data outcome after terminal (still first-manage/Visit or missing knows/does-not-know copy) while API/Accounts say Logged in — no account data
- **AT-08:** Home vs Accounts consistency
  - Home hero contradicts Accounts/API unsupported-data (still first-manage/Visit)

### Partials (block AT-15 / production-complete claim)
- **AT-04:** Could not fully simulate mid-flow expiry; compose=progress
- **AT-09:** No false Permission to Leave in hero while unsupported-data active (correct restraint); true all-clear beat not reached this session
- **AT-14:** Steady clear return unexercised; no false calm regression observed

## 3. Recommended fixes (fewest cycles)

### Cycle A — Home projects Amex canonical lifecycle (AT-01 / AT-08) + Fresh Install close (AT-00 / AT-09 / AT-14 / AT-15)

Single implementation cycle:

1. **Home hero for unsupported-data:** When Amex `product_lifecycle.state == unsupported-data`, Home must not keep the first-run “beginning to manage / Visit” ask as if nothing was learned. Project the same knows/does-not-know/why-next as Accounts/API (“Logged in — no account data” + stay-signed-in next action).
2. **Fresh Install completion:** Keep Discover → Confirm → Enable Monitoring → Chrome → Visit → terminal coherent; Founder re-run AT-00 live with real Chrome + Amex.
3. **Earned Permission to Leave:** Only after a true clear/success-with-data (or honest quiet watching with no outstanding Amex next action); retest AT-09/AT-14.

**Out of scope for A:** other providers; visual door migration; governance changes.

### Cycle B — only if Cycle A retest still fails

Session-expiry mid-flow (AT-04) if still Partial after live Amex: ensure progress observations clear when session is lost so compose cannot stay on `progress` after needs-login.

**Do not open** a governance/architecture cycle — documented product is sufficient; gaps are realization.

---

**Deploy:** still stopped.
