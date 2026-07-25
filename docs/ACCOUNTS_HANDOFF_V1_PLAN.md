# Accounts + First-Data Handoff V1 — Implementation Plan

**Branch:** `feat/product-flow-v1`  
**Status:** Ready for implementation  
**Product decisions:** [PRODUCT_FLOW_V1.md](PRODUCT_FLOW_V1.md) (approved D1–D5)  
**Related:** [HOME_V1.md](HOME_V1.md) · [ACCOUNT_DISCOVERY.md](ACCOUNT_DISCOVERY.md) · [ACCESS_FLOW.md](ACCESS_FLOW.md) · [ENGINEERING_CHARTER.md](ENGINEERING_CHARTER.md)

---

## Goal

Create one coherent path from:

```text
discovery → enrollment → required browser access → first successful verification → Home
```

**User outcome:** After Gmail discovery enrolls an account, the user always knows (1) which account Mighty is managing, (2) what Mighty is doing next, and (3) whether they must act — with exactly one primary action (or none). They never choose between competing setup destinations (Account Center vs Accounts vs connect modal vs Find accounts).

**Non-goals:**

- Account Detail implementation (D5)
- Full global navigation redesign (Find accounts may remain in nav; do not invent a fifth surface)
- New canonical account-state owner
- Speculative capabilities (digest email, mobile IA overhaul, Activity expansions)

---

## Current-state inventory

| Surface | Route / module | Role today | Problem |
|---------|----------------|------------|---------|
| Accounts | `GET /credentials` · `accounts_ui.py` · `_build_credentials_page` | Portfolio repair list | Correct destination, but not the only one |
| Account Center | `GET /account-center` · `account_center_ui.py` | Parallel connection cards | Duplicate repair UI; Worker popup deep-links here |
| Find accounts | `GET /email-scan` · Gmail/Outlook OAuth | Mailbox discovery | Post-Amex enroll redirects to `?connect=amex` modal |
| Enrollment | `discovery_enrollment` · `_register_account_source` | Canonical enroll write | Works; confirmation presentation missing |
| Verification | PAM · natural session · session verification | Access evidence | Correct; entry CTAs fragmented |
| Home | `/dashboard` · `home_state` · `home_projection` · `home_ui` | Briefing | WAITING demoted to ops → can say “You’re good” during first-data |
| Mighty in Chrome setup | `/extension-setup` · Attention SYSTEM | Install / API key | Copy says “Worker”; popup says “Account Center” |
| Extension popup | `extension/popup.js` · `popup.html` | Ambient glance | CTA → `/account-center` |
| Connect modal | `/credentials?connect=` · Amex FSM APIs | Parallel first-data ritual | Competes with Home handoff |
| Presence | `extension_version` · `load_worker_signal` · Attention `compile_worker_attention` | Detect installed/reachable | Home `worker_setup_needed` never passed from `app.py` (Attention owns interrupt) |

---

## Canonical destination

**`/credentials` (nav label: Accounts)** is the single customer destination for:

- connected accounts,
- incomplete setup,
- login repair,
- browser-access requirements,
- account management.

### `/account-center`

**Decision: redirect** to `/credentials` (preserve query string when safe).

| Option | Choice |
|--------|--------|
| Redirect | **Yes** — `GET /account-center` → `302 /credentials` |
| Alias UI | No — do not keep dual HTML |
| Remove module immediately | Optional follow-up; keep `account_center_ui.py` unused until a cleanup PR if tests allow redirect-only |

**Backward compatibility:**

- Extension popup, bookmarks, and old deep links hit redirect.
- Tests updated to expect redirect.
- Row CTAs that already point at `/credentials?connect=` remain valid secondary repair entries; they must not auto-steal Home’s post-enroll primary path.

---

## State model

Presentation mapping only — **no new account-state owner**. Facts come from Discovery, enrollment/lifecycle, AccountStatus / customer access, Attention, and Access Manager evidence.

| Customer state | Meaning | Canonical facts (read) | Primary surface |
|----------------|---------|------------------------|-----------------|
| **Found** | Discovery saw a provider | `account_discovery` disposition `discovered` / `eligible` | Find accounts |
| **Ready to add** | Ambiguous / low-confidence; user may add | disposition `ambiguous`; not auto-enrolled | Find accounts / Accounts |
| **Added** | Enrolled; watching; no first usable data yet | credentials stub + lifecycle waiting; not `UP_TO_DATE` | Home handoff + Accounts “Still setting up” |
| **Needs Mighty in Chrome** | Browser capture required and extension missing/unreachable | Attention SYSTEM (`INSTALL_WORKER`) and/or status `WAITING_FOR_EXTENSION` | Home (Attention) → `/extension-setup` |
| **Needs sign-in** | User must sign in at provider | Attention AUTH / `NEEDS_LOGIN` / readiness signed_out | Home (Attention) → provider; Accounts for audit |
| **Verifying** | Access/verify/extract in progress | `UPDATING` / `CHECKING` / active verification | Home progress / ops; Accounts waiting |
| **Ready** | First usable data confirmed | `UP_TO_DATE` + meaningful extraction | Home all-clear (or Attention if something else ranks) |
| **Could not verify** | User-visible failure after autonomy / escalation | `ERROR` / escalated Attention access_degraded or auth | Home Attention; Accounts repair |

Ambiguous discoveries never become Attention interrupts unless Attention already selects a materially useful, resolvable item (approved D4 — default: keep on Find accounts / Accounts only).

---

## Primary-action rules

Exactly **one** primary action or **none** per moment.

| State | Primary action | Label (customer) | Target |
|-------|----------------|------------------|--------|
| Empty portfolio | Connect Gmail | Connect Gmail | `/email-scan` |
| Found / Ready to add (on Find accounts) | Add account (optional) | Add | enroll API |
| Added + Mighty in Chrome missing | Set up Mighty in Chrome | Set up Mighty in Chrome | `/extension-setup` |
| Added + extension OK + needs visit | Visit provider | Visit {Provider} | Provider URL |
| Needs sign-in | Continue sign-in | Sign in | Provider login URL |
| Verifying | None (or disabled “Verifying…”) | — | — |
| Ready | None | — | — |
| Could not verify | Try again / Sign in (Attention-selected) | Try again / Sign in | Attention CTA → Accounts if audit |

**Suppressed as co-equal primaries during first-data handoff:**

- Find accounts nav as a competing hero CTA
- Auto-open `/credentials?connect=` after Gmail enroll
- Extension popup “Account Center” as a second product
- Dual filled buttons on Home (secondary may remain text-weight only)

---

## First-data handoff

| Step | What happens | Copy (intent) | Transition |
|------|--------------|---------------|------------|
| 1 | Enrollment succeeds (auto or Add) | — | Discovery → enrolled stub |
| 2 | Land on **Home** (not connect modal) | Lightweight confirmation: “Mighty is beginning to manage {Provider}.” | `story_kind=handoff` |
| 3 | If browser access required and Mighty in Chrome missing | “Set up Mighty in Chrome so Mighty can verify access while you browse.” | Attention SYSTEM or handoff CTA → `/extension-setup` |
| 4 | Else if sign-in / first visit needed | “Visit {Provider} in Chrome while signed in — Mighty handles the rest.” | CTA → provider URL |
| 5 | Verification runs (PAM / natural session) | “Verifying {Provider}…” / ops progress | status Verifying |
| 6 | First usable data confirmed | All-clear or wins | Ready |
| 7 | Home reflects result | “You’re good.” only when no required setup remains | all_clear |

**Gmail-first (D2):** Empty Home never requires Mighty in Chrome before Connect Gmail. Attention `compile_worker_attention` already skips empty portfolios — preserve that.

**Post-enroll redirect change:**

- From: `/credentials?connect=amex` (auto-opens modal)
- To: `/dashboard` (Home handoff story)

Connect modal remains available from Accounts for explicit repair, not as the default enroll landing.

---

## Home integration

Home remains a **pure projection** (`home_projection` composes; does not re-rank Attention).

| Moment | Home behavior |
|--------|---------------|
| Newly added | Featured handoff story from enrollment context (`HomeState.WAITING` featured) when Attention has no primary |
| Setup automatic | Answer communicates progress; CTA none or disabled while Verifying |
| One unavoidable step | Attention primary **or** handoff featured CTA (never both equal) |
| First verification complete | All-clear story; no primary CTA |
| Required setup hidden | **Forbidden** — do not emit “You’re good.” while state is WAITING or Attention interrupt |

**Story composition order (updated):**

1. `EMPTY` → enrollment empty story (Connect Gmail)
2. Attention primary present → Attention story
3. `WAITING` (no Attention primary) → **handoff confirmation** (use `home_state` featured; do not demote to all-clear)
4. Else → all-clear

Ops strip may still whisper secondary portfolio notes; it must not be the only signal for newly enrolled accounts.

Lightweight confirmation must **not** become a permanent Home section or require acknowledgement (D1).

---

## Terminology

| Use | Avoid in customer UI |
|-----|----------------------|
| Mighty in Chrome | worker, Worker, browser agent, Chrome helper, connector |
| Chrome extension | only in install / troubleshooting / store copy |
| Accounts | Account Center, Control center, credentials (as product name) |
| Find accounts | discovery engine |
| sign in | session expired (as primary), auth truth |
| verifying / verification | only when customer-understandable |
| — | sessions, synchronization, recovery planner, capability, provider-state internals, extraction |

Centralize copy in `mighty/user_copy.py`; update Attention SYSTEM strings, onboarding modal, `/extension-setup`, and extension popup defaults/`api_copy_bundle`.

Engineering identifiers (`WorkerSignal`, `INSTALL_WORKER`, table columns) may remain.

---

## Failure and recovery

- Reuse Recovery planner/store/supervisor before human interrupt.
- Attention owns interruption after escalation or human-only classes.
- When user involvement is required, **Accounts** (`/credentials`) is the audit/repair destination; Home shows the single Attention CTA.
- Do not invent a Recovery UI or expose capability names.

---

## Route consolidation

| Change | Behavior |
|--------|----------|
| `GET /account-center` | `302` → `/credentials` (optionally preserve `?filter=` if present and valid) |
| Extension popup CTA | href `/credentials`; label “Open Accounts” (or Attention-driven) |
| Gmail/Outlook callback after Amex auto-enroll | `302` → `/dashboard` |
| `/extension-setup` | Remains setup bridge; customer title uses Mighty in Chrome |
| `/credentials?connect=` | Remains for explicit Accounts repair; not default post-enroll land |
| Nav | No full redesign; Accounts stays `/credentials`; Find accounts may remain for this slice |
| Stale docs/tests | Update assertions that lock Account Center or connect redirect |

---

## Acceptance criteria

1. **One repair destination:** Customer CTAs for manage/repair accounts resolve to `/credentials` (or redirect through it). `/account-center` does not render a parallel product page.
2. **No duplicate primary CTA** on Home during first-data (one filled action max).
3. **Gmail-first:** Empty Home primary is Connect Gmail; Mighty in Chrome is not required before discovery.
4. **Mighty in Chrome only when needed:** Setup CTA appears only when enrolled accounts need browser access and extension is missing/unreachable (Attention or handoff rules).
5. **Deterministic state mapping:** Customer states above map from existing facts without a new store.
6. **Successful first-data transition:** Enroll → Home handoff → (setup or visit) → verifying → Ready reflected on Home without “You’re good” during WAITING.
7. **Home consistency:** Handoff confirmation names the account, next step, and whether action is required; not a permanent section.
8. **Route compatibility:** Old `/account-center` links redirect; tests cover redirect.
9. **Strict user isolation:** All reads/writes remain user-scoped (existing patterns).
10. **Desktop/mobile:** Desktop Chrome is capture path; mobile must not invent a sync ritual as the happy path (honest limitation OK).

---

## Testing plan

| Area | Coverage |
|------|----------|
| Route tests | `/account-center` → `/credentials`; Gmail callback → `/dashboard`; `/credentials` still renders |
| State / projection | Home WAITING → handoff story (not all-clear); EMPTY still Gmail-first; Attention still wins when primary present |
| CTA tests | Single primary on handoff; popup href `/credentials`; setup labels Mighty in Chrome |
| Enrollment → verification | Auto-enroll lands Home; Accounts waiting section; no forced connect modal |
| Home consistency | No “You’re good” while WAITING; confirmation copy fields present |
| Extension handoff | `api_copy_bundle` + popup defaults; Attention INSTALL_WORKER URL `/extension-setup` |
| Recovery / Attention | Worker compile still skips empty; auth interrupt unchanged ownership |
| User isolation | Existing attention/home/accounts isolation tests remain green |
| Terminology | Customer strings avoid Worker / Account Center / Control center where asserted |

---

## Implementation order

1. Route redirect + extension popup retarget + tests  
2. Terminology pass (`user_copy`, Attention SYSTEM, onboarding, extension-setup)  
3. Post-enroll land on Home  
4. Home projection handoff story for WAITING  
5. Accounts empty/footer CTA weight (Gmail primary; avoid competing filled CTAs)  
6. Focused tests + screenshot script fixtures  
7. Broader regression suite  

---

## Architecture Decisions (planned)

### AD-HANDOFF-1: Redirect Account Center; do not dual-maintain

- **Decision:** `/account-center` redirects to `/credentials`.  
- **Why:** One repair destination.  
- **Impact:** Extension and bookmarks keep working via redirect.

### AD-HANDOFF-2: WAITING may own Home hero when Attention is silent

- **Decision:** Amend Home V1B “Waiting never owns hero” for first-data handoff only when Attention has no primary.  
- **Why:** Approved D1 forbids “You’re good” while setup is incomplete.  
- **Impact:** `home_projection._compose_story` uses enrollment featured for WAITING; Attention still wins when present.

### AD-HANDOFF-3: Post-enroll lands on Home, not connect modal

- **Decision:** Gmail/Outlook auto-enroll redirects to `/dashboard`.  
- **Why:** Single handoff spine; connect modal remains Accounts repair only.

### AD-HANDOFF-4: Presentation mapping only

- **Decision:** Handoff customer states are a mapping over existing facts — no new canonical store.  
- **Why:** Charter invariant — one owner per domain.
