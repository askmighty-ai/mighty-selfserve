# AttentionCompiler — WorkerSignal → system (Milestone 4)

**Status:** Implemented  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4.2 / §4.3  
**Design note:** [ATTENTION_INTELLIGENT.md](ATTENTION_INTELLIGENT.md)  
**Module:** `mighty/attention_compiler.py` · loader `mighty/attention_loaders.py`

## Why this exists

When a user has enrolled accounts but the Chrome worker is missing or unreachable, Attention owes a single `system` blocker with `install_worker` CTA. Surfaces must not invent a separate “setup needed” ranking path.

```text
WorkerSignal  →  Optional[AttentionItem]   # system
```

---

## Mapping

| Condition | Output |
|-----------|--------|
| `enrolled_account_count <= 0` | `None` (empty onboarding is enrollment UX) |
| `installed` and `reachable` | `None` |
| `update_required` alone | `None` (not an M4 blocker) |
| not installed | `system` (`reason=worker_missing`) |
| installed but not reachable | `system` (`reason=worker_unreachable`) |

| Field | Value |
|-------|-------|
| class / urgency | `system` / `blocker` |
| fingerprint | `worker:setup` (stable across missing→unreachable) |
| attention_id | `att_{user_id}_system_worker` |
| source_kind / source_ref | `worker` / `worker:{user_id}` |
| cta_key | `install_worker` |
| provider | `None` |
| observed_at | `last_seen_at` |

### Loader facts

`load_worker_signal` reads `users.extension_version` / `extension_last_seen_at`.

- **installed:** version or last_seen present  
- **reachable:** last_seen within `WORKER_REACHABLE_SLA_SECONDS` (72h)  
- Missing users row / query failure → no signal (do not invent SYSTEM)

---

## Non-goals

- No ranking / overlays / delivery  
- No provider-specific branching  
- No forcing update-required into the hero  

---

## Tests

`tests/test_attention_compiler_worker.py`
