# Activity V1 — Implementation Plan

**Branch:** `feat/activity-v1`  
**Baseline inventory:** [ACTIVITY_V1_DISCOVERY.md](ACTIVITY_V1_DISCOVERY.md)  
**Status:** Product decisions approved. Ready for implementation.

---

## Approved product decisions

1. **Sources:** Activity V1 includes **only** `actions` and `action_execution_receipts`.  
   - **Deferred:** Changed, Discovered.  
   - **Excluded as timeline rows:** recovery, session, discovery, snapshot, opportunity events.
2. **Navigation:** Show Activity in nav only when the user has at least one pending Activity item **or** at least one historical Activity item.
3. **Terminal outcomes:** Include denied, expired, and cancelled in the default timeline under **Could not complete**. Preserve precise underlying outcome in details. Do not imply system failure when the user denied or cancelled.
4. **Receipt provenance (visible):** what Mighty attempted, what happened, why, relevant timestamps, provider/account context, authorization/policy rationale where applicable.  
   **Hidden:** raw hashes, internal IDs, store names, capability names, implementation metadata.
5. **Export/deletion parity for receipts is a V1 blocker.** Activity must not ship until all customer-visible Activity data is covered by applicable export and deletion paths.

---

## Goal

Ship the smallest useful customer Activity surface as a **pure server-side projection** over existing durable `actions` and `action_execution_receipts`.

**Customer question:**

> What has Mighty done, what happened, and why?

**Customer outcome:**

A signed-in user can open `/activity` and see a chronological timeline of completed or attempted user-relevant agent work—with approval controls when needed and readable provenance—without a new event bus, ranking engine, or canonical Activity store.

---

## Scope

### In scope (V1)

| Include | Why |
|---------|-----|
| Pure server-side projection over `actions` + `action_execution_receipts` | Approved foundation |
| Receipts merged into parent action items (not duplicate rows) | One primary timeline item per action |
| Customer page `GET /activity` (+ optional JSON projection API) | Missing route; CTAs already point here |
| Taxonomy: Needs approval · In progress · Completed · Could not complete | Approved customer language |
| Approve/deny via existing Activity-channel decide path | Pending authorization consumption |
| Conditional Activity nav (pending or historical projected items) | Approved visibility rule |
| Export + delete parity for receipts | V1 blocker |
| Chronological timeline grouped by date; incremental loading | Usable history |

### Explicitly out of scope (V1)

| Exclude | Why |
|---------|-----|
| New durable Activity event table / generalized event bus | Projection only |
| New ranking engine or Attention replacement | Attention owns interruption |
| Attention `surface=activity` as history source | Authorize-only interrupt filter |
| Changed / Discovered timeline rows | Deferred |
| Recovery, session, discovery, snapshot, opportunity as independent timeline rows | Approved exclusion |
| Mobile Activity tab | Web-first |
| Client-side joins | Server composes |

---

## Architecture invariants

- Activity V1 is a **pure server-side projection** over existing `actions` and `action_execution_receipts`.
- Receipts **merge into** their parent action item; they do **not** appear as duplicate primary rows.
- **One action → at most one primary timeline item.**
- Multiple attempts/receipts may appear **inside that item’s detail history**.
- Chronology uses the most meaningful customer-facing timestamp:
  - terminal states → completed / failed / cancelled / denied / expired timestamp (`decided_at` or latest receipt time when more meaningful),
  - in progress → latest meaningful update (`decided_at` or latest receipt/`created_at`),
  - needs approval → creation/request timestamp (`created_at`).
- Navigation visibility is derived from existence of **pending or historical projected items**.
- **No** new event bus, generalized activity store, ranking engine, or canonical domain model.
- Activity owns **only** the customer-facing projection.
- Canonical ownership remains with **Actions**, **Authorization**, and **Receipts**.
- Changed and Discovered are **explicitly deferred**.

---

## User-facing taxonomy

| Category | Display label | Underlying action states (not shown as labels) |
|----------|---------------|------------------------------------------------|
| `needs_approval` | Needs approval | `awaiting_authorization`, legacy `pending` |
| `in_progress` | In progress | `authorized`, `executing` |
| `completed` | Completed | `completed`; legacy success mappings where applicable |
| `could_not_complete` | Could not complete | `failed`, `denied`, `cancelled`, `expired`, legacy `timeout` |

**Could not complete wording rules:**

- User denied → explanation makes clear the user declined (not a system failure).
- User/system cancelled → cancellation language, not “failed.”
- Expired / timed out → waiting window ended; not a runtime crash.
- Failed execution → work could not finish; show readable outcome/rationale when present.

Copy never exposes store names, lifecycle enum strings, hashes, capability names, or internal IDs in primary UI.

---

## Inclusion rules

1. Include `actions` for the signed-in `user_id` whose lifecycle/status maps to one of the four categories above.
2. Exclude empty `proposed` rows with no user-visible authorization path and no receipt.
3. Attach all receipts for that `action_id` into the item detail; surface the latest for summary fields.
4. Strict user isolation: never mix users’ actions or receipts.
5. Deterministic `activity_id`: `action:<action_id>`.

### Ordering

- Sort by customer-facing `occurred_at` descending, then `activity_id` descending.
- UI groups by local calendar date; sort remains a single chronological list.

---

## Timeline item contract

| Field | Required | Description |
|-------|----------|-------------|
| `activity_id` | yes | `action:<action_id>` |
| `occurred_at` | yes | Customer-facing chronology timestamp |
| `category` | yes | One of the four taxonomy keys |
| `status_label` | yes | Human label (Needs approval, …) |
| `title` | yes | From action label |
| `explanation` | yes | Concise what/why (category-aware) |
| `provider` | no | Provider key when present |
| `provider_display_name` | no | User-facing account context |
| `action_id` | yes | Canonical action id (for commands; not shown as primary UI chrome) |
| `receipt_summaries` | no | Ordered readable attempt history for details |
| `user_action` | no | `approve_deny` when needs approval |
| `detail` | yes | Readable provenance block (attempted / happened / why / timestamps / policy rationale) — no forbidden internals |

---

## Data-source mapping

| Concern | Definition |
|---------|------------|
| **Canonical actions** | `mighty/agent_action_store.py` → `actions` |
| **Canonical receipts** | `mighty/execution_receipt.py` → `action_execution_receipts` |
| **Projection owner** | `mighty/activity_projection.py` (read-only composer) |
| **UI owner** | `mighty/activity_ui.py` (+ thin `app.py` route) |
| **Dedup** | One primary item per `action_id`; receipts nested |
| **Timestamp** | Per chronology rules above |
| **Detail** | Fields, decision explanation, readable receipt attempt history |
| **User action** | Existing `POST /dashboard/decide/<action_id>` (`auth_channel=activity`) |

Changed / Discovered / recovery / session / snapshots / opportunities: **not mapped into V1 rows**.

---

## Projection ownership

| Concern | Owner |
|---------|--------|
| Activity customer projection | `activity_projection` (compose only) |
| Activity HTML | `activity_ui` |
| Actions | `agent_action_store` |
| Receipts | `execution_receipt` |
| Authorization policy / decide | `authorization_policy` / `trusted_agent` |
| Interruption | Attention (unchanged) |

Activity must not become canonical writer for any of the above domains.

---

## Route and API

| Method | Path | Behavior |
|--------|------|----------|
| `GET` | `/activity` | Login-required HTML timeline |
| `GET` | `/api/activity` | Login-required JSON projection (`items`, `generated_at`, pagination cursor) |
| `POST` | `/dashboard/decide/<action_id>` | Reuse existing approve/deny |
| `GET` | `/dashboard/has-pending` | May remain for badge |

Nav link to `/activity` appears only when projection has pending or historical items.

---

## UX and information hierarchy

- **Headline:** Activity  
- **Subtitle:** What Mighty did, and what needs you.  
- **Timeline:** Newest first, grouped by date.  
- **Item:** title, explanation, status, timestamp, provider context when useful, Approve/Deny when needed, details disclosure.  
- **Details:** readable provenance + receipt attempt history; hide forbidden internals.  
- **Empty state:** calm, honest; no manufactured events. (Direct URL may still render empty if nav is hidden.)  
- **Loading / error:** calm; do not invent rows.  
- **Pagination:** cursor / limit with Load more.  
- **Visual language:** follow Home V1B calm hierarchy (status-first, restrained chrome, no dashboard clutter).

---

## Privacy, export, and deletion

**Blocker:** receipts must be covered before ship.

| Path | Required V1 behavior |
|------|----------------------|
| `GET /settings/export-csv` (or successor) | Export all customer-visible Activity fields for the user, including receipt-derived columns / rows joined to actions |
| `POST /settings/delete-activity` | Delete the user’s `actions` **and** `action_execution_receipts` (consistent with existing activity-deletion semantics) |

Do not expose receipt data in the UI that Settings cannot export or delete.

---

## Acceptance criteria

Objectively testable:

1. **No duplicate action/receipt rows:** each `action_id` yields ≤1 primary timeline item; receipts appear only in that item’s detail history.
2. **Terminal-state language:** denied/expired/cancelled/failed all map to **Could not complete**, with detail wording that does not call user denial a system failure.
3. **Chronology:** items sort by customer-facing `occurred_at` desc with stable tie-break.
4. **Authorization visibility:** needs-approval items appear from `actions` even when Attention is empty/snoozed.
5. **Hidden internal provenance:** UI/API customer fields omit raw hashes, store names, capability names, and do not present internal IDs as primary chrome.
6. **Export parity:** export includes customer-visible receipt data for the user’s Activity-visible actions.
7. **Deletion parity:** delete-activity removes that user’s `action_execution_receipts` along with actions (per Settings semantics).
8. **Conditional navigation:** Activity nav link present iff projection has pending or historical items; absent otherwise.
9. **Empty state:** zero included items → calm empty state; no fake rows.
10. **Pagination / incremental loading:** limit + cursor returns stable subsequent pages without duplicates.
11. **Route access:** `/activity` requires login; consistent with other customer pages.
12. **Account isolation:** user A never sees user B’s actions or receipts in `/activity` or `/api/activity`.

---

## Testing plan

- Projection composition and category mapping  
- Action/receipt deduplication  
- Chronology / occurred_at selection  
- Denied / expired / cancelled / failed wording  
- Authorization visibility without Attention  
- Receipt detail history (multi-attempt)  
- Hidden internal provenance assertions  
- Conditional navigation helper  
- Empty state rendering  
- Pagination cursor stability  
- User isolation  
- Export receipt coverage  
- Deletion receipt coverage  
- `/activity` route + UI rendering  

---

## Deferred (explicit)

- Changed (`account_changes`) as timeline rows  
- Discovered (`account_discovery`) as timeline rows  
- Recovery / session / snapshot / opportunity independent rows  
- Mobile Activity tab  

---

## Implementation sequence

1. `mighty/activity_projection.py` — compose actions + receipts.  
2. `mighty/activity_ui.py` — Home-V1B-aligned HTML.  
3. `GET /activity` + `GET /api/activity` in `app.py`.  
4. Conditional nav wiring.  
5. Export + delete receipt parity.  
6. Tests + screenshots under `docs/pr-screenshots/activity-v1/`.
