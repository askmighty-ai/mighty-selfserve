# Cycle Plan — Journey Narrator (UBE)

**Charter:** [CYCLE_CHARTER.md](CYCLE_CHARTER.md) (**Accepted & frozen**)  
**Audit brief:** [AUDIT_BRIEF.md](AUDIT_BRIEF.md)  
**Assessment:** [../ube-gap-assessment/UBE_GAP_ASSESSMENT.md](../ube-gap-assessment/UBE_GAP_ASSESSMENT.md)

## Strategy

Make Home an **event-based narrator**: persist **user-action** and **system-observation** events separately; compose every dashboard narrative state from identified event(s); never re-issue the opening ask without an event-backed explanation — including under interruptions and **R1**.

## Slices

| # | Slice | Status |
|---|-------|--------|
| 1 | Persist user-action events (Visit / Sign-in) | Done |
| 2 | Persist system-observation events separately | Done |
| 3 | Home composition + state→events attrs | Done |
| 4 | Waiting / non-progress / R1 repeat-ask copy | Done |
| 5 | Interruption-oriented sync (reload/focus via durable events) | Done |
| 6 | Tests + screenshots + Independent Audit — stop before deploy | In progress |

## Stop

Independent Audit (I1–I5 + R1) → Founder → deploy only on ask.
