# Dogfood V1 — One-Week Operational Plan

**Status:** Operational plan — not an implementation plan  
**Audience:** Dogfood testers, product, engineering  
**Duration:** One week  
**Branch / surface under test:** Product Flow V1 + Accounts + First-Data Handoff V1 (`feat/product-flow-v1` or the deploy that includes it)  
**Related:** [PRODUCT_FLOW_V1.md](PRODUCT_FLOW_V1.md) · [ACCOUNTS_HANDOFF_V1_PLAN.md](ACCOUNTS_HANDOFF_V1_PLAN.md) · [PRODUCT_MANIFESTO.md](PRODUCT_MANIFESTO.md) · [HOME_V1.md](HOME_V1.md)

---

## 1. Dogfooding goals

Use Mighty daily as a real customer for one week. The goal is not feature expansion — it is to learn whether the journey holds under ordinary life.

| Goal | Success looks like |
|------|--------------------|
| **Journey coherence** | Testers can complete discovery → enrollment → Mighty in Chrome (when needed) → first verification → Home without competing setup destinations |
| **Trust** | Home answers “Am I good?” honestly; silence when healthy; one clear ask when not |
| **Terminology** | Customer UI stays on Accounts / Find accounts / Mighty in Chrome / sign in — no Worker / Account Center / Control center confusion |
| **Autonomous repair** | Access hiccups either fix themselves or escalate once with a single human CTA |
| **Friction inventory** | Every real stuck moment is logged with severity, journey stage, and evidence |

**Non-goals for Dogfood V1:**

- Account Detail design or implementation  
- Global navigation redesign  
- Broadening auto-enroll providers beyond the current alpha set  
- Mobile capture parity  
- New Activity event types  

---

## 2. Daily workflow for a tester

**Environment:** Desktop Chrome with Mighty in Chrome installed. Use a real Gmail (or mailbox) you own. Prefer providers you already use (e.g. Amex).

### Morning (≈5 minutes)

1. Open Home (`/dashboard`). Do **not** start on Find accounts or Settings.
2. Read the featured story only. Note:
   - Are you good?
   - Is there exactly one primary action, or none?
   - Does the copy name the account and next step when setup is incomplete?
3. If Home asks for something, do **only that** action (set up Mighty in Chrome, visit provider, sign in, approve).
4. Glance at the Mighty in Chrome popup (≤3 seconds). Does it match Home’s emotional read?

### Midday / natural browsing (ambient)

5. Use Chrome normally. Visit enrolled providers while signed in when you would anyway.
6. Do **not** ritual “Sync now.” If you feel the urge, log it as friction.
7. If something seems broken, wait once (autonomous recovery) before intervening — unless Home already asks you to act.

### Evening (≈10 minutes)

8. Open Home again. Compare morning vs evening.
9. Open Accounts (`/credentials`) only if:
   - Home said something needs repair, or
   - you want to audit what Mighty knows.
10. Open Activity only if you use agents / have pending approvals (many testers will skip).
11. Fill the **Friction Log** for anything confusing, duplicated, wrong, or dead-ended.
12. Optional: note one sentence — “Today Mighty felt ___.”

### Do not

- Report known limitations listed in §4 as new bugs.  
- Redesign the product in the log — describe what happened.  
- Use mobile as the primary capture path.  
- Install parallel “Account Center” habits — Accounts is the repair destination.

---

## 3. Key journeys to exercise

Exercise each at least once during the week. Prefer real accounts over demo data.

| # | Journey | What to do | Pass signal |
|---|---------|------------|-------------|
| J1 | **First launch / Empty Home** | Fresh or empty portfolio → Home | Connect Gmail is the clear primary; no Mighty in Chrome demand before Gmail |
| J2 | **Gmail discovery → enroll** | Connect Gmail; allow auto-enroll | Land on Home with lightweight confirmation (which account, what’s next, whether you must act) — not a connect-modal ritual |
| J3 | **Mighty in Chrome when required** | After enroll, if capture needs the extension | Single “Set up Mighty in Chrome” path; popup / setup page use customer language |
| J4 | **First provider visit → verification** | Visit enrolled provider while signed in | Status moves toward verifying / ready without a sync button |
| J5 | **Steady Home** | Healthy day with data | “You’re good.”; no primary CTA; no setup guilt |
| J6 | **Accounts repair** | From Home or popup → Accounts | One destination (`/credentials`); old `/account-center` links still land on Accounts |
| J7 | **Sign-in required** | Let a session expire or sign out at provider | Attention asks once; Accounts shows honest status; Recovery tried silently first when applicable |
| J8 | **Ambiguous discovery** | Providers found but not auto-enrolled | Stay on Find accounts / Accounts; Home does not spam interrupts |
| J9 | **Re-entry after inactivity** | Skip a day, then open Home | No “start over” wizard; same ownership rules |
| J10 | **Activity (if applicable)** | Pending agent approval or completed action | Approvals + receipts readable; no recovery/session rows as fake activity |

---

## 4. Known limitations that should not be reported

Do **not** file these as Dogfood V1 defects (unless severity jumps — e.g. data loss or security):

| Limitation | Why it’s out of scope |
|------------|------------------------|
| No dedicated Account Detail drill-in | Explicitly deferred (Product Flow D5) |
| Find accounts still in primary nav | Navigation redesign deferred; note as IA feedback only if it causes a dead end |
| Route path `/credentials` while nav says Accounts | Known; customer label is Accounts |
| Auto-enroll limited to current visible provider set (alpha) | Config / product expansion, not a journey bug |
| Ambiguous discoveries not on Home | Approved (D4) |
| Mobile Sync / capture gaps | Desktop Chrome is the capture path |
| Weekly digest email missing | Parking lot |
| Admin / debug panels if toggled on | Not customer surface |
| Legacy connect modal still reachable from Accounts | Allowed for repair; only a bug if it steals post-enroll Home landing |
| Internal engineering terms in docs / admin | Customer UI only is in scope |
| Pre-existing flaky or unrelated test failures | Engineering backlog, not dogfood UX |
| Screenshot / local OAuth “not configured” in dev | Environment, not product |

**Report instead** when a known limitation **creates a dead end**, **duplicate primary CTA**, **false “You’re good.”**, or **wrong repair destination**.

---

## 5. Friction Log template

Copy one block per incident. Prefer short facts over essays.

```text
### Friction — YYYY-MM-DD — <short title>

- Tester:
- Severity: S0 / S1 / S2 / S3 / S4
- Journey stage: (first launch | Gmail | enroll | Mighty in Chrome | verify | Home | Accounts | Attention | Activity | recovery | other)
- Surface: (Home | Accounts | Find accounts | Mighty in Chrome popup | setup page | Activity | other)
- What I was trying to do:
- What I saw / read:
- What I expected:
- Primary CTA shown (if any):
- Competing CTAs / destinations (if any):
- Did Home say I was good while something was incomplete? (Y/N)
- Evidence: (screenshot path, URL, approximate time, provider)
- Repro: (steps, or “once / intermittent”)
- Workaround used:
- Suggested severity rationale (one line):
```

**Optional daily rollup** (end of day):

```text
### Daily rollup — YYYY-MM-DD
- Mood (one sentence):
- Journeys exercised:
- New frictions (IDs or titles):
- Blockers still open:
```

---

## 6. Severity definitions

| Severity | Meaning | Response expectation |
|----------|---------|----------------------|
| **S0 — Stop** | Cannot proceed; data loss; security/privacy break; false connected with fake data | Stop dogfood for that path; escalate immediately |
| **S1 — Journey break** | Dead end, competing primaries that strand setup, wrong destination (e.g. Account Center product), “You’re good” while required setup hidden | Fix before broadening; same-day triage |
| **S2 — Trust / clarity** | Wrong status language, missing confirmation after enroll, Attention spam, Recovery asks too early | Fix in dogfood week or gate exit |
| **S3 — Friction** | Awkward copy, extra clicks, Find accounts temptation, mild inconsistency | Backlog; do not block exit alone |
| **S4 — Polish** | Visual nit, non-blocking label preference | Note only |

When unsure between S1 and S2: if the tester **cannot complete the happy path without guessing**, it is **S1**.

---

## 7. Criteria for ending Dogfood V1

End the one-week phase when **all** of the following are true:

1. **Timebox met** — seven calendar days of intended daily use completed (or early stop on S0).  
2. **Journey coverage** — J1–J7 exercised at least once by at least one tester; J8–J10 as applicable.  
3. **Friction Log closed for the week** — all S0–S2 items triaged (fix / accept / defer with owner).  
4. **No open S0**; no unowned S1.  
5. **Written verdict** (short):  
   - Continue on current spine, or  
   - Patch then continue, or  
   - Halt broadening until named S1s are fixed.  
6. **Exit criteria in §10** reviewed explicitly (pass / fail / waive with reason).

---

## 8. Recommended instrumentation or logging already available

Prefer existing signals. Do not invent a new analytics stack for Dogfood V1.

| Signal | Where | Use in dogfood |
|--------|--------|----------------|
| Home enrollment / story | Home UI (`data-enrollment`, `data-story`) | Confirm handoff vs all-clear vs Attention |
| Attention view API | `GET /api/attention/view?surface=home\|worker\|…` | What interrupt was ranked |
| Account status / access loop | `GET /api/account-status` | Popup vs Home consistency |
| Extension heartbeat | `users.extension_version` / `extension_last_seen_at` | Mighty in Chrome presence |
| Discovery dispositions | `account_discovery` + Find accounts UI | Auto-enroll vs ambiguous |
| Session / verification | Access Manager / session verification jobs (server logs) | Whether verify ran without customer GET triggers |
| Recovery cases | Recovery store / supervisor metrics | Silent repair before human ask |
| Activity / receipts | `/activity`, `GET /api/activity` | Agent path only |
| Route timing logs | `[RouteTiming]` server logs | Perf only if a page feels broken |
| PR screenshots | `docs/pr-screenshots/accounts-handoff-v1/` | Baseline expected states |

**Tester practice:** For S1+, attach a Home screenshot and note whether Accounts / popup agreed. Paste Attention primary title if visible.

---

## 9. Suggested daily review cadence

| When | Who | Agenda (≤20 minutes) |
|------|-----|----------------------|
| **Daily standup / async** | Testers + one owner | New S0/S1; any false “You’re good”; one win |
| **Mid-week (Day 3–4)** | Product + eng | Pattern check: setup competition, terminology, verify stalls |
| **End of week** | Product + eng | Close Dogfood V1 per §7; score §10 exit criteria; decide next vertical |

**Owner role:** One person consolidates the Friction Log, assigns severity, and prevents duplicate “known limitation” noise.

---

## 10. Exit criteria before broadening implementation

Do **not** start Account Detail, nav IA redesign, or large new milestones until Dogfood V1 exits **green** or **amber with waivers**.

| # | Exit criterion | Green | Amber (waive with owner) | Red (block) |
|---|----------------|-------|---------------------------|-------------|
| E1 | Post-enroll lands on Home with confirmation, not a competing modal | Observed | Rare fluke with repro | Systematic connect-modal / dual-destination land |
| E2 | Single repair destination (Accounts); `/account-center` only redirects | Observed | Copy nit only | Live parallel Account Center UI |
| E3 | Gmail-first; Mighty in Chrome only when needed | Observed | Edge case documented | Extension required before Gmail on Empty |
| E4 | No “You’re good” while required setup incomplete | Observed | One mis-rank with fix plan | Repeatable false calm |
| E5 | One primary CTA (or none) on Home during first-data | Observed | Secondary text-weight only | Two filled competing setup CTAs |
| E6 | Sign-in / verify path completable without sync ritual | Observed | Slow verify with honest Waiting | Stuck with no CTA and no progress |
| E7 | Customer language: Mighty in Chrome / Accounts (no Worker / Account Center in primary UI) | Observed | Residual rare string | Systemic jargon |
| E8 | S0 = 0; S1 = 0 unowned | Met | S1 fixed or scheduled before next slice | Open S1 without owner/date |

**Broadening allowed after green/amber:** next vertical per Product Flow (e.g. Account Detail only after E1–E6 hold).

**Broadening forbidden on red:** do not merge into a “done” customer narrative or expand scope until reds clear.

---

## Document control

| Field | Value |
|-------|-------|
| Nature | Operational dogfood plan |
| Implementation | None — process and observation only |
| Merge | Do not merge this phase solely because the doc exists; merge product code only when product decides after dogfood |
