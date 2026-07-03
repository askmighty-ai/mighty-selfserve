# Mighty Product Manifesto

Mighty is a quiet co-pilot for your financial and loyalty life. It watches the accounts you already have, keeps them current in the background, and speaks up only when something is worth your time.

This document is the product north star. When design, engineering, or copy decisions conflict, come back here.

---

## What we believe

**People should not manually connect dozens of accounts.**

Your inbox already knows which airlines, hotels, and card issuers you use. Mighty discovers those accounts from Gmail and enrolls them automatically. Onboarding is not a checklist of two hundred "Add account" clicks.

**Mighty should work while you live your life.**

The Chrome extension watches normal browsing. When you visit a provider you are already logged into, Mighty detects the session and extracts data. There are no scheduled sync marathons, no popup tabs, and no ritual "Sync now" button for the happy path.

**The dashboard is a mirror, not a control panel.**

Home answers one question in five seconds: *Does anything need me?* When all is well, the experience is calm confirmation—not an empty dashboard begging for more setup. When something is blocked, Mighty surfaces one clear next step.

**Login is the only manual step.**

Signing into a provider is the user's job. Discovery, session detection, extraction, and refresh are Mighty's job. The dashboard shows results; it never impersonates the user.

**Mighty works quietly; the app speaks only when something needs you.**

Background updates are silent. Healthy accounts do not generate notifications. Urgency is reserved for real value at risk—expiring credits, session loss, failed extraction—not for engagement.

**Discovery, session, and extraction are separate concerns.**

Whether Mighty knows an account exists, whether you are logged in, and whether we have fresh data are three different questions. We do not collapse them into a single muddy "connected" flag. The UI reflects the actual state.

**Show truth, never placeholders.**

When real data exists, show it. When it does not, say so plainly—"waiting for first visit," not demo balances or fake perks. Trust is built by being accurate, not optimistic.

**Depth on demand.**

Home is an attention inbox, not a spreadsheet. Balances and details live in account views, opened when the user wants them. One featured action beats a wall of cards.

**Agents act with permission.**

For AI agents, Mighty is the authorization layer: consequential actions are logged, shown to the user, and approved inside Mighty—not summarized in chat. The record of what was approved must be complete enough to verify what happened.

---

## Principles (quick reference)

Use these names in pull requests and design reviews.

| Principle | In one line |
|-----------|-------------|
| **Zero bulk onboarding** | Gmail discovery auto-enrolls; no manual "Add × 200." |
| **Natural-session capture** | Extension watches normal visits; no forced sync rituals. |
| **Action only when blocked** | CTAs appear when Mighty cannot proceed—not for healthy accounts. |
| **Separate axes** | Discovery, session, and extraction stay independent. |
| **Works quietly** | Background success is silent; interrupt only when it matters. |
| **Login is manual** | User logs in; Mighty handles everything else. |
| **Truthful by default** | Real data or honest waiting states—never fake demo content. |
| **Attention inbox** | Home curates what needs you; depth lives in detail views. |
| **Verified authorization** | Agents request approval with full detail before acting. |

---

## What we do not build

- Per-account **Sync now** or **Connect** buttons on the happy path
- Dashboard grids that duplicate the Accounts maintenance view
- Scheduled sync marathons or proactively opened provider tabs
- Demo or placeholder data when real extraction has succeeded
- Engagement patterns that punish healthy users with empty states or upsells
- Internal lifecycle jargon exposed as primary UI ("worker," enum names, raw status codes)

---

## Roles in the product

| Part | Role | User mental model |
|------|------|-------------------|
| **Chrome extension** | Worker | "Mighty runs in the background while I browse." |
| **Dashboard / Home** | Control center | "I check in when I want; Mighty already did the work." |
| **Accounts** | Setup and repair | "I go here to fix something or audit connections." |
| **Activity** | Agent approvals | "I approve what my agents want to do." |

Sync is infrastructure, not a user ritual.

---

## Emotional target

Opening Mighty when all is well should feel like checking the weather on a clear day—brief confirmation, then done.

Opening when something matters should feel like one well-prioritized notification, not a control panel full of competing CTAs.

---

## How this evolves

The manifesto changes rarely. When it does, the change should be deliberate and discussed—not drifted into through incremental compromises.

Implementation will lag the manifesto. That is expected. Prefer partial alignment over features that contradict these principles.

For how engineers apply this document day to day, see [CONTRIBUTING_PRODUCT.md](../CONTRIBUTING_PRODUCT.md).
