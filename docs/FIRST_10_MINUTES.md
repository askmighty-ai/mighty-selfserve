# First 10 Minutes

**Status:** Canonical first-experience screenplay  
**Audience:** Product, design, engineering, and anyone shaping onboarding  
**Governing philosophy:** [TRUST_BY_DESIGN.md](TRUST_BY_DESIGN.md)  
**Related:** [PRODUCT_MANIFESTO.md](PRODUCT_MANIFESTO.md) · [PRODUCT_FLOW_V1.md](PRODUCT_FLOW_V1.md) · [HOME_EXPERIENCE.md](HOME_EXPERIENCE.md) · [ACCOUNT_DISCOVERY.md](ACCOUNT_DISCOVERY.md)

**Not this document:** Visual specs, final marketing copy, HTML, or implementation plans.

---

## Purpose

This document is the **screenplay** for a brand-new user's first experience with Mighty.

It defines every screen they encounter in roughly the first ten minutes — from landing through returning to Home after first verification — using Trust by Design as the rule:

> Trust is earned *before* access is requested.

The goal is not a prettier funnel. The goal is:

> A first-time user should understand Mighty, feel safe, and complete the one path that unlocks value — without confusion, surprise permissions, or competing next steps.

---

## Trust patterns we adopt (not visual imitation)

Mighty should borrow **trust-building patterns** from mature products — not their layouts, colors, or brand systems.

| Pattern | Learned from | How it applies to Mighty |
|---------|--------------|--------------------------|
| **Explain before you ask** | Plaid, Stripe | Never jump straight to Google OAuth or provider login. Prefaces answer why, benefit, limits, and what happens next. |
| **Least privilege, said out loud** | Plaid, 1Password | State what is requested and what is *not* requested at the moment of consent. |
| **User stays the operator** | 1Password, Stripe | User signs into providers; Mighty assists and observes. Never blur who authenticates. |
| **Progress that feels reversible** | Dropbox, Notion | Early commitment feels light; disconnect / not now / skip paths stay first-class. |
| **One job per screen** | Notion, Stripe | One primary question, one primary message, one primary CTA. |
| **Empty teaches, it does not apologize** | Notion, Dropbox | Empty Home is orientation, not a broken blank page. |
| **Competence through calm** | Stripe, 1Password | Professional polish and predictable outcomes beat urgency theater. |
| **Show the work when stakes are high** | Plaid, Stripe | Discovery results, verification status, and attention asks come with reviewable context. |

These patterns are constraints on information architecture and copy intent. They are not a mandate to look like any of those products.

---

## Journey overview

```text
1. Landing page
2. Account creation
3. Welcome
4. Trust introduction ("How Mighty works")
5. Gmail connection
6. Account discovery
7. Review discovered accounts
8. First dashboard
9. First provider sign-in
10. Returning to Home after verification
```

**Emotional arc (desired):**

Calm interest → low-friction commitment → oriented → informed consent → transparent discovery → ownership → guided first action → clear role split at login → quiet competence.

**Global rules for every screen:**

1. Answer one primary user question.
2. Offer one primary CTA (or none, when all-clear is intentional).
3. Explain before any sensitive permission.
4. Prefer visible process over unexplained magic.
5. Keep the user in control and able to leave or defer.
6. Never use developer jargon as primary customer language.
7. Never invent fake account data to fill emptiness.

---

# Ideal first experience

Each screen below is the desired experience. Current product gaps are analyzed after the full screenplay.

---

## 1. Landing page

### Purpose

Introduce Mighty as a serious, specific product that quietly watches accounts the user already has — and invites a low-risk next step.

### Primary user question

**What is Mighty, and is it worth my time?**

### Desired emotional state

Calm interest — “This might quietly help me.”

### Primary message

Mighty watches the loyalty and card accounts you already have and tells you when something is worth your time.

### Primary CTA

**Get started** (or **Start free**) → account creation

### Secondary information

- One short supporting sentence: discovery from email + quiet updates while you browse; you sign in when needed.
- Link to Sign in for returning users.
- Optional quiet links: Privacy, Terms (footer — not hero clutter).

### Trust signals that should appear

- Clear product identity and purpose in plain language (answers “Who are you?”).
- Professional, calm visual presence — competence without hype.
- Specific benefit, not vague “manage everything” claims.
- No urgency theater, fake metrics, or gimmick badges.

### Mistakes to avoid

- Generic SaaS landing that could belong to any fintech after removing the logo.
- Feature grids, stat strips, and multiple competing CTAs in the first viewport.
- Overclaiming security (“bank-grade,” “never stores anything”) without precise truth.
- Leading with Chrome extension install or Gmail as the first action before identity is established.
- Developer or internal language (“Worker,” “Attention compiler,” “OAuth”).

### Exit condition

User chooses **Get started** and enters account creation — or **Sign in** if they already have an account.

---

## 2. Account creation

### Purpose

Create a Mighty identity with minimal friction, while signaling that deeper access (Gmail, Chrome) comes later and stays optional until explained.

### Primary user question

**How do I begin safely?**

### Desired emotional state

Low-friction commitment — “This is easy and reversible.”

### Primary message

Create your Mighty account. You can connect data when you’re ready.

### Primary CTA

**Create account** (or continue with the chosen auth method)

### Secondary information

- Short reassurance: connecting Gmail and Chrome happens next, with explanation first.
- Sign-in link for existing users.
- Password / auth requirements stated clearly; errors are specific and calm.
- Time expectation only if honest (e.g. “about 5 minutes to first setup”) — never a fake countdown.

### Trust signals that should appear

- Reversibility: account creation is not the same as granting mailbox or browser access.
- Least privilege: no Gmail OAuth embedded inside signup.
- Consistent professional form design; no surveillance framing.
- Privacy / Terms accessible without burying the primary action.

### Mistakes to avoid

- Asking for Gmail, Chrome, or provider passwords on the signup form.
- Long questionnaires, profile completion, or engagement streaks.
- Multiple auth options that feel like a maze without a clear default.
- Guilt copy (“You’re almost there — don’t miss out”).
- Implying Mighty already has access to anything before the user grants it.

### Exit condition

Account is created and the user is authenticated, then moved to Welcome.

---

## 3. Welcome

### Purpose

Confirm arrival, name the product relationship, and set a single expectation for what the next few minutes will accomplish.

### Primary user question

**Did it work — and what happens now?**

### Desired emotional state

Oriented and lightly confident — “I’m in the right place.”

### Primary message

Welcome to Mighty. Next we’ll show how Mighty works, then help you find the accounts you already have.

### Primary CTA

**Continue** → Trust introduction

### Secondary information

- One-line reminder of the outcome: find accounts from email, then verify access when needed.
- Optional “I already know how this works” skip that still lands on the Gmail path with a shorter trust preface (never skip straight into OAuth).

### Trust signals that should appear

- Predictable sequence: welcome → explain → ask.
- No empty dashboard flash that looks broken between signup and orientation.
- Calm tone; success without celebration spam.

### Mistakes to avoid

- Dumping the user onto an empty Dashboard with no teaching.
- Opening a wall of nav items (Find accounts, Accounts, Activity, Settings) as if setup were daily work.
- Multiple equal CTAs (“Connect Gmail,” “Install extension,” “Add account,” “Tour”).
- Jargon-heavy welcome (“Control center,” “Worker ready”).

### Exit condition

User continues into the Trust introduction (“How Mighty works”).

---

## 4. Trust introduction (“How Mighty works”)

### Purpose

Answer the five trust questions *before* any sensitive permission — especially Gmail — so consent is informed, not coerced by a blank OAuth screen.

### Primary user question

**How does Mighty work, what will you ask for, and what won’t you do?**

### Desired emotional state

Informed readiness — “I understand the deal before I grant access.”

### Primary message

Mighty finds loyalty and card accounts from your email, keeps them current while you browse in Chrome, and only asks you to sign in when necessary. You stay in control.

### Primary CTA

**Connect Gmail** → Gmail connection preface / OAuth path  
(If the preface is a separate screen, CTA is **Continue to connect Gmail**.)

### Secondary information

Structure as a short, scannable explanation (not a terms wall):

1. **What Mighty does** — watches accounts you already have; speaks up when something needs you.
2. **What you’ll do** — connect Gmail once; later, sign into providers yourself when asked; use desktop Chrome when verification needs the browser.
3. **What Mighty will not do** — not send email as you; not require passwords stored in Mighty for discovery; not log into providers *as* you without you signing in.
4. **What happens next** — after Gmail, Mighty scans for known providers and shows what it found for your review / confirmation.
5. **Escape hatch** — you can disconnect Gmail later; you can add accounts manually instead.

Secondary CTA: **Add an account manually** (defer Gmail) — first-class, lower emphasis.

### Trust signals that should appear

| Trust question | How this screen answers |
|----------------|-------------------------|
| Who are you? | Clear Mighty identity and purpose |
| Why do you need this? | Gmail is for discovering accounts from mail evidence |
| What will you do with it? | Find loyalty/card relationships; then watch them |
| What won’t you do? | Explicit limits listed beside the ask |
| Can I leave? | Manual add + disconnect path acknowledged |

Also: calm visual pacing; no fake security badges; no surprise system dialog yet.

### Mistakes to avoid

- Skipping this screen and launching Google OAuth immediately.
- Explaining only benefits without limits.
- Leading with Chrome extension install before Gmail (violates Gmail-first trust order).
- Internal terminology (“Worker,” “natural session,” “PSS,” “Attention”).
- Overclaiming (“we never read your email”) if the product reads headers/senders for discovery — be precise.
- Multiple competing primaries that dilute informed consent.

### Exit condition

User proceeds to Gmail connection with understanding of why — or chooses manual add and leaves this Gmail path (alternate journey; still must land somewhere oriented).

---

## 5. Gmail connection

### Purpose

Obtain mailbox access through Google OAuth only after the trust preface, with a precise scope story and a clear waiting outcome.

### Primary user question

**Why Gmail — and what are the limits?**

### Desired emotional state

Informed consent — “I understand what you need, why, what you won’t do, and what happens next.”

### Primary message

Connect Gmail so Mighty can find loyalty and card accounts from your mail. Mighty uses this to discover accounts — not to manage your inbox.

### Primary CTA

**Continue to Google** → system OAuth

### Secondary information

Immediately before OAuth, reinforce:

- **Capability:** connect Gmail (readonly discovery posture as accurate for the product).
- **Benefit:** accounts appear without a bulk “Add account” checklist.
- **Limits:** not for sending mail; not for reading unrelated personal content as a product promise beyond what’s needed for discovery; not inbox management.
- **Next:** after approval, Mighty scans and shows what it found.
- **Cancel / Not now:** returns to a calm empty/home state with manual add still available.

After successful OAuth, show a brief confirmation: “Gmail connected — looking for accounts…”

### Trust signals that should appear

- Permission preface (never bare redirect).
- Least-privilege language that matches real scopes.
- Progress state so silence does not feel like failure.
- Reversibility: disconnect later via Settings / privacy path.
- Consistency with the Trust introduction (same story, not a new product).

### Mistakes to avoid

- Surprise OAuth with no prior screen.
- Vague CTA: “Connect to continue.”
- Mixing Outlook/IMAP as equal-weight primaries that confuse the default path (secondary is fine).
- Redirecting into an Amex-specific connect modal after Gmail as if that were the product.
- Asking for the Chrome extension in the same breath as Gmail consent.
- Developer scope names as the only explanation.

### Exit condition

Gmail is connected and discovery begins → Account discovery screen (waiting / scanning state).

---

## 6. Account discovery

### Purpose

Make the scan feel like a transparent process the user owns — not a black box that invents accounts behind their back.

### Primary user question

**Is something happening — and how are you finding accounts?**

### Desired emotional state

Transparency — “You’re working; I understand the method.”

### Primary message

Mighty is scanning your connected mail for known airlines, hotels, and card programs.

### Primary CTA

None while scanning (primary action is wait). Optional secondary: **Learn how discovery works**.

### Secondary information

- Short method line: matching known sender domains / program mail — not “AI read your entire life.”
- Expected duration band if honest; otherwise calm indeterminate progress.
- What will appear next: a list of candidates / enrolled accounts to review.
- If scan is slow: reassurance + “You can leave and come back; progress continues.”

### Trust signals that should appear

- Visible progress (animation or step label) so silence ≠ broken.
- Explanation of *how* (transparency over magic).
- No fake account names while waiting.
- Calm tone; no “Hurry, limited time” patterns.

### Mistakes to avoid

- Blank spinner with no explanation.
- Jumping straight to a populated dashboard with no discovery narrative.
- Auto-enrolling silently with zero confirmation of what was found.
- Celebratory confetti that feels manipulative before the user has reviewed results.
- Technical logs, worker IDs, or pipeline jargon.

### Exit condition

Scan completes with results (found / none / partial) → Review discovered accounts.

---

## 7. Review discovered accounts

### Purpose

Give the user ownership over what enters their portfolio. Discovery can feel magical or invasive; review + choice is the difference.

### Primary user question

**What did you find — and what do I keep?**

### Desired emotional state

Transparency and ownership — “You found what I expected; I choose what to track.”

### Primary message

Here are accounts Mighty found from your mail. Confirm what you want Mighty to watch.

### Primary CTA

**Confirm and continue** (or **Start watching these accounts**)

### Secondary information

- List of found providers with light evidence context (e.g. matched from mail senders — not message bodies).
- Distinction where needed:
  - **High-confidence / recommended** — preselected or clearly marked as ready to watch.
  - **Ambiguous** — optional add; not forced.
- What “watching” means next: Mighty will track these; balances/perks appear after verification in Chrome — not fake data now.
- Secondary actions: deselect, dismiss suggestion, add manually later.
- If none found: honest empty discovery — teach + manual add + optional rescan — not a dead end.

### Trust signals that should appear

- Reviewability: what was found and why (evidence summary).
- User control: select / deselect / dismiss.
- Separation of axes: found ≠ logged in ≠ has data.
- Clear next step after confirm (dashboard / first verification ask).
- No pressure to accept everything.

### Mistakes to avoid

- Silent auto-enroll with no reviewable confirmation.
- A spreadsheet of fake balances.
- Forcing enrollment of ambiguous matches.
- Attention spam for every low-confidence suggestion.
- “Select all 200 brands” bulk checklist theater.
- Collapsing “enrolled,” “connected,” and “synced” into one misleading status.

### Exit condition

User confirms the watched set (or honest empty + manual path) → First dashboard with a clear post-enrollment story.

---

## 8. First dashboard

### Purpose

Answer “Am I good?” for a brand-new portfolio: teach what Home is, confirm what is now watched, and present exactly one next step toward first real data.

### Primary user question

**What do I do now — and is Mighty working?**

### Desired emotional state

Oriented and guided — “I know what Mighty does and what one step unlocks it.”

### Primary message

Mighty is watching [N accounts / Provider names]. One step left for your first update: [Set up Mighty in Chrome | Visit Provider while signed in | You’re verifying — no action].

### Primary CTA

Exactly one, in priority order:

1. **Set up Mighty in Chrome** — if browser access is required and missing  
2. **Visit [Provider]** — if extension is ready and a natural visit is needed  
3. **None** — if verification is already in progress (“Mighty is verifying — you don’t need to do anything”)

### Secondary information

- Lightweight enrollment confirmation: which accounts, what Mighty is doing next, whether the user must act.
- One-sentence Home teaching: Home answers whether anything needs you.
- Quiet ops / secondary link: View accounts.
- Footer reassurance: Mighty runs in Chrome when set up; last checked when known.
- No competing filled buttons for Find accounts, Connect Amex, Account Center, etc.

### Trust signals that should appear

- One primary question / one action discipline.
- Honest waiting (no demo balances).
- Progress and next-step clarity.
- Consistent naming: Home (not “Dashboard” / “Control center”); Mighty in Chrome (not “Worker”).
- Calm empty-adjacent states that teach rather than apologize.

### Mistakes to avoid

- Empty-looking Home that says “You’re good” while first-data setup is incomplete and unstated.
- Multiple equal CTAs (Find accounts + Set up worker + Open provider + Connect modal).
- Nav that screams unfinished setup from every item.
- Fake points, placeholder perks, or demo mode as the default first impression.
- Internal labels in the hero.

### Exit condition

User understands status and either completes the single CTA (Chrome setup or provider visit / sign-in path) or waits calmly while verification runs.

---

## 9. First provider sign-in

### Purpose

When access needs the user, ask for sign-in with a clear role split: the user authenticates at the provider; Mighty helps and watches; Mighty does not become the password owner in the user’s mind.

### Primary user question

**Why do I need to sign in — and who is logging in?**

### Desired emotional state

Clear role split — “I sign in; Mighty helps and watches. I stay the operator.”

### Primary message

[Provider] needs your sign-in so Mighty can keep this account current. You’ll sign in on [Provider]’s site. Mighty does not sign in as you.

### Primary CTA

**Sign in to [Provider]** (opens provider in Chrome)

### Secondary information

- Why now: first verification, session missing, or access required for the watched account.
- What happens after: return to Mighty / continue browsing; Mighty captures what it needs from the visit when possible.
- What Mighty will not do: ask you to paste your provider password into Mighty for this step (unless a future explicit vault feature exists — do not imply it).
- **Not now** — defer without trapping; Home remains honest about waiting / needs sign-in.
- Desktop Chrome guidance when mobile cannot complete capture.

### Trust signals that should appear

- Explicit role split (user authenticates).
- Benefit + limits + next step (permission principles).
- Single CTA owned by Attention / Home — not duplicated across Account Center and modals.
- No urgency theater unless value is truly at risk (first verification usually is not “expires in 1 hour” fake urgency).

### Mistakes to avoid

- Blurring who authenticates.
- Password fields into Mighty presented as the normal path without a clear vault product story.
- Parallel “Connect Amex” ritual that feels like a second product.
- Multiple surfaces asking for the same sign-in with different words (“Worker,” “Account Center,” “Sync”).
- Implying Mighty will open invisible logins and take over the account.

### Exit condition

User signs in at the provider (or defers). When session/evidence succeeds, journey moves to Returning to Home after verification.

---

## 10. Returning to Home after verification

### Purpose

Close the first-value loop: show that Mighty worked, that Home is now a calm status surface, and that silence means success — not emptiness.

### Primary user question

**Did it work — and do I need to do anything else?**

### Desired emotional state

Quiet competence — “It’s working. I can leave.”

### Primary message

You’re good. Mighty verified [Provider / your accounts] and will watch quietly from here.

*(If first data is partial: honest variant — “Access verified. Balances will show as Mighty finishes the first update.” — still calm, still one story.)*

### Primary CTA

None required.

Optional low-emphasis: **View accounts** or review a single Recent Win if real data exists.

### Secondary information

- Proof of value only when real: a Recent Win or concrete status — never fabricated.
- Reminder of daily use model: open Home to see if anything needs you; background success stays quiet.
- If something still needs the user, do **not** show all-clear — show the one remaining Attention item instead (honesty over celebration).

### Trust signals that should appear

- All-clear as a first-class success state.
- Reviewable outcome (what was verified / updated).
- Predictability: same Home skeleton as before; content changed, product didn’t.
- No forced tour, rating prompt, or upsell immediately after first success.
- Competence without noise.

### Mistakes to avoid

- Fake celebration with no real verification.
- Immediately inventing a new setup checklist (“Next: add 12 more accounts”).
- Returning users to Find accounts as if nothing happened.
- Conflicting statuses across Home, Accounts, and extension popup.
- Guilt for leaving (“Come back tomorrow to keep your streak”).

### Exit condition

User understands they can leave. Steady-state begins: Home on return answers “Am I good?” with all-clear or one precise ask.

---

# Current Product Gap Analysis

Comparison of the **current Mighty experience** against the ideal screenplay above. Sources include Trust by Design, Product Flow V1, Home Experience / Home V1B, Accounts + First-Data Handoff V1, and customer copy patterns.

Priority scale for redesign: **P0** (trust-critical / blocks informed consent or creates dead ends), **P1** (clarity / coherence), **P2** (polish / consistency).

---

## 1. Landing page

| Dimension | Assessment |
|-----------|------------|
| **What currently works** | Auth entry points exist (`/`, `/signup`, `/login`). Product has a real manifesto-level story to tell. |
| **What reduces trust** | Risk of reading as an internal tool or generic app shell rather than a calm, specific consumer product. |
| **What creates confusion** | Unclear whether the first viewport answers “What is Mighty?” in one breath before asking for commitment. |
| **Missing trust signals** | Explicit purpose + professionalism + “who are you?” identity at hero strength; precise non-hype privacy posture. |
| **Missing explanations** | How Mighty differs from “another points dashboard” or sync app. |
| **Visual weaknesses** | First impression may not yet pass the Trust by Design brand/competence test (calm, intentional, premium, specific). |
| **Recommended redesign priority** | **P1** — strengthen identity and one-message landing before growth spend; not the deepest trust cliff, but it sets the emotional baseline. |

---

## 2. Account creation

| Dimension | Assessment |
|-----------|------------|
| **What currently works** | Signup/login/password reset routes exist; signup subcopy can mention Chrome timing without demanding Gmail in-form. |
| **What reduces trust** | Any bleed of Worker / control-center language near signup; implying connection is already done. |
| **What creates confusion** | Unclear boundary between “create account” and “grant Gmail / install extension.” |
| **Missing trust signals** | Explicit “you’ll connect data next, with explanation first”; reversible framing. |
| **Missing explanations** | What happens immediately after signup (welcome → how it works → Gmail). |
| **Visual weaknesses** | Form may feel utilitarian vs. calm commitment. |
| **Recommended redesign priority** | **P1** — keep signup light; fix sequencing and copy so Gmail is never implied as already granted. |

---

## 3. Welcome

| Dimension | Assessment |
|-----------|------------|
| **What currently works** | Post-auth users can land on Home; onboarding completion APIs exist; orientation intent is documented. |
| **What reduces trust** | `/onboarding` as a dead redirect; orientation collapsed into a dismissible modal that is easy to miss. |
| **What creates confusion** | Landing on Empty Home / Dashboard without a clear “you just arrived” beat. |
| **Missing trust signals** | Dedicated welcome confirmation that the product sequence is intentional. |
| **Missing explanations** | “What the next few minutes will do” as its own moment. |
| **Visual weaknesses** | Modal-as-welcome can feel like an interruption on a page that already looks like a product shell. |
| **Recommended redesign priority** | **P1** — insert a real Welcome beat (even if short) before trust intro / empty Home. |

---

## 4. Trust introduction (“How Mighty works”)

| Dimension | Assessment |
|-----------|------------|
| **What currently works** | Onboarding modal title “How Mighty works”; privacy line about raw page text; Chrome guidance improved toward “Mighty in Chrome” in places. |
| **What reduces trust** | Historical / residual jargon (“Worker,” “Control center”) in customer strings; explanation may emphasize extraction more than the Gmail discovery ask that comes next. |
| **What creates confusion** | Modal-on-Empty mixes “how updates work” with “connect Gmail” without a full five-question consent frame. |
| **Missing trust signals** | Explicit limits for **Gmail** (not only extraction privacy); “what won’t you do”; disconnect/manual paths as first-class. |
| **Missing explanations** | Ordered story: email discovery → watch → Chrome when needed → you sign in. Gmail-first preface before OAuth. |
| **Visual weaknesses** | Modal feels secondary to a Dashboard chrome that wasn’t earned yet. |
| **Recommended redesign priority** | **P0** — highest leverage trust screen before the highest-stakes ask. |

---

## 5. Gmail connection

| Dimension | Assessment |
|-----------|------------|
| **What currently works** | Gmail OAuth routes; Find accounts / email-scan surface; Home Empty primary CTA “Connect Gmail”; readonly discovery intent in product docs. |
| **What reduces trust** | Permission surprise risk if user hits OAuth without a strong preface; Find accounts as a primary nav item makes discovery feel like a daily tool. |
| **What creates confusion** | Post-OAuth legacy redirects (e.g. Amex connect) compete with the generic discovery narrative; Outlook/IMAP/manual paths can dilute the default. |
| **Missing trust signals** | Dedicated pre-OAuth screen with why / benefit / limits / next / cancel. |
| **Missing explanations** | Precise “what we read for discovery” in customer language. |
| **Visual weaknesses** | Scan page may feel utilitarian vs. a guided consent moment. |
| **Recommended redesign priority** | **P0** — Gmail is the peak early anxiety; trust must peak *before* Google’s dialog. |

---

## 6. Account discovery

| Dimension | Assessment |
|-----------|------------|
| **What currently works** | Discovery pipeline, store, policy, and auto-enroll for high-confidence providers are real product capabilities. |
| **What reduces trust** | Process can feel invisible — “magic” without method. |
| **What creates confusion** | User may not know scanning is underway vs. failed vs. finished. |
| **Missing trust signals** | Visible progress; plain-language method (“known program senders”). |
| **Missing explanations** | Waiting state that teaches what will appear next. |
| **Visual weaknesses** | Thin customer-visible “scanning” theater relative to the strength of the backend. |
| **Recommended redesign priority** | **P1** — backend is ahead of presentation; add transparent waiting. |

---

## 7. Review discovered accounts

| Dimension | Assessment |
|-----------|------------|
| **What currently works** | Ambiguous suggestions can remain on Find accounts; dispositions exist; manual add/dismiss paths exist; Product Flow D1 allows lightweight confirmation. |
| **What reduces trust** | Auto-enroll without a clear reviewable confirmation feels invasive even when helpful. |
| **What creates confusion** | “Did anything happen?” after scan; users re-open Find accounts as if enrollment failed. |
| **Missing trust signals** | Reviewability of what was found and why; explicit confirm of watched set. |
| **Missing explanations** | Found ≠ logged in ≠ has data — must be stated at confirmation time. |
| **Visual weaknesses** | Confirmation presentation historically thin; connect modals can steal the story. |
| **Recommended redesign priority** | **P0** — ownership over discovered accounts is the difference between magic and surveillance. |

---

## 8. First dashboard

| Dimension | Assessment |
|-----------|------------|
| **What currently works** | Home V1B sparse briefing; Empty story + Connect Gmail; Attention-owned interrupts; first-data handoff work toward one CTA; “You’re good” as a real success state later. |
| **What reduces trust** | Nav label “Dashboard”; residual competing destinations (Find accounts weight, historical Account Center / Worker language); risk of all-clear tone while first-data still needs a human step if handoff is incomplete. |
| **What creates confusion** | Multiple setup vocabularies across Home, Accounts, extension popup; dual account UIs historically. |
| **Missing trust signals** | Consistent naming; enrollment confirmation on Home; single orchestrated next step. |
| **Missing explanations** | “What Mighty is doing next” after enroll in one sentence. |
| **Visual weaknesses** | Legacy density risk beside V1B; ops strip vs hero tension if copy disagrees. |
| **Recommended redesign priority** | **P0** — this is where trust either converts to first verification or dies in competing CTAs. |

---

## 9. First provider sign-in

| Dimension | Assessment |
|-----------|------------|
| **What currently works** | Manifesto-correct model: login is manual; natural-session capture; Attention can ask for sign-in; Accounts handoff plan standardizes Visit / Sign in CTAs. |
| **What reduces trust** | Parallel connect modals and “Account Center” mental models blur the ask; Worker terminology implies Mighty is an autonomous logger-in. |
| **What creates confusion** | Who authenticates; whether passwords go into Mighty; whether Sync is required. |
| **Missing trust signals** | Explicit role-split copy on the ask itself; unified destination after the ask. |
| **Missing explanations** | What happens after sign-in; Not now as a first-class defer. |
| **Visual weaknesses** | Fragmented across Home Attention, Accounts rows, extension popup, connect modals. |
| **Recommended redesign priority** | **P0** — vulnerability peak; role clarity is non-negotiable. |

---

## 10. Returning to Home after verification

| Dimension | Assessment |
|-----------|------------|
| **What currently works** | All-clear story exists; Recent Wins from real changes; quiet ops; Chrome reassurance footer; autonomous recovery philosophy. |
| **What reduces trust** | Status vocabulary drift across surfaces after verification; incomplete proof that “something happened.” |
| **What creates confusion** | Success on one surface while another still screams setup. |
| **Missing trust signals** | A clear first-success beat (access verified / first update) before settling into ambient all-clear. |
| **Missing explanations** | What steady state feels like (“silence means success”). |
| **Visual weaknesses** | May under-celebrate *honestly* (no proof) or over-noise with leftover setup chrome. |
| **Recommended redesign priority** | **P1** — close the emotional loop once P0 handoff and sign-in clarity exist. |

---

## Cross-cutting gaps (span multiple screens)

| Gap | Why it matters in the first 10 minutes |
|-----|----------------------------------------|
| **Terminology drift** (Dashboard / Worker / Account Center / Control center vs Home / Mighty in Chrome / Accounts) | Users learn multiple products instead of one calm system. |
| **Competing primary CTAs** | Ambiguity at decision time feels unsafe (Trust by Design anti-pattern). |
| **Permission without preface** | Violates “never surprise” and tanks Gmail conversion. |
| **Weak discovery confirmation** | Turns helpful auto-enroll into unease. |
| **Axes collapsed in UI language** | “Connected” used loosely destroys competence trust. |
| **Empty without teaching** | Reads as broken, not onboarding. |
| **Extension before Gmail** | Wrong trust order; raises browser anxiety before mailbox value is clear. |

---

# Product Roadmap

Recommended redesign order. **Optimized for user trust and clarity**, not implementation difficulty.

## Phase A — Consent and comprehension (do first)

**Goal:** No sensitive ask without understanding; no jargon at the moment of commitment.

1. **Trust introduction (“How Mighty works”)** — full five-question frame; Gmail-first; limits + next step + escape hatch.  
2. **Gmail connection preface** — dedicated explain → OAuth → confirmation → scanning.  
3. **Customer terminology lock** — Home, Mighty in Chrome, Accounts; remove Worker / Account Center / Control center from customer-facing first-run surfaces.

*Why first:* Gmail anxiety is the highest early trust cliff. If this fails, nothing downstream matters.

## Phase B — Ownership and one path (do second)

**Goal:** Discovery feels owned; first dashboard has exactly one next step.

4. **Review discovered accounts / enrollment confirmation** — reviewable found set; found ≠ logged in ≠ has data; confirm watched accounts.  
5. **Account discovery waiting state** — transparent progress + method in plain language.  
6. **First dashboard handoff** — single CTA state machine: Empty → (after enroll) Mighty in Chrome if needed → Visit provider → Verifying → All clear; suppress competing primaries.

*Why second:* Users who consented still bounce when they cannot see what was found or what to do next.

## Phase C — Role clarity at access (do third)

**Goal:** Provider sign-in feels safe and singular.

7. **First provider sign-in ask** — explicit role split; one Attention/Home-owned CTA; Not now; retire parallel connect-modal theater from the happy path.  
8. **Unified repair destination** — Accounts only; no Account Center fork in first-run or extension CTAs.

*Why third:* Vulnerability peaks at login; confusion here undoes Gmail trust.

## Phase D — Close the loop and raise the floor (do fourth)

**Goal:** First success feels real; entry points match the screenplay.

9. **Returning to Home after verification** — honest first-success beat, then calm all-clear; cross-surface status consistency.  
10. **Welcome + Account creation sequencing** — welcome beat after signup; signup copy that defers access asks.  
11. **Landing page** — brand-first, one composition, calm interest; same story as in-product trust intro.

*Why fourth:* These reinforce trust once the critical permission and handoff path already work.

## Phase E — Steady-state trust maintenance (after first 10 minutes work)

Out of the strict first-ten-minutes screenplay but required so the promise survives day two:

- Empty-state catalog across Home / Accounts  
- Permission UX for reconnect / revoke  
- Status vocabulary consistency checklist  
- Avoid reintroducing Sync rituals and dual nav CTAs

---

## Success criteria for the first 10 minutes

A redesign of this journey is successful when a new user can:

1. Explain what Mighty does within 30 seconds of Welcome / first Home.  
2. State why Gmail is requested **before** finishing Google OAuth.  
3. State something Mighty will **not** do with that access.  
4. See what was discovered and what they chose to watch.  
5. Name the single next step toward first verification (or correctly wait).  
6. State that **they** sign into providers — Mighty does not sign in as them.  
7. Return to Home and feel all-clear as success, not emptiness.

Instrument drop-off at: landing → signup → trust intro → OAuth start → OAuth complete → review confirm → first CTA complete → post-verification Home.

Treat qualitative “I felt safe connecting” as a first-class signal alongside conversion.

---

## Document control

| Field | Value |
|-------|-------|
| Version | V1 |
| Nature | First-experience screenplay + gap analysis + trust-ordered roadmap |
| Governing doc | [TRUST_BY_DESIGN.md](TRUST_BY_DESIGN.md) |
| Implementation | Not in this document — design contracts and plans come next |
| Suggested follow-ons | `TRUST_COPY_SYSTEM.md`, `PERMISSION_UX_SPEC.md`, `ONBOARDING_INFORMATION_ARCHITECTURE.md` (as listed in Trust by Design) |
