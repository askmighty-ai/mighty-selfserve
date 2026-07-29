# Executive Review — Complete American Express Experience

**Cycle:** `docs/cycles/complete-amex-experience/`  
**Target Founder time:** ≤20 minutes (+ AT-00 Fresh Install when scheduling a full walkthrough)  
**Independent audit:** **Accept for Founder review** ([INDEPENDENT_AUDIT.md](INDEPENDENT_AUDIT.md)) — not a deploy clearance  
**Deploy:** **stopped**

---

## 1. Headline

Amex is now the **reference customer journey** with one canonical lifecycle story: nested “Unable to verify” closed for unsupported-data; Accounts CTA aligns; Chrome setup wins over Amex Visit when the worker is missing; narrator does not contradict Chrome-setup primary.

**Engineering complete only when AT-00–AT-15 all pass** (charter). Automated contracts for AT-05 / AT-08 / AT-13 shipped; **AT-00 Fresh Install and AT-15 remain live Founder gates**.

---

## 2. How to falsify (Founder)

1. **AT-00** — Brand-new invite user, no extension: signup → confirm Amex → Chrome → Visit → terminal → steady Home. Every next step obvious; no Home/Accounts contradiction.  
2. **AT-05 / AT-08** — After unsupported-data terminal: Home and Accounts both say logged-in-no-data (not nested Unable to verify).  
3. **AT-13** — Uninstall/disable extension with Amex watched needing sign-in: primary is Chrome setup, not Visit Amex.  
4. **AT-11 / R2** — Visit → immediate return: intent only, no verifying/do-nothing.  
5. **AT-10** — From Home alone after a cycle: can answer “Is Mighty working?” without opening Amex.

Screenshots (support only): `docs/pr-screenshots/complete-amex-experience/`

---

## 3. Product Realization report

| Status | Documented sections | Notes |
|--------|---------------------|-------|
| **Fully realized (this cycle)** | Amex unsupported-data nested honesty; Home↔Accounts label agreement for that terminal; Chrome-first when worker missing (Amex); narrator non-contradiction with Chrome primary | AT-05/08/13 contracts |
| **Remain partial** | AT-00 / AT-15 live Fresh Install → steady watching; full UBE milestone; other providers | Founder walkthrough required |
| **Unspecified decisions** | **None** — Amex-only demotion and lifecycle labels follow Accepted charter | — |

---

## 4. Ask

Independent Audit **Accept**. Run **AT-00 Fresh Install** (+ spot AT-05 / AT-08 / AT-13), then authorize deploy or Return.
