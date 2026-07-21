# AttentionItem — frozen candidate contract

**Status:** Implemented (PR 2A)  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §4 / Part XIV  
**Module:** `mighty/attention.py`

## Why this exists

`AttentionItem` is the **output contract** of the Attention Engine (AttentionCompiler). It is a pure, immutable candidate for user attention derived entirely from platform facts.

This PR freezes the model only. It does **not** implement the compiler, ranking, overlays, persistence, Home, or notifications. Future PRs must consume this contract without changing it.

Given identical platform facts, identical `AttentionItem` values must be produced.

---

## Responsibility boundary

| Layer | Owns | Does not own |
|-------|------|--------------|
| **AttentionItem (this module)** | Immutable candidate shape, enums, serialize/validate | Ranking, overlays, copy English, delivery |
| **AttentionCompiler (later)** | Emit candidates from AuthTruth / Authorize / Benefit / … | Store writes, UI |
| **AttentionStore (later)** | Snooze / dismiss / in_flight / receipts (overlays) | Creating items |
| **Ranker / AttentionState (later)** | Primary + queue order, silence | Mutating item fields |
| **AttentionView / surfaces (later)** | Windowing, capability gating, resolved copy | Inventing candidates |
| **AuthTruth / Authorize / …** | Source facts referenced by `source_ref` | Attention shape |

---

## Model

```text
AttentionItem
  schema_version          # contract version (1)
  attention_id            # stable id for overlays, events, tie-break
  user_id
  attention_class         # RFC AttentionClass
  urgency                 # intrinsic band; must match class (RFC §7)
  provider                # optional subject provider id
  fingerprint             # root-cause identity for dedupe / lifecycle
  reason.code             # structured machine reason (no English)
  cta_key                 # machine CTA (no label text)
  source_kind             # owning input system
  source_ref              # id in owning system
  observed_at             # underlying fact timestamp (ISO-8601) or null
  becomes_stale_at        # intrinsic deadline when part of the fact
  interruption_expected   # auth-derived expected vs unexpected human
```

Supporting types: `AttentionClass`, `AttentionUrgency`, `AttentionSourceKind`, `AttentionCtaKey`, `AttentionReason`.

---

## Field ownership and consumers

Every field has at least two independent consumers or a documented architectural justification.

| Field | Why it exists | Owner (producer) | Consumers |
|-------|---------------|------------------|-----------|
| `schema_version` | Evolve the wire contract safely | Contract / compiler | Serializers, readers, migrations |
| `attention_id` | Stable handle across compile passes | Compiler (deterministic from facts) | AttentionStore overlays, events, ranking tie-break, delivery correlation |
| `user_id` | Scope candidates to one user | Compiler | Store, delivery, analytics |
| `attention_class` | Kind of interrupt (auth, authorize, …) | Compiler | Ranker (§7), silence, metrics, copy resolution |
| `urgency` | Intrinsic urgency band | Compiler | Ranker pairing rule, delivery SLA |
| `provider` | Subject provider when applicable | Compiler | Ranking tie-break, Account surfaces, event correlation |
| `fingerprint` | Root-cause identity (opened vs updated vs cleared) | Compiler | Lifecycle events, overlay GC, CAPTCHA-same-item rule |
| `reason` | Structured why (e.g. `login` → `captcha`) | Compiler | Copy keys, `attention.updated` materiality, metrics buckets |
| `cta_key` | What command resolves the candidate | Compiler | Command dispatch (§4.5), View label resolution |
| `source_kind` | Which input system produced the fact | Compiler | Activity filter, analytics |
| `source_ref` | Join key back to AuthTruth / Authorize / Benefit / … | Compiler | Clear/join paths, debugging, authorize terminal mapping |
| `observed_at` | Timestamp from the underlying fact (not wall-clock emit time) | Compiler (from source fact) | Freshness, event materiality, determinism |
| `becomes_stale_at` | Intrinsic deadline when the fact has one | Compiler (from benefit/signal) | Ranker within `value_at_risk`, delivery timing |
| `interruption_expected` | Bootstrap/expected vs unexpected human (auth) | Compiler (from AuthTruth) | Product metrics (Part X), copy tone |

---

## Intentionally omitted fields

These belong to later PRs or other layers. They must **not** be added to `AttentionItem`.

| Omitted | Belongs to | Why omitted |
|---------|------------|-------------|
| `title` / `body` / `cta_label` / English copy / HTML | View / copy resolver | Presentation; PR 2A keeps structured keys only. RFC §4.1 sketch included resolved English — superseded for this contract. |
| `hero` / `primary` / queue position / ranking score | Ranker → `AttentionState` | Ranking output, not candidate intrinsic |
| `dismissed` / `snoozed` / `in_flight` / `until` | `AttentionOverlay` / Store | Interaction state; candidates remain emitted while overlays hide them |
| `delivered` / notification history / read-unread | Delivery / Store receipts | Channel state |
| `created_at` / `updated_at` as wall-clock first-seen | Would require Store memory | Breaks pure determinism from facts; use `observed_at` from the source fact |
| `requires_human` | — | Rejected by RFC D13; redundant with class/urgency |
| `account_id` | Optional later via `source_ref` | Scenarios covered without a dedicated field |
| Surface capability flags (e.g. desktop-only CTA) | AttentionView | See gap below |

Snooze and dismiss are **Store commands**, not `cta_key` values.

---

## Serialization and validation

- `AttentionItem.to_dict()` / `AttentionItem.from_dict()` round-trip.
- Enums serialize as their string values.
- `reason` serializes as `{"code": "..."}` (string form accepted on input).
- Validation rejects unknown enums, empty ids, whitespace reason codes, wrong `schema_version`, and class/urgency pairs outside RFC §7.
- The object is `@dataclass(frozen=True)` — no mutation after construction.

---

## Part XIV scenario coverage

Scenarios from RFC Part XIV expressed **only** as candidate sets. Overlay, ranking, and delivery behavior are noted but not modeled.

| # | Scenario | AttentionItem representation |
|---|----------|------------------------------|
| 1 | Extension session expired | One `auth_blocker` (`reason=login`, `cta_key=start_provider_login`). Cleared = empty set. |
| 2 | Runtime MFA mid-session | One `auth_blocker` (`reason=mfa`, `interruption_expected=false`). Recovering = empty set. |
| 3 | CAPTCHA during login | Same `fingerprint` / `attention_id`; `reason` changes `login` → `captcha` (one item identity). |
| 4 | Snooze blocker | Same `auth_blocker` candidate; snooze is overlay — not on the item. |
| 5 | Agent authorize | One `agent_authorization` (`source_kind=authorize`, `cta_key=open_activity_approval`). |
| 6 | Multi-provider signed_out | Multiple `auth_blocker` items (one per provider). Primary = later ranker. |
| 7 | Phone-only | `access_degraded` for stale (not `auth_blocker`). Capability gating = View gap. |
| 8 | Bootstrap MFA expected | Same `auth_blocker` class; `interruption_expected=true`. |
| 9 | Dual path | **Empty set** — Runtime needs_human on non-primary method is not a customer candidate. |
| 10 | Dismiss opportunity + snooze login | Both candidates still emitted; dismiss/snooze are overlays; primary policy is ranker. |

Unit fixtures: `tests/test_attention.py` (`TestPartXivScenarios`).

---

## Documented gaps (do not extend the model here)

1. **Surface capability / completable CTA (scenario 7)**  
   Mobile cannot complete `browser_session` login. Completeness is a property of (CTA key × surface capability), not of the candidate. **Owner:** AttentionView (later). Do not add `cta_completable_on` to `AttentionItem`.

2. **Resolved customer English**  
   RFC §4.1 / §8 still describe compiler-resolved `title`/`body`/`cta_label` on the item. This contract deliberately keeps copy off the item. **Owner:** copy resolver feeding AttentionView / delivery payload (later). Structured inputs: `attention_class` + `reason` + `cta_key` + `provider`.

3. **Overlay lifecycle fields**  
   Snooze, durable dismiss, in_flight, delivery receipts remain on AttentionStore overlays (RFC §4.4).

4. **Ranking / silence / primary**  
   Owned by AttentionState after compile + overlays (RFC §7).

If a future scenario cannot be represented with this contract, document a new gap — do not silently widen the model in a consumer PR.

---

## Non-goals (this PR)

- No AttentionCompiler / engine
- No ranking
- No AttentionStore / persistence
- No Home / Worker / Push integration
- No notifications
- No provider-specific logic
- No mutable interaction state
