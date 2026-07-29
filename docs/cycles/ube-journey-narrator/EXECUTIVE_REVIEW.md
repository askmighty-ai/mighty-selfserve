# Executive Review — Journey Narrator (UBE)

**Cycle:** `docs/cycles/ube-journey-narrator/`  
**Target Founder time:** ≤20 minutes  
**Independent audit:** **Accept for Founder review** ([INDEPENDENT_AUDIT.md](INDEPENDENT_AUDIT.md)) — continuity + I1–I5 + **R1**; **not** a deploy clearance  
**Deploy:** **stopped**

---

## 1. Headline

Home now persists **user-action** and **system-observation** events separately and composes the featured story from them. After Visit Amex, returning to Mighty acknowledges the Visit and — when still asking to sign in — **explains why the previous attempt did not confirm a session (R1)**.

---

## 2. How to falsify (Founder)

1. From Home, Visit / Sign in to Amex (new tab).  
2. Hard-reload Mighty before Chrome confirms anything.  
3. Confirm story still knows you visited (not cold first-ask copy).  
4. If Sign-in is asked again, confirm body explains the prior attempt failed to produce a confirmed session.  
5. Confirm `data-narrative-events` on Home marks which events the story reflects.

---

## 3. Ask

After Independent Audit **Accept**: authorize deploy, or Return with cheapest continuity fix.
