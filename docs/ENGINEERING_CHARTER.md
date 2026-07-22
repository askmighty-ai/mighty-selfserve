# Mighty Engineering Charter

**Status:** Canonical  
**Audience:** Lead Engineer (human or agent) delivering approved milestones  
**Related:** [ROADMAP.md](ROADMAP.md) · [CONTRIBUTING_ENGINEERING.md](CONTRIBUTING_ENGINEERING.md) · [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md)

This charter grants standing authority and constraints for autonomous engineering delivery. Milestone prompts should **reference** this document rather than restate it.

---

## Engineering Authority

The Lead Engineer has standing authority to perform all routine engineering work necessary to complete the approved milestone.

**Authorized without further approval:**

- Inspect the repository, history, RFCs, and documentation  
- Create, rename, delete, and rebase feature branches  
- Commit, amend, squash, and reorder commits on owned feature branches  
- Use `git push --force-with-lease` on feature branches you own  
- Resolve routine merge conflicts  
- Open, edit, retarget, reopen, and merge pull requests for the milestone  
- Delete merged feature branches  
- Run builds, tests, formatters, linters, and static analysis  
- Update documentation  
- Refactor implementation details while preserving approved contracts  
- Split or combine PRs when it improves reviewability  
- Create follow-on PRs as needed  
- Fix routine CI failures  
- Revert your own milestone work if necessary for safe recovery  

Assume ownership of the implementation. If a reasonable engineering decision is required and it does not alter approved architecture, **make the decision**, record it under Architecture Decisions in the living milestone report when significant, and continue.

Prefer forward progress over unnecessary escalation. Do not stop for routine engineering decisions.

IDE confirmation dialogs are tooling confirmations, not architectural escalations.

### Not authorized

- Rewrite `main` history  
- Bypass branch protections  
- Merge while required tests are failing  
- Expose secrets  
- Change production infrastructure unrelated to the milestone  
- Alter approved architectural invariants  
- Change product behavior outside the milestone  

---

## Architectural Invariants

These are standing constraints for Attention Platform work unless an escalated decision explicitly amends them.

1. **`AttentionState` is the single source of truth** for attention decisions.  
2. **`AttentionView` formats `AttentionState` for each surface** — presentation only.  
3. **Consumers render `AttentionView`.** They do not reinterpret or re-rank `AttentionState`.  
4. **Each domain owns exactly one projection and one compiler** (gather) path.  
5. **Domain compilers own domain logic.** `compile_attention_candidates` only gathers candidates.  
6. **`AttentionEngine` composes existing stages.** It does not implement business policy.  
7. **Shared policy must not contain provider-specific branching.**  
8. **Attention failures must never block Home, Worker, or synchronization.**  
9. **Prefer deletion of obsolete code** over long-lived parallel implementations.  

Normative ownership and ranking: [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md).

---

## Escalation Policy

Escalate **only** if:

1. An RFC is internally inconsistent.  
2. An architectural invariant would be violated.  
3. Multiple materially different architectural approaches exist.  
4. A product or UX decision is required.  
5. A security, privacy, data-loss, or production-risk concern is discovered.  

When escalating, provide:

| Field | Content |
|-------|---------|
| Issue | What is blocked |
| Evidence | What you observed |
| Options | Concrete alternatives |
| Recommendation | Preferred option |
| Tradeoffs | Cost of each option |
| Exact decision required | The choice the decision-maker must make |

Do not merely report uncertainty.

---

## Self-review checklist

Before every merge, verify:

- [ ] Focused PR (one coherent change)  
- [ ] Passing tests  
- [ ] Documentation updated  
- [ ] Architectural invariants preserved  
- [ ] No duplicate policy introduced  
- [ ] Sufficient observability  
- [ ] Safe failure behavior  
- [ ] Obsolete code removed where practical  
- [ ] Significant decisions recorded in the living milestone report (M6+)  

---

## PR philosophy

- **Small, reviewable PRs.** Prefer a sequence of focused merges over a single large landing.  
- **Design note first** for non-trivial milestones — propose order, interfaces, risks, and tests before producer/feature PRs.  
- **Extend, do not redesign** unless the milestone objective explicitly authorizes redesign.  
- **Integrate with the platform** — new producers plug into existing compiler / loader / view seams.  
- **Docs travel with code.** Design notes, compiler docs, and living milestone reports update in the same milestone.  
- **Product-facing PRs** also follow [CONTRIBUTING_PRODUCT.md](../CONTRIBUTING_PRODUCT.md) and the manifesto.  

---

## Definition of Done

A milestone is complete when **all** of the following are true:

1. Success criteria in the design note / roadmap entry are met.  
2. Relevant tests pass on `main` (or equivalent CI).  
3. Documentation is updated (design note, domain docs, living milestone report).  
4. Architectural invariants are preserved.  
5. Living milestone report is filled with required sections (see [milestones/README.md](milestones/README.md)).  
6. Architecture Decisions for significant judgment calls are recorded (required beginning with Milestone 6).  
7. Roadmap **Current milestone** is updated to the next milestone or planning state.  
8. Chat may include a short completion summary; the **repository living report is authoritative**.  

Return to the decision-maker only when a genuine escalation is required or the milestone is complete.
