# RFC: Authentication & Attention Platform (v2)

**Status:** Design proposal (no implementation)  
**Version:** 2 — replaces v1; no backward compatibility with v1 shapes or dual-ownership rules  
**Date:** July 2026  
**Related:** [ACCESS_FLOW.md](ACCESS_FLOW.md) · [ACCOUNT_STATE.md](ACCOUNT_STATE.md) · [CONNECTORS.md](CONNECTORS.md) · [PROVIDER_RUNTIME.md](PROVIDER_RUNTIME.md) · [HOME_EXPERIENCE.md](HOME_EXPERIENCE.md) · [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md) · [PRODUCT_MANIFESTO.md](PRODUCT_MANIFESTO.md)

---

## Executive summary

Mighty needs two things that v1 incorrectly fused into one “platform peer to Provider Runtime”:

1. **Access truth** — a single product-facing read model of whether a provider session is authenticated, for the account’s **primary** access method.
2. **Attention** — a product policy layer that ranks what deserves human time and delivers it quietly.

**v2 stance:**

- **Writes for authentication stay where they already belong:** Access Manager (extension / PSS) and Provider Runtime (AccessState publication). Nothing in this RFC adds a second auth write store.
- **`AuthTruth` is a pure projection** of those writes for the primary access method only.
- **Attention** owns ranking, silence, dismiss/snooze overlays, delivery, and the Home/Worker hero. It does not own browsers, recovery, or auth evidence.
- **AccountState** owns per-account mirror fields for Accounts/detail. It does **not** own the cross-account hero CTA.

**Design principle:** One write plane for auth. One compiler for attention. One ranking table. One analytics owner per human moment.

---

## Part 0 — Principal-review decision log

Every v1 review recommendation is accepted or rejected here. Rejected items are not left ambiguous in the body.

| # | Recommendation | Decision | Rationale |
|---|----------------|----------|-----------|
| D1 | Split access truth vs attention; stop selling one infra peer to Runtime | **Accept** | Different writers, lifetimes, consumers. Attention is a product policy layer; access truth is an access read model. |
| D2 | AuthTruth is projected, not a stored authority / second write path | **Accept** | PSS + AccessState remain writers; dual auth stores guarantee divergence. |
| D3 | One AuthTruth per provider = primary access method only | **Accept** | Non-primary method health stays on AccessState / ops surfaces. |
| D4 | Auth interruption enum ⊆ auth only; remove agent/worker/focus | **Accept** | Those are attention or CTA concerns. |
| D5 | Compiler-only creation of open AttentionItems; Store = overlays | **Accept** | Authorize rows are compiler inputs, not Store upserts of items. |
| D6 | One ranking table; lex tie-break only; delete conflicting asides | **Accept** | Multiple ranking stories made the RFC non-implementable. |
| D7 | Replace recovery matrix with Runtime `needs_human` + reason | **Accept** | Attention must not re-implement the recovery planner. |
| D8 | Tighten events; declare analytics owner; no overloaded opened/dismissed/silence | **Accept** | Product interrupt analytics = attention events; Runtime ops = AccessTimeline. |
| D9 | Connectors must not depend on AuthTruth / AAP ports | **Accept** | Connectors use Runtime session APIs only. |
| D10 | AccountState must not own Home hero (`next_recommended_action` demoted) | **Accept** | Target architecture: Attention owns cross-account primary; AccountState may expose per-account repair copy for Accounts/detail only. |
| D11 | Keep `financial > loyalty` as platform sort | **Reject** | Provider category in core forces special cases. Lexicographic `provider` then `attention_id` only. |
| D12 | Split into two separate RFC documents | **Reject** | One doc, two layers, clearer than doc sprawl. Ownership table below is the seam. |
| D13 | Add `requires_human` flag on items | **Reject** | Redundant with urgency/`AttentionClass`. Drop the slogan-field. |
| D14 | Emit both AccessTimeline and attention events as co-equal “interrupt happened” | **Reject** | One human moment → one analytics owner (see §6). |
| D15 | AttentionView surface for Control Center | **Reject** | Control Center reads AccessState only. |
| D16 | List `passive_extension` as a separate auth adapter | **Reject** | Passive goes through Access Manager; not a second writer. |

---

## Part I — Layers

```text
Product surfaces
  Home · Accounts · Activity · Worker · Push · Email
        │                    │
        │ AttentionView      │ AccountState (per-account depth)
        ▼                    ▼
┌───────────────────────────┐   ┌─────────────────────────────┐
│  ATTENTION (product)      │   │  ACCOUNT MIRROR (product)   │
│  compiler · overlays ·    │   │  AccountState projection    │
│  delivery · ranking       │   │  (no cross-account hero)    │
└─────────────▲─────────────┘   └──────────────▲──────────────┘
              │ inputs                         │
              │ AuthTruth · AuthorizeRow ·      │
              │ BenefitSignal · WorkerSignal    │
              │ AccountState (data/repair)      │
┌─────────────┴─────────────────────────────────┴─────────────┐
│  ACCESS TRUTH (read model)                                   │
│  AuthTruth = project(primary method evidence)                │
└─────────────▲──────────────────────────▲────────────────────┘
              │                          │
┌─────────────┴──────────┐    ┌──────────┴────────────────────┐
│ Access Manager + PSS   │    │ Provider Runtime               │
│ (browser_session)      │    │ AccessState + needs_human      │
└────────────────────────┘    └────────────────────────────────┘
```

**Not a peer of Provider Runtime.** Runtime remains the managed-browser subsystem. Attention sits above access adapters the same way Home sits above account data — product policy, not another browser.

---

## Part II — Ownership table (canonical)

Exactly one owner per responsibility. If a row lists a consumer, that consumer may read but must not redefine.

| Responsibility | Owner | Must not own |
|----------------|-------|--------------|
| Browser / CDP / keepalive / recovery planner | **Provider Runtime** | Attention, AuthTruth store |
| Product signal “human needed on managed runtime” | **Provider Runtime** (`needs_human`, `needs_human_reason`) | Attention re-deriving from planner enums |
| Extension verification enqueue + PSS definitive writes | **Access Manager** | Home GET, AttentionCompiler, extractors |
| Choose primary `access_method` for an account | **AccountState** (enrollment / account config) | Attention inventing failover |
| Product auth read model for primary method | **AuthTruth projector** | Persisting a competing auth ledger |
| Non-primary method health | **AccessState** (ops) / Access Manager diagnostics | Customer Home hero |
| Open attention candidates | **AttentionCompiler** (pure) | Authorize API creating items; surfaces ranking |
| Dismiss / snooze / in_flight overlays | **AttentionStore** | Auth writes |
| Delivery attempts + receipts | **AttentionDelivery** | Re-ranking |
| Cross-account primary CTA / silence | **Attention** (`AttentionState`) | AccountState, Home ad-hoc filters |
| Per-account status line / detail repair copy | **AccountState** | Push fan-out, Home hero |
| Agent pending approval rows | **Authorize store** (existing) | AuthTruth.interruption |
| Benefit / value-at-risk signals | **Benefit classifier** (existing inputs) | AuthTruth |
| Worker install / reachability signal | **Worker/extension health** input | AuthTruth.interruption |
| Customer English for attention | **AttentionCompiler** via copy keys → resolved strings on `AttentionItem` | Surfaces inventing status copy |
| Ops interrupt timeline (Runtime) | **AccessTimeline** | Product “unexpected human minutes” |
| Product interrupt / attention analytics | **Attention events + AAP metrics** | AccessTimeline as product source of truth |
| Connector session use | **Provider Runtime APIs** | AuthTruthPort, AttentionPort |
| Control Center / access card UI | **AccessState** | AttentionView |

---

## Part III — Access truth (read model)

### 3.1 `AuthTruth`

Projected, not authoritative storage. Rebuilt from the primary access method’s latest accepted evidence.

```python
@dataclass(frozen=True)
class AuthTruth:
    schema_version: int                 # 2
    user_id: str
    provider: str

    state: AuthenticationState          # signed_in | signed_out | login_unknown
    access_method: AccessMethod         # primary only: browser_session | managed_runtime | api | manual

    evidence_class: EvidenceClass       # definitive | weak | none
    evidence_source: str                # access_manager | runtime_publication
    evidence_id: str | None

    observed_at: str                    # when evidence was taken
    projected_at: str                   # when projector ran

    interruption: AuthInterruption      # auth-only; see below
    interruption_expected: bool         # bootstrap/enroll vs unexpected mid-session
    needs_human: bool                   # true when human must act for THIS primary method
    needs_human_reason: str | None      # sanitized enum code from adapter

    # Freshness labels only — not a second duration-clock system
    evidence_age_seconds: float | None
    stale: bool                         # evidence older than provider policy TTL; does NOT change state
```

**Removed from v1 AuthTruth:** `account_key` (defer until multi-account is real), Runtime continuity clocks (`authenticated_session_started_at`, etc. — remain on AccessState only), free-form ownership of recovery.

### 3.2 `AuthInterruption` (auth-only)

```text
none
login
mfa
captcha
consent
unknown_human
```

Not auth interruptions (handled elsewhere as compiler inputs): agent approval, worker missing, runtime window focus, benefits.

### 3.3 Projection rules

| Primary `access_method` | Source | `needs_human` |
|-------------------------|--------|---------------|
| `browser_session` | Access Manager / PSS → AuthenticationState mapping | `state=signed_out` → true, reason `login`; else false unless definitive human challenge recorded as interruption ≠ none |
| `managed_runtime` | AccessState publication | Runtime `needs_human` (see §3.4) |
| `api` / `manual` | Future / N/A | Policy-specific; default false |

Hard rules (unchanged intent from auth module):

1. Extraction never revises auth state.
2. Transport error / timeout → `login_unknown`, never `signed_out`.
3. Only definitive evidence yields `signed_in` / `signed_out`.
4. `stale=true` never alone flips state to `signed_out`.
5. Storage/transport casings normalize at the projector boundary (lowercase enums).

### 3.4 Runtime product signal (replaces recovery matrix)

Provider Runtime continues to own planner enums internally. For product projection it **must** publish:

```text
needs_human: bool
needs_human_reason: login | mfa | captcha | consent | unknown_human | null
interruption_expected: bool
authentication_state: SIGNED_IN | SIGNED_OUT | LOGIN_UNKNOWN
```

Attention mapping is exactly one rule:

```text
if primary_method == managed_runtime and needs_human:
    AuthTruth.needs_human = true
    AuthTruth.interruption = needs_human_reason or unknown_human
else:
    # browser_session path uses PSS/Auth mapping only
```

No Attention table over `recovering` / `awaiting_user` / `failed`. Ops UIs keep reading AccessState.

### 3.5 Canonical auth write path

```text
Evidence observed
  → Access Manager (browser_session)  OR  Runtime publish (managed_runtime)
  → PSS / AccessState (existing stores)
  → AuthTruth projector (read model)
  → AttentionCompiler input
```

There is **no** `record_auth_evidence` product API that writes a parallel ledger. Optional debug projections may re-run the projector; they do not accept client-supplied terminals.

### 3.6 Canonical auth read path

```text
GET product / AttentionCompiler / AccountState projector
  → AuthTruth projector (user, provider)
  → single AuthTruth for primary access_method
```

Ops:

```text
Control Center / access card → AccessState (full)
Admin conflict tools may show non-primary method diagnostics alongside AuthTruth
```

---

## Part IV — Attention

### 4.1 Objects

```python
@dataclass(frozen=True)
class AttentionItem:
    schema_version: int                 # 2
    attention_id: str                   # stable: hash(user, class, provider, fingerprint_key)
    user_id: str
    class_: AttentionClass
    urgency: AttentionUrgency           # blocker | time_sensitive | opportunity | informational
    provider: str | None
    fingerprint: str                    # identity of root cause for dedupe
    title: str
    body: str
    cta_label: str | None
    cta_action: AttentionAction
    created_at: str
    updated_at: str
    becomes_stale_at: str | None
    source_kind: SourceKind             # auth | authorize | benefit | worker | account_data | trust
    source_ref: str                     # id in owning system

@dataclass
class AttentionOverlay:
    """Store-only — never created by authorize/auth adapters as an 'item'."""
    attention_id: str
    status: OverlayStatus               # clear | snoozed | in_flight | durable_dismissed
    until: str | None                   # snooze end
    started_at: str | None              # in_flight start (Supervisor timeout uses this)
    updated_at: str

@dataclass(frozen=True)
class AttentionState:
    schema_version: int
    primary: AttentionItem | None
    remaining: tuple[AttentionItem, ...]  # ranked after primary; empty if none
    silence: SilenceVerdict | None        # None = not silent (effective ranks 1–5 visible)
    # Full product snapshots may also carry user_id / generated_at / counts;
    # the pure ranker contract is primary + remaining + silence only.

@dataclass(frozen=True)
class AttentionView:
    surface: Literal["home", "accounts", "activity", "worker", "push", "email"]
    primary: AttentionItem | None       # same attention_id as AttentionState.primary when visible
    secondary: tuple[AttentionItem, ...]
    health_counts: AttentionCounts
    render_hints: AttentionRenderHints
```

**Lifecycle of an item** is derived, not a second FSM store:

| Derived status | Rule |
|----------------|------|
| `absent` | Compiler did not emit fingerprint |
| `open` | Emitted and overlay `clear` |
| `snoozed` | Emitted and overlay `snoozed` and now < until |
| `in_flight` | Emitted and overlay `in_flight` and within timeout |
| `durable_dismissed` | Emitted and overlay durable (opportunities only) |
| `visible_primary` | First open (non-snoozed, non-dismissed) after ranking |

Compiler emits candidates → overlays filter → ranker picks primary → views window the queue.

### 4.2 `AttentionClass`

| Class | SourceKind | Typical urgency |
|-------|------------|-----------------|
| `auth_blocker` | auth | blocker |
| `agent_authorization` | authorize | blocker |
| `system` | worker / platform | blocker |
| `trust` | trust | blocker |
| `value_at_risk` | benefit | time_sensitive |
| `access_degraded` | auth (`stale` or unknown without needs_human) | informational |
| `data_gap` | account_data | informational |
| `opportunity` | benefit | opportunity |

### 4.3 Compiler inputs (only)

```text
AuthTruth[]                 # primary method per provider
AuthorizeRow[]              # pending agent approvals
BenefitSignal[]             # expiring / savings (existing classifiers)
WorkerSignal                # installed / reachable
AccountState[]              # data_status, per-account repair — not hero
AttentionOverlay[]          # from AttentionStore
```

### 4.4 AttentionStore (overlays only)

| May persist | Must not persist |
|-------------|------------------|
| snooze / durable dismiss | AuthTruth |
| in_flight + started_at | Authorize row bodies |
| delivery receipts | Ranked queue snapshots as authority |
| | “Open items” created by side APIs |

**`in_flight` timeout owner:** AttentionSupervisor. Default 30 minutes without AuthTruth/Authorize terminal clearing the fingerprint → overlay cleared back to `clear` (item shows open again). No browser I/O.

### 4.5 Canonical attention write path (commands)

```text
User/surface
  → POST /api/attention/{id}/snooze|dismiss|cta
  → AttentionStore overlay update
  → optional side command:
        cta start_provider_login → Access Manager request_provider_verification(user_check_now)
        cta open_activity_approval → navigate only
        cta focus_managed_runtime → Runtime bridge (only if Runtime API auth exists)
  → recompile (read path)
```

Commands never invent AuthTruth.

### 4.6 Canonical attention read path

```text
GET /api/attention
GET /api/attention/view?surface=home|worker|…
  → load compiler inputs
  → AttentionCompiler (pure) → candidates
  → apply overlays
  → apply ranking policy (§7) → AttentionState
  → surface window → AttentionView
```

Home **must not** re-rank. Activity **filters** the already-ranked queue to `source_kind=authorize` (or class `agent_authorization`) without changing global rank order — the Home primary `attention_id` remains the product primary even if Activity’s visible top is different. Delivery always keys off **AttentionState.primary** for push (v1 push rule: one item).

---

## Part V — State machines (minimal)

### 5.1 AuthTruth terminals

```text
login_unknown ──definitive signed_in──► signed_in
login_unknown ──definitive signed_out─► signed_out
signed_in     ──definitive signed_out─► signed_out
signed_out    ──definitive signed_in──► signed_in

weak/error/timeout → login_unknown (never signed_out)
stale flag toggles independently of state
```

### 5.2 Attention candidate + overlay

```text
compiler emits fingerprint ──► candidate
overlay clear + ranked      ──► visible
overlay snoozed             ──► hidden until `until`, then clear
overlay durable_dismissed   ──► hidden while fingerprint unchanged (opportunity only)
overlay in_flight           ──► visible with in-progress copy; timeout → clear
fingerprint gone            ──► candidate absent (overlays GC’d by supervisor)
```

Blockers: snooze allowed (max 1h); durable dismiss rejected.  
Opportunities: durable dismiss allowed (e.g. 7d).

---

## Part VI — Event model

### 6.1 Analytics ownership

| Question | Event stream |
|----------|--------------|
| Did Runtime escalate / recover? | **AccessTimeline** only |
| Did product ask a human for attention? Was it delivered / snoozed / cleared? | **Attention events** only |
| Did primary-method auth terminal change? | **`auth.*` projection events** (emitted by projector, not a second ledger) |

Do not treat AccessTimeline `awaiting_user` and `attention.opened` as interchangeable counters.

### 6.2 Auth projection events

| Event | When | Notes |
|-------|------|-------|
| `auth.projected` | Projector output changed (any field material to consumers) | Replaces v1 `recorded` + `changed` dual meaning |
| `auth.terminal_changed` | `state` changed | Subset signal for simple consumers |
| `auth.needs_human_changed` | `needs_human` or interruption changed | |
| `auth.stale_changed` | `stale` toggled | **Not** logout |

No `auth.rejected` product event — illegal writes never reach a second store; adapters log locally.

### 6.3 Attention events

| Event | When | Notes |
|-------|------|-------|
| `attention.opened` | Fingerprint newly present in candidate set | Not used for snooze expiry |
| `attention.updated` | Same fingerprint, material copy/urgency/CTA change | |
| `attention.reopened` | Was snoozed/in_flight-timeout; visible again | **Not** `opened` |
| `attention.primary_changed` | Primary `attention_id` changed | |
| `attention.snoozed` | Overlay snooze set | |
| `attention.dismissed` | Durable dismiss (opportunities only) | |
| `attention.cta_started` | in_flight set | |
| `attention.cleared` | Fingerprint left candidate set | `reason`: root_cause_gone \| authorize_approved \| authorize_denied \| superseded |
| `attention.delivered` | Channel send ok | |
| `attention.delivery_failed` | Channel send failed | |
| `attention.sla_breached` | Primary blocker not delivered in SLA | |
| `attention.silence_changed` | `silence` enum changed | Includes all_clear, suppressed, awaiting_data |

### 6.4 Correlation

`user_id`, `provider?`, `attention_id?`, `evidence_id?`, `trace_id`. No secrets, HTML, cookies.

---

## Part VII — Ranking policy (single table)

**Higher band wins. Within a band, lower rank number wins.**

| Rank | Class | Urgency (must match) |
|------|-------|----------------------|
| 1 | `trust` | blocker |
| 2 | `agent_authorization` | blocker |
| 3 | `auth_blocker` | blocker |
| 4 | `system` | blocker |
| 5 | `value_at_risk` | time_sensitive |
| 6 | `opportunity` | opportunity |
| 7 | `access_degraded` | informational |
| 8 | `data_gap` | informational |

**Effectiveness (before ranking / silence)**

An item is **ineffective** when `becomes_stale_at is not None` and `now >= becomes_stale_at`. Ineffective items are excluded from ranking and silence evaluation. The clock (`now`) is supplied by the caller — no internal wall-clock reads.

**Total order among effective items**

1. Lower rank number wins.
2. Within rank 5 only: earlier `becomes_stale_at` wins; `becomes_stale_at=None` sorts last.
3. Lexical tie-break: `provider` ASC (treat `provider=None` as `""`), then `attention_id` ASC.

Input order must never affect output. Select exactly one **primary** when any effective item exists; **remaining** is the rest of the effective set in the same total order.

**Silence**

`SilenceVerdict` is only: `all_clear` | `suppressed` | `awaiting_data`.  
`AttentionState.silence` is **optional**: `None` means at least one effective rank 1–5 item is visible — the product is **not silent**. Do not invent an `active` verdict.

| Verdict | Condition |
|---------|-----------|
| `None` (not silent) | At least one effective item in ranks 1–5 |
| `all_clear` | No effective items in ranks 1–5 (queue may still hold ranks 6–8) |
| `awaiting_data` | No effective ranks 1–5; at least one effective rank 7–8 (`awaiting_data` wins over `all_clear`) |
| `suppressed` | Rank 1–4 exists but all such items snoozed (Home shows suppressed honesty, not fake All clear). Requires overlays — not produced by the pure ranker alone. |

`all_clear` means no effective ranks 1–5, **not** that the entire queue is empty. An effective rank 6–8 item may still be selected as **primary** while `silence=all_clear`. Opportunities (rank 6) may show below fold when `all_clear`; they never create `all_clear` by themselves and never fill the hero when ranks 1–4 are snoozed.

**Delivery SLA (unchanged intent)**

| Urgency | First delivery attempt | Channels |
|---------|------------------------|----------|
| blocker | 60s | push (if on) + worker badge; Home on next read |
| time_sensitive | 15m | Home + optional push |
| opportunity | next Home read | no push default |
| informational | Home/Accounts | never push |

**Push always targets `AttentionState.primary` only** (single item).

This ranking **is** the question hierarchy: trust → do something (agent/auth/system) → value → opportunity → waiting/degraded.

---

## Part VIII — UI contracts

1. Home / Worker / Push read **AttentionView** / delivery payload only for hero and silence.
2. Accounts / Account detail read **AccountState** for per-account rows; may use Attention counts for filter chips, not a second hero.
3. Activity reads authorize audit detail + AttentionView filtered to agent items; badge count = open authorize-sourced candidates after overlays.
4. Control Center + Runtime access card read **AccessState** only.
5. Exactly one Home primary CTA = `AttentionState.primary`.
6. Copy lives on `AttentionItem` (compiler-resolved). Surfaces do not invent status English.
7. Empty onboarding remains enrollment UX outside Attention (not an AttentionItem).

### CTA vocabulary

```text
start_provider_login
open_provider_surface
focus_managed_runtime      # gated on Runtime API auth
install_worker
open_activity_approval
open_account_detail
connect_gmail
snooze
dismiss
noop
```

---

## Part IX — Extension protocol

1. Evidence → Access Manager only (unchanged ACCESS_FLOW write boundary).
2. `GET` attention never enqueues verification.
3. Popup = `AttentionView(worker)`.
4. Alarms: `ensure-due` (access) and attention GET are separate calls.
5. Content scripts emit signals through Access Manager; they do not rank attention.
6. Non-primary Runtime degradation does not appear in Worker/Home when primary is `browser_session` and AuthTruth.needs_human is false.

---

## Part X — Metrics (product)

Owned by Attention + AuthTruth projector metrics — not AccessTimeline.

| Metric | Definition |
|--------|------------|
| Unexpected human minutes / account / week | Time `auth_blocker` visible with `interruption_expected=false`, plus visible `agent_authorization` |
| False silence rate | Rank 1–4 visible candidate with no delivery receipt within SLA |
| Attention spam rate | Opportunity deliveries + reopened push storms |
| Auth terminal transition count | From `auth.terminal_changed` |
| Snooze return rate | `attention.reopened` after `attention.snoozed` for auth_blocker |

---

## Part XI — Relationship to AccountState

| Field / concern | AccountState | Attention |
|-----------------|--------------|-----------|
| `connection_state` / session health labels | Yes | Reads via AuthTruth / AccountState inputs |
| `data_status` | Yes | → `data_gap` candidates |
| Per-account repair copy on Accounts row | Yes | No |
| Cross-account Home hero | **No** (v2 target) | **Yes** |
| `next_recommended_action` as global hero | **Removed from target architecture** | Replaced by `AttentionState.primary` |

ACCOUNT_STATE.md should be amended in a follow-on edit to demote `next_recommended_action` to per-account only. Until that doc edit lands, **this RFC wins** on hero ownership.

---

## Part XII — Risks (residual)

| Risk | Mitigation in v2 |
|------|------------------|
| Primary method wrong in AccountState | Enrollment owns it; admin tool to view AuthTruth vs AccessState |
| Projector lag after PSS write | Compiler/read path projects inline (or sync trigger); no eventual-only hero |
| in_flight stuck | Supervisor timeout 30m |
| Provider special cases via copy keys | Allowed; provider id not in ranking table |
| Runtime `needs_human` underspecified by adapter | Contract test on publication schema |

---

## Part XIII — Roadmap

| Phase | Outcome |
|-------|---------|
| **P0** | Accept this v2; freeze ranking table + AuthInterruption + event names |
| **P1** | AuthTruth projector over Access Manager + Runtime `needs_human`; shadow vs PSS |
| **P2** | AttentionCompiler + overlays + fixtures (scenarios below); shadow vs Home |
| **P3** | Read cutover Home/Worker; push uses AttentionState.primary |
| **P4** | Remove Home-local ranking; amend AccountState hero field |
| **P5** | Runtime focus CTA only after Runtime API auth |

**Milestone 2 (Attention Core) status:** P2 complete for the pure core + thin engine + Home/Worker **shadow recording** (no customer cutover, no push). See [ATTENTION_ENGINE.md](ATTENTION_ENGINE.md).

**Milestone 3 (Platform Adoption):** Complete — AttentionView, agreement metrics, Home/Worker cutover (default **on**), legacy Home attention ranking removed. Rollback via `ATTENTION_CUTOVER_*=shadow|off`. See [ATTENTION_PLATFORM_ADOPTION.md](ATTENTION_PLATFORM_ADOPTION.md) · [ATTENTION_CUTOVER.md](ATTENTION_CUTOVER.md).

**Milestone 4 (Intelligent Attention):** Complete — Benefit / Worker / data_gap producers, AttentionSupervisor, primary delivery + receipts, HTTP attention commands. Extends the platform; does not redesign it. See [ATTENTION_INTELLIGENT.md](ATTENTION_INTELLIGENT.md).

Out of scope: credential storage as default path, multi-item push, household attention, Connector→AuthTruth.

---

## Part XIV — Normative scenario pins (v2)

Scenarios validate the single ownership model; they are not a second policy source.

1. **Extension session expired** — Access Manager writes PSS signed_out → AuthTruth.needs_human → compiler `auth_blocker` → primary → push. CTA → overlay in_flight → Access Manager user_check_now. Definitive signed_in → candidate cleared → `attention.cleared`.
2. **Runtime MFA mid-session** — Runtime sets needs_human=mfa, expected=false → AuthTruth (if primary=managed_runtime) → auth_blocker. While needs_human false and recovering, no auth_blocker. AccessTimeline records ops; product metrics use attention events.
3. **CAPTCHA during login** — Same fingerprint/auth_blocker; `auth.needs_human_changed` / `attention.updated`; no second item.
4. **Snooze blocker** — `attention.snoozed`; silence=suppressed if no other blockers; Accounts still shows needs login via AccountState; on expiry `attention.reopened` (not opened).
5. **Agent authorize** — AuthorizeRow input → compiler `agent_authorization` → may be primary per ranking (rank 2). Store does not upsert an item. Clear when authorize terminal → `attention.cleared{approved|denied}`.
6. **Multi-provider signed_out** — Multiple auth_blockers; primary = lex(`provider`,`attention_id`); one push; no financial>loyalty rule.
7. **Phone-only** — stale≠signed_out; mobile cannot complete browser_session; completable CTA only on desktop capability.
8. **Bootstrap MFA expected** — Runtime interruption_expected=true → metrics bucket expected; same attention class if customer-bound.
9. **Dual path** — Primary browser_session signed_in; Runtime needs_human → **no** customer auth_blocker; AccessState shows ops pain.
10. **Dismiss opportunity + snooze login** — durable dismiss opportunity; snooze login → suppressed, opportunity does not become primary.

---

## Part XV — One-page reference

### Ownership (abbreviated)

```text
Runtime          → AccessState, needs_human, AccessTimeline
Access Manager   → PSS evidence writes, verify enqueue
AccountState     → primary access_method, per-account mirror
AuthTruth        → project(primary evidence)          [read model]
AttentionCompiler→ open candidates                    [pure]
AttentionStore   → snooze / dismiss / in_flight / receipts
AttentionDelivery→ channel fan-out of primary
Authorize store  → pending approvals                  [compiler input]
Home/Worker/Push → AttentionView / primary delivery
Control Center   → AccessState
Connectors       → Runtime session APIs only
```

### Write paths

```text
Auth evidence:     adapter → Access Manager | Runtime publish → (stores) → projector
Attention command: surface → AttentionStore overlay → optional Access Manager / Runtime command
Authorize create:  agent → Authorize store            → compiler input on next read
```

### Read paths

```text
Auth:       consumer → AuthTruth projector
Attention:  consumer → compile → overlays → rank §7 → AttentionState → View
Ops access: consumer → AccessState
Account UI: consumer → AccountState
```

### Events

```text
AccessTimeline     = Runtime ops
auth.*             = projection diffs (terminal / needs_human / stale)
attention.*        = product attention lifecycle (opened/updated/reopened/snoozed/dismissed/cleared/…)
```

### Ranking

```text
trust > agent_authorization > auth_blocker > system
  > value_at_risk > opportunity > access_degraded > data_gap
exclude: becomes_stale_at set and now >= becomes_stale_at
rank 5: earlier becomes_stale_at; None last
tie: (provider or "") ASC, attention_id ASC
silence: None if ranks 1–5; else awaiting_data if 7–8; else all_clear
         (suppressed only with overlays)
```

---

**One sentence:** Access Manager and Runtime write session evidence; AuthTruth projects the primary method; Attention compiles one ranked queue and is the only owner of the cross-account hero and product interrupt analytics.
