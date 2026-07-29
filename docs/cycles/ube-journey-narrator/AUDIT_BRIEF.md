# Independent Audit Brief — Journey Narrator (UBE)

**Role:** Independent Auditor only — [INDEPENDENT_AUDIT_CHARTER.md](../../INDEPENDENT_AUDIT_CHARTER.md)  
**Cycle:** `docs/cycles/ube-journey-narrator/`  
**Charter:** [CYCLE_CHARTER.md](CYCLE_CHARTER.md) (**Accepted & frozen**)

---

## Binding success criterion

> After a meaningful Home action (especially Visit / Sign-in), a Founder returning to Mighty can see what just happened, what Mighty is waiting for, and why any next ask exists — and cannot conclude the product forgot their action.

Judge **Founder perception of continuity**, including under interruption and **R1** — not “event rows exist.”

---

## Governing rule R1 (mandatory)

> The dashboard may never request a repeated user action without first explaining why the previous attempt did not produce the expected outcome.

Hard fail if Sign-in/Visit is re-offered after a prior Visit/Sign-in user-action without an explicit why-previous-failed explanation bound to narrative events.

---

## Event-model falsification (required)

1. **Separate persistence** — User-action events and system-observation events are distinct.  
2. **State → event(s)** — Every user-visible dashboard narrative state identifies which event(s) it reflects.  
3. **No cold amnesia** — Visit then reload/poll must not re-issue the opening ask without acknowledging the Visit user-action event.

---

## Interruption scenarios (required for Accept)

| ID | Scenario | Hard fail |
|----|----------|-----------|
| **I1** | Reload mid-journey (Visit → hard reload, no observation yet) | Visit forgotten; cold opening CTA |
| **I2** | Tab return / focus poll with no extension progress | Amnesia or silent reset; no waiting/non-progress bound to Visit |
| **I3** | Late system observation after Visit | Observation-only jump that erases Visit without explanation |
| **I4** | Interrupted / abandoned Visit | Pretends Visit never happened **or** pretends success |
| **I5** | Contradictory observation (still needs login after Visit) | Cold identical Sign-in with no continuity (**also R1**) |

---

## Disposition

Write `INDEPENDENT_AUDIT.md` in this folder. **Accept for Founder review** or **Return to Cursor**. Do not deploy. Do not edit production code.
