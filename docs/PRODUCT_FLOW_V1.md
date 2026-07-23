# Product Flow V1

**Status:** Review document — defines the customer journey; does not implement it.  
**Audience:** Product, engineering, design  
**Related:** [PRODUCT_MANIFESTO.md](PRODUCT_MANIFESTO.md) · [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md) · [HOME_EXPERIENCE.md](HOME_EXPERIENCE.md) · [HOME_V1.md](HOME_V1.md) · [ACCOUNT_DISCOVERY.md](ACCOUNT_DISCOVERY.md) · [ACCESS_FLOW.md](ACCESS_FLOW.md) · [ATTENTION_AUTONOMOUS_RECOVERY.md](ATTENTION_AUTONOMOUS_RECOVERY.md) · [ACTIVITY_V1_IMPLEMENTATION_PLAN.md](ACTIVITY_V1_IMPLEMENTATION_PLAN.md) · [ROADMAP.md](ROADMAP.md)

---

## Purpose

Define the complete customer journey from first launch through steady-state autonomous operation, using the existing Home, Activity, Discovery, Enrollment, Accounts, Attention, Authorization, Recovery, and Chrome-extension capabilities.

This is an **implementation-oriented product-flow document**, not a vision essay. Each stage names entry/exit conditions, canonical owners, customer experience, existing code, and genuine gaps only.

**Canonical journey (happy path):**

```text
First launch
  → Chrome extension connection
  → Gmail connection
  → Automatic account discovery
  → Enrollment and confirmation
  → Initial access verification
  → Home first-use state
  → Steady-state autonomous operation
```

Interrupt loops (authorization, access failure, recovery, required sign-in, new discovery, re-entry) overlay this path; they do not replace it.

---

# Global journey rules

1. Users should never need to understand sessions, synchronization, recovery planners, stores, projections, or providers as technical concepts.
2. **Home** answers: “Am I good?”
3. **Activity** answers: “What has Mighty done?”
4. **Accounts** answers: “What does Mighty know and manage?”
5. **Attention** owns interruption.
6. **Activity** owns history presentation, not canonical history.
7. **Recovery** remains autonomous until user intervention is genuinely necessary.
8. Every action and important conclusion must remain explainable from evidence and policy.
9. A user must always know:
   - what Mighty needs from them,
   - what Mighty is doing,
   - and what Mighty has completed.
10. Each stage should have **one clear primary action** or **no action**.
11. The journey should avoid dead ends, duplicate calls to action, and competing setup paths.

**Surface ownership (customer language):**

| Surface | Customer question | Owns |
|---------|-------------------|------|
| Home (`/dashboard`) | Am I good? / Do I need to do anything? | Briefing; renders Attention primary |
| Activity (`/activity`) | What has Mighty done? | Agent action timeline + approvals + receipts projection |
| Accounts (`/credentials`) | What does Mighty know and manage? | Portfolio audit, repair, manual add, discovery entry |
| Attention | Interrupt ranking | Compile → rank → `AttentionView` only |
| Recovery | Silent repair | Planner + store + supervisor before human ask |
| Chrome extension | Is Mighty working while I browse? | Ambient capture; glance popup — not a destination |

---

# Journey stages

## 1. First launch

### User question

What is Mighty, is it safe, and what is the one step that unlocks value?

### Entry condition

User completes signup or login with no enrolled accounts and has not finished the one-time orientation modal (or is returning with empty portfolio).

### Required facts

| Domain | Role |
|--------|------|
| `users` (session, `onboarded`) | Auth + first-visit flag |
| `home_state` / `home_projection` | Empty enrollment story |
| Attention | Usually silence when portfolio empty |
| `user_copy` | Orientation copy |

### Customer experience

- Land on Home (`/dashboard`), never a multi-step wizard tab.
- One-time modal: how Mighty works (Chrome + quiet watching + login is manual) and privacy posture.
- Empty Home story: one-sentence product explanation; no fake balances.
- Primary path points at connecting email (and later Chrome); secondary path allows manual add.

### Primary action

Dismiss orientation / continue to Home Empty (then Connect Gmail as the Empty CTA).

### Exit condition

User is signed in, oriented (`onboarded=1` or equivalent), and Home Empty is ready to accept the Connect Gmail (or manual add) action.

### Failure and recovery

- Signup/login failure → stay on auth forms with clear errors.
- Modal dismissed without connecting → remain on Empty until return; no guilt streaks.

### Existing implementation

| Kind | Location |
|------|----------|
| Routes | `/`, `/signup`, `/login`, `/logout`, `/forgot-password`, `/reset-password/<token>`, `/dashboard` |
| Onboarding | `/onboarding` (redirects to `/dashboard`); `POST /api/onboarding/complete`, `/onboarding/complete`, `/onboarding/skip` |
| Projection / UI | `mighty/home_state.py`, `mighty/home_projection.py`, `mighty/home_ui.py` |
| Copy | `mighty/user_copy.py` (`ONBOARDING_*`) |
| Docs | [HOME_EXPERIENCE.md](HOME_EXPERIENCE.md) Empty state; [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md) Q10 |

### Gaps

- `/onboarding` is a dead redirect; orientation is modal-only — acceptable if documented, but competing agent-prompt endpoints (`/onboarding/generate-prompt`) still suggest a parallel “agent setup” path.
- Customer copy still says **Worker** / **Control center** in the modal (`user_copy`), contradicting manifesto “no internal jargon as primary UI.”

---

## 2. Chrome extension connection

### User question

Is Mighty able to watch my browsing so accounts can update without a sync ritual?

### Entry condition

Home, Settings, Waiting story, or extension popup indicates the Chrome extension is missing, unconfigured, or disconnected; or user opens `/extension-setup` from Settings / setup CTA.

### Required facts

| Domain | Role |
|--------|------|
| API key / user auth for extension | Extension identity |
| Extension presence (heartbeat / account-status) | Connected vs missing |
| Access Manager / natural session | Downstream readiness after connect |
| Attention (system/worker loaders) | Interrupt only when blocked |

### Customer experience

- Setup page hands the extension an API key automatically (meta tag on `/extension-setup`).
- Popup confirms Mighty is running in the background (glance, ≤ few seconds).
- User is told to keep using desktop Chrome; no password entry into Mighty.
- After connection, return focus to Home or Accounts — not a separate “extension app.”

### Primary action

Install / enable the Chrome extension and open `/extension-setup` once (or follow Home “Set up …” CTA).

### Exit condition

Extension is configured with the user’s key and can call extension APIs (`/api/extension/*`); Home/Accounts no longer treat “extension missing” as the sole blocker.

### Failure and recovery

- Extension not installed → clear install CTA; no silent pretend-connected.
- Key not picked up → reload setup page; Settings retains setup link.
- Mobile users → honest “use desktop Chrome” (mobile is read/approve, not capture).

### Existing implementation

| Kind | Location |
|------|----------|
| Routes | `/extension-setup`, `/settings` (worker/setup link), `/api/my-key`, `/api/account-status` |
| Extension | `extension/background.js` (auto-setup from `/extension-setup`), `extension/popup.html` + `popup.js` |
| APIs | `/api/extension/capture`, `…/accounts`, `…/natural-session/observe`, `…/session-verification/*` |
| Attention | Worker / system loaders via `attention_loaders` → AttentionView |
| Copy | `EXT_*`, `WORKER_*` in `user_copy.py` |
| Docs | [ACCESS_FLOW.md](ACCESS_FLOW.md), [NATURAL_SESSION.md](NATURAL_SESSION.md), [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md) Worker |

### Gaps

- **Competing repair destinations:** Worker popup CTA opens `/account-center` (“Account Center”), while primary nav Accounts is `/credentials` — split mental model.
- Extension and Settings still lead with **Worker** terminology.
- Order vs Gmail is not enforced: users can connect extension before or after Gmail, producing two “first setup” feelings.

---

## 3. Gmail connection

### User question

How does Mighty learn which accounts I already have without a bulk “Add account” checklist?

### Entry condition

Home Empty primary CTA, Accounts “Find accounts,” or direct `/email-scan`; OAuth may also start from email auth routes.

### Required facts

| Domain | Role |
|--------|------|
| `email_connections` | OAuth tokens |
| Email scan / mailbox adapters | Gmail (primary), Outlook, IMAP |
| Privacy / Settings | What Mighty may read |

### Customer experience

- User connects Gmail (readonly) via OAuth.
- Copy states headers/senders for discovery — not full inbox management.
- On success, discovery runs; user is not asked to pick from hundreds of logos first.
- Secondary: Outlook / IMAP on the same Find accounts surface; manual add remains available on Accounts.

### Primary action

**Connect Gmail.**

### Exit condition

Mailbox connection stored; scan/discovery pipeline can run for this user.

### Failure and recovery

- OAuth denied / revoked → stay on Find accounts with retry; Home Empty remains truthful.
- Partial mailbox (Outlook/IMAP) → same discovery pipeline; do not invent a second product path.
- Privacy concern → Settings privacy + skip to manual add (secondary).

### Existing implementation

| Kind | Location |
|------|----------|
| Routes | `/email-scan`, `/email/gmail/auth`, `/email/gmail/callback`, `/email/outlook/auth`, `/email/outlook/callback` |
| APIs | `POST /api/email/scan/imap`, `GET /api/email/suggestions`, dismiss/add |
| Modules | `mighty/email_scan.py`, discovery pipeline entry from callbacks |
| UI | `_EMAIL_SCAN_PAGE` in `app.py`; nav label **Find accounts** |
| Docs | [ACCOUNT_DISCOVERY.md](ACCOUNT_DISCOVERY.md), M7 |

### Gaps

- **Find accounts** is a fifth primary nav item; architecture caps primary nav at Home · Accounts · Activity · Settings and places Gmail under Home Empty / Accounts.
- After OAuth, historical Amex-specific redirects (`/credentials?connect=amex`) can still compete with the generic discovery → Waiting path.

---

## 4. Automatic account discovery

### User question

Which of my real-world provider relationships did Mighty detect from email evidence?

### Entry condition

Successful mailbox connection or rescan triggers the discovery pipeline.

### Required facts

| Domain | Role |
|--------|------|
| **Discovery Store** (`account_discovery`) | Durable discovery facts |
| **Discovery Policy** | Match, confidence, disposition (pure) |
| Sender registry (`SITE_SENDER_DOMAINS`) | Matching input |
| `email_suggestions` | Compatibility projection for scan UI |

### Customer experience

- Discovery is Mighty’s job — no bulk checkbox wall.
- High-confidence providers become enrollment candidates (next stage).
- Ambiguous / non-auto-enroll stays on discovery/scan surfaces — not Attention spam.
- Rescans refresh evidence without wiping dismiss/enroll intent.

### Primary action

None (automatic). Optional: dismiss or manually add an ambiguous suggestion on Find accounts.

### Exit condition

Discovery facts reconciled: dispositions set (`discovered` / `eligible` / `ambiguous` / `dismissed` / `already_enrolled` / `ignored`); eligible set known for enrollment.

### Failure and recovery

- No matches → honest empty discovery; keep Connect / manual add.
- Ambiguous matches → remain on Find accounts for optional Add; do not auto-enroll.
- Scan errors → retry on Find accounts; Home stays Empty or prior state.

### Existing implementation

| Kind | Location |
|------|----------|
| Modules | `mighty/discovery_store.py`, `discovery_policy.py`, `discovery_pipeline.py`, `discovery_metrics.py`, `email_scan.py` |
| Docs | [ACCOUNT_DISCOVERY.md](ACCOUNT_DISCOVERY.md), [milestones/MILESTONE_7.md](milestones/MILESTONE_7.md) |

### Gaps

- Customer-visible “what we found” summary after scan is weak relative to this stage’s name — pipeline is complete; **confirmation presentation** is thin (see stage 5).
- Auto-enroll set defaults to `CUSTOMER_VISIBLE_PROVIDERS` (narrow in alpha) — expected, but expansion is config/product policy, not a missing store.

---

## 5. Enrollment and confirmation

### User question

Which accounts is Mighty now watching, and can I trust that list?

### Entry condition

Discovery marks providers eligible for auto-enroll, or user manually Adds / registers a provider.

### Required facts

| Domain | Role |
|--------|------|
| `discovery_enrollment` → `_register_account_source` | Canonical enrollment write |
| Credentials / enrolled sources | Watched-account stubs |
| AccountState / lifecycle | Post-enrollment mirror (waiting — no fake data) |
| Home Empty → Waiting | Enrollment context |

### Customer experience

- High-confidence accounts enroll automatically as **watching / waiting** — not “connected with data.”
- User should see a clear confirmation of *what is now tracked* (count + names), without a spreadsheet of balances.
- Ambiguous items remain optional Add — not silently enrolled.
- Axes stay separate: enrolled ≠ logged in ≠ has data.

### Primary action

None for auto-enroll; optional **Add** for ambiguous suggestions; optional dismiss.

### Exit condition

At least one enrolled watched account exists (or user explicitly chose manual-only path); Home leaves Empty for Waiting / Attention-driven briefing; Accounts lists enrolled rows.

### Failure and recovery

- Enroll write fails → retain discovery fact; retry Add; no fake success.
- User dismisses suggestions → disposition dismissed; rescans preserve intent.
- Wrong provider enrolled → Accounts disconnect / delete path.

### Existing implementation

| Kind | Location |
|------|----------|
| Modules | `mighty/discovery_enrollment.py`, `_register_account_source` in `app.py`, `account_lifecycle.py`, `account_state.py` |
| APIs | `POST /api/email/suggestions/add`, `/credentials/register`, `/api/credentials` |
| UI | Accounts list sections; Home Waiting ops context |
| Docs | M7 AD-M7-3/5; [PRODUCT_ACCOUNT_STATE.md](PRODUCT_ACCOUNT_STATE.md) |

### Gaps

- **No dedicated enrollment confirmation moment** after auto-enroll (toast / Home story / Accounts highlight). Users can miss that Mighty already enrolled accounts and re-enter Find accounts.
- Manual Amex connect modal (`/credentials?connect=…`, `/api/connect/amex*`) still feels like a parallel enrollment ritual beside Gmail auto-enroll.

---

## 6. Initial access verification

### User question

When will Mighty get real data, and what do I need to do once?

### Entry condition

Accounts enrolled; extension connected (or this stage surfaces extension-missing as the blocker); no meaningful extraction yet for priority account(s).

### Required facts

| Domain | Role |
|--------|------|
| Provider Access Manager | Canonical verification enqueue / evidence |
| `provider_session_state` (PSS) | Session evidence |
| Natural Session | Browse observe / ensure-due |
| Session verification FSM | Job lifecycle |
| AuthTruth | Access read model |
| Recovery | Must not interrupt yet unless human-only |

### Customer experience

- Honest Waiting: visit the provider in Chrome while logged in — login is manual; capture is automatic.
- One primary CTA: **Open [Provider]** or **Set up Chrome extension** if missing — not both equal-weight.
- No Sync-now happy path; no fake points.
- Verification and first extraction happen in the background after a natural visit.

### Primary action

**Open the highest-priority waiting provider in Chrome** (while logged in), or set up the extension if that is the blocker.

### Exit condition

Definitive session evidence recorded for at least one enrolled account and/or first successful extraction cycle completed (or Attention escalates a genuine human login need).

### Failure and recovery

- Signed out / login required → Attention (after Recovery rules) asks for sign-in in Chrome.
- Extension missing → single setup CTA.
- Inconclusive probes → keep Waiting; do not invent connected.
- Customer GETs must never enqueue verification (Access Flow invariant).

### Existing implementation

| Kind | Location |
|------|----------|
| Modules | `provider_access_manager.py`, `provider_session_state.py`, `session_verification.py`, `natural_session.py`, `auth_truth.py`, `session_access.py`, `customer_account_access.py` |
| APIs | `/api/extension/session-verification/*`, `/api/extension/natural-session/observe`, `/api/providers/amex/check`, `/api/account-status` |
| UI | Home Waiting / ops strip; Accounts “Still setting up” / “Sign in required”; Amex connect waiting modal |
| Docs | [ACCESS_FLOW.md](ACCESS_FLOW.md), [AUTH_TRUTH.md](AUTH_TRUTH.md), [NATURAL_SESSION.md](NATURAL_SESSION.md) |

### Gaps

- **Largest end-to-end dead end risk:** after enrollment, users face multiple CTAs (Find accounts, Set up worker, Open provider, Account Center, Connect Amex modal) without one orchestrated handoff into first verification.
- Home V1B correctly demotes Waiting from the hero, but ops-strip + Accounts + Worker popup can still disagree on wording (“Waiting for worker” vs “Sign in required” vs “Still setting up”).

---

## 7. Home first-use state

### User question

Am I good? Is Mighty working? Do I need to do anything?

### Entry condition

User opens Home after enrollment and/or first verification attempts; portfolio may be Waiting, Update, or reaching first All clear / Attention interrupt.

### Required facts

| Domain | Role |
|--------|------|
| AttentionState → AttentionView | Featured interrupt / opportunity |
| `home_state` | Enrollment/ops context only |
| `home_projection` / `home_ui` | Briefing composition |
| Freshness / Change store | Recent Wins |
| Activity pending count | Quiet ops note |

### Customer experience

- Sparse briefing: greeting, **one** featured story, optional Recent Wins, quiet ops strip, Chrome reassurance footer.
- Empty → Connect Gmail; otherwise Attention primary or “You’re good.”
- Waiting/Update never steal the hero when Attention is silent — they live in ops.
- Exactly one primary CTA when action is required; none when all clear.

### Primary action

Whatever Attention (or Empty enrollment) selects — typically Connect Gmail, Open provider, Set up extension, Approve (link to Activity), or Sign in — **or no action**.

### Exit condition

User understands current status; if blocked, completes the single CTA; if clear, can leave (Open → Read → Leave).

### Failure and recovery

- Attention failure must not blank Home — fall back to honest ops / all-clear projection rules.
- Conflicting setup links must not appear as co-equal primary buttons.

### Existing implementation

| Kind | Location |
|------|----------|
| Route | `GET /dashboard` |
| Modules | `home_state.py`, `home_projection.py`, `home_ui.py`, `attention_consumer.py`, `change_store.py` / freshness |
| APIs | `/api/attention/view?surface=home`, Attention snooze/dismiss/cta |
| Docs | [HOME_V1.md](HOME_V1.md), [HOME_EXPERIENCE.md](HOME_EXPERIENCE.md), [ATTENTION_VIEW.md](ATTENTION_VIEW.md) |

### Gaps

- Nav label remains **Dashboard** while product language is **Home**.
- Legacy dashboard density code still exists beside Home V1B; risk of regressions toward multi-CTA.
- Debug Truth/Capability panels must stay debug-only (already decided; guard in reviews).

---

## 8. Attention and authorization

### User question

Do I need to do anything right now? (Including: may my agent act?)

### Entry condition

Compiled Attention primary exists (auth blocker, agent authorization, trust, access degraded after escalation, value-at-risk, opportunity, system/extension), or an agent proposes an action requiring approval.

### Required facts

| Domain | Role |
|--------|------|
| Attention compiler / engine / state / view | Interrupt ranking + customer English |
| Overlays / store / supervisor / delivery | Snooze, dismiss, in-flight, push |
| Trusted Agent + authorization policy + user policy | Propose / decide / execute |
| Actions store | Durable action lifecycle |
| Recovery escalation gate | Auth/degraded only after autonomy exhausted or human-only |

### Customer experience

- Home (and Worker glance / push) shows **one** thing.
- Agent authorization: full detail before Approve; channels include Activity, `/approve/<token>`, API decide.
- Surfaces must not re-rank Attention.
- Silence when nothing deserves interruption.

### Primary action

Complete the Attention CTA (sign in, open provider, approve/deny, set up extension, etc.).

### Exit condition

Attention primary cleared (completed, snoozed, dismissed per policy) or authorization decided; Home returns to all-clear or next ranked item.

### Failure and recovery

- User ignores → supervisor/timeouts per Attention rules; push optional.
- Deny/cancel agent action → Activity records Could not complete with honest user-declined language.
- Recovery still running → do not ask human yet.

### Existing implementation

| Kind | Location |
|------|----------|
| Attention modules | `attention*.py`, `attention_loaders.py`, `attention_consumer.py` |
| Auth modules | `trusted_agent.py`, `authorization_policy.py`, `user_policy.py`, `agent_action_store.py` |
| Routes / APIs | `/api/attention/*`, `/api/authorize`, `/api/decide`, `/api/execute`, `/approve/<token>`, `POST /dashboard/decide/<action_id>` |
| Docs | [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md), [TRUSTED_AGENT_AUTHORIZATION.md](TRUSTED_AGENT_AUTHORIZATION.md), [ATTENTION_COMPILER_AUTHORIZE.md](ATTENTION_COMPILER_AUTHORIZE.md) |

### Gaps

- Multiple approval surfaces (Activity, token page, Home link) are intentional channels — but copy still says “Authorization Required” in places that feel developer-toned.
- Opportunity → Attention bridge cleanup remains parking-lot (Roadmap) — not required for auth loop coherence.

---

## 9. Activity and receipts

### User question

What has Mighty done, what happened, and why?

### Entry condition

User opens Activity (nav when pending or historical items exist), follows Home ops link for approvals, or receives approval deep link / push.

### Required facts

| Domain | Role |
|--------|------|
| `actions` | Canonical agent action lifecycle |
| `action_execution_receipts` | Append-only execution evidence |
| `activity_projection` | Customer timeline (presentation only) |
| Authorization / policy | Provenance for why allowed/denied |

### Customer experience

- Chronological timeline: Needs approval · In progress · Completed · Could not complete.
- Receipts merge into parent action detail — not duplicate rows.
- Approve/Deny when pending.
- No recovery/session/discovery rows in V1 timeline.
- Export/delete parity via Settings.

### Primary action

**Approve** or **Deny** when an item needs approval; otherwise none (read history).

### Exit condition

Pending decision recorded; or user finished reviewing history.

### Failure and recovery

- Missing receipts → show honest incomplete detail; do not invent success.
- Denied/expired → Could not complete with non-failure wording when user-driven.

### Existing implementation

| Kind | Location |
|------|----------|
| Routes | `GET /activity`, `GET /api/activity`, `POST /dashboard/decide/<action_id>` |
| Modules | `activity_projection.py`, `activity_ui.py`, `execution_receipt.py`, `agent_action_store.py` |
| Settings | `/settings/export-csv`, `POST /settings/delete-activity` |
| Nav | Conditional via `activity_nav_visible()` |
| Docs | [ACTIVITY_V1_IMPLEMENTATION_PLAN.md](ACTIVITY_V1_IMPLEMENTATION_PLAN.md) |

### Gaps

- Changed / Discovered timeline rows deferred by approved V1 decisions — not a journey blocker for agent authorization.
- `docs/ACTIVITY_V1_DISCOVERY.md` is stale where it implies `/activity` missing — documentation debt only.
- Mobile has no Activity tab — acceptable for web-first; note for later.

---

## 10. Account detail

### User question

What’s in this account? What changed for this provider?

### Entry condition

User drills in from Home, Accounts, Activity context link, or notification deep link for one provider.

### Required facts

| Domain | Role |
|--------|------|
| AccountState / snapshots | Per-account mirror |
| Extracted fields / benefits | Hero facts |
| Freshness / Change | Field-level history |
| Customer account access presentation | Honest status line |
| Attention | Only if this account is the interrupt context |

### Customer experience

- Single-provider depth: identity, balances/perks, honest status, recent changes.
- One contextual CTA when blocked (sign in / open provider); none on happy path.
- Not a tab — drill-in only.
- No cross-account deals feed.

### Primary action

None when healthy; **Sign in** / **Open provider** when blocked.

### Exit condition

User got depth or completed the single repair CTA; back stack returns to Home or Accounts origin.

### Failure and recovery

- No data yet → Waiting for first visit (truthful).
- Stale / degraded → status line + Attention if escalated.
- Wrong data → trust/privacy paths; no silent overwrite theater.

### Existing implementation

| Kind | Location |
|------|----------|
| Partial routes | `/dashboard?account=…` links, `/credentials?connect=…` connect/waiting flows, `/api/fields-panel/<source>`, `/api/field-history/<source>`, benefits APIs |
| Modules | `account_state.py`, `account_presentation.py`, `accounts_ui.py`, `account_center_ui.py`, snapshots / freshness |
| Docs | [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md) Account detail; [ACCOUNT_SNAPSHOTS.md](ACCOUNT_SNAPSHOTS.md), [FRESHNESS_CHANGE.md](FRESHNESS_CHANGE.md) |

### Gaps

- **No coherent customer Account detail surface** matching architecture (dedicated drill-in with hero facts + history). Depth is fragmented across dashboard account params, credentials connect modals, fields panels, and admin-ish APIs.
- Dual list UIs (`/credentials` vs `/account-center`) confuse which list is the parent of detail.

---

## 11. Steady-state autonomous operation

### User question

Am I good? (Usually: yes.) Is anything worth my attention?

### Entry condition

Extension connected; accounts enrolled; access/extraction working for the portfolio; no standing setup blockers.

### Required facts

| Domain | Role |
|--------|------|
| Natural Session + PAM | Quiet re-verify / capture on browse |
| Freshness / Change | Meaningful deltas for Wins / depth |
| Value Intelligence | Opportunity facts |
| Attention | Rare interrupts only |
| Recovery | Silent repair on failure |
| Home All clear | Default emotional read |

### Customer experience

- Open Home → “You’re good.” → leave.
- Background success is silent.
- Worker popup: keeping accounts up to date.
- Accounts rarely opened.
- Activity only when agents act.
- Interruptions only for real blockers or high-value opportunities Attention selects.

### Primary action

None.

### Exit condition

N/A (steady state). Exit into interrupt loops when facts change.

### Failure and recovery

- Access fails → Recovery loop (stage 12) before human ask.
- New mailbox evidence → discovery re-entry without forcing setup theater.
- Inactivity → re-entry via Home; no “you’re behind” engagement guilt.

### Existing implementation

| Kind | Location |
|------|----------|
| Modules | Natural session, PAM, recovery supervisor, attention delivery, change store, value intelligence |
| Home | All-clear story in `home_projection` / `home_ui` |
| Docs | Manifesto; Architecture Part I–II; M6–M12 complete per [ROADMAP.md](ROADMAP.md) |

### Gaps

- Weekly digest email still parking-lot — optional reassurance channel, not required for in-app steady state.
- Residual sync ritual APIs/UI in extension and mobile Sync tab contradict “no sync happy path” if still user-visible — treat as cleanup / honesty, not new capability.

---

## 12. Recovery when access fails

### User question

Something broke — is Mighty fixing it, or do I need to sign in?

### Entry condition

Access/probe/runtime failure facts observed for a provider.

### Required facts

| Domain | Role |
|--------|------|
| Recovery Planner (pure) | Next capability |
| Recovery Store | Case + attempts |
| Recovery Executor / Supervisor | Heartbeat observe→plan→execute |
| PAM (`trigger_source=internal_recovery`) | Browser session repair execution |
| Attention | Human interrupt only after escalate / human-only |

### Customer experience

- Prefer silence while Mighty retries safe capabilities.
- If human needed (MFA, CAPTCHA, consent, exhausted): Attention asks for sign-in / trust step with one CTA.
- Never expose planner capability names or case state machines.
- After success: return to steady state without celebration spam.

### Primary action

None while autonomous; **Sign in** / required human step only when escalated.

### Exit condition

Recovery case `succeeded` or `cancelled`, or escalated Attention resolved by user; PSS/access facts healthy again.

### Failure and recovery

- Exhausted autonomy → escalate once; Accounts available for audit.
- User cannot complete login on mobile → honest desktop Chrome guidance.

### Existing implementation

| Kind | Location |
|------|----------|
| Modules | `recovery_planner.py`, `recovery_store.py`, `recovery_executor.py`, `recovery_supervisor.py` |
| Docs | [ATTENTION_AUTONOMOUS_RECOVERY.md](ATTENTION_AUTONOMOUS_RECOVERY.md), [RECOVERY_PLANNER.md](RECOVERY_PLANNER.md), M6 |

### Gaps

- Customer-visible “Mighty is fixing this” reassurance during long autonomous recovery is minimal (ops strip / access degraded copy) — optional polish; do not invent a Recovery UI surface.
- Legacy sync / Amex probe paths marked do-not-extend still exist — operational cleanup, not a new product stage.

---

# State-transition map

```text
[First launch]
      │  signup/login + orientation
      ▼
[Connection]  ←── Chrome extension setup ──┐
      │                                    │
      │  Gmail (primary) / mailbox         │
      ▼                                    │
[Discovery] ───────────────────────────────┤
      │  high-confidence facts             │
      ▼                                    │
[Enrollment] ── confirm watched set ───────┤
      │                                    │
      ▼                                    │
[Verification] ←── natural visit in Chrome ┤
      │  session + first data              │
      ▼                                    │
[Home] ────────────────────────────────────┤
      │                                    │
      ▼                                    │
[Autonomous operation] ◄───── silence ─────┘

Loops (from Home / Autonomous / Verification):

  Authorization loop
    Attention(agent_authorization) → Activity or /approve/<token>
    → decide → receipt → Activity history → Home

  Access failure loop
    failure facts → Recovery (autonomous)
      ├─ succeeded → Autonomous (silent)
      └─ escalate → Attention (sign-in / trust)
           → user signs in in Chrome → Verification → Home

  Required user sign-in
    Attention auth CTA → Open provider / login in Chrome
    → PAM evidence → clear Attention → Home

  New account discovery
    Rescan / new mailbox evidence → Discovery
    → auto-enroll or optional Add → Enrollment → Verification → Home

  Re-entry after inactivity
    Open app → Home (All clear | Attention primary | Waiting ops)
    → no setup wizard; no competing “start over”
```

---

# Screen and route inventory

Recommendations only — **do not redesign global navigation in this document’s implementation.**

| Route / surface | Current purpose | Journey stage(s) | Correctly placed? | Duplicates? | Customer-friendly label? | Remain top-level nav? |
|-----------------|-----------------|------------------|-------------------|-------------|--------------------------|------------------------|
| `/dashboard` | Home briefing | 1, 6–8, 11–12 | Yes (default landing) | Legacy density risk | **No** — labeled Dashboard; should be Home | **Yes** (as Home) |
| `/activity` | Agent timeline + approvals | 8–9 | Yes | No | Yes | **Yes**, conditional visibility OK |
| `/credentials` | Accounts list / repair / manual add | 5–6, 10, 12 | Yes as Accounts | Overlaps `/account-center` | Path says credentials; nav says Accounts | **Yes** (Accounts) |
| `/email-scan` | Mailbox connect + suggestions | 3–5 | Stage-correct; **nav weight wrong** | Competes with Home Empty CTA | “Find accounts” OK | **Recommend no** — fold under Accounts / Home Empty |
| `/account-center` | Parallel account connection UI | 2, 6, 10 | **No** — Worker deep-link orphan | **Yes** — duplicates Accounts | “Account Center” ≠ Accounts | **No** |
| `/extension-setup` | Extension API key handoff | 2 | Yes (bridge) | Mild overlap with Settings | “Worker” heavy | No (bridge / Settings) |
| `/settings` | Profile, privacy, notifications, API key, setup link | 2, trust | Yes | Gmail re-scan overlap with Find accounts | Mostly yes | **Yes** |
| `/login`, `/signup`, password reset | Auth | 1 | Yes | No | Yes | No |
| `/onboarding` | Redirect to Home | 1 | Dead route | Agent prompt endpoints parallel | N/A | No |
| `/approve/<token>` | Token authorization | 8–9 | Yes (channel) | Channel duplicate of Activity by design | “Authorization Required” slightly stiff | No |
| `/candidates/<source>` | Uncertain field/benefit review | — | Orphan | Power/internal | No | No |
| `/privacy`, `/tos`, `/privacy/audit-log`, `/privacy/domains` | Legal / trust tooling | Trust | Yes | — | Mixed | No |
| Gmail/Outlook OAuth routes | Mailbox connection | 3 | Yes | — | N/A | No |
| Extension popup | Ambient status | 2, 6, 11–12 | Yes as channel | CTA target wrong (`/account-center`) | “Account Center” / Worker | No (ambient) |
| Extension background / content | Capture, verify, natural session | 2, 6, 11–12 | Yes | Legacy sync paths | Internal | No |
| Mobile tabs (Dashboard / Sync / Settings) | Read + WebView capture | Partial | Sync ritual conflicts manifesto | Sync vs web natural-session | Sync not friendly | Out of web nav scope |
| `/dashboard?account=` / fields panels | Fragmented depth | 10 | Incomplete | Multiple depth entry points | Mixed | Detail is drill-in, not nav |
| `/credentials?connect=` | Per-provider connect/waiting | 5–6 | Legacy onboarding ritual | Parallel to discovery enroll | Connect language | No |

---

# Cross-surface consistency audit

| Issue | Surfaces | Why it hurts the journey |
|-------|----------|--------------------------|
| **Dashboard vs Home** | Nav, docs, Home V1 | Users and reviewers don’t share one name for the “Am I good?” surface |
| **Worker / Control center / Account Center / Accounts** | Onboarding modal, Settings, extension popup, `/credentials`, `/account-center` | Same concepts, four labels; setup feels like multiple products |
| **Dual account UIs** | `/credentials` vs `/account-center` | Worker says manage connections → Account Center; app nav says Accounts — repair ownership unclear |
| **Find accounts as primary nav** | Sidebar vs Home Empty | Duplicate setup CTAs; violates four-item nav rule; suggests discovery is daily work |
| **Status vocabulary drift** | Home, Accounts, Worker, Attention | “You’re good” vs “Sign in required” vs “Still setting up” vs “Waiting for worker” vs “Needs you” — same axes, different words |
| **Home all-clear vs incomplete setup** | Home V1B hero vs Find accounts / extension missing / Waiting ops | Possible: hero calm while another surface still screams setup — especially with Find accounts always visible |
| **Amex connect modal vs Gmail auto-enroll** | Credentials connect, OAuth callback redirects | Competing enrollment rituals |
| **Activity vs Attention** | Home ops, Activity, `/approve` | Ownership is mostly correct (Attention interrupts; Activity history); watch for double primary CTAs on pending approve |
| **Internal terminology in customer UI** | `user_copy`, extension setup, onboarding | “Worker,” credentials path, authorization jargon |
| **Mobile Sync** | Mobile vs web manifesto | Sync ritual on mobile contradicts natural-session story |
| **Candidates / admin-ish depth** | `/candidates`, fields panels | Leak power-user internals near customer paths |

**Ownership of CTAs (target model):**

| Moment | Single owner of primary CTA |
|--------|----------------------------|
| Empty portfolio | Home → Connect Gmail |
| Extension missing | Home (or Settings link) → extension setup |
| Waiting for first visit | Home ops / Attention → Open provider |
| Login required | Attention → Sign in in Chrome |
| Agent approval | Attention → Activity (or token page) |
| Repair audit | Accounts only |
| History / receipt | Activity only |

---

# Highest-priority implementation gaps

Ranked by impact on completing a coherent end-to-end journey.

## 1. Unify Accounts as the single repair / connection destination

| | |
|--|--|
| **User impact** | Ends the largest navigation dead end: Worker and Home send people to different “account” products than the app nav. |
| **Architectural owner** | Accounts presentation (`accounts_ui`) + extension popup CTA targets; retire or redirect `account_center_ui` |
| **Reuse** | `/credentials` Accounts sections, AccountState, customer access presentation, Attention CTAs |
| **Scope** | S — redirect `/account-center` → Accounts; point popup/setup copy at Accounts or Home; delete duplicate CTAs |
| **Dependencies** | Copy pass for “Account Center” strings; extension release |
| **Risk** | Low — consolidation, not new domain |
| **Order** | First |

## 2. Orchestrate post-enrollment first-data handoff (stages 5→7)

| | |
|--|--|
| **User impact** | Eliminates competing setup CTAs after Gmail enroll so users reach first real data without guessing. |
| **Architectural owner** | Home projection (ops + Empty/Waiting) consuming Attention + enrollment context; Accounts secondary |
| **Reuse** | Home V1B, Attention worker/auth loaders, PAM/natural session, discovery enrollment waiting stubs |
| **Scope** | M — one ordered primary CTA state machine for Empty→extension→Waiting→Open provider; suppress duplicate nav CTAs during first-use |
| **Dependencies** | Gap 1 (single repair destination); extension detection signals |
| **Risk** | Medium — easy to accidentally re-rank Attention on Home |
| **Order** | Second |

## 3. Enrollment confirmation presentation

| | |
|--|--|
| **User impact** | Users know what Mighty enrolled and stop re-running Find accounts as if nothing happened. |
| **Architectural owner** | Discovery metrics/facts → Home / Accounts presentation (not a new store) |
| **Reuse** | `account_discovery`, discovery pipeline results, Accounts list, Home Waiting story |
| **Scope** | S–M — post-scan confirmation on Home or Accounts (“Tracking N accounts”) without fake data |
| **Dependencies** | None beyond existing discovery facts |
| **Risk** | Low if confirmation is presentation-only |
| **Order** | Third |

## 4. Customer terminology alignment (Home, extension, Accounts)

| | |
|--|--|
| **User impact** | Trust and comprehension; removes jargon that forces users to learn internal roles. |
| **Architectural owner** | `user_copy.py` + nav labels in `app.py` + extension popup strings |
| **Reuse** | Existing copy module as single registry |
| **Scope** | S — Dashboard→Home; demote Worker/Account Center language; keep Accounts |
| **Dependencies** | Product decisions on final customer terms (see below) |
| **Risk** | Low code risk; medium product-opinion risk if renamed without decision |
| **Order** | Fourth (can partially parallel gap 1) |

## 5. Coherent Account detail drill-in

| | |
|--|--|
| **User impact** | Answers “What’s in this account?” without dumping users into connect modals or admin-ish panels. |
| **Architectural owner** | AccountState + snapshots + freshness/change; new presentation projection only |
| **Reuse** | AccountState, field history APIs, benefits, customer access status, Home deep links |
| **Scope** | M–L — one drill-in route/section; wire Home/Accounts/Activity links; avoid fifth nav item |
| **Dependencies** | Gap 1 (clear parent list); avoid building detail on `/account-center` |
| **Risk** | Medium–high if built as an isolated screen before journey handoff works |
| **Order** | Fifth — after first-data loop works |

---

# Recommended next vertical slice

**Unify Accounts + first-data handoff (gaps 1 and 2 as one vertical):**

Make `/credentials` (Accounts) the only customer repair/connection list; redirect `/account-center` and point the Chrome extension popup at Accounts or Home; then enforce a single post-enrollment primary CTA path on Home from “extension needed” → “visit provider” → first verification, without Find accounts or connect modals competing as co-equal primaries.

**Why this slice (firm):**

| Criterion | Fit |
|-----------|-----|
| User impact | Removes the worst dead end between enrollment and first value |
| Reuse | Home V1B, Attention, Accounts UI, PAM, natural session, discovery waiting stubs |
| End-to-end journey | Connects stages 2–7 into one completable path into Home |
| Largest dead end | Dual Account Center vs Accounts + scattered first-use CTAs |
| Wrong-screen risk | Avoids building Account detail or a new wizard before the spine works |

Do **not** implement Account detail, nav IA redesign beyond CTA targets/labels needed for this slice, or Activity expansions in this slice.

---

# Product decisions

Only decisions that genuinely need CEO/Product input (repository + manifesto/architecture already resolve the rest).

## D1 — Enrollment confirmation explicitness

- **Issue:** After auto-enroll, should Mighty show an explicit “We found and are watching: …” confirmation, or is Home Waiting + Accounts list enough?
- **Recommendation:** Show a **lightweight confirmation on Home** (Waiting story / ops) listing enrolled providers by name — not a separate wizard step and not a checkbox wall.
- **Consequence:** Users trust discovery immediately; Find accounts becomes optional audit, not the place they hunt for proof of enrollment.

## D2 — Extension before vs after Gmail

- **Issue:** Is Connect Gmail always the first Empty CTA, with extension setup only when blocking verification — or should first-run require Chrome extension before mailbox connect?
- **Recommendation:** Keep **Gmail first** (manifesto zero-bulk onboarding); promote extension only when it is the blocker for first data (Attention / Home ops).
- **Consequence:** Faster path to a watched-account list; some users briefly see Waiting before extension install — acceptable if CTA ordering is strict (gap 2).

## D3 — Customer name for the Chrome extension

- **Issue:** Manifesto still uses “Worker” as an internal role name while also listing “worker” as jargon to avoid in primary UI. What should customers see?
- **Recommendation:** Customer-facing **“Mighty in Chrome”** / **“Chrome extension”**; reserve Worker for engineering docs only.
- **Consequence:** Copy and setup screens need a one-time rename; extension store listing should match.

## D4 — Ambiguous discoveries

- **Issue:** When Gmail finds providers outside the auto-enroll set, should Home mention them, or only Find accounts?
- **Recommendation:** Keep ambiguous items **only on Find accounts / Accounts** — no Attention spam (per M7). Optionally a single quiet Home ops note: “N more accounts you can add.”
- **Consequence:** Preserves calm Home; may slow coverage growth until auto-enroll set expands.

## D5 — Account detail timing vs depth ambition

- **Issue:** Ship a minimal truthful detail drill-in now, or wait until after the first-data handoff slice?
- **Recommendation:** **Wait** until after the recommended vertical slice (gap 5 stays fifth). Depth without a working first-data path increases isolated-screen risk.
- **Consequence:** Short-term “What’s in this account?” remains partially answered via Accounts rows and Home wins only.

---

## Out of scope for this document

- Implementing any of the gaps or the recommended slice
- Global navigation redesign beyond recorded recommendations
- New milestones numbering / roadmap edits (update Roadmap when a slice starts)
- Mobile IA overhaul

---

## Document control

| Field | Value |
|-------|-------|
| Version | V1 |
| Nature | Journey definition + audit |
| Implementation | None in this change |
