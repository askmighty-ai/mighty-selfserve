# AttentionCompiler — platform facts → AttentionItem

**Status:** Implemented (PR 2B auth; PR 2F authorize; PR 2G access_degraded)  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4 / Part XIV  
**Module:** `mighty/attention_compiler.py`  
**Depends on:** [AUTH_TRUTH.md](AUTH_TRUTH.md) (PR1), [ATTENTION_ITEM.md](ATTENTION_ITEM.md) (PR 2A)  
**Authorize slice:** [ATTENTION_COMPILER_AUTHORIZE.md](ATTENTION_COMPILER_AUTHORIZE.md)  
**Degraded slice:** [ATTENTION_COMPILER_ACCESS_DEGRADED.md](ATTENTION_COMPILER_ACCESS_DEGRADED.md)

## Why this exists

The AttentionCompiler turns platform facts into immutable `AttentionItem` candidates. Producers so far:

```text
AuthTruth     →  Optional[AttentionItem]   # PR 2B auth_blocker
AuthorizeRow  →  Optional[AttentionItem]   # PR 2F agent_authorization
AuthTruth     →  Optional[AttentionItem]   # PR 2G access_degraded (stale / login_unknown)
inputs[]      →  tuple[AttentionItem, ...] # PR 2H gather (no ranking)
```

Given identical inputs, identical `AttentionItem` values (or `None`) must be produced. Fingerprints and `attention_id`s are deterministic and replay-stable.

Gather: [ATTENTION_COMPILER_CANDIDATES.md](ATTENTION_COMPILER_CANDIDATES.md).

---

## Responsibility boundary

| Layer | Owns | Does not own |
|-------|------|--------------|
| **AttentionCompiler (this module)** | Pure AuthTruth → auth_blocker mapping; deterministic ids | Ranking, overlays, store, UI, copy |
| **AttentionItem (PR 2A)** | Frozen candidate contract | Creating candidates |
| **AuthTruth (PR1)** | Primary-method auth projection | Attention shape |
| **AttentionStore / Ranker / View (later)** | Overlays, order, surfaces | Inventing candidates |

---

## Auth-blocker mapping

AuthTruth does not use `*_required` terminal strings as its public state. Human-need is projected as `needs_human` + `needs_human_reason` / `interruption` (RFC §3). The brief vocabulary maps as follows:

| Brief phrase | AuthTruth condition | Compiler output |
|--------------|---------------------|-----------------|
| `signed_in` (no human) | `needs_human is False` | `None` |
| `login_required` | `needs_human` + reason `login` | `auth_blocker` (`reason=login`) |
| `mfa_required` | `needs_human` + reason `mfa` | `auth_blocker` (`reason=mfa`) |
| `captcha_required` | `needs_human` + reason `captcha` | `auth_blocker` (`reason=captcha`) |
| `consent_required` | `needs_human` + reason `consent` | `auth_blocker` (`reason=consent`) |
| `unknown_human` | `needs_human` + reason `unknown_human` | `auth_blocker` (`reason=unknown_human`) |

Additional rules in this PR:

* **Stale alone** (`stale=True`, `needs_human=False`) emits nothing from the **blocker** path. See PR 2G `compile_access_degraded_attention`.
* **Dual path** never reaches this function as a customer blocker: AuthTruth already projects the **primary** method only; non-primary Runtime `needs_human` stays ops-only.
* **Reason resolution:** prefer `needs_human_reason`, else `interruption` when it is a known auth reason, else `unknown_human`.
* **CTA:** `browser_session` → `start_provider_login`; `managed_runtime` → `focus_managed_runtime`; other methods → `noop` (they do not project `needs_human` today).
* **`becomes_stale_at`:** always `None` for auth blockers (deadlines belong to benefit/signal facts).
* **`observed_at` / `interruption_expected`:** passed through from AuthTruth.
* **`projected_at`:** never copied onto the item (would break fact-derived determinism).

---

## Deterministic identity

| Field | Formula |
|-------|---------|
| `fingerprint` | `auth:{provider}:needs_human` |
| `attention_id` | `att_{user_id}_auth_blocker_{provider}_needs_human` |
| `source_ref` | `auth_truth:{user_id}:{provider}` |
| `source_kind` | `auth` |
| `attention_class` / `urgency` | `auth_blocker` / `blocker` |

Provider is lowercased. **Fingerprint and `attention_id` do not include the interruption reason**, so login → captcha is one candidate identity with an updated `reason` (RFC Part XIV scenario 3).

Helpers: `auth_blocker_fingerprint`, `auth_blocker_attention_id`, `auth_truth_source_ref`, `compile_auth_attention`.

---

## Non-goals (this PR)

- No ranking / primary selection
- No AttentionStore / overlays / persistence
- No Home / Worker / Push / Activity integration
- No notifications, APIs, UI, or metrics
- No Benefit / Worker / AccountState compiler inputs

---

## Tests

`tests/test_attention_compiler.py` — exhaustive golden `to_dict()` fixtures for every auth-blocker reason, plus replay-stability proofs (identical Truth → identical Item; reason-change keeps fingerprint/`attention_id`).

`tests/test_attention_compiler_authorize.py` — AuthorizeRow → agent_authorization (PR 2F).

`tests/test_attention_compiler_access_degraded.py` — AuthTruth → access_degraded (PR 2G).

`tests/test_attention_compiler_candidates.py` — multi-input gather (PR 2H).
