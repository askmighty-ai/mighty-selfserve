# AttentionCompiler — BenefitSignal → value_at_risk / opportunity (Milestone 4)

**Status:** Implemented  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4.2 / §4.3  
**Design note:** [ATTENTION_INTELLIGENT.md](ATTENTION_INTELLIGENT.md)  
**Module:** `mighty/attention_compiler.py` · loader `mighty/attention_loaders.py`

## Why this exists

Expiring certificates/credits and savings opportunities already exist as `action_items`. Attention must compile them into ranked candidates so Home/Worker do not keep a parallel recommendation/hero policy.

```text
BenefitSignal  →  Optional[AttentionItem]   # value_at_risk | opportunity
```

---

## Mapping

Eligible `btype`: `is_actionable` (certificate / travel_credit / cash_credit) or `is_needs_attention` (payment_due / renewal).

| Condition | Class |
|-----------|-------|
| urgency `urgent`/`soon`, or `days_left <= 14` | `value_at_risk` |
| otherwise (eligible) | `opportunity` |
| non-eligible btype | `None` |

One signal emits **at most one** item (value_at_risk XOR opportunity).

| Field | value_at_risk | opportunity |
|-------|---------------|-------------|
| urgency | `time_sensitive` | `opportunity` |
| reason | `value_at_risk` | `opportunity` |
| fingerprint | `benefit:{provider}:{field_key}` | same |
| attention_id | `att_{user}_value_at_risk_{provider}_{field_key}` | `…_opportunity_…` |
| source_ref | `action_item:{id}` when available | same |
| becomes_stale_at | `exp_date` | `None` |
| cta_key | `open_account_detail` | same |

### Loader

`load_benefit_signals` reads open `action_items` (not dismissed / completed / snoozed). No scoring/ranking in the loader.

---

## Non-goals

- No partnerships / `_generate_opportunities` second source yet  
- No provider branching in shared ranking  
- No Home recommendation ranking restoration  
- Value Intelligence (`account_opportunities`, Milestone 10) computes durable
  opportunity **facts** and does **not** emit AttentionItems — loaders may
  consume those facts later without a second ranker  

---

## Tests

`tests/test_attention_compiler_benefit.py`
