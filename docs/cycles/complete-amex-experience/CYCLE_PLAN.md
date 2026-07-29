# Cycle Plan — Complete American Express Experience

**Charter:** [CYCLE_CHARTER.md](CYCLE_CHARTER.md) (**Accepted & frozen** + AT-00)  
**Rule:** Engineering complete only when AT-00–AT-15 all pass.

---

## Success criteria

- [ ] AT-00 Fresh Install walkthrough path coherent (invite → steady Amex watching)
- [ ] AT-01–AT-14 scenario contracts hold on Amex customer path
- [ ] AT-15 integration requires AT-00–AT-14
- [ ] Home ↔ Accounts (+ nested) share one Amex lifecycle
- [ ] Narrator R1/R2 preserved on Amex Visit path
- [ ] Chrome vs Amex primary never contradictory without explanation
- [ ] Focused tests + screenshots + Independent Audit package

---

## Slice sequence

| # | Slice | Closes |
|---|--------|--------|
| 1 | Nested N1 + meaning order (`customer_account_access`) | AT-05, AT-08 |
| 2 | Accounts CTA/next-action for unsupported-data | AT-05, AT-08 |
| 3 | Chrome-first when worker missing on Amex path (Attention + handoff) | AT-13, AT-00 |
| 4 | Home waiting labels use canonical lifecycle (not nested contradict) | AT-08, AT-10 |
| 5 | Regression suite `test_complete_amex_experience.py` | AT-00–AT-14 contracts as automatable |
| 6 | Screenshots + cycle report + audit brief | Packaging |

---

## Test strategy

- Unit: status_label / meaning / Accounts CTA / Chrome rank after no-qualifying and dual-blocker
- Preserve existing `test_ube_journey_narrator`, `test_amex_value_pipeline_lb`, `test_visit_amex_home_base`
- Founder AT-00 / AT-15 remain live walkthrough gates

---

## Pause triggers

Per charter. Escalate if Amex-only fix would require inventing multi-provider framework or Amex balances.
