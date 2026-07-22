# Milestone reports

Living, authoritative records for Mighty engineering milestones.

**Operating system:** [ROADMAP.md](../ROADMAP.md) · [ENGINEERING_CHARTER.md](../ENGINEERING_CHARTER.md) · [CONTRIBUTING_ENGINEERING.md](../CONTRIBUTING_ENGINEERING.md)

## Convention

| Path | Role |
|------|------|
| `docs/milestones/MILESTONE_<N>.md` | Authoritative milestone record |
| Design notes under `docs/` (e.g. `ATTENTION_*.md`) | Architecture / implementation detail |

Create `MILESTONE_<N>.md` when a milestone starts. Keep it current as PRs merge. At completion, fill every section below.

Chat completion summaries are convenience only — **this folder is the source of truth**.

## Required sections (at completion)

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

### Architecture Decisions

Capture significant autonomous engineering judgment made during the milestone — not implementation detail.

For each decision, record:

- **Decision** — what was chosen  
- **Why it was chosen** — the reasoning that made it the right call  
- **Alternatives considered** — if any  
- **Long-term architectural impact** — what this commits us to or unlocks  

Update this section as decisions are made; do not wait until milestone close.

## Index

| Milestone | Title | Status | Report |
|-----------|-------|--------|--------|
| 4 | Intelligent Attention | Complete | [MILESTONE_4.md](MILESTONE_4.md) |
| 5 | Autonomous Attention | Complete | [MILESTONE_5.md](MILESTONE_5.md) |
| OS | Engineering Operating System | Complete | [MILESTONE_OS.md](MILESTONE_OS.md) |
| 6 | Autonomous Recovery | Complete | [MILESTONE_6.md](MILESTONE_6.md) |
| 7 | Automatic Account Discovery and Enrollment | Complete | [MILESTONE_7.md](MILESTONE_7.md) |
| 8 | Natural-Session Coverage | Complete | [MILESTONE_8.md](MILESTONE_8.md) |
| 9 | Freshness and Change Intelligence | Pending | Create at kickoff |
