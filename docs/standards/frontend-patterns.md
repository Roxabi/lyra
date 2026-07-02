---
title: Frontend Patterns — factory
description: Astryx component-API gotchas for the dashboard SPA, captured during the shadcn/Radix → Astryx migration.
---

# Frontend Patterns — factory

> Status: LIVING
> Scope: `apps/dashboard/` — durable Astryx component-API facts, not migration status
> Context: dashboard is migrating shadcn/Radix → Astryx (`@astryxdesign/core`, epic #2087); this
> doc captures API behavior worth remembering past any single slice. For migration progress/plan
> see `artifacts/plans/astryx-migration.md`.

Astryx names and props are **not** 1:1 with shadcn — always pre-flight a component before
swapping it in: `bun run astryx component <X> --props --source`. The CLI's `--props` output has
been observed to *omit or truncate* real prop unions (see `Stack.direction` and `Text.as`
below) — when in doubt, check the `.d.ts`, not just the CLI summary.

## `Stack` <!-- drift-ignore -->

- **Defaults to vertical** (`direction="vertical"`, `flexDirection: column`). For a horizontal
  row (title + action, a button row) you must pass `direction="horizontal"` explicitly — with
  the default, `justify="between"` maps to the vertical axis and children stack instead of
  sitting side by side.
- **Default cross-axis is `stretch`.** Any small, natural-width child (a `Badge`, a `Button`) <!-- drift-ignore -->
  that is a *direct* child of a vertical `Stack` renders full-width — badges become bars, submit <!-- drift-ignore -->
  buttons span the card. Fix with `align="start"` on the `Stack` (when all children should stay <!-- drift-ignore -->
  natural-width) or `className="self-start"` on the one element that shouldn't stretch
  (unlayered Tailwind wins over Astryx's layered CSS by cascade-layer position, not
  specificity). This is the old shadcn `block` → Astryx `flex` layout-flow trap: `CardContent` <!-- drift-ignore -->
  used to be `block` (children shrink-to-fit); `Stack` stretches all children by default. <!-- drift-ignore -->

## `Card` <!-- drift-ignore -->

- Bare container — no `CardHeader`/`CardTitle`/`CardDescription`/`CardContent`/`CardFooter` <!-- drift-ignore -->
  compound API. Default padding is unconditional (not header-aware zero-out), roughly
  `spacing-3` (~12px) via the `roxabi`/`neutralTheme` token, applied per side regardless of
  content.
- Accepts `className`, but surface styles (border, shadow, background, radius) are
  StyleX-layered (`@layer astryx-base`) — an unlayered Tailwind surface class does **not** win
  by specificity. Drop surface Tailwind (`border*`, `shadow*`, `bg-*`, `rounded-*`); keep only
  layout Tailwind (`grid`, `max-w-*`, `col-span-*`, `overflow-*`).
- `Text`: `type` maps `label`→title, `supporting`→description, plus `body`/`large`/`display-*`. <!-- drift-ignore -->
  `as` **does** accept `h1|h2|h3` (the CLI `--props` summary truncates this union — verify via
  the `.d.ts`) — set `as="h3"` on card titles to keep real heading semantics.
- `ClickableCard` (`label` required + `onClick`) is the interactive variant. <!-- drift-ignore -->
- **Full-bleed trap:** an old `<CardContent className="p-0">` that let a table/list render
  edge-to-edge (cells carrying their own `px-*`) breaks under `Card`'s unconditional default <!-- drift-ignore -->
  padding — the table ends up double-padded and misaligned against the title. Fix:
  `<Card padding={0}>` + inset the header row itself via `className="px-6 pt-6"` (keeps the
  title aligned with the table's own `px-6` cells; the table stays flush). Note `padding` is
  neither a surface nor a layout class — it doesn't fall under the "drop surface / keep layout"
  rule above; it's a real Astryx prop.
- If a compositional `Table` sits inside a `Card padding={0}`: Astryx's `TableCell` edge <!-- drift-ignore -->
  compensation is `max(var(--container-padding-inline-start), 8px)`; `Card padding={0}` zeroes
  that CSS var, so it collapses to the 8px floor (not the intended density value) and edge
  columns sit ~16px inside a `px-6` sibling header. Restore alignment with explicit
  `pl-6`/`pr-6` on the first/last cells (unlayered Tailwind wins here too).

## `Dialog` / `AlertDialog` <!-- drift-ignore -->

- Native `<dialog>` + `showModal()` — free modal focus-trap, title-focus-on-open, and
  focus-restore-to-opener for free.
- Props: `isOpen` (**not** `open`), `onOpenChange`, `purpose` (`'required'|'form'|'info'`),
  `width`, `maxHeight`, `padding`. Header content is a separate **`DialogHeader`** child <!-- drift-ignore -->
  (`title` — rendered as a focus-on-open `h2` — `subtitle`, and `onOpenChange` which wires its
  own close-X button); `Dialog` itself takes no `title`/`description` props. <!-- drift-ignore -->
  `AlertDialog` (`title`/`description`/`actionLabel`/`onAction`/`actionVariant="destructive"`, <!-- drift-ignore -->
  focus-on-cancel, no outside-dismiss) is for destructive confirmations only.
- **Blocker-class gotcha:** `Dialog` renders `<dialog>{children}</dialog>` **unconditionally**, <!-- drift-ignore -->
  even while closed — the browser hides it via UA CSS. Do **not** gate the consumer with
  `if (!isOpen) return null`: that unmounts the `Dialog`, and its close effect (which calls <!-- drift-ignore -->
  `dialog.close()` and restores focus to the opener) has no unmount cleanup — it only runs when
  `isOpen` flips to `false` *while still mounted*. Unmounting on close silently drops
  focus-restore, an accessibility regression that negates the point of migrating to a native
  dialog. Keep the `Dialog` mounted at all times and let `isOpen` toggle. <!-- drift-ignore -->
  - Consequence: a closed dialog's content (form fields, pickers, default values) stays live in
    the DOM. Scope page-level test queries away from it, e.g.
    `.filter(el => !el.closest("dialog"))` — the same class of trap as an always-mounted
    Popover.
- **No accessible name by default:** `Dialog`/`DialogHeader` do not wire `aria-labelledby` to <!-- drift-ignore -->
  the title (`DialogHeader`'s heading has no `id`), so the modal is unnamed to assistive tech. <!-- drift-ignore -->
  Fix from the call site: pass `aria-label={title}` directly to `<Dialog>` — it spreads onto the
  native `<dialog>` element.
- `DialogHeader`'s close-X button label is hardcoded English `"Close"` with no override prop — <!-- drift-ignore -->
  tracked as a known gap, not a migration regression (the bespoke pre-Astryx dialog also used
  `aria-label="Close"`).
- `purpose="form"` sets `allowBackdropClick=false` (only Escape or the close button dismiss —
  correct for input forms, prevents accidental data loss from an outside click);
  `purpose="info"` allows backdrop dismiss.
- **jsdom:** `<dialog>.showModal()` throws "Not implemented" — polyfill
  `HTMLDialogElement.prototype.showModal`/`show` (set `open = true`) and `close` (set
  `open = false` + dispatch a `close` `Event`) in `test-setup`. Existing render→fill→submit <!-- drift-ignore -->
  dialog tests pass unchanged once polyfilled.

## `Toast` <!-- drift-ignore -->

- `useToast()` returns `showToast` **directly** (not `{ showToast }`).
- `showToast({ body, type })` returns a dismiss function. `type` is `'info' | 'error'` **only**
  — there is no `success` variant; map an old `toast.success(...)` call to `type: 'info'`.
- `<ToastViewport position="bottomEnd">` (`topEnd`/`topStart`/`bottomEnd`/`bottomStart` — no
  `center`) replaces the old `<Toaster>`. It is a context-provider wrapper (renders
  `ToastContext.Provider` + the toast stack, no wrapping DOM element of its own) and **must be
  an ancestor of every `useToast` caller** — mount it once near the app shell.
- `isTopLayer` defaults to `true`, but this does **not** guarantee a toast paints above a
  *later-opened* modal `<dialog>`: CSS top-layer stacking is by promotion order.
  `ToastViewport` promotes once at mount time (before any dialog exists); `Dialog.showModal()` <!-- drift-ignore -->
  promotes the dialog later, so the dialog paints above and inerts the toast viewport
  underneath it. An error toast fired while a dialog is open is effectively hidden.
  - **Fix:** surface dialog-context mutation errors **inline** (a `Banner` driven off the <!-- drift-ignore -->
    mutation's error state), not via toast. Keep success toasts as-is — the dialog closes
    first, so the toast is visible afterward.
- `useToast()` has a lazy fallback when called under a `LayerProvider`/`AppShell` ancestor, but <!-- drift-ignore -->
  **warns** ("No LayerProvider found") if no explicit `ToastViewport` is mounted. In unit tests, <!-- drift-ignore -->
  wrap render helpers in `<ToastViewport>` (or mock `useToast` to a spy asserting the
  `{ body, type }` payload).

## Cross-cutting: dependency-removal PRs and the base-branch merge trap

When a slice deletes a cross-cutting dependency (e.g. removing `sonner` after migrating to
Astryx `Toast`) and `staging` gains a *new* call-site of that dependency between your branch <!-- drift-ignore -->
point and merge, the local worktree (behind `staging`) builds clean — but PR CI builds
`branch + base` merged (`refs/pull/N/merge`), so the removed symbol resurfaces as a build/test
failure that only appears post-push. Before pushing any dependency-removal PR: `git fetch &&
git rebase origin/staging`, re-grep the removed symbol across the whole `src` tree, then push
`--force-with-lease` (sanctioned specifically for this case — the project's general
no-force-push convention targets bare `--force`, see `AGENTS.md`).

## See also

- `artifacts/plans/astryx-migration.md` — migration driver, slice plan, and M₁ post-merge visual
  verification protocol (epic #2087)
- `docs/standards/backend-patterns.md` — the backend equivalent of this doc
