# Trust by Design

**Status:** Canonical product philosophy  
**Audience:** Product, design, engineering, and anyone shaping a customer-facing experience  
**Scope:** Every surface a user sees — landing, signup, Home, Accounts, permissions, empty states, daily use  
**Not this document:** Visual specs, copy finalization, or implementation plans

---

## Mission

**Trust by Design** is the rule that every customer-facing experience must earn the user's confidence *before* asking for access.

The goal is not prettier UI.

The goal is:

> A first-time user should trust Mighty enough to connect their accounts.

Mighty asks for sensitive capabilities: Gmail access, browser presence, and help signing into loyalty and card programs. That ask only succeeds when the product feels safe, clear, and competent.

Trust is the product. Features are how trust is exercised.

This philosophy is informed by lessons from mature trust-centric products (Plaid, 1Password, Dropbox, Notion, Stripe) — not by copying their layouts or branding. What transfers is the underlying principle set: explain before you ask, take only what you need, keep the user in control, and make outcomes predictable.

---

## The user's emotional journey

Every stage has a **current emotion** (what users typically feel without deliberate trust design), a **desired emotion** (what Mighty must create), and a **why**.

### Landing

| | |
|---|---|
| **Current emotion** | Skeptical curiosity — "Another account tool?" |
| **Desired emotion** | Calm interest — "This might quietly help me." |
| **Why** | The first impression must feel professional and specific, not hype. Users decide in seconds whether Mighty is serious enough to warrant more attention. |

### Sign up

| | |
|---|---|
| **Current emotion** | Mild resistance — "Do I really need another login?" |
| **Desired emotion** | Low-friction commitment — "This is easy and reversible." |
| **Why** | Signup should feel like opening a notebook, not joining a surveillance product. Friction and ambiguity here poison every later permission ask. |

### First dashboard

| | |
|---|---|
| **Current emotion** | Confusion or emptiness — "What am I supposed to do?" |
| **Desired emotion** | Oriented and guided — "I know what Mighty does and what one step unlocks it." |
| **Why** | An empty Home that looks broken destroys trust. The first dashboard must teach the product and point to a single next action. |

### Connecting Gmail

| | |
|---|---|
| **Current emotion** | Anxiety — "Why do they need my email?" |
| **Desired emotion** | Informed consent — "I understand what you need, why, what you won't do, and what happens next." |
| **Why** | Gmail is the highest-stakes early ask. Trust must peak *before* the OAuth screen — never during it. |

### Discovering accounts

| | |
|---|---|
| **Current emotion** | Unease or wonder — "How did you find these?" |
| **Desired emotion** | Transparency and ownership — "You found what I expected; I choose what to keep." |
| **Why** | Discovery can feel magical or invasive. The difference is explanation and user control over what enters the portfolio. |

### Signing into providers

| | |
|---|---|
| **Current emotion** | Vulnerability — "Am I giving you my passwords / access?" |
| **Desired emotion** | Clear role split — "I sign in; Mighty helps and watches. I stay the operator." |
| **Why** | Provider login is where users fear takeover. Mighty must never blur who authenticates, what Mighty can do, or what happens after. |

### Daily use

| | |
|---|---|
| **Current emotion** | Alert fatigue or neglect — "Is this noisy? Is it even working?" |
| **Desired emotion** | Quiet competence — "When nothing needs me, I feel relief. When something does, I trust the ask." |
| **Why** | Trust is maintained by predictability: calm when clear, precise when attention is required, never theatrical. |

---

## The five trust questions

Before granting access, every user asks some version of these five questions. Mighty must answer each explicitly — in UI, copy, and behavior — not imply them.

### 1. Who are you?

**User fear:** Unknown product with opaque ownership and motives.

**Mighty answers by:**
- Presenting a clear product identity and purpose in plain language
- Looking consistent and professional across every surface
- Avoiding gimmicks that signal amateur or extractive intent

### 2. Why do you need this?

**User fear:** Access requested without a meaningful reason.

**Mighty answers by:**
- Stating the specific capability requested (e.g. Gmail)
- Linking it to a concrete user benefit (find and watch accounts)
- Never requesting permission as a vague "connect to continue"

### 3. What will you do with it?

**User fear:** Hidden scanning, selling data, or acting without consent.

**Mighty answers by:**
- Describing the intended use in user terms ("find loyalty and card accounts from your mail")
- Separating discovery from enrollment when the user must choose
- Making reviewable evidence available where claims are made (what was found, when, why attention is needed)

### 4. What won't you do?

**User fear:** Overreach — reading everything, sending mail, changing accounts, sharing data.

**Mighty answers by:**
- Stating clear limits alongside every sensitive ask
- Reinforcing least privilege: only what is needed for the stated job
- Never implying broader powers than the product actually has

### 5. What happens if something goes wrong — and can I leave?

**User fear:** Lock-in, irreversible access, no escape hatch.

**Mighty answers by:**
- Making disconnect, revoke, and control paths visible and real
- Explaining recovery when access breaks (re-auth, degraded states) without blame or panic
- Treating user control as a permanent promise, not a settings afterthought

---

## Trust pillars

These pillars are non-negotiable. Every screen, flow, and automation must uphold them.

### Transparency

Show what Mighty is doing, why, and with what data — in language a first-time user understands. Prefer visible process over "magic." Surprise is a trust failure even when the outcome is helpful.

### Least privilege

Ask only for the access required for the current job. Do not request broad permissions "for later." Expand scope only when a new, explained benefit justifies it.

### User control

The user decides what to connect, what to enroll, when to sign in, and when to stop. Mighty advises and automates within consent; it does not assume ownership of the user's accounts or attention.

### Predictability

Same situations produce the same kinds of outcomes. Layouts, language, and escalation patterns stay consistent. Users should never wonder whether a screen is a new product or a broken one.

### Competence

The product must feel capable: accurate discovery, clear status, timely asks, and graceful degradation. Polish without reliability is theater. Reliability without calm explanation is still anxiety.

### Calm automation

Automation should feel quiet and optional-in-spirit: Mighty works in the background and interrupts only when human action is necessary. Success looks like fewer interruptions over time, not more dashboard noise.

---

## Product promises

Mighty should consistently reinforce a small set of promises. **Wording is not final** — these are directional commitments to productize later.

Draft promises (to refine):

1. **We only ask for help when necessary.**  
   If Mighty can proceed without the user, it should. Interrupts are earned.

2. **We only collect what we need.**  
   Scope matches the job. No speculative data grabs.

3. **You stay in control.**  
   Connect, enroll, approve, disconnect — the user remains the authority.

4. **We explain what we're doing.**  
   Before, during, and after sensitive steps — purpose, benefit, limits, next step.

5. **We show our work when it matters.**  
   Claims that affect trust (found accounts, required logins, failures) come with reviewable context.

6. **When nothing needs you, we stay quiet.**  
   All-clear is a first-class success state, not an empty failure.

---

## Information hierarchy

Every onboarding (and core) screen answers **one primary question**, gives **one primary answer**, and offers **one primary action**. Competing goals are forbidden.

| Screen / moment | Primary question | Primary answer | Primary action |
|-----------------|------------------|----------------|----------------|
| **Landing** | What is Mighty? | Mighty watches the accounts you already have and tells you when something is worth your time. | Start / Sign up |
| **Sign up** | How do I begin safely? | Create an account quickly; you can connect data when you're ready. | Create account / Continue |
| **First dashboard (empty)** | What do I do first? | Connect Gmail so Mighty can find your accounts. | Connect Gmail |
| **Gmail permission intro** | Why Gmail — and what are the limits? | To discover loyalty and card accounts from your mail; here is what we use and what we don't. | Continue to Google / Cancel |
| **Post-Gmail waiting** | Is something happening? | Mighty is scanning; results will appear here. | Wait / optional secondary: learn more |
| **Account discovery results** | What did you find — and what do I keep? | Here are candidate accounts from your mail; you choose what to track. | Confirm / select accounts |
| **Provider sign-in ask** | Why do I need to sign in? | This account needs your login so Mighty can keep it current; you authenticate, Mighty assists. | Sign in / Not now |
| **Daily Home (all clear)** | Does anything need me? | No — everything we're watching looks fine. | None required (optional: review accounts) |
| **Daily Home (attention)** | What needs me, and why? | One prioritized item with a clear reason and next step. | Complete that one action |

**Rule:** If a screen cannot name its primary question, answer, and action in one sentence each, it is not ready to ship.

---

## Permission principles

Permissions are trust events, not checkboxes.

### Rules

1. **Never surprise.**  
   The user should know a permission is coming before the system dialog appears.

2. **Always explain why.**  
   State the capability and the product reason in plain language.

3. **Always explain benefit.**  
   Tie the ask to something the user wants (found accounts, current balances, fewer missed renewals — as accurate for the product).

4. **Always explain limits.**  
   Say what Mighty will not do with this access.

5. **Always explain what happens next.**  
   After grant or deny: what the UI will show, how long it may take, and how to undo.

### Sequence (conceptual)

1. Context (where we are in the journey)  
2. Ask + why + benefit + limits  
3. System permission / provider OAuth  
4. Confirmation of outcome  
5. Immediate next step or calm waiting state  

Skipping straight to a system prompt is an anti-pattern.

---

## Empty-state philosophy

Empty is not "nothing." Empty is teaching.

An empty screen must:

1. **Teach** — what this area of the product is for  
2. **Reduce anxiety** — clarify that emptiness is expected, not a failure  
3. **Explain future value** — what will appear here once the user acts or Mighty finishes work  
4. **Guide one action** — a single primary path forward  

Never merely say "No items," "No accounts," or "Nothing here."

Empty states are onboarding continuing by other means. They inherit the same one-question / one-action discipline as the rest of the product.

---

## Trust signals

Use every honest opportunity to increase confidence. Do not invent fake badges or unverifiable claims.

| Signal | How it shows up |
|--------|-----------------|
| **Privacy language** | Clear statements of purpose and limits near sensitive asks |
| **Security** | Accurate description of how access works; no overclaiming |
| **Reviewability** | Users can inspect what was found, when, and why attention fired |
| **Evidence** | Status, timestamps, and concrete account context — not vibes |
| **Approvals** | Explicit user consent for enrollment, access expansion, and high-impact actions |
| **Progress** | Visible scanning / update states so silence does not feel like failure |
| **Guarantees** | Only promises Mighty can keep; prefer precise limits over absolute slogans |
| **Professional polish** | Typography, spacing, and interaction quality that signal competence |
| **Consistency** | Same patterns for asks, empty states, success, and failure across surfaces |
| **Reversibility** | Disconnect, dismiss, and "not now" paths that feel first-class |
| **Calm tone** | No urgency theater when nothing is urgent |

Trust signals compound. A polished empty state plus a clear Gmail explainer plus a quiet all-clear is stronger than any single badge.

---

## Visual principles

Describe the *feeling*, not CSS.

Mighty's customer-facing surfaces should feel:

- **Calm** — low arousal; no alarmist chrome when the state is fine  
- **Intentional** — every element earns its place; no decorative clutter  
- **Confident** — clear hierarchy; the product knows what matters  
- **Minimal** — one primary message and action per view  
- **Premium** — careful typography and spacing that imply seriousness about money and accounts  
- **Consistent** — shared structure so new screens feel familiar instantly  

Visual design serves trust. Decoration that compete with the primary question undermine it.

---

## Anti-patterns

Explicitly prohibited in customer-facing experiences:

| Anti-pattern | Why it fails trust |
|--------------|--------------------|
| **Developer terminology** | Users don't connect accounts because of "OAuth scopes," "workers," or "compilers" |
| **Internal implementation details** | Leaking architecture signals the product was built for engineers, not customers |
| **Multiple competing CTAs** | Ambiguity at decision time feels unsafe |
| **Dead ends** | Screens with no next step imply brokenness |
| **Blank pages** | Emptiness without teaching reads as failure |
| **Unclear empty states** | "No items" creates anxiety instead of orientation |
| **Permission surprises** | System dialogs without prior explanation feel extractive |
| **Inconsistent page layouts** | Layout thrash makes the product feel unstable |
| **Fake urgency** | Manufactured scarcity or alarm erodes long-term trust |
| **Overclaiming security/privacy** | Unverifiable guarantees are worse than honest limits |
| **Blurring who authenticates** | Implying Mighty logs in *as* the user without clarity destroys confidence |

---

## Success metrics

Trust by Design is successful when outcomes are measurable and observable.

### Understanding

- A new user can explain what Mighty does within **30 seconds** of first dashboard.
- Users can state **why Gmail is requested** before completing OAuth.
- Users can state **what Mighty will not do** with that access.

### Structure

- Every page answers **one primary question**.
- Every page has **one primary action** (or a deliberate all-clear with none required).
- Empty states always include teach + reassure + future value + one action.

### Permission health

- Trust-building explanation appears **before** every sensitive permission request.
- Permission drop-off is diagnosable (confusion vs refusal vs technical failure).
- "Not now" / revoke paths are used without trapping the user.

### Ongoing trust

- All-clear states feel like success (qualitative + return behavior).
- Attention asks are acted on because they are understood, not because they are noisy.
- Support and feedback rarely include "I didn't know you would…" surprises.

Instrument what you can; treat qualitative "I felt safe connecting" as a first-class signal alongside conversion.

---

## Next documents

Follow-on design documents (philosophy → specifics). **Do not implement yet** — define next.

Recommended sequence:

1. **`TRUST_COPY_SYSTEM.md`** — Voice, tone, and draft patterns for explanations, limits, empty states, and permission intros (still not final marketing copy).  
2. **`PERMISSION_UX_SPEC.md`** — Screen-by-screen permission journeys (Gmail, provider sign-in, reconnect, revoke) mapped to the five trust questions.  
3. **`ONBOARDING_INFORMATION_ARCHITECTURE.md`** — Full onboarding map: primary question / answer / action per screen, including waiting and discovery.  
4. **`EMPTY_STATE_CATALOG.md`** — Canonical empty states across Home, Accounts, and related surfaces.  
5. **`TRUST_SIGNALS_CHECKLIST.md`** — Review checklist for PRs and design reviews against pillars, anti-patterns, and metrics.

These documents translate Trust by Design into reviewable design contracts. Implementation plans come only after those contracts exist.
)
