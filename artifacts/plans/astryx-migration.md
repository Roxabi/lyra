# Astryx migration — /goal driver

> Epic **#2087** · dashboard `apps/dashboard`: **shadcn/Radix → Astryx** (`@astryxdesign/core`, facebook/astryx).
> Sweep driver for `/goal`: strict-sequential, one slice PR at a time, `/code-review` + `/fix` per PR, **M₁ post-merge visual verification** before the next slice unblocks.

## Goal

Migrate the dashboard **fully** to Astryx — primitives **and** shell. End state = 100% Astryx, no permanent bespoke UI layer. Purpose: evaluate/adopt Astryx (Meta OSS, React + StyleX, agent-ready CLI) in depth.

Hands-on verified (2026-07-01): installs clean under **bun 1.3.14**, **React 19** peer OK, `astryx doctor` green (detects bun), coexists with **Tailwind v4** via cascade layers, and the earlier "gaps" (FilterChip / ListToolbar / SortableTableHeader) **dissolve** (`ToggleButton`/`Token`, `Toolbar`, `Table` + `useTableSortable`). Astryx `Icon` accepts any SVG → Phosphor glyphs render through it.

## Locked decisions

| # | Decision |
|---|---|
| 1 | Scope **100% Astryx** incl. shell (`AppShell/TopNav/SideNav/MobileNav`) |
| 2 | Theme = custom `roxabi` via `defineTheme` sourced from `brand/` tokens |
| 3 | Toast → Astryx `useToast`/`showToast` (success→info; only info/error exist) |
| 4 | Icons: Phosphor glyphs, rendered via Astryx `Icon` + `registerIcons` |
| 5 | Versioning: caret `^0.1.2`, lockfile committed, **no exact pin** |
| 6 | CLI in CI: `astryx doctor` gate + `setup-node` Node 24; `upgrade` codemods stay manual |

## Slices (native blocked-by chain — `/goal` sweeps one at a time)

| Slice | Issue | Size | blocked-by |
|---|---|---|---|
| 0 Foundation PoC (install + `@layer` + `Theme` + Separator→Divider + CI gate) | **#2088** | S | — (ready) |
| 1 Theme `roxabi` (defineTheme, dark/light reconcile) | #2089 | L | #2088 |
| 2 Shell (AppShell/TopNav/SideNav/MobileNav) | #2090 | L | #2089 |
| 3 Icons (Astryx Icon + registerIcons) | #2091 | M | #2090 |
| 4 Primitives (Avatar, DropdownMenu, Skeleton, TextArea) | #2092 | M | #2091 |
| 5 Badge + Alert→Banner | #2093 | M | #2092 |
| 6 Card family | #2094 | M | #2093 |
| 7 Inputs (Field+TextInput, SegmentedControl, Selector) | #2095 | L | #2094 |
| 8 Dialog + AlertDialog | #2096 | M | #2095 |
| 9 Toast (sonner→useToast) | #2097 | M | #2096 |
| 10 Table/Toolbar/Chip | #2098 | L | #2097 |
| 11 Button + LinkProvider | #2099 | L | #2098 |
| 12 Cutover (delete shadcn bridge + deps) | #2100 | M | #2099 |

Only `blocked_by == 0` open slices are sweep-ready → at any moment exactly one slice is actionable, in order.

## Per-slice loop

```
1. Worktree (reuse .worktrees/astryx — never re-run setup-worktree, re-branch in place):
   cd /home/mickael/projects/roxabi-factory/.worktrees/astryx    # absolute path (cwd-drift trap)
   git fetch origin
   git checkout -b feat/astryx-sN origin/staging                 # fresh base = slice N-1 merged
   bun install --frozen-lockfile
2. Implement the slice scope (pre-flight `bun run astryx component <X> --props --source` before each swap).
3. PR → base staging, body `Closes #<sliceIssue>` (do NOT link the epic #2087).
4. /code-review  → /fix  → gates green (dashboard_build, dashboard_unit_test, lint_js/Biome, typecheck).
5. Merge (merge-commit, not squash).
6. **M₁ post-merge visual verification** (below) — light + dark.
7. Next slice unblocks automatically; repeat.
```

Worktree traps (recorded): a worktree can't `checkout staging` (main holds it) → branch off `origin/staging`; missing `fetch` → stale base → broken wiring; `bun run`/install in-worktree churns `bun.lock` (intended in S0, otherwise `git restore bun.lock` + stage explicitly).

## M₁ post-merge visual verification (protocol)

M₁ (`roxabituwer`, 192.168.1.16) **auto-pulls + redeploys** on every staging merge:

```
merge → staging  ─┐
                  ├─ publish.yml builds & pushes  ghcr.io/roxabi/factory:staging-svc   (~few min)
                  └─ M₁: podman autoupdate=registry + factory-quadlet-sync `make converge`  (~5 min cadence)
                        → factory-dashboard restarts on the new image
```

Dashboard is served on **M₁ tailnet:8765** (`PublishPort=${TAILSCALE_IPV4}:8765:8765`, tailnet-only). URL (confirmed reachable, HTTP 200):

- `http://roxabituwer:8765` (MagicDNS) · `http://roxabituwer.goose-logarithm.ts.net:8765`

**Steps**

1. Watch the image build: `gh run watch` on the `publish` workflow until `staging-svc` is pushed.
2. Confirm M₁ took the new image (don't trust timing alone):
   - `ssh roxabituwer 'podman auto-update --dry-run'` → factory-dashboard should show an update pending/applied.
   - `ssh roxabituwer 'systemctl --user show -p ActiveEnterTimestamp factory-dashboard'` → restart timestamp is post-merge.
3. Open `http://roxabituwer:8765`, **hard-refresh** (bust SPA cache).
4. Visual checklist for the slice:
   - [ ] **Light + dark** both correct (toggle) — no unstyled/inverted cascade (the `@layer` order is load-bearing).
   - [ ] The migrated component(s) render + behave (focus, keyboard, hover).
   - [ ] **DesignSystemPage** (component showcase) shows no regression.
   - [ ] The feature pages that consume the migrated component(s) are intact.
   - [ ] No console errors from Astryx theme/registry.
5. If broken: revert the slice PR (previous `staging-svc` image is still tagged); do **not** advance to the next slice.

Slice-specific visual focus: S1 theme colors/radius across the app · S2 nav/shell layout + mobile drawer · S3 every icon glyph present · S7/S10 listbox/table ARIA + mobile · S12 full page-by-page diff.

## Running the sweep

`/goal` (readiness = native blocked-by only; prose refs are invisible). It will pick **#2088** first. Between slices, the M₁ visual gate above is the human/agent checkpoint before the next slice is worked.
