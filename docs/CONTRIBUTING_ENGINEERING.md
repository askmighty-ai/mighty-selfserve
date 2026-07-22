# Contributing to Mighty — Engineering Guide

**Status:** Canonical  
**Audience:** Engineers and agents delivering milestones  
**Related:** [ROADMAP.md](ROADMAP.md) · [ENGINEERING_CHARTER.md](ENGINEERING_CHARTER.md) · [milestones/README.md](milestones/README.md) · [CONTRIBUTING_PRODUCT.md](../CONTRIBUTING_PRODUCT.md)

This guide explains how engineering work is organized in this repository. Product principles live in the [Product Manifesto](PRODUCT_MANIFESTO.md); this document covers **process and repository conventions**.

After the engineering operating system lands, milestone prompts should be short: objective, success criteria, and pointers to these canonical docs.

---

## Canonical operating documents

| Document | Role |
|----------|------|
| [ROADMAP.md](ROADMAP.md) | Vision, milestone roadmap, current milestone, parking lot |
| [ENGINEERING_CHARTER.md](ENGINEERING_CHARTER.md) | Authority, invariants, escalation, DoD |
| [CONTRIBUTING_ENGINEERING.md](CONTRIBUTING_ENGINEERING.md) | This guide — how work flows |
| [milestones/MILESTONE\_\<N\>.md](milestones/README.md) | Authoritative record of what a milestone delivered |
| Domain RFCs / design notes under `docs/` | Architecture and implementation detail |

Chat summaries are convenience only. **The repository is the source of truth.**

---

## How milestones work

1. **Select scope** from [ROADMAP.md](ROADMAP.md) (current candidates or parking lot promotions).  
2. **Kickoff**  
   - Create `docs/milestones/MILESTONE_<N>.md` (Status: In progress).  
   - Open a Design Note PR describing order, interfaces, risks, tests.  
   - Update Roadmap **Current milestone**.  
3. **Deliver** via a sequence of small, reviewable PRs under [ENGINEERING_CHARTER.md](ENGINEERING_CHARTER.md) authority.  
4. **Keep the living report current** as PRs merge (especially Architecture Decisions).  
5. **Complete** when Definition of Done is met; fill all required report sections; update Roadmap.  

Do not begin the next numbered implementation milestone until any declared process gate (e.g. operating-system docs) is merged.

### Typical PR sequence

```text
Design note → foundational seams → producers / features → supervisor/delivery/ops → metrics/cutover → replay/docs close
```

Adjust order after inspecting `origin/main`. Prefer extend-and-integrate over redesign unless the milestone explicitly authorizes redesign.

---

## Living milestone reports

Path: `docs/milestones/MILESTONE_<N>.md`  
Convention: [milestones/README.md](milestones/README.md)

Create the file at kickoff. Update it as work progresses. At completion, fill every required section.

**Required sections (at completion):**

1. Objective  
2. PRs merged  
3. Architecture changes  
4. Architecture Decisions *(required beginning with Milestone 6)*  
5. Final production data flow  
6. Validation performed  
7. Tests executed  
8. Metrics added  
9. Technical debt  
10. Lessons learned  
11. Recommendation for the next milestone  

---

## Design notes

For non-trivial milestones, land a design note under `docs/` before feature PRs (e.g. `ATTENTION_*.md`).

A design note should cover:

- Objective and non-goals  
- Proposed implementation order  
- Architectural impact (relative to invariants)  
- Interfaces / module seams  
- Risks  
- Testing strategy  
- Success criteria  

Link the design note from the living milestone report and from the Roadmap current-milestone entry.

---

## Architecture Decisions

Beginning with **Milestone 6**, record significant autonomous engineering judgment in the living milestone report under **Architecture Decisions** — not as a dump of implementation detail.

For each decision:

| Field | Meaning |
|-------|---------|
| **Decision** | What was chosen |
| **Why it was chosen** | Reasoning |
| **Alternatives considered** | If any |
| **Long-term architectural impact** | What this commits us to or unlocks |

Update as decisions are made; do not wait until milestone close. Routine mechanical choices do not need entries.

---

## Repository conventions

### Branches and PRs

- Feature branches off `main`; open PRs early when useful.  
- Prefer squash merge via GitHub; respect branch protection and CI.  
- Force-push only with `--force-with-lease` on owned feature branches (see Charter).  
- Delete merged feature branches.  

### Documentation

- Domain docs (`docs/ATTENTION_*.md`, `docs/ACCOUNT_STATE.md`, …) stay aligned with code in the same milestone.  
- Living reports are authoritative for milestone outcomes; design notes are authoritative for intended design.  
- Do not invent parallel “status” docs that drift from Roadmap + living reports.  

### Code and tests

- Match existing module and naming patterns in `mighty/`.  
- Attention producers: loader → compiler gather → engine wiring → View copy if needed → tests.  
- Prefer golden / replay tests for ranking and lifecycle.  
- Attention failures must degrade safely — never fail Home/Worker/sync.  

### Product

- Product-facing changes follow [CONTRIBUTING_PRODUCT.md](../CONTRIBUTING_PRODUCT.md).  
- Customer English for attention resolves through Attention copy keys / `AttentionView`, not ad-hoc surface strings.  

### Secrets and safety

- Never commit secrets or bypass required checks.  
- Do not change production infrastructure unrelated to the milestone.  

---

## Short milestone prompt template

Future prompts can look like:

```text
Lead Engineer under docs/ENGINEERING_CHARTER.md.
Follow docs/CONTRIBUTING_ENGINEERING.md and docs/ROADMAP.md.

Milestone N — <title>
Objective: …
Success criteria: …
Design note: docs/<…>.md (create if missing)
Living report: docs/milestones/MILESTONE_N.md

Deliver autonomously. Escalate only per Charter.
```

Do not restate authority, invariants, escalation, or DoD unless amending them.
