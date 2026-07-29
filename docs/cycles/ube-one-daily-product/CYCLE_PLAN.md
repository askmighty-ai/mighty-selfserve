# Cycle Plan — One Daily Product (UBE)

**Charter:** [CYCLE_CHARTER.md](CYCLE_CHARTER.md) (**Accepted & frozen**)  
**Assessment:** [../ube-gap-assessment/UBE_GAP_ASSESSMENT.md](../ube-gap-assessment/UBE_GAP_ASSESSMENT.md)

## Strategy

Make production’s authenticated home participate in the **same component families** already used by Accounts / Activity / Settings (`authenticated_app_shell`), so a Founder cannot locate an implementation seam. Preserve dashboard/home functionality; do not redesign landing or auth.

## Slices

| # | Slice | Component family |
|---|-------|------------------|
| 1 | Re-frame `/dashboard` body in shared MDS authenticated document | Shell + navigation + brand + type |
| 2 | Remove customer Inter sidebar / mobile drawer from daily home | Navigation |
| 3 | Bridge remaining home affordances (pills, primary links) to pine/token language — no behavior change | Status / CTA continuity |
| 4 | Tests: production-like `/dashboard` has `data-app-shell="mds"`, no `class="sidebar"`, same nav hrefs as Accounts | Perception guards |
| 5 | Screenshots + cycle report + Independent Audit (**perception** brief) — **stop before deploy** |

## Anti-patterns

- Page paint / Inter color tweaks without shared shell  
- Declaring UBE milestone complete  
- Unrelated cleanup  
- Deploy before audit  

## Stop

Independent Audit (perception) → Founder → deploy only on explicit ask.
