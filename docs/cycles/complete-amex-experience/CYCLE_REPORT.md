# Cycle Report — Complete American Express Experience

**Status:** Implemented — Independent Audit **Accept for Founder review** — **deploy stopped**  
**Started:** 2026-07-29  
**Charter:** [CYCLE_CHARTER.md](CYCLE_CHARTER.md) (**Accepted & frozen** + AT-00) · **Plan:** [CYCLE_PLAN.md](CYCLE_PLAN.md) · **Audit:** [INDEPENDENT_AUDIT.md](INDEPENDENT_AUDIT.md)  
**Deploy:** **stopped** until Founder walkthrough (AT-00–AT-15)

---

## Delivered

| Slice | Note |
|-------|------|
| Nested N1 closed | `resolve_status_label` returns “Logged in — no account data” for unsupported-data; never “Unable to verify” |
| Meaning order | `resolve_meaning` prefers unsupported / extraction_failed before generic “not seen yet” |
| Accounts CTA | Amex unsupported-data shows Visit/Open CTA (`data-amex-lifecycle="unsupported-data"`) |
| Home waiting chips | Prefer `presentation_label` (canonical lifecycle) |
| AT-13 Chrome-first | When worker missing, Amex AUTH_BLOCKER suppressed in `compile_attention_candidates` |
| Narrator vs Chrome | Journey overlay does not rewrite Chrome-setup primary featured card |
| Tests | `tests/test_complete_amex_experience.py` + LB nested-label assert |
| Screenshots | `docs/pr-screenshots/complete-amex-experience/` |

## Suite

```text
.venv/bin/pytest tests/test_complete_amex_experience.py tests/test_amex_value_pipeline_lb.py -q
→ 14 passed
```

Broader related (narrator + access + worker): green in focused runs during delivery.

## Acceptance Tests status (engineering evidence)

| AT | Automatable evidence | Live Founder gate |
|----|----------------------|-------------------|
| AT-00 Fresh Install | Path pieces exist; **live walkthrough required** | **Required** |
| AT-01–AT-04, AT-06–AT-07, AT-09–AT-12, AT-14 | Prior Amex + narrator suites + this cycle’s honesty fixes | Founder spot-check |
| AT-05 Unsupported | Nested label + meaning + Accounts CTA tests | Confirm nested field |
| AT-08 Home↔Accounts | presentation_label / status_label agreement tests | Confirm live |
| AT-10 Home-alone | Lifecycle honesty reduces opacity | Confirm live |
| AT-13 Chrome vs Amex | Compiler + narrator skip tests | Confirm dual-blocker |
| AT-15 Integration | Requires AT-00–AT-14 | **Required** |

## Explicitly not done

- Other providers / multi-provider framework  
- Visual door migration  
- Deploy  
- Declaring UBE complete  

## Residual risks

- AT-00 / AT-15 remain Founder live gates — engineering cannot self-certify Fresh Install.  
- Non-Amex AUTH_BLOCKER ranking unchanged when Chrome missing (Amex-only demotion).  
- `home_ui` bare `except` around narrator overlay unchanged (prior audit N1).
