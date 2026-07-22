# Home V1B — Daily Executive Briefing

**Status:** Implemented (V1B — UX refinement of V1A)  
**Product design:** [HOME_EXPERIENCE.md](HOME_EXPERIENCE.md)  
**Architecture:** [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md) · [ENGINEERING_CHARTER.md](ENGINEERING_CHARTER.md)  
**Related:** [ATTENTION_VIEW.md](ATTENTION_VIEW.md) · [FRESHNESS_CHANGE.md](FRESHNESS_CHANGE.md)

## Objective

Home answers one question: **Am I good?**

It should feel like a briefing from an exceptional chief of staff — calm, confident, intentionally sparse — not a dashboard of competing sections.

## Page anatomy (V1A)

| Region | Role |
|--------|------|
| **Greeting** | Time-aware name + date only |
| **Featured story** | Exactly one narrative: attention required, important opportunity, or all clear |
| **Recent Wins** | Proof of value from existing meaningful `account_changes` (omit if none) |
| **Working quietly** | Tiny ops strip for refresh / setup / pending approvals — never the hero |
| **Footer** | Chrome reassurance + last checked |

Removed from the primary surface: account-health chip strips, metrics “Also” rows, secondary opportunity lists, waiting-row tables, Truth Dashboard chrome.

## Featured story composition

Presentation composition only — **does not re-rank Attention**:

1. **Empty enrollment** → onboarding story (`HomeStateResult.featured`)  
2. **Attention primary present** → that item is the story (interrupt or opportunity as Attention already decided)  
3. Else → **all clear** story (“You’re good.”) — even when enrollment is Waiting/Update  

Waiting and Update never own the hero. They compress into the ops strip.

Opportunities appear on Home **only** when Attention has already selected them as primary. Secondary Attention opportunities are not listed on Home (they belong in Accounts / Activity / digests).

## Ownership (unchanged)

| Concern | Owner | Home role |
|---------|-------|-----------|
| Interrupt / opportunity ranking | Attention | Render primary as featured story |
| Enrollment / operational context | `resolve_home_state` | Ops strip + empty onboarding |
| Meaningful changes | Freshness / Change (`change_alerts_from_store`) | Recent Wins lines |
| Agent approvals pending | Activity pending count | Quiet ops note |
| AuthTruth / Capability | Capability modules | Debug only |

## Modules

| Module | Responsibility |
|--------|----------------|
| `mighty/home_projection.py` | Compose briefing DTOs — presentation only |
| `mighty/home_ui.py` | Sparse briefing HTML |
| `mighty/home_state.py` | Enrollment/operational context — no attention ranking |

## Architecture Decisions

### AD-HOME-1: Projection module, not a new domain

- **Decision:** `home_projection` only maps existing models into render DTOs.  
- **Why:** Preserves one owner per domain; Home stays a consumer.

### AD-HOME-2: Truth Dashboard demoted to debug

- **Decision:** Capability panels render only when `show_access_debug` is true.

### AD-HOME-3: Briefing over portfolio chrome (V1A)

- **Decision:** Demote health chips / metrics / secondary recs; elevate one story + Recent Wins + ops strip.  
- **Why:** “Am I good?” is answered by the story, proven by wins, with ops as footnotes.  
- **Alternatives considered:** Keep Control Tower / health strip as co-equal sections (rejected — dashboard density).  
- **Impact:** Accounts remains the repair surface; Home does not duplicate it.

### AD-HOME-4: Recent Wins from change store

- **Decision:** Project `change_alerts_from_store` (meaningful changes only).  
- **Why:** Freshness/Change already owns meaningful deltas; Home must not invent win scoring.  
- **Impact:** Empty wins section is omitted — silence is correct when nothing material happened.

### AD-HOME-5: V1B status-first hierarchy (UX only)

- **Decision:** Dominant status line (“You’re good.” / needs attention / worth attention); outcome copy without account counts; no primary CTA when all clear; subtle freshness; whisper-weight ops.  
- **Why:** Five-second “Am I good?” read; calm confidence over dashboard density.  
- **Impact:** No ownership or ranking changes — presentation and copy only.
