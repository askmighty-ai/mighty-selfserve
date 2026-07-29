# Audit Brief — Complete American Express Experience

**Auditor role:** [Independent Audit Charter](../../INDEPENDENT_AUDIT_CHARTER.md)  
**Charter:** [CYCLE_CHARTER.md](CYCLE_CHARTER.md) — **Acceptance Tests AT-00 through AT-15 are binding**  
**Cycle report:** [CYCLE_REPORT.md](CYCLE_REPORT.md)

---

## What to falsify

Hostile falsification of the Complete Amex Experience. **Engineering is complete only when every Acceptance Test passes.** Prefer live Founder-path probes over field-count theater.

### Must fail the product if

1. **AT-00** Fresh Install walkthrough hits a dead end, opaque step, false progress, or Home/Accounts contradiction.  
2. **AT-05 / AT-08** Nested `customer_access.status_label` says “Unable to verify” while top-level is “Logged in — no account data” (or Accounts contradicts Home).  
3. **AT-13** Chrome missing + Amex needs sign-in → primary teaches Amex Visit without explaining Chrome-first (or narrative overwrites Chrome setup).  
4. **R1 / R2** on Amex Visit path regress (verifying/do-nothing from intent; cold amnesia).  
5. Invented Amex balances or sticky Extracting after terminal NO_ACCOUNT_DATA.

### Evidence to inspect

- `mighty/customer_account_access.py` — `resolve_status_label`, `resolve_meaning`  
- `mighty/attention_compiler.py` — Amex AUTH demotion when worker missing  
- `mighty/journey_narrative.py` — Chrome-setup overlay skip  
- `app.py` — `_accounts_primary_cta_html` unsupported Amex CTA  
- `mighty/home_state.py` — `_waiting_row_label`  
- `tests/test_complete_amex_experience.py`  
- Screenshots: `docs/pr-screenshots/complete-amex-experience/`

### Suite

```bash
.venv/bin/pytest tests/test_complete_amex_experience.py tests/test_amex_value_pipeline_lb.py tests/test_ube_journey_narrator.py -q
```

### Verdict options

- **Accept for Founder review** — automated contracts hold; live AT-00/AT-15 remain Founder falsification  
- **Return** — any hard fail above sticks
