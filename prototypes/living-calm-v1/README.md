# Living Calm V1 — Design Prototype

Standalone HTML/CSS/JS exploration of **Living Calm**: evolving Mighty from a polished interface into a memorable product.

**Not production.** Does not modify:

- Production design system (`static/design-system`, `mighty/design_system`)
- Production pages / customer UI
- Trust V1 (`prototypes/trust_v1/` — frozen)

## Design docs

- [`docs/LIVING_CALM_V1.md`](../../docs/LIVING_CALM_V1.md)
- [`docs/QUIET_FIELD_V2.md`](../../docs/QUIET_FIELD_V2.md)
- [`docs/BRAND_PERSONALITY.md`](../../docs/BRAND_PERSONALITY.md)
- [`docs/VISUAL_HIERARCHY.md`](../../docs/VISUAL_HIERARCHY.md)
- [`docs/HOME_CONCEPTS.md`](../../docs/HOME_CONCEPTS.md)

## Open

```bash
cd prototypes/living-calm-v1
python3 -m http.server 8771 --bind 127.0.0.1
```

Visit `http://127.0.0.1:8771`.

## Surfaces

| File | Explores |
|------|----------|
| `index.html` | Hub + scope guardrails |
| `quiet-field.html` | Quiet Field as primary metaphor |
| `personality.html` | Emotional identity |
| `hierarchy.html` | Four hierarchy levels |
| `home-minimal.html` | Home · Minimal Calm |
| `home-living-field.html` | Home · Living Quiet Field |
| `home-operational.html` | Home · Operational Calm |
| `review.html` | Printable review deck (all concepts) |

Home pages accept `?state=clear|attention|opportunity`.

## Review packet

Screenshots + PDF: `docs/pr-screenshots/living-calm-v1/`

## Rationale discipline

Every major surface includes a short **Design decision** note stating why the composition exists. Full rationale lives in the docs above.
