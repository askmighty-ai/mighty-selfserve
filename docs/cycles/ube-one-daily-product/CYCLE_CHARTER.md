# Cycle Charter — One Daily Product (UBE)

**Charter status:** **Accepted & frozen** (Founder directive 2026-07-29: proceed; perceive coherence over restyling; migrate by component family; Independent Audit on Founder perception before deploy)  
**Freeze rule:** Scope, success criteria, non-goals, and pause triggers below are frozen. Amend only with Founder re-acceptance.

**Area slug:** `ube-one-daily-product`  
**Parent milestone:** [Unified Beta Experience](../../milestones/MILESTONE_UNIFIED_BETA_EXPERIENCE.md)  
**Driven by:** [UBE Gap Assessment](../ube-gap-assessment/UBE_GAP_ASSESSMENT.md)  
**Evidence:** [FOUNDER_SESSION_2_VISUAL_LD.md](../../beta-evidence/FOUNDER_SESSION_2_VISUAL_LD.md) · [FOUNDER_SESSION_2_VISUAL_SURFACE_MIGRATION.md](../../beta-evidence/FOUNDER_SESSION_2_VISUAL_SURFACE_MIGRATION.md)

---

## Intake

| Field | Content |
|-------|---------|
| **Area** | Authenticated application — one continuous product across daily Home and sibling surfaces |
| **Outcome** | A Founder navigating the authenticated application cannot identify where one implementation ends and another begins |
| **Non-goals** | Visual restyling for its own sake; new providers; AI; analytics; landing redesign; auth-door unification; extension popup; vocabulary glossary; inventing Amex balances; unrelated cleanup / dead-code purge |
| **Hard constraints** | Feature-neutral UBE; migrate by **component family** (shell, nav, type, status/empty language) not by stylesheet patch or page paint; preserve all existing functionality; dedicated routes; Truth Over Completeness; no Inter sidebar + MDS hybrid on the customer path |
| **Known philosophy** | Mighty is where the user lives; one product perception beats implementation completeness theater |
| **Review** | Independent Audit evaluates **Founder perception** (not checklist of files migrated). **No deploy** until audit Accept + Founder go-ahead |

---

## Success criterion (binding)

> **A Founder navigating the authenticated application cannot identify where one implementation ends and another begins.**

Supporting checks (evidence, not substitutes for the sentence above):

1. Production (and production-like) daily home shares the same application chrome families as Accounts / Activity / Settings — brand, navigation, typography, primary affordance language.  
2. No Inter indigo sidebar on the customer daily-home path.  
3. UBE Visual + Navigation gates improve for Authenticated Application **including production**.  
4. Independent Auditor falsifies perception (walk Home → Accounts → Activity → Settings) rather than counting CSS files.

---

## Migration method

Migrate by **component family**, in this order of priority:

1. Application shell / frame  
2. Navigation  
3. Typography + brand mark  
4. Status / empty / primary CTA language continuity  

Do **not** measure done by “dashboard CSS updated.” Preserve every existing home behavior (projections, Visit flows, polls, banners).

---

## Pause triggers

- Restyling page chrome while leaving a second product generation on the customer path  
- Hybrid: MDS nav wrapped around an Inter sidebar-era frame and calling it coherent  
- Expanding into landing / login / popup in this cycle  
- “Cleanup” refactors unrelated to authenticated coherence  
- Deploy before Independent Audit Accept  

---

## Governing citations

- UBE milestone · gates Visual language + Navigation model  
- Decision [2026-07-29-unified-beta-experience.md](../../product/decisions/2026-07-29-unified-beta-experience.md)  
- Founder Vision: one product; competence trust  
- Product System: Mighty as system of engagement  
