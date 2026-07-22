# Mighty Engineering Roadmap

**Status:** Canonical  
**Audience:** Lead Engineer and contributors delivering milestones  
**Related:** [PRODUCT_MANIFESTO.md](PRODUCT_MANIFESTO.md) · [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md) · [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) · [ENGINEERING_CHARTER.md](ENGINEERING_CHARTER.md) · [CONTRIBUTING_ENGINEERING.md](CONTRIBUTING_ENGINEERING.md)

This document is the living product and architecture roadmap for engineering milestones. Update it when a milestone starts, completes, or when parking-lot items are promoted.

Milestones are **capability-led**: each delivers a user-visible capability. Supporting work (internal dashboards, test reconciliation, flag deletion, isolated metrics) belongs inside the capability milestone it supports, or as focused operational work — not as standalone product milestones.

---

## Product vision

Mighty is a quiet co-pilot for financial and loyalty life. It watches accounts the user already has, keeps them current in the background, and speaks up only when something is worth their time.

Home answers one question in five seconds: *Does anything need me?* Login is the only manual step. Discovery, session detection, extraction, and refresh are Mighty’s job. Healthy accounts stay silent.

Canonical product north star: [PRODUCT_MANIFESTO.md](PRODUCT_MANIFESTO.md). Long-horizon surface and question model: [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md).

---

## Architecture vision

Mighty separates **discovery**, **enrollment**, **access truth**, **recovery**, and **attention**:

| Layer | Role |
|-------|------|
| **Discovery** | Durable provider-relationship facts from mailbox evidence (`account_discovery`) |
| **Enrollment** | Canonical watched-account write (`_register_account_source` / discovery enroll) |
| **Access writers** | Access Manager (extension / PSS) and Provider Runtime (`AccessState`, `needs_human`) |
| **AuthTruth** | Pure projection of primary access-method evidence — not a second auth write store |
| **Recovery** | Deterministic planner + lifecycle that attempts safe autonomous repair before human interrupt |
| **Natural Session** | Passive browse / ensure-due freshness decisions; executes only through PAM; defers to Recovery |
| **AccountState** | Per-account mirror for Accounts/detail; does **not** own discovery or the cross-account hero |
| **Freshness / Change** | Data currency + meaningful snapshot diffs (`account_changes`); facts for History/Briefs — not Attention ranking |
| **Value Intelligence** | Durable opportunity facts from snapshots (`account_opportunities`); computes value — not Attention ranking |
| **Attention** | Product policy: compile → overlays → rank → `AttentionState` → `AttentionView` → surfaces / delivery |

One write plane for auth. One recovery lifecycle owner. One compiler gather path for attention. One ranking table. One analytics owner per human moment.

Recovery does **not** rank user attention. Attention does **not** decide recovery strategy. User interruption is the final capability, not the default.

Normative Attention architecture: [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md).

Target production flow (Attention + Recovery):

```text
Failure / access facts
  → Recovery Supervisor → Planner (deterministic capabilities)
  → Recovery lifecycle (attempt history + outcome)
  → on exhaustion / human-only → escalated gate
  → compile_attention_candidates (gather only; auth/trust/degraded gated)
  → compose_attention (overlays + ranking)
  → AttentionState → AttentionView → Home / Worker / …
  → AttentionDelivery · AttentionSupervisor · attention_metrics
  → recovery_metrics (supervisor heartbeat)
```

---

## Milestone roadmap

### Completed foundations

| Milestone | Title | Status | Record |
|-----------|-------|--------|--------|
| 1 | Access truth foundations (AuthTruth / AccountState seam) | Complete | RFC + domain docs |
| 2 | Attention Core | Complete | [ATTENTION_ENGINE.md](ATTENTION_ENGINE.md) |
| 3 | Platform Adoption | Complete | [ATTENTION_PLATFORM_ADOPTION.md](ATTENTION_PLATFORM_ADOPTION.md) |
| 4 | Intelligent Attention | Complete | [milestones/MILESTONE_4.md](milestones/MILESTONE_4.md) |
| 5 | Autonomous Attention | Complete | [milestones/MILESTONE_5.md](milestones/MILESTONE_5.md) |
| OS | Engineering Operating System | Complete | [milestones/MILESTONE_OS.md](milestones/MILESTONE_OS.md) |

### Capability roadmap

| Milestone | Capability | User outcome | Status | Record |
|-----------|------------|--------------|--------|--------|
| 6 | Autonomous Recovery | When something breaks, Mighty tries every safe recovery path before asking for help | Complete | [milestones/MILESTONE_6.md](milestones/MILESTONE_6.md) |
| 7 | Automatic Account Discovery and Enrollment | Accounts appear from the user’s existing digital life without bulk “Add account” rituals | Complete | [milestones/MILESTONE_7.md](milestones/MILESTONE_7.md) |
| 8 | Natural-Session Coverage | Mighty captures and maintains sessions through normal browsing, not sync marathons | Complete | [milestones/MILESTONE_8.md](milestones/MILESTONE_8.md) |
| 9 | Freshness and Change Intelligence | Users know what changed and that data is current — without status-dashboard noise | Complete | [milestones/MILESTONE_9.md](milestones/MILESTONE_9.md) |
| 10 | Value Intelligence | Mighty surfaces value at risk and opportunities worth acting on | Complete | [milestones/MILESTONE_10.md](milestones/MILESTONE_10.md) |
| 11 | Trusted Agent Authorization | Agents act only with verified, inspectable human approval | Pending | — |
| 12 | Trust, Privacy, and Control | Users understand and control what Mighty can see and do | Pending | — |

RFC phase mapping (P0–P5) for Attention foundations lives in [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) Part XIII. Living reports under `docs/milestones/` are authoritative for what shipped.

---

## Current milestone

**None (planning).** Milestone 10 — Value Intelligence is complete.

| Field | Value |
|-------|-------|
| **Last completed** | M10 — [milestones/MILESTONE_10.md](milestones/MILESTONE_10.md) |
| **Next** | Milestone 11 — Trusted Agent Authorization |

---

## Success criteria

### Platform (standing)

- `AttentionState` is the single source of truth for attention decisions  
- `AttentionView` is presentation-only; consumers do not re-rank or invent policy  
- One projection / one compiler gather path per domain; engine composes without business policy  
- One owner for recovery lifecycle state; recovery policy is deterministic and independently testable  
- Attention failures and recovery failures never block Home, Worker, account reads, or unrelated sync  
- Prefer deletion of obsolete parallel paths over permanent dual stacks  

### Per milestone

Defined in the milestone design note and living report. At minimum: user capability delivered, tests green, docs updated, invariants preserved, Architecture Decisions recorded (M6+).

### Operational work (not product milestones)

Cutover flag deletion, AuthTruth test reconciliation, admin metrics dashboards, and similar supporting tasks ship inside the capability milestone they support (or as focused ops PRs). Criteria for Attention cutover retirement: [ATTENTION_CUTOVER_RETIREMENT.md](ATTENTION_CUTOVER_RETIREMENT.md).

---

## Parking lot

Capabilities and technical items deliberately **not** committed as numbered product milestones. Promote via roadmap update + design note when ready.

| Item | Notes |
|------|-------|
| Multi-item push / email primary | v1 push targets `AttentionState.primary` only |
| Household / multi-user attention | Out of RFC v2 scope |
| Credential storage as default auth path | Explicit non-goal |
| Connector → AuthTruth dependency | Forbidden by RFC; Connectors use Runtime session APIs |
| Full Provider Runtime Control Center restore | Thin `runtime_access_state` shipped for AuthTruth/Trust; full CC separate |
| Opportunity sources beyond `action_items` | M10 shipped `account_opportunities`; Attention loader bridge still parking-lot |
| First-class product analytics event table | Supporting observability; not a product milestone |
| Weekly digest / Daily Brief surfaces | Product Architecture horizon |
| Mobile-complete auth CTAs for browser_session | Capability-aware; phone may not complete login |
| Runtime focus CTA bridge | Supporting Autonomous Recovery / Runtime auth; not standalone milestone |
| Hard-delete cutover flags after soak | Operational work after retirement criteria pass |

---

## How to update this document

1. At milestone kickoff: set **Current milestone**, link the living report and design note.  
2. As scope changes: update parking lot; record Architecture Decisions in the living report.  
3. At milestone completion: mark status Complete, point to the living report, set Current → next milestone or “none / planning”.  
4. Do not duplicate full PR lists here — those belong in `docs/milestones/MILESTONE_<N>.md`.  
5. Do not invent new product milestones for dashboards, flag cleanup, or isolated metrics.
