# Dashboard design system doctrine

> Scope: `apps/dashboard/` — how tokens, cascade layers, and component libraries fit together.
> For Astryx component API gotchas see [`docs/standards/frontend-patterns.md`](../../docs/standards/frontend-patterns.md).

## Token pipeline (SSoT)

| Layer | Location | Role |
|---|---|---|
| **Forge values** | `brand/tokens/*.css` | Canonical color, spacing, typography, motion, elevation, status tokens |
| **Astryx theme** | `apps/dashboard/src/astryx-theme/built/roxabi.theme.css` | `defineTheme` output — maps Forge tokens into Astryx component variables |
| **Tailwind theme** | `apps/dashboard/src/index.css` `@theme inline` | Shell semantic utilities (`bg-card`, `text-muted-foreground`, …) bound directly to Forge vars |
| **TS mirror** | `packages/shared/src/brand.ts` | Typed constants for non-CSS consumers; values must match `brand/tokens/colors.css` |

**Rule:** never duplicate token values in dashboard code. Change Forge tokens once; theme + Tailwind follow.

## Cascade-layer contract (anti-drift rule #1)

`apps/dashboard/src/index.css` declares a fixed layer stack:

```
reset → theme → base → astryx-base → astryx-theme → brand → components → utilities
```

Low → high priority. This ordering is **load-bearing**:

- **Astryx** (`astryx-base`, `astryx-theme`) owns component padding, surfaces, and StyleX-generated rules.
- **Brand** (`brand/`) supplies document defaults and marketing tokens — not component overrides.
- **Tailwind utilities** sit on top so layout/spacing classes in the shell still win over Astryx surfaces.

### Brand reset isolation (#2148)

Universal `*{ margin; padding; box-sizing }` lives in `brand/reset.css`, **not** `brand/base.css`.

- Dashboard: `@import "../../../brand/reset.css" layer(reset)` **before** Astryx imports, then `@import "../../../brand/core.css" layer(brand)` (tokens + `base.css` only — **not** `styles.css`, which would re-pull reset into `layer(brand)`).
- Marketing SSG: imports `brand/styles.css` unlayered (`reset.css` + `core.css`) — unchanged single entry point.

**Never** put universal selectors (`*`, `*::before`, `*::after`) in `brand/base.css` or any stylesheet imported as `layer(brand)`.

### Unlayered escape hatch

Rules in `index.css` outside `@layer` (e.g. `textarea[data-mono]`, `.fd-scroll`) intentionally beat layered Astryx/brand rules. Use sparingly and document why.

## Component decision tree

```
Need UI?
├─ Does Astryx ship it? (Card, Dialog, Table, Stack, Badge, …)
│  └─ YES → Use @astryxdesign/core. Pre-flight: `bun run astryx component <X> --props --source`
│           Drop surface Tailwind on Astryx primitives; keep layout Tailwind only.
├─ Is it shell chrome (nav, page layout, data-dense lists)?
│  └─ YES → Prefer `components/ui/*` (Tailwind + semantic theme utilities)
└─ Is it a one-off page composition?
   └─ Compose Astryx primitives + ui/ helpers; avoid new bespoke primitives.
```

| Use Astryx | Use `components/ui/` |
|---|---|
| Cards, dialogs, tables, form fields, toasts, banners | List toolbars, filter chips, sortable headers, empty states |
| Design-system primitives on `/design-system` | Cockpit layout, nav chrome, dense fleet/job lists |
| Anything with StyleX-layered surfaces | Layout/spacing utilities that must beat Astryx layers |

## Do / don't

### Do

- Import brand tokens through `brand/styles.css` in `layer(brand)` — never re-copy token blocks.
- Keep `color-scheme` and `data-theme` in sync (`lib/theme.ts` + inline bootstrap in `index.html`).
- Run `bun run astryx doctor` before shipping Astryx changes.
- Use `Card padding={0}` + explicit cell/header insets for full-bleed tables (see frontend-patterns).
- Mount `Dialog` always; toggle `isOpen` — never `return null` on close (focus restore).

### Don't

- Add universal resets or `*{padding:0}` inside `layer(brand)` or unlayered brand imports.
- Put surface Tailwind (`border*`, `bg-*`, `shadow*`, `rounded-*`) on Astryx primitives expecting them to win.
- Introduce shadcn/Radix or parallel primitive libraries.
- Fork token values in TS/TSX when a CSS var already exists.
- Toast errors while a `Dialog` is open — use inline `Banner` (top-layer stacking).

## Pre-ship checklist

- [ ] `bun run build:dashboard` clean
- [ ] `bun run typecheck` + `bun run lint` clean
- [ ] Light **and** dark theme manually checked on touched pages
- [ ] `/design-system` page — no padding/surface regressions
- [ ] `uv run pytest tests/e2e/dashboard/test_dashboard_visual.py -v` green (or update baselines with `UPDATE_DASHBOARD_SNAPSHOTS=1` after intentional visual change)
- [ ] New CSS imports assigned to the correct `@layer` (see cascade contract above)
- [ ] Astryx component swaps verified against `docs/standards/frontend-patterns.md`

## See also

- [`docs/standards/frontend-patterns.md`](../../docs/standards/frontend-patterns.md) — Astryx API gotchas (Stack stretch, Card padding, Dialog mount, Toast top-layer)
- [`artifacts/plans/astryx-migration.md`](../../artifacts/plans/astryx-migration.md) — migration driver and M₁ visual verification protocol
- [`brand/tokens/`](../../brand/tokens/) — Forge token SSOT
