# DESIGN.md — Roxabi Factory dashboard design system

Purpose: prescriptive doctrine for AI agents and humans changing UI in `apps/dashboard`. Read this **before** touching `apps/dashboard/src/index.css`, `brand/`, `apps/dashboard/src/astryx-theme/`, or `apps/dashboard/src/components/ui/`. Companion to the interactive catalog at `apps/dashboard/src/pages/DesignSystemPage.tsx` (route `/design-system`) — this file is the *why/rule*, that page is the *live proof*.

Scope: **system-level** visual/UI doctrine (tokens, cascade layers, color/spacing/elevation usage, component decision tree). Per-component Astryx **API gotchas** (Stack default-vertical/stretch, Card padding & full-bleed traps, Dialog focus-restore/`isOpen`, Toast `useToast`/top-layer) live in the companion `docs/standards/frontend-patterns.md` — read that when wiring a specific primitive. Behavioral/process rules live in `AGENTS.md`/`CLAUDE.md`. This file does not repeat either.

> Note: `frontend-patterns.md` documents per-component padding patterns (`pl-6`/`pr-6` edge cells, `Card padding={0}` full-bleed tables) — some were workarounds for the pre-#2152 cascade bug (§3, now fixed). Prefer the §3 layer contract over adding new per-component padding patches to mask layer mistakes.

---

## 1. Purpose & how to use this file

- Every rule below is grounded in the actual files in this repo, not generic design advice. If a rule and the code ever disagree, the code is currently right and this file is stale — fix the file, don't silently follow the drifted code.
- Read order for any UI change: **Do/Don't (§6)** for a fast lookup → **Component decision tree (§5)** to pick where the change goes → **How to add/restyle (§7)** for the mechanical steps → **Checklist (§9)** before shipping.
- This file exists because a real regression shipped: the Astryx migration (epic #2087, S0–S12) introduced an app-wide padding collapse and several off-brand defaults that survived CI green. §3 and §8 document the confirmed root causes as hard rules so they cannot recur silently.

---

## 2. Token pipeline & SSoT

**Single source of truth: `brand/tokens/*.css`.** Nothing else may hardcode a color/size/duration literal. Two independent consumption paths exist — a value intended for both must be wired in *both*:

```
brand/tokens/*.css  (SSoT: colors.css, typography.css, spacing.css, elevation.css, motion.css, status.css, fonts.css)
        │
        ├──► brand/styles.css → apps/dashboard/src/index.css `@theme inline` block
        │        → Tailwind semantic utilities (bg-card, text-muted-foreground, bg-primary, …)
        │
        └──► apps/dashboard/src/astryx-theme/roxabi.theme.ts  `tokens: {}` (maps brand var → Astryx token name)
                 → `bun run theme:build` (Astryx CLI)
                 → apps/dashboard/src/astryx-theme/built/{roxabi.theme.css, roxabi.js, roxabi.d.ts, roxabi.variants.d.ts}
                 → index.css imports built CSS · main.tsx `<Theme theme={roxabiTheme}>` consumes built JS
```

- `roxabi.theme.ts` is a **mapping file only** (`"--color-accent": "var(--accent)"`, never a literal). It `extends: neutralTheme` from `@astryxdesign/theme-neutral` and overrides **only 4 token families**: accent color, font family, radius, motion. Everything else (foreground-on-accent, elevation, semantic status colors, categorical palette) is inherited unmodified from `theme-neutral` — see §8.
- **Always consume the built artifacts** (`@/astryx-theme/built/roxabi`), never import `roxabi.theme.ts` source directly at runtime — `defineTheme({extends: neutralTheme})` re-resolves `icons = neutralTheme.icons` at import time, which would re-clobber the app's global Phosphor icon registry (`registerAppIcons()`, called once in `main.tsx`). This is why `roxabiTheme` deliberately has **no `icons:` key** (comment at `roxabi.theme.ts:54-64`, #2091).
- `theme:build` is manual and not wired into any build path — the `theme_build_drift` CI gate (`scripts/check-theme-build-drift.sh`) diffs a fresh build of `roxabi.theme.ts` against the committed `built/` tree and fails on drift. Edit source, rebuild, commit both.

### Exact Tailwind semantic vocabulary (`index.css:40-72`, `@theme inline`)

| Tailwind token | Sourced from | Note |
|---|---|---|
| `--color-background` | `var(--bg)` | |
| `--color-card` | `var(--bg-card)` | |
| `--color-popover` | `var(--bg-elevated)` | |
| `--color-primary` | `var(--accent)` | brand CTA orange |
| `--color-primary-foreground` | `var(--accent-on)` | |
| `--color-secondary` / `--color-muted` | `var(--bg-elevated)` | |
| **`--color-accent`** | **`var(--bg-card-hover)`** | **hover SURFACE tone — NOT the brand orange. See §6.** |
| `--color-brand` | `var(--accent)` | the actual brand CTA orange utility |
| `--color-brand-foreground` | `var(--accent-on)` | |
| `--color-brand-strong` | `var(--accent-hover)` | |
| `--color-destructive` | `var(--util-red)` | |
| `--color-border` | `color-mix(in srgb, var(--border-hi) 35%, transparent)` | |
| `--color-status-open/-closing/-error/-idle` | `brand/tokens/status.css` | reserved jobs-panel vocabulary, distinct from accent |
| `--font-sans` / `--font-mono` | `var(--font-body)` / `var(--font-mono)` | |
| `--radius-sm/-md/-lg` | `var(--r-sm/-md/-lg)` | |

---

## 3. The cascade-layer contract — anti-drift rule #1

`index.css:9` declares the **entire** layer order once, low→high priority:

```css
@layer reset, theme, base, astryx-base, astryx-theme, brand, components, utilities;
```

| # | Layer | Populated by |
|---|---|---|
| 1 | `reset` | `brand/reset.css` (`@import … layer(reset)` in `index.css:11`) — universal box reset, below `astryx-base` |
| 2 | `theme` | `tailwindcss/theme.css` |
| 3 | `base` | `tailwindcss/preflight.css` |
| 4 | `astryx-base` | `@astryxdesign/core/astryx.css` (self-wraps its ENTIRE contents in `@layer astryx-base{}` internally) |
| 5 | `astryx-theme` | `built/roxabi.theme.css` (theme tokens + component refinements) |
| 6 | `brand` | `brand/core.css` (imported with explicit `layer(brand)` in `index.css:24` — tokens + `base.css` only, **not** `styles.css`) |
| 7 | `components` | reserved, currently **empty** — no file writes into it |
| 8 | `utilities` | `tailwindcss/utilities.css` |

**Rule of CSS cascade layers**: for normal-priority declarations, **layer declaration order wins outright, before specificity is even considered.** A later-declared layer beats an earlier one regardless of selector specificity — a `*` selector in a later layer beats a `.deeply.chained.selector` in an earlier layer.

### The bug this rule prevents (#2148 — now fixed)

The universal box reset `*, *::before, *::after { box-sizing; margin: 0; padding: 0 }` used to live in `brand/base.css`, which is imported into `@layer brand` (via `brand/styles.css` → `index.css`). A nested bare `@import` inherits the importing sheet's layer — it does **not** escape to unlayered — so the reset landed inside `@layer brand` (position 6), *after* `astryx-base` (4) and `astryx-theme` (5).

Result: that zero-specificity reset **beat every Astryx component's own padding** (`padding-inline`/`padding-block` StyleX rules in `astryx-base`), regardless of StyleX's specificity-inflation. Every Astryx primitive (Button, Badge, Card, TextInput, …) computed `padding: 0` → cramped/clipped controls app-wide. **This was the confirmed root cause of the Astryx-migration quality regression.**

**The fix (#2148 / #2152, merged staging):** the universal reset was extracted from `brand/base.css` into `brand/reset.css`. Dashboard imports it as `@import "../../../brand/reset.css" layer(reset)` (`index.css:11`, position 1, below `astryx-base`), then imports `brand/core.css` into `layer(brand)` — **not** `brand/styles.css`, which would re-pull the reset into `layer(brand)` and recreate the bug. Marketing SSG keeps the single unlayered entry point (`brand/styles.css` = `reset.css` + `core.css`) unchanged. Verified: Astryx Button padding `0`→`8px 12px`, Badge `0`→`0 8px`; visual gate now covers `/design-system`.

**HARD RULE: no universal reset selector (`*`, `*::before`, `*::after`, or any catch-all) may live in a layer positioned after `astryx-base`** (i.e. `brand`, `components`, `utilities`). If you need a global reset:
- put it in `brand/reset.css` and import with `layer(reset)` (position 1, *before* `astryx-base`) so Astryx's own layers can legitimately win over it, or
- scope it away from Astryx subtrees, or
- drop it — Astryx's own `@astryxdesign/core/reset.css` (`index.css:14`) already normalizes box-sizing/margin/padding for everything Astryx renders; a second unscoped reset in the wrong layer is redundant risk, not defense in depth.

`brand/base.css` intentionally has no universal reset (a comment there points here). **Never re-introduce a universal selector into `brand/base.css` or any file imported as `layer(brand)`** — keep globals in `brand/reset.css` + `layer(reset)`, or scope them.

### The correct way to use unlayered precedence (contrast case)

`index.css:102-117` (`textarea[data-mono]`) is a deliberately unlayered, single-property, narrowly-scoped override that intentionally beats Astryx's StyleX font-family on one attribute-selected element — documented inline with the exact mechanism. This is the *safe* pattern: **narrow selector + single property + explained why**, vs. the bug's *unsafe* pattern: **universal selector + multiple properties + no awareness of layer position**. When you need to override Astryx, prefer this narrow-unlayered-rule style over adding anything to `brand`/`components`/`utilities` that could touch elements it wasn't meant for.

---

## 4. Token usage — color, typography, spacing, elevation, motion

### Color

- Brand accent (Forge Orange) = `--accent` (`#e85d04` dark / `#c2410c` light) with `-hover`/`-press`/`-dim`/`-glow` steps. **One brand signal per view.**
- Foreground-on-accent = `--accent-on` (`#fafafa` dark / `#ffffff` light, `brand/tokens/colors.css:34,75`). Astryx's own equivalent token is `--color-on-accent` (not "accent-foreground") — see §8 for the current wiring gap.
- Surfaces ladder deep→elevated: `--bg` → `--bg-elevated` → `--bg-card` → `--bg-card-hover`. Use the ladder for depth, not shadows, in the default UI (elevation tokens exist but are largely unconsumed — §8).
- Utility semantics (`--util-teal/-green/-amber/-red/-plum/-telegram/-discord`) are for docs/diagrams/status dots — **not** brand accent substitutes.
- Status vocabulary (`--status-open/-closing/-error/-idle`) is reserved for the jobs panel — distinct namespace from accent, do not conflate.

### Typography

Inter (body) · Outfit (headings) · JetBrains Mono (job IDs, logs, code). Chakra Petch is marketing-only, not loaded in the dashboard.

| Token | Value | Use |
|---|---|---|
| `--fs-display` | 2.25rem (36px) | hero numerals only |
| `--fs-h1` | 1.5rem (24px) | page title |
| `--fs-h2` | 1.25rem (20px) | section title |
| `--fs-h3` | 1.0625rem (17px) | card/subsection title |
| `--fs-lead` | 1rem (16px) | lead paragraph |
| `--fs-body` | 0.9375rem (15px) | default body |
| `--fs-sm` | 0.8125rem (13px) | secondary/meta text |
| `--fs-mono` | 0.75rem (12px) | `.mono` class, code |
| `--fs-micro` | 0.625rem (10px) | `.eyebrow` labels |

Weights: `--fw-regular`400 / `-medium`500 / `-semibold`600 / `-bold`700 / `-heavy`800. Tracking: `--tracking-display`-0.03em / `-tight`-0.02em / `-mono`0.1em. Leading: `--leading-tight`1.1 / `-snug`1.35 / `-body`1.6.

### Spacing & radius

Base unit 4px: `--space-1..16` = 4/8/12/16/20/24/32/40/48/64px, aliased `--s-xs`(4)…`--s-2xl`(64). Radius: `--r-sm`4px / `--r-md`8px / `--r-lg`12px / `--r-xl`20px / `--r-full`9999px. Astryx radius mapping (`roxabi.theme.ts:40-45`): `--radius-inner→r-sm`, `--radius-element→r-md`, `--radius-container→r-lg`, `--radius-chat→r-lg`, `--radius-page→r-xl`. Layout constants: `--maxw`1440px, `--header-h`56px, `--sidebar-w`360px.

### Elevation

Tokens exist (`brand/tokens/elevation.css`): `--shadow-sm`, `--shadow-md`, `--shadow-panel`, `--glow-accent`, `--glow-ember`. **They are currently unconsumed anywhere in source or the compiled bundle** — not wired into `roxabi.theme.ts`, not referenced by any component. This predates Astryx (it was never wired pre-migration either) — a standing gap, not a regression. Astryx surfaces get elevation today from `theme-neutral`'s own generic `--shadow-low/-med/-high` (not brand-tinted). See §8/§6 for the rule on what to do about this.

### Motion

`--dur-micro`120ms / `--dur-short`200ms / `--dur-long`420ms, `--ease-out`cubic-bezier(.16,1,.3,1) / `--ease-in-out`cubic-bezier(.45,0,.55,1). Astryx mapping: `--duration-fast/-medium/-slow` ← micro/short/long, `--ease-standard` ← ease-out. **Restraint is a rule, not a vibe**: this is a dense instrument-panel cockpit UI — use motion for state feedback (hover, toggle, loading), never decoratively. If a component doesn't already animate in Astryx, don't add motion to make it "feel alive."

---

## 5. Component decision tree

```
Need a UI element?
│
├─ Does @astryxdesign/core export a primitive for it?
│   (Card, Badge, Banner, Selector, TextInput, TextArea, Table, Skeleton,
│    Stack, Text, Toast, ClickableCard, ToggleButton, Divider, Button, …)
│   │
│   ├─ YES, and no app-specific API/behavior needed
│   │     → use it directly. No wrapper. (e.g. PresenceBadge uses Astryx Badge inline.)
│   │
│   └─ YES, but ≥3 call-sites need a stable/simplified API, or app-specific
│      behavior the primitive doesn't have (responsive collapse, extra field,
│      legacy prop-shape compat)
│         → wrap it in apps/dashboard/src/components/ui/*.tsx, composing the
│           Astryx primitive INTERNALLY (100% Astryx, never Radix/shadcn).
│           Document WHY at the top of the file.
│           (button.tsx, filter-chip.tsx, segmented-control.tsx,
│            popover-select.tsx, separator.tsx, sortable-table-header.tsx,
│            list-toolbar.tsx are all this pattern.)
│
└─ NO Astryx primitive exists for this pattern at all
      → hand-roll with Tailwind utilities bound to the semantic vocabulary
        in index.css `@theme inline` (bg-card, border-border,
        text-muted-foreground, rounded-xl, …). Never raw hex/px.
        (empty-state.tsx is the one legitimate example — no Astryx
         EmptyState primitive exists.)
```

Never: reach for a new dependency (Radix, another UI kit) when Astryx or Tailwind-on-tokens covers it. Never: recreate an Astryx component's internals from raw markup because a variant is "close but not quite" — see §6 on the no-bordered-variant constraint.

Note on `list-toolbar.tsx`: it deliberately does **not** use Astryx's `Toolbar` — `Toolbar` wires arrow-key roving focus over descendant buttons/inputs, which would double-navigate against `SegmentedControl`'s own roving tabindex nested inside it. Plain flex containers are correct here. This is documented in the file — read existing wrapper comments before assuming "should use the Astryx X" is always right.

---

## 6. Do / Don't

**Do**
- Consume tokens via `var(--token)` or Astryx's own generated CSS vars — never hardcode hex/px/ms in product code.
- Use `bg-primary`/`text-primary-foreground` or `bg-brand`/`text-brand-foreground` for the Forge-orange CTA color in Tailwind-styled elements.
- Wrap an Astryx primitive in `components/ui/` only when ≥3 call-sites need a stable API or genuine app-specific behavior — document why at the top of the file.
- Run `bun run --cwd apps/dashboard theme:build` and commit the refreshed `built/` artifacts whenever `roxabi.theme.ts` changes.
- Check WCAG contrast (≥4.5:1 normal text, ≥3:1 large text/UI) whenever you pair a new background token with a foreground token — compute it, don't eyeball it.
- Add new component demos to `DesignSystemPage.tsx` when you add a new pattern — it's the living proof, not just documentation.

**Don't**
- Don't add a universal selector reset (`*`, `*::before`, `*::after`) to any file loaded into the `brand`, `components`, or `utilities` layers — it will beat every Astryx component style regardless of specificity. (See the confirmed pre-#2152 `brand/base.css` bug in §3 — reset now lives in `brand/reset.css` + `layer(reset)`; do not replicate the old pattern.)
- Don't assume Tailwind's `accent`/`bg-accent`/`text-accent` utility is the brand orange — `--color-accent` in `@theme inline` (`index.css:53`) maps to `var(--bg-card-hover)`, a hover **surface** tone, not the CTA color. Use `primary`/`brand` for the orange.
- Don't hand-roll a `border` CSS override to fake a bordered Button/Badge — Astryx v0.1.2 ships **zero** bordered variants anywhere (`borderWidth:0` baked into every variant). Design around it (§8), don't fight the primitive with overrides that will silently break on the next `theme:build`.
- Don't invent ad hoc `box-shadow`/`--glow-*` values on a leaf component to "add depth" — brand elevation tokens exist but are intentionally not yet wired into Astryx (§4/§8). Wire them centrally in `roxabi.theme.ts` via a reviewed change, never per-component.
- Don't import `roxabi.theme.ts` (the TS source) at runtime anywhere except the `theme:build` pipeline — always consume `@/astryx-theme/built/roxabi`.
- Don't add an `icons:` key to `roxabiTheme`'s config — it will silently override the app's global Phosphor icon registry on every render.
- Don't add a second/duplicate global reset "to be safe" — Astryx's own `reset.css` already covers everything Astryx renders.
- Don't hand-edit `apps/dashboard/src/astryx-theme/built/*` — it's generated. Edit `roxabi.theme.ts`, then rebuild.
- Don't shrink Astryx component padding/sizing via override classes to "fix" cramped controls — that papers over cascade-layer mistakes (§3) instead of fixing them. Padding collapse from #2148 is fixed on staging; if controls still look cramped, check layer placement first.

---

## 7. How to add or restyle a component

1. Check whether `@astryxdesign/core` already has the primitive/variant (grep `node_modules/@astryxdesign/core/src/`, or check what's already demoed in `DesignSystemPage.tsx`).
2. If a variant is close-but-not-exact, use the closest existing Astryx variant (§8 workarounds) — don't override internals.
3. Need an app-specific API surface or behavior? Add a thin wrapper in `apps/dashboard/src/components/ui/`, composing the Astryx primitive internally. Document the rationale at the top of the file.
4. Genuinely no Astryx equivalent? Hand-roll with Tailwind utilities against the `@theme inline` semantic vocabulary — never raw hex/px.
5. Changing a brand value (color/font/radius/motion/spacing/elevation)? Edit the SSoT in `brand/tokens/*.css` — never hardcode it downstream.
6. Changing how a brand value maps into Astryx? Edit `apps/dashboard/src/astryx-theme/roxabi.theme.ts`'s `tokens: {}` block — always `var(--brand-token)`, never a literal, and if you add a background/color token, also wire its foreground pair (§6) with a contrast check.
7. Run `bun run --cwd apps/dashboard theme:build` → regenerates `src/astryx-theme/built/{roxabi.theme.css,roxabi.js,roxabi.d.ts,roxabi.variants.d.ts}`. Commit these — CI's `theme_build_drift` gate (`scripts/check-theme-build-drift.sh`) diffs a fresh build against the committed copy and **fails the pipeline** on drift.
8. Add/update the demo in `DesignSystemPage.tsx` so the change is visually reviewable in the catalog.
8b. **If your change alters rendering** (spacing, color, layout), regenerate the cockpit visual-regression baseline and **eyeball it** before committing: `bun run build:dashboard` then `UPDATE_DASHBOARD_SNAPSHOTS=1 uv run pytest tests/e2e/dashboard/test_dashboard_visual.py` (writes `tests/e2e/dashboard/snapshots/cockpit-{dark,light}.png`; regenerate with the pinned playwright so it matches CI). CI's `ci` job runs this gate at 3% tolerance. **Never re-bless a snapshot you haven't looked at** — re-baselining to a regressed render is exactly how the padding bug shipped green (see the root-cause review).
9. Run Astryx's own health check before pushing: `node apps/dashboard/node_modules/@astryxdesign/cli/bin/astryx.mjs --json doctor` (mirrors CI's `astryx_doctor` gate — catches Node-floor, core↔cli version misalignment, missing peer deps; only `status:"fail"` blocks, warnings are tolerated).
10. `bun run lint` (Biome, `lint_js`) + `bun run --filter @roxabi-factory/dashboard test` (Vitest, `dashboard_unit_test`, pre-push) + `bun run build:dashboard` (`dashboard_build`, CI) must pass.

---

## 8. Known constraints & workarounds (Astryx v0.1.2)

| Constraint | Detail | Workaround / rule |
|---|---|---|
| No bordered variant | `Button`/`Badge` render `border:0` on every variant, no exceptions | `button.tsx` maps shadcn `outline`→Astryx `ghost` (transparent, preserves fill-vs-transparent contrast; `secondary` would flatten it). Badge has no outline option at all — `DesignSystemPage.tsx` maps both "Secondary" and "Outline" demo labels to `variant="neutral"` intentionally, not a bug. If a real border is required: use a wrapping container that draws the border around a `ghost`/plain control — never override the primitive's `border` |
| Only 4 token families wired | `roxabi.theme.ts` overrides accent-color, font, radius, motion only | `--color-on-accent` (Astryx's real foreground token), elevation (`--shadow-*`/`--glow-*`), and semantic status colors are all still `theme-neutral` defaults, not brand. Wiring `--color-on-accent` to brand's `--accent-on` naively would *regress* dark-mode contrast (5.12:1→3.35:1, failing AA) — check contrast before wiring, don't blindly copy the brand token |
| Elevation dead | Brand `--shadow-*`/`--glow-*` tokens loaded but zero consumers | Don't add one-off shadows on leaf components; wire centrally in `roxabi.theme.ts` if brand elevation is genuinely needed |
| Icon registry ownership | `<Theme>` re-registers `theme.icons` on every render | `roxabiTheme` has no `icons:` key on purpose (#2091); icons owned exclusively by `registerAppIcons()` in `main.tsx`, called once at boot |
| Built-vs-source import | Importing `roxabi.theme.ts` source directly re-clobbers icons | Always import `@/astryx-theme/built/roxabi`, never the `.ts` source, outside the build pipeline |
| `components` layer unused | Declared in `index.css:9`, position 7, nothing writes into it | Reserved home for hand-authored component-scoped CSS that is neither Tailwind utilities nor Astryx StyleX — use it if that need ever arises, don't invent a new layer |
| `Separator` a11y delta | Old Radix wrapper defaulted `role="none"` (hidden); Astryx `Divider` always renders `role="separator" aria-orientation` | Pass `aria-hidden` explicitly for purely decorative dividers |
| `ListToolbar` ≠ Astryx `Toolbar` | `Toolbar`'s roving-focus model would double-navigate against a nested `SegmentedControl`'s own roving tabindex | Plain flex containers, Astryx primitives only for the leaf controls inside |
| Brand reset vs Astryx layers | Universal reset imported into `layer(brand)` (pre-#2152) outranked `astryx-base` | **Fixed — `brand/reset.css` + `layer(reset)`; see §3. Never import `brand/styles.css` into dashboard `layer(brand)`** |

---

## 9. Checklist an agent runs before shipping UI

1. Did I check `DesignSystemPage.tsx` / `@astryxdesign/core` for an existing primitive before hand-rolling markup?
2. Did I use semantic Tailwind classes (`bg-card`, `text-muted-foreground`, `border-border`, `bg-primary`/`bg-brand` for CTA) — zero raw hex/px in the diff?
3. If I touched CSS outside a component's own props: which `@layer` does it land in, and does it avoid a universal selector that could beat `astryx-base`/`astryx-theme` (§3)?
4. If I touched `roxabi.theme.ts`: did I run `bun run --cwd apps/dashboard theme:build` and commit `built/`?
5. If I added/changed a background color token: did I wire its matching foreground token and verify WCAG contrast (≥4.5:1 normal / ≥3:1 large+UI)?
6. Did I avoid faking a bordered variant, a one-off shadow, or any other override that fights an Astryx primitive instead of designing around its actual capabilities (§8)?
7. Did I add/update the demo in `DesignSystemPage.tsx` for any new pattern?
8. `astryx doctor` clean (no `fail` checks)? `bun run lint`, dashboard Vitest suite, `bun run build:dashboard`, and the `tests/e2e/dashboard/` visual-regression gate all green? If my change altered rendering, did I regenerate **and eyeball** the cockpit snapshots (never rubber-stamp)?
9. Am I about to add padding/margin/sizing overrides to compensate for cramped Astryx controls? If yes — stop and verify cascade-layer placement (§3) first; don't mask a layer-order regression with per-component patches.
