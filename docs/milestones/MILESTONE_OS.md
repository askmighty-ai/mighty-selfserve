# Process Milestone — Engineering Operating System

**Status:** Complete (upon merge of operating-system PR)  
**Type:** Documentation / process  
**Gate:** Milestone 6 implementation must not begin until this lands on `main`

## Objective

Establish repository-level canonical operating documents so future milestone prompts can reference authority, invariants, roadmap, and process instead of repeating them.

## PRs merged

| PR | Theme |
|----|--------|
| *(this PR)* | `ROADMAP.md`, `ENGINEERING_CHARTER.md`, `CONTRIBUTING_ENGINEERING.md` + index wiring |

## Architecture changes

None to runtime/Attention code. Process architecture only:

- Three canonical docs under `docs/`  
- Living milestone convention unchanged; Architecture Decisions already required from M6  

## Architecture Decisions

### AD-OS-1: Canonical OS as three docs

- **Decision:** Split operating system into Roadmap, Charter, and Contributing Engineering.  
- **Why:** Separates *where we are going*, *who may decide what*, and *how work flows* so milestone prompts can cite one doc at a time.  
- **Alternatives considered:** Single mega-README; keep process only in chat prompts.  
- **Long-term impact:** Prompts stay short; repo remains authoritative when chat context is lost.

### AD-OS-2: Process OS before Milestone 6

- **Decision:** Treat OS establishment as a hard gate before M6 implementation.  
- **Why:** M6+ living reports require Architecture Decisions; authority/invariants should be repo-canonical first.  
- **Alternatives considered:** Start M6 in parallel with docs.  
- **Long-term impact:** Cleaner milestone boundaries; less prompt drift.

## Final production data flow

Unchanged. See [MILESTONE_5.md](MILESTONE_5.md) and [ROADMAP.md](../ROADMAP.md).

## Validation performed

- Content derived from Product Manifesto, Attention RFC, M4/M5 living reports, and prior Lead Engineer charter language used in milestone prompts.  
- Cross-linked from root README and milestones index.

## Tests executed

N/A (documentation only).

## Metrics added

None.

## Technical debt

- Milestones 1–3 lack dedicated living reports under `docs/milestones/` (status lives in RFC / design notes). Optional backfill.  
- Root README still leads with deploy/API; engineering OS is linked, not the primary landing narrative.

## Lessons learned

- Encoding standing authority in-repo prevents prompt length growth and loss of constraints across sessions.  
- Architecture Decisions belong in living reports; the Charter only requires that they exist.

## Recommendation for the next milestone

Kick off **Milestone 6** using the short prompt template in [CONTRIBUTING_ENGINEERING.md](../CONTRIBUTING_ENGINEERING.md). Choose scope from [ROADMAP.md](../ROADMAP.md) M6 candidates; create `MILESTONE_6.md` and a design note before implementation PRs.
