# Product Architecture

Long-term product architecture for Mighty — designed as if launching to millions.

**One mental model:** Mighty is a quiet assistant that watches the accounts you already have, keeps them current while you live your life, and speaks up only when something is worth your time.

**Design posture:** Less software, more assistant. The product should feel like trust in the background — not a dashboard you manage.

**Horizon:** Five years. This document outlives any single screen or feature launch.

**Related:** [PRODUCT_MANIFESTO.md](PRODUCT_MANIFESTO.md) · [HOME_EXPERIENCE.md](HOME_EXPERIENCE.md)

---

## Part I — The questions users are asking

Users do not wake up wanting a "financial dashboard" or a "loyalty aggregator." They wake up with questions. Every surface, notification, and future feature must trace back to one of these questions.

The questions below are ordered by frequency and emotional weight. A healthy user with fifty synced accounts should rarely need to go past the first two.

---

### 1. Am I good?

**Why they're asking**

They opened Mighty — or felt a notification — and want a fast emotional read: is my financial and loyalty life okay, or is something on fire? This is the weather-check question. It assumes Mighty is already doing work on their behalf.

**Ideal answer feels like**

Checking the weather on a clear day. One breath: "You're all set." No homework, no guilt, no empty dashboard begging for setup. If something is wrong, the answer pivots immediately to *Do I need to do anything?* — never leaves them in ambiguous limbo.

**Delivery**

| Mode | When |
|------|------|
| **Automatic** | Home in **All clear** state; optional weekly email digest for users who want reassurance without opening the app |
| **Navigation** | Never required for the answer itself. Account detail is optional depth, not validation |

---

### 2. Do I need to do anything?

**Why they're asking**

Something may be blocked, expiring, or worth acting on. They want a single prioritized answer — not a todo list, not a wall of red badges. Login, expiring credits, and agent approvals all collapse into this one question.

**Ideal answer feels like**

One well-prioritized notification from a trusted assistant: "Here's the one thing worth your time, and here's exactly what to do." If the answer is no, it should be as short as *Am I good?*

**Delivery**

| Mode | When |
|------|------|
| **Automatic** | Home hero (Login, Recommendation, or featured blocker); push notification when urgency crosses threshold; Activity badge when agent approval pending |
| **Navigation** | User taps through only to *complete* the action — provider login in Chrome, account detail for context, Activity to approve. The question itself is answered on Home without navigation |

---

### 3. Is Mighty working?

**Why they're asking**

Trust is fragile in the first week and after any failure. They enrolled accounts but see no data, or data stopped updating, or they read a news story about AI scraping. They need to know the assistant is alive, honest, and not pretending.

**Ideal answer feels like**

Quiet confidence — not a status dashboard. "Mighty is tracking 12 accounts · Updated today" or an honest "Waiting for first visit — visit Amex in Chrome while logged in." Never fake progress, never silent failure.

**Ideal answer does NOT feel like**

A DevOps console. No worker heartbeats, queue depths, or sync schedules.

**Delivery**

| Mode | When |
|------|------|
| **Automatic** | Home **Waiting** and **Update** states; Account health strip; Worker popup glance ("Running in Chrome"); first-extraction toast once per account |
| **Navigation** | Accounts (filtered to waiting/broken) when user wants to audit; Settings → Worker only for install/repair — not routine checking |

---

### 4. What changed?

**Why they're asking**

Balances shifted, a credit posted, a certificate appeared, a session expired. They want the delta — not a full re-read of every account. Often triggered by a notification or a vague sense that "something moved."

**Ideal answer feels like**

A concise briefing from someone who already read the statement: "Amex posted your $40 dining credit. Delta miles unchanged." Surprises should be explained; routine stability should be silent.

**Delivery**

| Mode | When |
|------|------|
| **Automatic** | Home Daily Brief when change is actionable or notable; push for material value changes or session loss; passive silence when nothing meaningful changed |
| **Navigation** | Account detail → **History** section for field-level changes over time; optional "Changes" filter on Accounts. No standalone Changelog tab |

---

### 5. What is worth my attention?

**Why they're asking**

They have time and want to optimize — use a credit before it expires, book with a cert, apply an upgrade. This is opportunistic, not anxious. Different from *Do I need to do anything?* because urgency is lower and dismissal is fine.

**Ideal answer feels like**

A thoughtful tip from someone who knows their accounts — one featured opportunity with a clear why, not a deals feed or card-marketing engine.

**Delivery**

| Mode | When |
|------|------|
| **Automatic** | Home **Recommendation** state; up to two secondary rows below hero; optional monthly "opportunities" line in digest email |
| **Navigation** | Account detail for full perk inventory; never a separate Deals or Offers tab |

---

### 6. Can I trust this?

**Why they're asking**

Before connecting Gmail, before letting an agent book a flight, after a wrong balance, when considering household sharing. Trust is the prerequisite for every other question.

**Ideal answer feels like**

Radical honesty and user control. "Here's exactly what we read, what we store, what we never see (your passwords), and what we did on your behalf." Errors are admitted plainly, not smoothed over with demo data.

**Delivery**

| Mode | When |
|------|------|
| **Automatic** | Truthful states everywhere (no placeholders when real data exists); provenance line on recommendations ("Surfaced from Amex during your latest update"); Activity audit trail for agent actions |
| **Navigation** | Settings → Privacy, data export, delete account; onboarding privacy modal once; Activity for verification of consequential actions |

---

### 7. What's in this account?

**Why they're asking**

They want depth on one provider — points balance, expiring perks, payment due date, status tier. Home intentionally withholds this. The question arises when they're planning a trip, paying a bill, or verifying Mighty got it right.

**Ideal answer feels like**

Opening a well-organized wallet card — everything Mighty knows about *this* provider, fresh as of last visit, with honest gaps labeled.

**Delivery**

| Mode | When |
|------|------|
| **Automatic** | Never — depth is always intentional |
| **Navigation** | Account detail, reached from Home, Accounts, search, or notification deep link |

---

### 8. How do I fix something?

**Why they're asking**

Login expired, worker missing, wrong account matched, they want to disconnect a provider or re-scan Gmail. Something is broken or they want to change the set of watched accounts. This is maintenance, not daily use.

**Ideal answer feels like**

A repair bench, not the living room. Clear diagnosis, one fix path, no shame. "Amex needs login — sign in in Chrome." Done.

**Delivery**

| Mode | When |
|------|------|
| **Automatic** | Home surfaces the highest-priority fix when user is blocked; otherwise silent |
| **Navigation** | Accounts (primary repair surface); Settings for worker install, Gmail re-scan, account deletion |

---

### 9. Did my agent do what I approved?

**Why they're asking**

Agents will book flights, send emails, redeem credits, and move money. Users must verify consequential actions were executed as authorized — not summarized in chat, not buried in logs.

**Ideal answer feels like**

A signed receipt: full detail of what was requested, what was approved, what happened, and when. Disputes are rare because approval UI was complete enough to prevent surprises.

**Delivery**

| Mode | When |
|------|------|
| **Automatic** | Push when approval needed; quiet confirmation when action completes |
| **Navigation** | Activity (approvals + completed actions); optional email receipt for high-stakes actions |

---

### 10. How do I start?

**Why they're asking**

First visit. They don't know what Mighty is, whether it's safe, or what the one step is that unlocks value. Fear of another "connect 200 accounts" onboarding wall.

**Ideal answer feels like**

A host greeting you at the door — one sentence of what Mighty is, one action (Connect Gmail), and an honest path for skeptics (add manually). Under five minutes to first real data.

**Delivery**

| Mode | When |
|------|------|
| **Automatic** | Home **Empty** and **Waiting** states; one-time privacy modal; Gmail scan auto-enrolls |
| **Navigation** | Accounts for manual add; Settings → Worker if Chrome extension missing. No multi-step wizard tab |

---

## Question hierarchy (when answers conflict)

When two questions compete for the same pixel, resolve in this order:

1. **Can I trust this?** — never sacrifice honesty for calm
2. **Do I need to do anything?** — blockers beat opportunities
3. **Is Mighty working?** — honest waiting beats fake data
4. **Am I good?** — calm confirmation when true
5. **What is worth my attention?** — only after the above are satisfied
6. **What changed?** — depth on demand
7. Everything else — navigate, don't broadcast

---

## Part II — Product surfaces

Surfaces are **destinations** — places a user chooses to go. Channels (notifications, email, Worker popup) deliver answers without navigation and are defined in Part III.

Mighty intentionally has **few surfaces**. Most daily value is delivered automatically on Home or passively by the Worker. New surfaces require extraordinary justification.

### Surface map (five-year steady state)

```
                    ┌─────────────┐
         ┌─────────│    Home     │─────────┐
         │         └──────┬──────┘         │
         │                │                │
    ┌────▼────┐     ┌─────▼─────┐    ┌─────▼─────┐
    │ Account │     │  Accounts │    │  Activity │
    │ (detail)│     │  (list)   │    │ (approvals)│
    └────┬────┘     └─────┬─────┘    └─────┬─────┘
         │                │                │
         └────────────────┼────────────────┘
                          │
                    ┌─────▼─────┐
                    │ Settings  │
                    └───────────┘

   Ambient (not in nav): Worker popup · Notifications · Search overlay
```

**Primary navigation (maximum four items):** Home · Accounts · Activity (badge when pending) · Settings

Account detail is a drill-in, not a tab. Search is an overlay, not a tab. Onboarding is Home states, not a tab.

---

### Home

**Purpose**

The attention inbox. Answer *Am I good?* and *Do I need to do anything?* in five seconds. Curate what needs the user today; defer everything else.

**Primary user question**

*Does anything need me?*

**Contents**

- Daily Brief (greeting, date, priority summary)
- One featured action (exactly one primary CTA)
- Account health strip (counts by status, freshness)
- Up to two secondary recommendation rows (Recommendation state only)
- Footer reassurance ("Mighty runs in Chrome · Last checked …")

See [HOME_EXPERIENCE.md](HOME_EXPERIENCE.md) for the six Home states and [HOME_V1.md](HOME_V1.md) for the pure-projection implementation.

**Explicitly does NOT belong here**

- Full account card grid
- Per-account sync or connect buttons on the happy path
- Demo or placeholder data when real data exists
- Settings, privacy policy, worker install (except as secondary link when blocked)
- Agent configuration or API keys
- Field-level history or spreadsheets of balances
- Engagement patterns (streaks, "add more accounts" guilt)

**Relationship to other surfaces**

| Surface | Relationship |
|---------|--------------|
| **Account detail** | Home links in for depth; never duplicates full account body |
| **Accounts** | Home health strip deep-links filtered list; Home never replaces repair workflows |
| **Activity** | Home links to Activity only when approvals pending |
| **Settings** | No standing link in All clear; inline only when worker missing |
| **Worker** | Home footer acknowledges Worker; install flow deep-links Settings/setup |
| **Notifications** | Push lands on Home in the matching state — hero must fulfill notification promise |

**Navigation philosophy**

Home is the default landing after sign-in and the default return from any deep link. Users should be able to open Mighty, get their answer, and leave — most days without tapping anything.

---

### Account (detail)

**Purpose**

Depth on demand for one provider. Answer *What's in this account?* and *What changed?* for a single loyalty or financial relationship.

**Primary user question**

*What's in this account?*

**Contents**

- Provider identity (name, icon, category)
- Hero facts Mighty has extracted (balances, tier, key dates)
- Perks and credits with expiry
- Honest status line (Up to date · Needs login · Waiting for first visit)
- Change history (recent field deltas — "MR points: 140,000 → 142,500 on Jun 28")
- Single contextual CTA when blocked (Log in · Open provider) — not on happy path

**Explicitly does NOT belong here**

- Cross-account recommendations
- Other providers
- Sync now on happy path
- Agent setup
- Gmail discovery
- Marketing for new card products unrelated to user's holdings

**Relationship to other surfaces**

| Surface | Relationship |
|---------|--------------|
| **Home** | Home may show one hero fact; detail is authoritative |
| **Accounts** | Parent list; back navigation returns to Accounts or Home depending on entry |
| **Activity** | Agent actions on this account link here for context |
| **Settings** | No direct link unless account deletion |

**Navigation philosophy**

Always a drill-in, never a tab. Entry from Home, Accounts, search, or notification deep link. User chose depth — respect that intent and don't redirect to Home mid-read.

---

### Accounts

**Purpose**

Setup, audit, and repair. Answer *How do I fix something?* and *Is Mighty working?* across the full portfolio — when Home's summary isn't enough.

**Primary user question**

*What is Mighty tracking, and what needs repair?*

**Contents**

- Enrolled accounts list with honest status per account (separate axes: discovered, session, extraction)
- Gmail scan entry ("Find accounts from email")
- Manual add flow
- Filters: All · Up to date · Waiting · Needs login
- Per-account actions: disconnect, reconnect, view detail
- "How Mighty works" explainer (collapsed by default for returning users)

**Explicitly does NOT belong here**

- Daily brief or featured recommendations
- Full perk inventory (that's Account detail)
- Agent approval queue
- Demo data
- Bulk "sync all" as primary action

**Relationship to other surfaces**

| Surface | Relationship |
|---------|--------------|
| **Home** | Accounts is maintenance; Home is daily. Health strip on Home links here filtered |
| **Account detail** | Drill-in for one row |
| **Settings** | Worker install linked from waiting accounts; Gmail OAuth settings overlap — prefer Accounts for re-scan |
| **Activity** | No overlap |

**Navigation philosophy**

Users visit when something is wrong or when adding coverage — not on calm days. Returning users with healthy accounts may not open Accounts for months. That's success.

---

### Activity

**Purpose**

Verified authorization for agents and audit of consequential actions. Answer *Did my agent do what I approved?* and surface pending *Do I need to do anything?* for agent requests.

**Primary user question**

*What did I (or my agent) do, and what needs my approval?*

**Contents**

- Pending approvals (full detail — fields, amounts, recipients — before user taps Approve)
- Completed actions with timestamp and outcome
- Rejected or expired requests
- Link to relevant Account detail for context

**Explicitly does NOT belong here**

- General Mighty update log or sync history
- Marketing or feature announcements
- Chat transcript with an agent (approval record is the source of truth)
- Account setup or login repair

**Relationship to other surfaces**

| Surface | Relationship |
|---------|--------------|
| **Home** | Badge on Activity nav when pending; Home may link "1 approval waiting" |
| **Account detail** | Actions reference the account they affect |
| **Settings** | Agent API keys and MCP config live in Settings; Activity is consumption not configuration |
| **Notifications** | Push deep-links to specific approval |

**Navigation philosophy**

Activity appears in primary nav only when the user has enabled agents or has pending items. Otherwise hide or collapse — most consumer users may never open it until agents mature. Never manufacture activity to justify the tab.

---

### Settings

**Purpose**

Trust, control, and advanced configuration. Answer *Can I trust this?* for privacy-conscious users and power users configuring workers, notifications, and agents.

**Primary user question**

*What does Mighty have access to, and how do I control it?*

**Contents**

- Profile (name, email)
- Worker / Chrome extension status and setup link
- Notifications preferences (what Mighty may interrupt for)
- Privacy (what we read, what we store, retention)
- Data export and delete account
- Agent / API access (advanced — API keys, MCP)
- Optional: weekly digest email toggle

**Explicitly does NOT belong here**

- Daily account balances or recommendations
- Login repair for specific providers (→ Accounts)
- Approval queue (→ Activity)
- Gmail scan as primary CTA (→ Accounts or Home Empty)

**Relationship to other surfaces**

| Surface | Relationship |
|---------|--------------|
| **Home** | Settings linked only when blocked (worker missing) or from global nav |
| **Accounts** | Gmail re-scan may live in either; Accounts owns "what's tracked," Settings owns "what access Mighty has" |
| **Activity** | Agent config here; approvals there |
| **Worker** | Extension setup page is a bridge between Settings and Worker |

**Navigation philosophy**

Visited rarely — during setup, when trust is questioned, or when leaving Mighty. A healthy user shouldn't need Settings monthly. Calm Settings means the product works without tuning.

---

### Worker (Chrome extension)

**Purpose**

Ambient proof that Mighty runs while you browse. Answer *Is Mighty working?* without opening the dashboard. Not a destination — a glance.

**Primary user question**

*Is Mighty running right now?*

**Contents**

- Running / updating / setup needed
- Last update time
- Count of accounts updated recently
- One line when login needed ("Amex needs login")
- Link to open Home or Accounts only when action required

**Explicitly does NOT belong here**

- Full account balances
- Settings screens
- Approval flows
- Marketing

**Relationship to other surfaces**

| Surface | Relationship |
|---------|--------------|
| **Home** | Home is the mirror; Worker is the engine. Footer on Home references Worker |
| **Accounts** | Login repair starts in Chrome; Accounts shows full list |
| **Settings** | Setup and install |

**Navigation philosophy**

Never in primary app nav. Popup is ≤3 seconds of attention. If the popup needs more, the answer belongs on Home or Accounts.

---

## Part III — Channels (not surfaces)

Channels deliver answers without requiring the user to choose a destination.

| Channel | Questions answered | Philosophy |
|---------|-------------------|------------|
| **Push notification** | Do I need to do anything? · What changed? (urgent) | One notification → one action. Deep-link to exact context. Never engagement nags |
| **Email digest** | Am I good? · What is worth my attention? (low urgency) | Opt-in weekly. Calm summary, not drip campaign |
| **In-app toast** | Is Mighty working? | Once per milestone (first extraction). Silent after |
| **Search overlay** | What's in this account? · How do I fix something? | Universal find — accounts, perks, actions. Overlay, not tab. Future: natural language |
| **Onboarding modal** | Can I trust this? · How do I start? | Once per user. Privacy + how it works. Not a wizard app |

---

## Part IV — Navigation philosophy

### The rule of three touches

Most healthy sessions: **Open → Read → Leave** (zero taps).

Blocked sessions: **Open → One tap → Chrome** (complete action outside Mighty).

Depth sessions: **Open → Account detail → Leave** (two taps).

If a flow routinely exceeds three taps, the feature is in the wrong surface.

### Default landing

Always **Home**. Never Accounts, never Activity, never a setup wizard.

### Badges

Only **Activity** (pending approvals) and **Accounts** (needs login count, optional) earn nav badges. Home never needs a badge — it *is* the badge for the whole product.

### Back stack

Account detail remembers entry point (Home vs Accounts). Back returns to origin, not always Home.

### Mobile (five-year)

Mobile is **read and approve**, not **capture**. Worker stays desktop Chrome. Mobile Home mirrors web Home; blocked states explain "Use desktop Chrome to log in." No compromised second-class experience — honest scope limitation.

### What we will not add to primary nav

- Deals · Offers · Rewards
- Sync · Connections · Integrations
- Insights · Analytics · Trends
- Chat · Assistant · Copilot (agents approve in Activity; they don't become a chat tab)
- Social · Referrals · Leaderboards

These are feature gravity wells. They become the product instead of serving the product.

---

## Part V — Principles for adding future features

Every feature proposal — now or in five years — must pass this gate before design or engineering begins.

### The three questions

1. **Which user question does this answer?**  
   If it doesn't map to Part I, it doesn't ship.

2. **Which existing surface owns that question?**  
   Default to extending the owner. New surfaces are a last resort.

3. **If no surface owns it, should a new surface exist?**  
   Ask: *Will millions of users need this weekly?* If no, use a channel, overlay, or Account detail section instead.

### Additional gates

| Gate | Pass | Fail |
|------|------|------|
| **Frequency** | Monthly or rare for most users | Daily visit required for healthy users |
| **Calm** | Silent on success | Celebrates routine background work |
| **Trust** | Shows truth or honest gaps | Placeholder, demo, or optimistic data |
| **Assistant** | Mighty acts; user confirms only when blocked | User manages/syncs/schedules |
| **Depth** | Available on demand | Pushed to Home hero by default |
| **Navigation** | Extends existing surface | Adds fifth tab or new app section |

### Feature patterns and their homes

| Pattern | Owner | Example |
|---------|-------|---------|
| Urgent blocker | Home hero | Session expired |
| Expiring value | Home Recommendation | Dining credit deadline |
| Portfolio status | Home Account health → Accounts | 2 need login |
| Single-account facts | Account detail | MR balance |
| Field history | Account detail | Points changed Jun 28 |
| New provider discovery | Accounts (+ Home Empty CTA) | Gmail scan |
| Agent wants to act | Activity + push | Book flight with miles |
| Privacy / export | Settings | Delete my data |
| Background success | Nowhere (silent) | Nightly refresh succeeded |
| Cross-account optimization | Home Recommendation (one featured) | Use cert before hotel trip |
| Household / shared accounts | Accounts + Settings (future) | Family Amex |
| Travel context | Home Recommendation (future) | "Your Tokyo trip — cert expires before departure" |
| Bill pay reminder | Home Recommendation or push | Payment due in 3 days |
| New provider category (crypto, insurance) | Accounts enrollment + Account detail | Same architecture, new adapter |

### When to create a new surface

Only when **all** of these are true:

- Answers a distinct top-level question not subordinate to an existing one
- Expected weekly use for a large segment of users
- Cannot be an overlay, section, or channel without confusing the mental model
- Survives the five-year test ("Would this still deserve its own tab with 50M users?")

Historical examples of things that should **never** become surfaces: Sync, Deals, Chat, Analytics.

---

## Part VI — Decision matrix

Use this matrix in design reviews and pull requests when deciding where functionality belongs.

### Step 1 — Identify the question

| User question | Primary owner | Auto or navigate? |
|---------------|---------------|-------------------|
| Am I good? | Home (All clear) | Automatic |
| Do I need to do anything? | Home hero | Automatic; navigate to complete |
| Is Mighty working? | Home Waiting/Update + Worker popup | Automatic |
| What changed? | Account detail History; Home if actionable | Automatic if urgent; else navigate |
| What is worth my attention? | Home Recommendation | Automatic |
| Can I trust this? | Settings + truthful states everywhere | Navigate for policy; automatic for honesty |
| What's in this account? | Account detail | Navigate |
| How do I fix something? | Accounts | Navigate |
| Did my agent do what I approved? | Activity | Navigate; push when pending |
| How do I start? | Home Empty/Waiting | Automatic |

### Step 2 — Placement decision tree

```
New functionality proposed
        │
        ▼
Which user question? ──→ None maps → STOP
        │
        ▼
Does owner surface exist? ──→ No → Will users need this weekly?
        │                              │
       Yes                             No → Channel or overlay
        │                              │
        ▼                              Yes → Propose new surface (rare)
Add to owner surface
        │
        ▼
Does it belong in Home hero?
        │
   ┌────┴────┐
  Yes       No
   │         │
   ▼         ▼
Blocked    Account detail,
or urgent  Accounts, Settings,
value?     or Activity
   │
┌──┴──┐
Yes  No
 │    │
 ▼    ▼
Hero  Secondary row
      or silence
```

### Step 3 — Home hero eligibility

| Criterion | Hero yes | Hero no |
|-----------|----------|---------|
| User blocked without action | ✓ | |
| Real value expires ≤7 days | ✓ | |
| Agent approval pending | Link to Activity, not hero | ✓ |
| Informational opportunity | | ✓ (secondary row max) |
| Background success | | ✓ (silent) |
| Healthy account updated | | ✓ (silent) |
| First-time empty | ✓ (Connect Gmail) | |

### Step 4 — Red flags (reject or redesign)

| Red flag | Why | Redirect to |
|----------|-----|-------------|
| "Add Sync now to account cards" | Violates natural-session capture | Silent background |
| "New Insights tab" | Feature gravity; answers What changed? | Account detail |
| "Dashboard grid of all accounts" | Duplicates Accounts | Home health strip |
| "Chat with your finances" | Trust + verification need Activity | Activity approvals |
| "Demo data when empty" | Violates trust | Honest Empty state |
| "Notify on every update" | Violates calm | Push only for urgency |
| "Onboarding wizard tab" | Violates minimal nav | Home states |
| "Settings primary CTA for Gmail" | Wrong question owner | Home Empty |
| "Worker popup shows balances" | Wrong surface depth | Home / Account detail |
| " Fifth nav item" | Violates minimal nav | Fold into existing four |

### Step 5 — PR checklist (copy into reviews)

- [ ] Named the user question from Part I
- [ ] Identified owning surface from Part II
- [ ] Confirmed not listed in surface "does NOT belong"
- [ ] Hero placement justified via Step 3 matrix
- [ ] Healthy user with 50 accounts won't see this daily
- [ ] Success path is silent; blocker path is clear
- [ ] No new primary nav item without architecture review

---

## Part VII — Five-year evolution (architecture-stable)

These capabilities grow **inside** the architecture above — they do not rewrite it.

| Direction | Question served | Where it lives |
|-----------|-----------------|----------------|
| More providers | Is Mighty working? · What's in this account? | Accounts + Account detail |
| Smarter recommendations | What is worth my attention? | Home Recommendation |
| Agents doing more | Did my agent do what I approved? | Activity |
| Household accounts | What's in this account? | Accounts scoping + Settings sharing |
| Mobile companion | Am I good? · Do I need to do anything? | Home (read-only) + Activity approve |
| Natural language search | What's in this account? | Search overlay |
| Proactive trip context | What is worth my attention? | Home Recommendation |
| Financial planning | What is worth my attention? | Home — never a Planning tab |
| Notifications maturity | Do I need to do anything? | Channels — tighter deep links |

The architecture survives because the **questions** are stable. People will still ask *Am I good?* in 2031. The answers get richer; the surfaces stay few.

---

## Mental model (one paragraph for the team)

Mighty is an assistant with four rooms and a window. **Home** is the front door — answer and leave. **Accounts** is the workshop — fix and extend what's watched. **Account detail** is the drawer — one provider's facts when you need them. **Activity** is the receipt book — what agents did and what they need permission for. **Settings** is the lockbox — trust and control. The **Worker** is the assistant walking beside you in Chrome; you glance, you don't move in. Everything else — push, email, search — is the assistant tapping your shoulder when it matters. Build features by asking which question they answer and which room they belong in. If it doesn't fit a room, it probably shouldn't exist.

---

## Document status

| Input | Status |
|-------|--------|
| PRODUCT_MANIFESTO.md | Used |
| HOME_EXPERIENCE.md | Used |
| UX_PRINCIPLES.md | Not in repo — principles aligned with manifesto and Home doc |
| MAGIC_MOMENTS.md | Not in repo — magic moment referenced via first extraction / Recommendation |

This document is product architecture only. No implementation spec.
