# Mighty Production Design System

**Status:** Production UI foundation (opt-in)  
**Branch intent:** Tokens + component contracts implemented; pages not migrated.

## What this is

A composable design system for future customer-facing UI, derived from:

- [TRUST_BY_DESIGN.md](../TRUST_BY_DESIGN.md)
- [FIRST_10_MINUTES.md](../FIRST_10_MINUTES.md)
- [MIGHTY_VISUAL_SYSTEM_V1.md](../MIGHTY_VISUAL_SYSTEM_V1.md)
- [MIGHTY_COMPONENT_LIBRARY.md](../MIGHTY_COMPONENT_LIBRARY.md)
- [MIGHTY_ICONOGRAPHY.md](../MIGHTY_ICONOGRAPHY.md)

It does **not** redesign or restyle existing production pages. Callers opt in by wrapping markup in `.mds` and using `mds-*` classes / Python renderers.

## Layout

| Path | Role |
|------|------|
| `static/design-system/tokens.css` | Colors, spacing, type, radii, shadows, motion |
| `static/design-system/base.css` | Typography utilities, focus, layout |
| `static/design-system/motion.css` | Motion + `prefers-reduced-motion` |
| `static/design-system/components.css` | Component styles |
| `static/design-system/mighty-ds.css` | Bundle import |
| `mighty/design_system/` | Python token registry + accessible HTML renderers |
| `/admin/design-system` | Admin-only showcase (Storybook equivalent) |

## Usage (future pages)

```python
from mighty.design_system import render_button, render_status_badge

html = render_button("Continue to Google", variant="primary", size="lg", block=True)
```

```html
<div class="mds mds-atmosphere">
  <!-- component markup -->
</div>
<link rel="stylesheet" href="/static/design-system/mighty-ds.css"/>
```

## Non-goals

- No customer page migration in this delivery
- No changes to `prototypes/trust_v1/` (frozen reference)
- No production behavior changes for existing routes
