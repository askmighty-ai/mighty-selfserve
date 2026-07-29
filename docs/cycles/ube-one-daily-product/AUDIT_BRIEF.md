# Independent Audit Brief — One Daily Product (UBE)

**Role:** Independent Auditor only — [INDEPENDENT_AUDIT_CHARTER.md](../../INDEPENDENT_AUDIT_CHARTER.md)  
**Cycle:** `docs/cycles/ube-one-daily-product/`  
**Delivery claim:** Production customer `/dashboard` participates in the same authenticated chrome families as Accounts / Activity / Settings.

---

## Binding success criterion (falsify this)

> A Founder navigating the authenticated application cannot identify where one implementation ends and another begins.

**Judge Founder perception, not implementation completion.** Counting migrated files, CSS bridges, or helper functions is not Accept criteria.

---

## Required walk (hostile inspection)

Production-like env: `HOME_OS_ENABLED` unset, `DEMO_MODE` unset, `MIGHTY_ENV=production` (or equivalent gate-off).

1. Sign in → GET `/dashboard`  
2. Navigate to `/credentials`  
3. Navigate to `/settings`  
4. If Activity nav is present → `/activity`  

On each step, attempt to locate an **implementation seam** via:

- Application shell / frame  
- Navigation set and Home target  
- Brand mark  
- Typography (Inter vs Fraunces/Jakarta)  
- Primary affordance / status language continuity  

Hard fails if customer `/dashboard` still has `class="sidebar"` or `family=Inter` document chrome, or if Home nav points at a dead/gated `/home` while Accounts use MDS.

---

## Authority & artifacts

| Artifact | Path |
|----------|------|
| Charter (Accepted & frozen) | `CYCLE_CHARTER.md` |
| Plan | `CYCLE_PLAN.md` |
| Report | `CYCLE_REPORT.md` |
| Executive | `EXECUTIVE_REVIEW.md` |
| Gap assessment | `../ube-gap-assessment/UBE_GAP_ASSESSMENT.md` |
| UBE milestone | `../../milestones/MILESTONE_UNIFIED_BETA_EXPERIENCE.md` |
| Shell | `mighty/authenticated_app_shell.py` |
| Wrap helper | `app.py` `_dashboard_as_mds_authenticated_document` |
| Tests | `tests/test_ube_one_daily_product.py` |
| Screenshots | `docs/pr-screenshots/ube-one-daily-product/` |
| Prior residual | `../visual-surface-migration/INDEPENDENT_AUDIT.md` (production Inter home) |

---

## Out of scope for Return noise

- Enabling Home OS in production  
- Landing / login door  
- Body-content parity between Home OS Quiet Field and legacy home sections (chrome claim only)  
- Taste among Vision-neutral aesthetics inside one family  
- Unrelated cleanup suggestions  

---

## Disposition

Write `INDEPENDENT_AUDIT.md` in this folder. Recommend **Accept for Founder review** or **Return to Cursor**. Do not deploy. Do not edit production code.
