# Activity v1 — Discovery Inventory

**Branch:** `feat/activity-v1`  
**Scope:** Inventory only. No architecture, redesign, or implementation proposals.  
**Product definition (existing):** [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md) defines Activity as the receipt book for agent authorization and consequential actions. [TRUSTED_AGENT_AUTHORIZATION.md](TRUSTED_AGENT_AUTHORIZATION.md) assigns history presentation to Activity (`actions` + receipts).

---

## Existing models

### Agent actions (authorization history)

| Item | Location | Notes |
|------|----------|-------|
| Table `actions` | SQLite; schema owned by `mighty/agent_action_store.py` (also bootstrapped in `app.py`) | Durable Action lifecycle |
| Dataclass `AgentAction` | `mighty/agent_action_store.py` | Fields include lifecycle, status, consequence level, agent/provider, fingerprint, proposal hash, decision/explanation, timestamps |
| Lifecycle states | Documented in `docs/TRUSTED_AGENT_AUTHORIZATION.md` | `proposed` → `awaiting_authorization` → `authorized` / `denied` → `executing` → `completed` / `failed`; also `cancelled`, `expired` |
| Legacy status mapping | Same docs + store | `pending` / `approved` / `denied` / `timeout` / `logged` still understood for Activity/Attention compatibility |
| List API (module) | `list_actions()` in `agent_action_store.py` | Mature read path |

**Maturity:** Fully implemented. Explicitly intended as Activity’s primary history source.

### Execution receipts

| Item | Location | Notes |
|------|----------|-------|
| Table `action_execution_receipts` | `mighty/execution_receipt.py` | Append-only; unique on `(action_id, execution_attempt)` |
| Dataclass `ExecutionReceipt` | Same | Authorization decision/time/channel, execution result/attempt, proposal/receipt/prev hashes, detail, provider, timestamps |
| Integrity | `compute_receipt_hash`, `verify_receipt_integrity` | Hash chain support |
| List API (module) | `list_receipts(db, action_id=…, user_id=…)` | Module-level listing exists |

**Maturity:** Fully implemented store. No customer-facing list/detail HTTP surface found.

### Attention (current interrupt queue — not customer Activity history)

| Item | Location | Notes |
|------|----------|-------|
| `AttentionItem` | `mighty/attention.py` | Immutable candidate contract (class, urgency, provider, reason, source, CTA, timestamps) |
| `AttentionView` / `AttentionPresentation` | `mighty/attention_view.py` | Presentation projection over ranked Attention state |
| Overlay table `attention_overlay` | `mighty/attention_store.py` / `attention_overlay.py` | Current snooze / durable dismiss / in-flight — not command history |
| Delivery table `attention_delivery_receipt` | `mighty/attention_delivery.py` | Delivery attempt receipts (push/email path), distinct from action execution receipts |
| Authorize loader | `mighty/attention_loaders.py` → `load_authorize_rows` | Bridges awaiting `actions` into Attention authorize candidates |
| CTA target | `attention_view.py` | `open_activity_approval` resolves to `"/activity"` |

**`surface=activity` (important boundary):** When `GET /api/attention/view?surface=activity` (or `build_attention_view(..., surface="activity")`) runs, the view **filters the already-ranked Attention queue to `AttentionClass.AGENT_AUTHORIZATION` only**. It does **not** list completed actions, receipts, account changes, opportunities, recovery, discovery, or snapshots. It is an authorization-focused interrupt projection, **not** a complete customer Activity history.

**Maturity:** Mature interrupt/ranking platform. Canonical history for agent work remains `actions` + `action_execution_receipts`; Attention only projects pending authorize interrupts.

### Account changes

| Item | Location | Notes |
|------|----------|-------|
| Table `account_changes` | `mighty/change_store.py` | Durable change events from snapshot diffs |
| Dataclass `AccountChangeEvent` | Same | Provider, snapshot links, outcome, summary, fields, fingerprint, suppression, meaningful count, timestamps |
| Dedupe table `account_change_fingerprints` | Same | Supports suppression/dedupe |
| List API (module) | `list_account_changes()`, `change_alerts_from_store()` | Chronological and alert projections |
| Producers | `mighty/freshness_change.py`, `change_intelligence.py`, `freshness_policy.py` | Post-snapshot change creation and semantics |
| Legacy `field_history` | Created in `app.py` | Per-field change rows; fallback when change store empty |

**Maturity:** Canonical change store is mature. Legacy `field_history` still populated and used as fallback.

### Account snapshots

| Item | Location | Notes |
|------|----------|-------|
| Table `account_snapshots` | `mighty/account_snapshot.py` | Immutable append-only customer data snapshots |
| List/load helpers | `list_account_snapshots`, `load_latest_snapshots_by_provider`, `load_customer_snapshot_items` | Historical and current reads |
| Docs | `docs/ACCOUNT_SNAPSHOTS.md` | Declares snapshots as customer data source of truth |

**Maturity:** Fully implemented. Customer historical list HTTP API not found (admin only).

### Opportunities

| Item | Location | Notes |
|------|----------|-------|
| Table `account_opportunities` | `mighty/opportunity_store.py` | Durable opportunity records |
| Dataclass `OpportunityRecord` | Same | Kind, score/urgency/value, lifecycle state, snapshot correlation, timestamps, metadata |
| Lifecycle | Same + `value_intelligence.py` | States include discovered / active / consumed / expired / dismissed (mutates same row) |
| List API (module) | `list_opportunities()` | Filtering by state supported |
| Legacy generation | `app.py` `_generate_opportunities` / scoring in `mighty/scoring.py` | Separate from durable store; used by `/api/opportunities` |

**Maturity:** Durable store is mature as current/lifecycle state. Not an append-only transition log. Legacy API path still separate.

### Recovery history

| Item | Location | Notes |
|------|----------|-------|
| Table `recovery_case` | `mighty/recovery_store.py` | Case root cause, status, escalation, next attempt, timestamps |
| Table `recovery_attempt` | Same | Append-only attempts (capability, outcome, detail, timestamp) |
| `load_history(case_id)` | Same | Ordered case + attempts |
| `list_active_cases_for_user` | Same | Active cases only |
| Producers | `recovery_planner.py`, `recovery_supervisor.py`, `recovery_executor.py` | Decisioning and execution |

**Maturity:** Durable case/attempt history exists. No general customer all-case history query/surface found. Product docs separate Recovery (`ASK_HUMAN` login repair) from agent authorize.

### Discovery events / facts

| Item | Location | Notes |
|------|----------|-------|
| Table `account_discovery` | `mighty/discovery_store.py` | One row per `(user_id, provider)` |
| Dataclass `DiscoveryFact` | Same | Source/evidence, confidence, disposition, first/last seen, enrollment, display/category |
| List API (module) | `list_discovery_facts()` | Current facts |
| Producers | `discovery_pipeline.py`, `discovery_policy.py`, `discovery_enrollment.py` | Processing and enrollment |
| Legacy `email_suggestions` | `app.py` | Compatibility projection still present |

**Maturity:** Durable current discovery facts. Not append-only event history (historical context limited to first/last seen).

### Session / verification events

| Item | Location | Notes |
|------|----------|-------|
| Table `provider_session_verification` | `mighty/session_verification.py` | Durable verification lifecycle (request/start/complete, terminal reason, trigger, requester) |
| Table `provider_session_state` | `mighty/provider_session_state.py` | Current strongest/latest session evidence per provider — not historical |
| `SessionEvidenceTimelineEvent` | `mighty/login_truth.py` | Computed admin diagnostic timeline from session state, probes, cached observations, legacy signals |
| Amex verification timeline diagnostics | `mighty/verification_timeline_diagnostics.py` | Admin/operator, sanitized, temporary orientation |

**Maturity:** Verification lifecycle store is mature. Session evidence timeline is a computed projection, mainly admin-facing.

### Other implemented history-like stores (legacy / supporting)

| Item | Location | Notes |
|------|----------|-------|
| `field_observations` | `app.py` | First/last-seen aggregates per provider/field |
| `privacy_audit_log` | `app.py` | Capture/privacy events |
| `intent_history` | `app.py` | User-intent detections with timestamps |
| `notifications_sent` | `app.py` | Notification delivery history |
| `action_items` | `app.py` | Legacy reminder/benefit action lifecycle; Attention benefit loader still reads it |
| In-memory `Action` DTO | `mighty/action.py` | Unified presentation DTO (recommendations, alerts, discovery, approvals) — not a durable universal event entity |
| Daily brief models | `mighty/daily_brief.py`, `daily_brief_ui.py` | Narrative/list over current actions — briefing, not chronological Activity |
| Home wins | `HomeWin` in `mighty/home_projection.py` | Projects meaningful account changes as “Recent Wins” |
| Capability presentation timeline | `PresentationTimelineEvent` / `PresentationTimelineSection` in `mighty/capability_state.py`; rendered by `home_ui.py`; populated via `customer_capability_presentation.py` | Grouped “Truth Timeline” rows for access/capability detail |

---

## Existing APIs

### Directly Activity-related (customer)

| Endpoint | File | What it exposes |
|----------|------|-----------------|
| `GET /api/attention/view?surface=activity` | `app.py` | AttentionView filtered to pending `agent_authorization` items only — not a full Activity history API |
| `POST /dashboard/decide/<action_id>` | `app.py` | Approve/deny via Activity channel (`auth_channel="activity"`) |
| `GET /dashboard/has-pending` | `app.py` | Boolean pending-actions probe (badge) |
| `POST /api/record` | `app.py` | Record-only agent action; returns lifecycle + receipt |
| `POST /api/authorize` | `app.py` | Propose/authorize path |
| `GET /api/status/<action_id>` | `app.py` | Single action status |
| `POST /api/decide` | `app.py` | Decision API (chat/token path) |
| `POST /api/execute` | `app.py` | Execution trigger |
| `POST /api/log-decision` | `app.py` | Decision logging |
| `GET /approve/<token>` (+ POST) | `app.py` | Token-based approval UI |
| `GET /settings/export-csv` | `app.py` | CSV export of `actions` (filename `mighty-activity-*.csv`) |
| `POST /settings/delete-activity` | `app.py` | Deletes `actions`, `action_items`, `field_history`, `field_candidates`, `intent_history`, `field_observations` — not newer durable stores |

### Related customer read/write APIs (reusable facts, not unified Activity)

| Endpoint | File | What it exposes |
|----------|------|-----------------|
| `GET /api/field-history/<source>` | `app.py` | Prefers `account_changes`; falls back to `field_history`; returns changes + summaries |
| `GET /api/reminders` (+ summary/snooze) | `app.py` | Reminders merged with change alerts (`change_alerts_from_store` / legacy heuristics) |
| `GET /api/opportunities` | `app.py` | Legacy generated opportunities (not clearly backed by `account_opportunities`) |
| `POST /api/intent/log`, `GET /api/intent/recent`, `GET /api/intent/summary` | `app.py` | Intent history write/read |
| `GET /api/account-status` | `app.py` | Current account/capability status (includes timeline-oriented capability detail) |
| `GET /api/policy` (+ PATCH/PUT) | `app.py` | User policy (explains allow/deny; not Activity records) |
| Attention command APIs | `app.py` | `/api/attention/<id>/snooze|dismiss|cta` — overlay/interaction, not history listing |
| `GET /privacy/audit-log` | `app.py` | Privacy audit events |

### Admin / diagnostic projections

| Endpoint / page | File | What it exposes |
|-----------------|------|-----------------|
| `/admin/sync-history` | `app.py` | `field_history`, privacy audit, sync metadata |
| `/admin/sync-timeline` | `app.py` | Current sync timeline from `account_data` |
| `/admin/account-snapshots`, `GET /api/admin/account-snapshots` | `app.py` | Historical snapshot inspection |
| `/admin/session-evidence` | `app.py` | `gather_session_evidence_timeline()` |
| `GET /api/admin/debug/amex-verification-timeline` | `app.py` | Flag-gated Amex verification diagnostics |

### External agent integration

| Surface | File | Notes |
|---------|------|-------|
| MCP tools `request_authorization`, `check_authorization`, `record_action` | `mighty_mcp.py` | Producer/consumer of agent Action/receipt flow |

### Module-level projections without customer HTTP list APIs

| Projection | Module | Gap relative to HTTP |
|------------|--------|----------------------|
| `list_actions` | `agent_action_store.py` | Used by dashboard/settings paths; no dedicated Activity history JSON API |
| `list_receipts` | `execution_receipt.py` | No customer receipt-list route found |
| `list_opportunities` | `opportunity_store.py` | No direct canonical customer API found |
| `load_history` (recovery) | `recovery_store.py` | No customer recovery-history route found |
| `list_discovery_facts` | `discovery_store.py` | No dedicated customer discovery-history route found |
| `list_account_snapshots` | `account_snapshot.py` | Admin API only |

**Not found:** Customer route `GET /activity` (or equivalent dedicated Activity page). CTA URLs and docs reference `/activity`, but no matching customer page route was found.

---

## Existing UI components

### Action-history HTML builders (embedded in Flask; partially unwired)

| Piece | Location | Notes |
|-------|----------|-------|
| `build_feed_html` / `action_card_html` / `STATUS_BADGE` | `app.py` | Build pending + historical action cards from `actions` rows (approve/deny buttons for pending) |
| Dashboard loads `actions` and calls `build_feed_html` | `app.py` `/dashboard` | Feed HTML is computed; `{feed_html}` placeholder is **not** present in the current page template, so the feed is not rendered |
| Activity-log / action-card CSS | `app.py` inline CSS (`/* Activity log */`, `.action-card`, badges) | Styles for the action-card feed still exist |
| Leftover feed-tab JS | `app.py` `switchFeedTab('activity'\|'accounts')` | References `fview-*` / `ftab-*` DOM ids; `fview-activity` / `ftab-activity` markup is **not** present in the current template |
| Pending badge fetch | `app.py` → `/dashboard/has-pending` | Badge probe for pending actions |
| Recently-changed feed builder | `app.py` dashboard render path | Builds compact lines from `field_history`, then explicitly sets `recently_found_html = ""` (suppressed) |
| Settings export / delete activity controls | `app.py` settings UI | Export CSV of `actions`; delete scoped to listed legacy/`actions` tables |
| Token approve page | `/approve/<token>` | Existing approval interaction UI |

These are **possible Activity presentation helpers**, not a live customer `/activity` page. Canonical action/receipt facts remain in the stores above.

### Reusable presentation helpers (HTML string renderers)

| Component / DTO | Location | Notes (possible projection reuse) |
|-----------------|----------|-----------------------------------|
| `AttentionPresentation`, authorize CTA resolution | `mighty/attention_view.py` | Pending approval presentation; Activity surface filter is authorize-only (see Attention boundary above) |
| `HomeCard`, `attention_to_card` | `mighty/home_projection.py` | Compact interrupt/opportunity card DTO |
| Featured/secondary card renderers, recent-wins list | `mighty/home_ui.py` | Card and list HTML patterns |
| `HomeWin` / recent wins | `home_projection.py` + `home_ui.py` | DTO for meaningful account-change lines (Home projects these; Activity may reuse the pattern) |
| Timeline row / grouped timeline | `home_ui.py` (`_presentation_timeline_row`, `_render_timeline_sections`) | Chronological status/evidence rows |
| `PresentationTimelineEvent` / sections | `mighty/capability_state.py` (+ population in `customer_capability_presentation.py`, render in `home_ui.py`) | Truth-timeline pattern |
| Account center cards / status badges / relative timestamps | `mighty/account_center_ui.py` | Account-context chrome, not event rows |
| `PriorityActionItem` + featured/secondary action rows | `mighty/daily_brief_ui.py` | Urgency icons and value badges; curated, non-chronological |
| Daily brief projections | `mighty/daily_brief.py` | Shaping of current action sets |

### Mobile

| Piece | Location | Notes |
|-------|----------|-------|
| Account list / cards / empty states / refresh | `mobile/app/(tabs)/index.tsx` | No Activity tab, models, or API types found |

### Product / design references (docs only)

| Doc | Activity UI intent already written |
|-----|-------------------------------------|
| `docs/PRODUCT_ARCHITECTURE.md` | Primary nav; pending badge; approvals + completed/rejected/expired; no sync/marketing/chat |
| `docs/ATTENTION_VIEW.md` | `surface=activity` filter rules and limits |
| `docs/ATTENTION_COMPILER_AUTHORIZE.md` | Authorize CTA → Activity |
| `docs/AUTHENTICATION_ATTENTION_PLATFORM.md` | Delivery/surface contracts including Activity |
| `docs/milestones/MILESTONE_11.md` / `MILESTONE_12.md` | Auth ↔ Attention ↔ Activity integration notes; M12 mentions customer Policy explanation on Activity detail cards as follow-on |

---

## Ownership

Canonical owner = module/table that is the source of truth. Producers write; surfaces only read or command.

| Data | Canonical owner | Producers | Existing consumers / surfaces |
|------|-----------------|-----------|-------------------------------|
| Agent actions | `mighty/agent_action_store.py` → `actions` | `trusted_agent.py`, authorize/record/decide APIs, `mighty_mcp.py` | Attention authorize loader; `/dashboard/decide`; `/dashboard/has-pending`; CSV export; Attention activity surface |
| Execution receipts | `mighty/execution_receipt.py` → `action_execution_receipts` | `trusted_agent.py` (on execute) | Returned by `/api/record`; module `list_receipts`; no customer receipt UI found |
| Authorization policy decisions | `mighty/authorization_policy.py` (+ `user_policy.py`, `policy_store.py`) | Trusted-agent propose path | Explains allow/deny/require; facts live on `actions` / receipts |
| Attention candidates | `mighty/attention.py` contract; produced by Attention compilers/loaders | Compilers + loaders (e.g. authorize from `actions`) | `/api/attention/view` (incl. authorize-only `surface=activity`); Home |
| Attention overlays | `mighty/attention_store.py` → `attention_overlay` | Attention command APIs | Overlay application in Attention state |
| Attention delivery receipts | `mighty/attention_delivery.py` → `attention_delivery_receipt` | Delivery path | Delivery/SLA metrics — not Action history |
| Account snapshots | `mighty/account_snapshot.py` → `account_snapshots` | Successful extraction/persist path | Customer account projections; admin snapshot APIs |
| Account changes | `mighty/change_store.py` → `account_changes` | `freshness_change.py` after snapshot persist | `/api/field-history`; reminders/change alerts; Home Recent Wins |
| Change semantics | `change_intelligence.py`, `freshness_policy.py` | Invoked by freshness/change path | Labels/filtering for changes |
| Opportunities (durable) | `mighty/opportunity_store.py` → `account_opportunities` | `value_intelligence.py` after snapshot persist | Module list/reconcile; no direct customer Activity API |
| Opportunities (legacy projection) | `app.py` + `mighty/scoring.py` | Sync/dashboard opportunity generation | `/api/opportunities` |
| Recovery cases/attempts | `mighty/recovery_store.py` | Recovery planner/supervisor/executor | Attention gating; supervisor internals; no customer history UI |
| Discovery facts | `mighty/discovery_store.py` → `account_discovery` | Discovery pipeline/enrollment | Legacy email-suggestion compatibility |
| Session verification lifecycle | `mighty/session_verification.py` | Provider access manager / extension paths | Account status; admin diagnostics |
| Current session state | `mighty/provider_session_state.py` | Provider access manager | Login truth / current access surfaces |
| Session evidence timeline | `mighty/login_truth.py` (computed) | Reads session/probe/legacy sources | Admin session-evidence page |
| Legacy field history | `app.py` → `field_history` | Sync/capture paths | Fallback for field-history API and change alerts |
| Privacy audit | `app.py` → `privacy_audit_log` | Capture/data lifecycle | `/privacy/audit-log`; admin sync-history |
| Intent history | `app.py` → `intent_history` | `/api/intent/log` | Intent recent/summary; opportunity relevance |
| Legacy action items | `app.py` → `action_items` | Sync `populate_action_items` | Dashboard reminders; Attention benefit signals |
| History presentation (product role) | Activity (declared; page not implemented) | — | Documented as a **projection/reader** of `actions` + receipts — not a canonical store |
| Interruption (product role) | Attention | — | Decides whether/when to interrupt; `surface=activity` is authorize-only |

Product boundary already documented (not a redesign): Activity is declared as **history presentation** of agent actions/receipts; Attention owns **interruption**; Recovery owns **access-repair human**; sync/general Mighty update logs are explicitly excluded from Activity in `PRODUCT_ARCHITECTURE.md`. Account changes, opportunities, recovery, discovery, and snapshots remain separately owned source domains that *could* be projected later — they are not today a unified Activity stream.

---

## Gaps

Genuine missing capabilities only (inventory of absences):

1. **No customer `/activity` page/route** — CTAs and docs target `/activity`; Attention `surface=activity` exists as an authorize filter; dedicated customer Activity UI route was not found.
2. **No unified customer Activity history API** — Nothing combines `actions` + `action_execution_receipts` into a single customer read model/endpoint (module list helpers exist separately).
3. **No customer receipt list/detail HTTP API or UI** — `list_receipts` exists in-module only; receipts are returned on write paths such as `/api/record`.
4. **No customer API for durable `account_opportunities`** — Store + `list_opportunities` exist; `/api/opportunities` still uses the legacy generator.
5. **No customer recovery case/attempt history API or UI** — `load_history` is case-scoped and internal/supervisor-oriented; `list_active_cases_for_user` is active-only.
6. **No customer discovery-history API** — `account_discovery` is current-fact storage (updated in place), not an event log; no dedicated history endpoint.
7. **No customer account-snapshot history API** — Admin-only snapshot listing; customer helpers are projection/load oriented.
8. **Activity export/delete incomplete vs newer stores** — `/settings/export-csv` exports `actions` only; `/settings/delete-activity` does not clear receipts, snapshots, `account_changes`, opportunities, recovery, or discovery.
9. **Mobile has no Activity surface** — No Activity tab, types, or API client for approvals/history.
10. **Opportunity and discovery lack append-only transition history** — Lifecycle/current rows exist; prior state transitions are not retained as separate events (limitation of existing models, not a missing store module).
11. **No customer Activity detail surface for action provenance** — `decision_explanation` and receipt fields exist on durable records; there is no customer Activity detail UI to present them (Milestone 12 notes this as a follow-on).

Non-gaps (already present, even if unused by a dedicated Activity page): durable `actions` lifecycle, immutable receipts, Attention authorize-only `surface=activity` filter, decide/pending/export paths for `actions`, account_changes listing, snapshot store, recovery attempt append log, session verification lifecycle store, `build_feed_html`/`action_card_html` builders, and reusable HTML card/timeline render helpers.

---

## Key Conclusions

1. **Strongest foundation:** Durable `actions` and `action_execution_receipts` are the strongest existing foundation for Activity. Module list helpers (`list_actions`, `list_receipts`) already exist; product docs assign history presentation to Activity over these facts.
2. **Additional source domains, not one stream:** Account changes, opportunities, recovery, discovery, and snapshots are separately owned durable domains. They are not one unified event stream and must not be treated as a single store.
3. **No customer `/activity` route:** CTAs and docs reference `/activity`, but no customer page/route implementation was found.
4. **No unified customer history projection/API:** There is no customer-facing projection or API that spans `actions` and `action_execution_receipts` into one timeline.
5. **No customer receipt-list experience:** Receipts are durable and listable in-module, but there is no customer receipt list/detail HTTP API or UI.
6. **Export/delete coverage must be audited before broader exposure:** Existing `/settings/export-csv` and `/settings/delete-activity` center on `actions` (and some legacy tables). They do not currently cover newer durable stores such as receipts, snapshots, `account_changes`, opportunities, recovery, or discovery. Any Activity expansion that surfaces those domains must audit export/deletion coverage first.
7. **Attention `surface=activity` is not Activity history:** It is an authorization-focused filter over the Attention interrupt queue. It cannot substitute for a customer history of completed or attempted work.
