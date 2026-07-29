# 06 — Product Decisions

**Status:** Product System (MPS)  
**Audience:** Anyone revisiting why a durable choice was made  
**Typical length:** Short index here; one page per decision in `decisions/`  
**Authority:** Institutional memory — not meeting notes

---

## Purpose

Record durable product choices so future work does not re-litigate settled ground or lose context trapped only inside checkpoint specs.

## What belongs here

- Decisions that constrain future product work
- Reason, affected areas, status
- Links to source experience or review when relevant

## What does not belong here

- Meeting notes, brainstorm lists, or temporary spikes
- Implementation tickets or bug fix logs
- Restatements of Constitution principles (cite [01](01_constitution.md) instead)
- Full screenplays

---

## Format

Create one file per decision:

`docs/product/decisions/YYYY-MM-DD-short-slug.md`

```markdown
# Decision: <short title>

| Field | Value |
|-------|-------|
| **Date** | YYYY-MM-DD |
| **Status** | Proposed \| Accepted \| Superseded \| Deprecated |
| **Decision** | One or two sentences stating what we chose |
| **Vision rule** | Named Founder Vision rule(s) that imply this decision |
| **Reason** | Why this choice (operational application of the Vision rule) |
| **Affected areas** | Manifesto / Constitution / Mental model / Design / Voice / Experience / Architecture / named surfaces |
| **Supersedes** | Optional link to prior decision |
| **Source** | Optional: experience doc, PR, review |

## Notes

Optional short context. No meeting transcripts.
```

### Status meanings

| Status | Meaning |
|--------|---------|
| **Proposed** | Drafted; not yet binding |
| **Accepted** | Binding until superseded |
| **Superseded** | Replaced by a newer decision (link it) |
| **Deprecated** | No longer in force; kept for history |

---

## Index

| Date | Decision | Status | File |
|------|----------|--------|------|
| 2026-07-27 | Confirm selection enrolls watching (Amex) | Accepted | [2026-07-27-confirm-enrolls-watching.md](decisions/2026-07-27-confirm-enrolls-watching.md) |
| 2026-07-27 | Beta path: no provider password UI in Mighty | Accepted | [2026-07-27-no-provider-password-ui.md](decisions/2026-07-27-no-provider-password-ui.md) |
| 2026-07-27 | Invite cohort: sideload Mighty in Chrome | Accepted | [2026-07-27-sideload-extension-install.md](decisions/2026-07-27-sideload-extension-install.md) |
| 2026-07-27 | Gmail preface headers/senders accuracy | Accepted | [2026-07-27-gmail-preface-accuracy.md](decisions/2026-07-27-gmail-preface-accuracy.md) |
| 2026-07-27 | Steady-State Home: all-clear earned; UPDATE / needs-user block You’re good | Accepted | [2026-07-27-steady-state-all-clear-honesty.md](decisions/2026-07-27-steady-state-all-clear-honesty.md) |
| 2026-07-27 | Steady-State Home: demo content opt-in only on customer path | Accepted | [2026-07-27-steady-state-demo-opt-in.md](decisions/2026-07-27-steady-state-demo-opt-in.md) |
| 2026-07-27 | First Success: role-split sign-in ask + Not now defer | Accepted | [2026-07-27-first-success-role-split-defer.md](decisions/2026-07-27-first-success-role-split-defer.md) |
| 2026-07-27 | First Success: one-shot Home beat before ambient all-clear | Accepted | [2026-07-27-first-success-beat.md](decisions/2026-07-27-first-success-beat.md) |
| 2026-07-27 | First Success: demote connect-modal from happy path | Accepted | [2026-07-27-first-success-demote-connect-modal.md](decisions/2026-07-27-first-success-demote-connect-modal.md) |
| 2026-07-28 | Factory reset returns to public landing | Accepted | [2026-07-28-factory-reset-public-landing.md](decisions/2026-07-28-factory-reset-public-landing.md) |
| 2026-07-28 | Extension-setup never dead-ends | Accepted | [2026-07-28-extension-setup-never-dead-end.md](decisions/2026-07-28-extension-setup-never-dead-end.md) |
| 2026-07-28 | Enable Monitoring does not claim Watching is on before Chrome | Accepted | [2026-07-28-watching-state-before-chrome.md](decisions/2026-07-28-watching-state-before-chrome.md) |
| 2026-07-28 | Extension-setup is interactive verification (I’ve installed Mighty) | Accepted | [2026-07-28-extension-setup-verify-flow.md](decisions/2026-07-28-extension-setup-verify-flow.md) |
| 2026-07-28 | Extension-setup requires normal Chrome window (not Incognito) | Accepted | [2026-07-28-extension-setup-browser-context.md](decisions/2026-07-28-extension-setup-browser-context.md) |
| 2026-07-28 | Instrument extension detection handshake (beta diagnostics) | Accepted | [2026-07-28-extension-detection-handshake.md](decisions/2026-07-28-extension-detection-handshake.md) |
| 2026-07-28 | Bound Amex extraction lifecycle (no sticky Extracting) | Accepted | [2026-07-28-amex-bounded-extraction-lifecycle.md](decisions/2026-07-28-amex-bounded-extraction-lifecycle.md) |
| 2026-07-28 | Login preserves deep-link `next` through failed auth | Accepted | [2026-07-28-login-preserves-next.md](decisions/2026-07-28-login-preserves-next.md) |
| 2026-07-28 | Dual authenticated visual systems are a Learning Distorter | Accepted | [2026-07-28-visual-dual-system-ld.md](decisions/2026-07-28-visual-dual-system-ld.md) |
| 2026-07-28 | Mighty is the system of engagement; providers are temporary workspaces | Accepted | [2026-07-28-mighty-system-of-engagement.md](decisions/2026-07-28-mighty-system-of-engagement.md) |
| 2026-07-28 | Visit provider keeps Mighty as home base (new tab) | Accepted | [2026-07-28-visit-amex-home-base.md](decisions/2026-07-28-visit-amex-home-base.md) |
| 2026-07-29 | Unified Beta Experience is the governing beta gate | Accepted | [2026-07-29-unified-beta-experience.md](decisions/2026-07-29-unified-beta-experience.md) |
| 2026-07-29 | Suspend visual migration; State Model narrator is next UBE repair | Accepted | [2026-07-29-ube-state-model-narrator.md](decisions/2026-07-29-ube-state-model-narrator.md) |
| 2026-07-28 | Visual consistency migrates by surface family | Accepted | [2026-07-28-visual-surface-family-migration.md](decisions/2026-07-28-visual-surface-family-migration.md) |
| 2026-07-28 | Legacy dashboard retirement is environment-aware | Accepted | [2026-07-28-legacy-dashboard-env-aware-retirement.md](decisions/2026-07-28-legacy-dashboard-env-aware-retirement.md) |
| 2026-07-26 | Discovery requires explicit confirm before monitoring | Accepted | [2026-07-26-discovery-requires-confirm.md](decisions/2026-07-26-discovery-requires-confirm.md) |
| 2026-07-26 | Enable Monitoring: outcome before mechanism; CTA “Enable updates” | Accepted | [2026-07-26-enable-monitoring-outcome-first.md](decisions/2026-07-26-enable-monitoring-outcome-first.md) |
| 2026-07-26 | Welcome is one composition; Get started → signup | Accepted | [2026-07-26-welcome-one-composition.md](decisions/2026-07-26-welcome-one-composition.md) |

Add new rows when decisions are filed. Prefer extracting choices from approved experiences over inventing new philosophy.

---

## When to file a decision

File when:

- A checkpoint makes a choice that should outlive that checkpoint’s UI details
- A review settles a contested product question
- Exploration (e.g. Living Calm) is **adopted** or **explicitly declined**
- Two principles appear to conflict and a precedence is chosen for a class of cases

Do **not** file for routine implementation details that do not change product meaning.
