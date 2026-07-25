# Production Design System — Product review packet

Admin showcase review for the opt-in Mighty production design system.

**Do not embed these images in chat.** Open files from the repository.

## Files

| File | What it shows |
|------|----------------|
| `desktop.png` | Full-page `/admin/design-system` at ~1440px width |
| `mobile.png` | Full-page `/admin/design-system` at ~390px width |
| `PRODUCTION_DESIGN_SYSTEM_REVIEW.pdf` | Desktop + mobile + enlarged component sections with state captions |
| `README.md` | This guide |

## Showcase coverage

The PDF section pages document each of the 16 component contracts and the states visible in the showcase (and interaction captures where static markup alone cannot show hover/focus/open):

1. **Button** — primary/secondary/ghost/destructive/link; lg/sm; loading; disabled; icon; block; hover; focus-visible
2. **Card** — surface / soft / field
3. **Section** — page section with eyebrow + lede
4. **Hero** — home healthy, home attention, marketing + Quiet Field
5. **Status Badge** — quiet/success, waiting, attention, review, neutral
6. **Trust Card** — reassure / limit / consequence
7. **Permission Card** — preface stack with limits row + primary/ghost actions
8. **Timeline** — authorized / lifecycle / completed
9. **Account Row** — current, suggestion/review, selectable attention
10. **Empty State** — first-use + no-results
11. **Modal** — open confirm dialog (focus trap)
12. **Progress Stepper** — horizontal onboarding + vertical discovery (live)
13. **Navigation** — app + marketing
14. **Form Controls** — text, helper, validation error, checkbox, switch
15. **Toast** — success / info / attention / error
16. **Banner** — waiting / attention / success (+ dismiss)

Plus design tokens and icon set pages in the showcase.

## Source

- Route: `/admin/design-system` (admin-only)
- Implementation: `static/design-system/`, `mighty/design_system/`
- Contracts: `docs/MIGHTY_COMPONENT_LIBRARY.md`, `docs/MIGHTY_VISUAL_SYSTEM_V1.md`, `docs/MIGHTY_ICONOGRAPHY.md`

## Non-goals

- No customer-facing page migration
- No component/token/route source changes in this review commit
