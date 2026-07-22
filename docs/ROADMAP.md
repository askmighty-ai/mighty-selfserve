# Mighty Engineering Roadmap

**Status:** Canonical  
**Audience:** Lead Engineer and contributors delivering milestones  
**Related:** [PRODUCT_MANIFESTO.md](PRODUCT_MANIFESTO.md) · [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md) · [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) · [ENGINEERING_CHARTER.md](ENGINEERING_CHARTER.md) · [CONTRIBUTING_ENGINEERING.md](CONTRIBUTING_ENGINEERING.md)

This document is the living product and architecture roadmap for engineering milestones. Update it when a milestone starts, completes, or when parking-lot items are promoted.

---

## Product vision

Mighty is a quiet co-pilot for financial and loyalty life. It watches accounts the user already has, keeps them current in the background, and speaks up only when something is worth their time.

Home answers one question in five seconds: *Does anything need me?* Login is the only manual step. Discovery, session detection, extraction, and refresh are Mighty’s job. Healthy accounts stay silent.

Canonical product north star: [PRODUCT_MANIFESTO.md](PRODUCT_MANIFESTO.md). Long-horizon surface and question model: [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md).

---

## Architecture vision

Mighty separates **access truth** from **attention**:

| Layer | Role |
|-------|------|
| **Access writers** | Access Manager (extension / PSS) and Provider Runtime (`AccessState`, `needs_human`) |
| **AuthTruth** | Pure projection of primary access-method evidence — not a second auth write store |
| **AccountState** | Per-account mirror for Accounts/detail; does **not** own the cross-account hero |
| **Attention** | Product policy: compile → overlays → rank → `AttentionState` → `AttentionView` → surfaces / delivery |

One write plane for auth. One compiler gather path for attention. One ranking table. One analytics owner per human moment.

Normative architecture: [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md).

Target production flow (Attention):

```text
AuthTruth · Authorize · Trust · Worker · Benefit · AccountState
  → compile_attention_candidates (gather only)
  → compose_attention (overlays + ranking)
  → AttentionState
  → AttentionView(surface) → Home / Worker / …
  → AttentionDelivery (primary push + receipts)
  → AttentionSupervisor (timeout / GC / reopen)
  → attention_metrics (supervisor heartbeat)
```

---

## Milestone roadmap

| Milestone | Title | Status | Record |
|-----------|-------|--------|--------|
| 1 | Access truth foundations (AuthTruth / AccountState seam) | Complete | RFC + domain docs |
| 2 | Attention Core | Complete | [ATTENTION_ENGINE.md](ATTENTION_ENGINE.md) |
| 3 | Platform Adoption | Complete | [ATTENTION_PLATFORM_ADOPTION.md](ATTENTION_PLATFORM_ADOPTION.md) |
| 4 | Intelligent Attention | Complete | [milestones/MILESTONE_4.md](milestones/MILESTONE_4.md) |
| 5 | Autonomous Attention | Complete | [milestones/MILESTONE_5.md](milestones/MILESTONE_5.md) |
| OS | Engineering Operating System | Complete | This roadmap + charter + contributing |
| 6 | *(not started)* | Pending | Create `milestones/MILESTONE_6.md` at kickoff |

RFC phase mapping (P0–P5) lives in [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) Part XIII. Engineering milestones are the delivery vehicle; living reports under `docs/milestones/` are authoritative for what shipped.

---

## Current milestone

**None (planning).** Engineering Operating System is complete. Milestone 6 has not started.

| Field | Value |
|-------|-------|
| **Last completed** | OS — [milestones/MILESTONE_OS.md](milestones/MILESTONE_OS.md) |
| **Next** | Milestone 6 — scope TBD at kickoff from candidates below |
| **Gate cleared** | Canonical OS docs on `main`; M6 implementation may begin after kickoff design note |

### Milestone 6 candidates (from M5)

Promote or defer at M6 kickoff; do not treat as committed scope until the M6 design note lands.

1. Runtime focus CTA bridge after Runtime API auth exists  
2. Hard-delete cutover flags after soak criteria pass ([ATTENTION_CUTOVER_RETIREMENT.md](ATTENTION_CUTOVER_RETIREMENT.md))  
3. Admin metrics dashboard over `attention_metric_snapshot`  
4. Unexpected-human-minutes time series  
5. Reconcile/expand AuthTruth tests against the thin Runtime store  
6. Activity surface filter for authorize items  

---

## Success criteria

### Platform (standing)

- `AttentionState` is the single source of truth for attention decisions  
- `AttentionView` is presentation-only; consumers do not re-rank or invent policy  
- One projection / one compiler gather path per domain; engine composes without business policy  
- Attention failures never block Home, Worker, or sync  
- Prefer deletion of obsolete parallel paths over permanent dual stacks  

### Per milestone

Defined in the milestone design note and living report. At minimum: objective met, tests green, docs updated, invariants preserved, Architecture Decisions recorded (M6+).

### Cutover retirement (open operational goal)

Objective criteria in [ATTENTION_CUTOVER_RETIREMENT.md](ATTENTION_CUTOVER_RETIREMENT.md). Flags may be hard-deleted only after soak criteria pass.

---

## Parking lot

Capabilities deliberately **not** committed to the next milestone. Promote via roadmap update + design note when ready.

| Item | Notes |
|------|-------|
| Multi-item push / email primary | v1 push targets `AttentionState.primary` only |
| Household / multi-user attention | Out of RFC v2 scope |
| Credential storage as default auth path | Explicit non-goal |
| Connector → AuthTruth dependency | Forbidden by RFC; Connectors use Runtime session APIs |
| Full Provider Runtime Control Center restore | Thin `runtime_access_state` shipped for AuthTruth/Trust; full CC separate |
| Opportunity sources beyond `action_items` | Partnerships / richer generators |
| First-class product analytics event table | Logs + receipts + metric snapshots today |
| Weekly digest / Daily Brief surfaces | Product Architecture horizon; not Attention core |
| Mobile-complete auth CTAs for browser_session | Capability-aware; phone may not complete login |

---

## How to update this document

1. At milestone kickoff: set **Current milestone**, link the living report and design note.  
2. As scope changes: update candidates vs parking lot; record Architecture Decisions in the living report.  
3. At milestone completion: mark status Complete, point to the living report, set Current → next milestone or “none / planning”.  
4. Do not duplicate full PR lists here — those belong in `docs/milestones/MILESTONE_<N>.md`.
