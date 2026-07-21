# AttentionCompiler — AuthorizeRow → AttentionItem (PR 2F)

**Status:** Implemented (PR 2F)  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4.3 / Part XIV #5  
**Module:** `mighty/attention_compiler.py`  
**Depends on:** [ATTENTION_ITEM.md](ATTENTION_ITEM.md) (PR 2A); existing `actions` authorize store (facts only)

## Why this exists

Agent pending approvals are compiler inputs, not AttentionStore upserts (RFC D5). This PR adds the second pure producer:

```text
AuthorizeRow  →  Optional[AttentionItem]
```

Given identical pending rows, identical `AttentionItem` values (or `None`) must be produced. Cleared when the authorize store reaches a terminal status — the compiler simply stops emitting.

---

## Responsibility boundary

| Layer | Owns | Does not own |
|-------|------|--------------|
| **compile_authorize_attention (this PR)** | Pending row → `agent_authorization` mapping; deterministic ids | Approving/denying actions, ranking, overlays |
| **Authorize store (`actions`)** | Pending / approved / denied facts | AttentionItem creation |
| **AttentionStore** | Snooze / dismiss / in_flight overlays | Creating authorize candidates |
| **Auth compiler (PR 2B)** | AuthTruth → auth_blocker | Authorize rows |

---

## AuthorizeRow (compiler input)

Minimal frozen fact shape — not a second ledger. Loaders map `actions` rows into this:

```text
action_id     # actions.id
user_id
status        # pending | approved | denied | expired | …
created_at    # → observed_at when present
expires_at    # → becomes_stale_at when present
provider      # optional subject provider
```

---

## Mapping

| Status | Output |
|--------|--------|
| `pending` | one `agent_authorization` |
| any other (approved, denied, expired, …) | `None` |

Emitted item fields:

| Field | Value |
|-------|-------|
| `attention_class` / `urgency` | `agent_authorization` / `blocker` |
| `reason` | `pending_authorization` |
| `cta_key` | `open_activity_approval` |
| `source_kind` | `authorize` |
| `fingerprint` | `authorize:row:{action_id}` |
| `attention_id` | `att_{user_id}_agent_authorization_row{action_id}` |
| `source_ref` | `authorize:{action_id}` |
| `provider` | lowercased row.provider or `None` |
| `observed_at` | `created_at` |
| `becomes_stale_at` | `expires_at` (ranker drops when `now >= expires_at`) |
| `interruption_expected` | `False` (authorize is never auth-bootstrap) |

Status comparison is case-insensitive. Empty `action_id` / `user_id` rejected.

---

## Non-goals (this PR)

- No DB loader / SQL against `actions`
- No approve/deny commands
- No ranking, overlays, Home, Activity UI, push
- No AuthTruth changes
- No multi-input `compile_all` gather (callers zip lists later)

---

## Tests

`tests/test_attention_compiler_authorize.py` — golden fixtures, terminal statuses → None, replay stability, identity helpers aligned with Part XIV scenario 5.
