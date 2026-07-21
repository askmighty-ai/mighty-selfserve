# AttentionCompiler — AuthTruth → access_degraded (PR 2G)

**Status:** Implemented (PR 2G)  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4.2 / Part XIV #7  
**Module:** `mighty/attention_compiler.py`  
**Depends on:** [ATTENTION_COMPILER.md](ATTENTION_COMPILER.md) (PR 2B), [ATTENTION_ITEM.md](ATTENTION_ITEM.md)

## Why this exists

Not every AuthTruth human-adjacent signal is a blocker. When the primary method is **stale** or **login_unknown** without `needs_human`, the product still owes an informational candidate — never a false `signed_out` / login CTA storm.

```text
AuthTruth  →  Optional[AttentionItem]   # access_degraded
```

`compile_auth_attention` (blocker) remains unchanged and takes precedence when `needs_human`.

---

## Mapping

| Condition | Output |
|-----------|--------|
| `needs_human` | `None` (blocker path owns this Truth) |
| `stale` and not `needs_human` | `access_degraded` (`reason=stale`) |
| `state=login_unknown` and not `needs_human` and not already emitted via stale rule | `access_degraded` (`reason=login_unknown`) |
| otherwise | `None` |

If both `stale` and `login_unknown`, emit **one** item with `reason=stale` (stale is the stronger freshness signal). Fingerprint identity does not include the reason.

| Field | Value |
|-------|-------|
| class / urgency | `access_degraded` / `informational` |
| fingerprint | `auth:{provider}:access_degraded` |
| attention_id | `att_{user_id}_access_degraded_{provider}` |
| source_ref | `auth_truth:{user_id}:{provider}` (same join as blocker) |
| cta_key | `open_account_detail` |
| becomes_stale_at | `None` |
| observed_at / interruption_expected | from AuthTruth |

---

## Non-goals

- No change to auth_blocker emission rules
- No ranking / overlays / Home
- No phone capability gating (AttentionView — scenario 7 gap)
- No AccountState `data_gap` producer

---

## Tests

`tests/test_attention_compiler_access_degraded.py`
