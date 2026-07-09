---
title: Astryx migration — analyse pré-décision, REX & comparaison shadcn v2
date: 2026-07-09
epic: "#2087"
scope: apps/dashboard (v1 Astryx) · apps/dashboard-v2 (greenfield shadcn, branche séparée)
status: recap consolidé (sources primaires listées en annexe)
---

# Astryx migration — analyse pré-décision, REX & comparaison shadcn v2

Recap consolidé à partir des artefacts du repo et de l'historique git `staging` (juillet 2026).

---

## 1. Où est l'analyse pré-Astryx ?

**Document de référence :** [`artifacts/plans/astryx-migration.md`](../plans/astryx-migration.md) (epic **#2087**).

L'évaluation pré-migration n'est pas un fichier isolé : elle est intégrée au plan de migration, validée en PoC le **2026-07-01** avant le slice S0 (#2088).

### Synthèse de l'analyse pré-migration

**Objectif :** évaluer/adopter Astryx en profondeur — migration **100 %** (primitives + shell), pas de couche bespoke permanente.

**Verdict PoC (2026-07-01) :**

| Critère | Résultat |
|---|---|
| Install sous **bun 1.3.14** | Clean |
| **React 19** peer | OK |
| `astryx doctor` | Green (détecte bun) |
| Coexistence **Tailwind v4** | OK via cascade layers |
| Gaps initiaux shadcn | **Dissolus** (voir mapping ci-dessous) |
| Icons Phosphor | OK via `Icon` + `registerIcons` |

**Gaps shadcn → équivalents Astryx** (blocage initial avant PoC) :

| Composant bespoke shadcn (v1) | Équivalent Astryx |
|---|---|
| `FilterChip` | `ToggleButton` / `Token` |
| `ListToolbar` | `Toolbar` |
| `SortableTableHeader` | `Table` + `useTableSortable` |

**Décisions verrouillées :**

1. Scope 100 % Astryx incl. shell (`AppShell/TopNav/SideNav/MobileNav`)
2. Thème custom `roxabi` via `defineTheme` depuis `brand/`
3. Toast → `useToast`/`showToast` (success→info ; seuls info/error existent)
4. Icons Phosphor via Astryx `Icon` + `registerIcons`
5. Versioning caret `^0.1.2`, lockfile commité, pas de pin exact
6. CI : `astryx doctor` gate + Node 24 ; upgrade codemods manuels

**Plan :** 12 slices séquentiels (S0 fondation → S12 cutover shadcn), avec **vérif visuelle M₁** post-merge obligatoire entre chaque slice (protocole dans le plan).

---

## 2. REX migration Astryx (post-mortem)

**Documents primaires :**

- [`artifacts/analyses/astryx-dashboard-quality-regression.claude.md`](astryx-dashboard-quality-regression.claude.md) (2026-07-02)
- [`apps/dashboard/DESIGN.md`](../../apps/dashboard/DESIGN.md) (doctrine forward)
- [`artifacts/analyses/2026-07-03-dashboard-uxui-audit.claude.md`](2026-07-03-dashboard-uxui-audit.claude.md) (audit UX post-migration)
- [`docs/standards/frontend-patterns.md`](../../docs/standards/frontend-patterns.md) (gotchas API Astryx)

### Ce qui a bien marché

- **Approche slice par slice** : dé-risquage progressif, coexistence shadcn/Astryx pendant S0–S11
- **PoC S0** : cascade layers + `<Theme>` sans casser le shadcn restant (foundation byte-identical)
- **CI** : `astryx doctor` + `theme_build_drift` = garde-fous utiles
- **Fondations** : tokens brand→Astryx, i18n, landmarks a11y, `EmptyState` — le socle tient (audit UX 03/07 : *« setup à ~80 % »*)

### Causes racines (qualité, 02/07)

| # | Cause | Sévérité | Statut |
|---|---|---|---|
| **1** | Reset universel `*{padding:0}` dans `@layer brand` **après** `astryx-base` → padding 0 sur tous les primitives | Critical | **Fixé** (#2152 / `brand/reset.css` → `layer(reset)`) |
| **2** | Table auto-layout collapse (`max-w` sur `<td>`) → Fleet/Agents cassés | Critical | Follow-up #2109 |
| **3** | `--color-on-accent` jamais câblé dans `roxabi.theme.ts` | High | Follow-up |
| **4** | Astryx v0.1.2 : zéro variante bordered → `outline→ghost` silencieux | High | Follow-up |
| **5** | Tokens elevation jamais mappés | Medium | Dette pré-existante (PR #1771), pas régression |
| **6** | Seulement 4 familles de tokens câblées dans `roxabi.theme.ts` | Medium | Structurel |

### Échecs process

1. **Gate visuel re-baseliné sur l'état cassé** — `test_dashboard_visual.py` ne couvrait que `/chat`, pas `/design-system`
2. **`DesignSystemPage` jamais eyeballed** — le catalogue montrait le bug sans qu'on le voie
3. **Pas de doc système avant la migration** — `DESIGN.md` est né *après* la régression
4. **Cascade layers jamais auditées** comme contrat cross-cutting — un diff d'une ligne, invisible en review
5. **Coexistence hybride sans règle** — Astryx + Tailwind hand-rolled sur le même écran
6. **API Astryx traitée comme du HTML stylable** — props droppées (`className` sur Table, `icon` vs children sur Button)

### Bilan chiffré

**Qualité (02/07, pré-fix padding) :**

- 9 pages sur 10 score ≤ 2/5
- 2 pages score 1/5 (tables Fleet + Agents fonctionnellement cassées)
- Signature commune : padding collapse sur boutons/badges/inputs

**UX (03/07, post-fix padding #2152) :**

- 2 P1 · 29 P2 · 23 P3 sur 54 constats vérifiés (68 agents, vérif adversariale)
- Verdict : *« bonnes fondations, mauvaise couche de composition »*
- 7 racines systémiques : `ErrorState` manquant, i18n non lié à `language`, status map absente, tokens light non bridgés (AA fail), API Astryx mal comprise, pas de `h1`, responsive limité au shell

---

## 3. Comparaison : migration Astryx (v1) vs réinstallation shadcn (dashboard-v2)

La réinstallation shadcn vit sur **`feat/dashboard-v2-greenfield`** (pas encore mergée sur `staging` au moment de ce recap).

| Dimension | Migration Astryx (v1, juin–juil.) | Réinstall shadcn v4 (v2 greenfield) |
|---|---|---|
| **Stack** | `@astryxdesign/core` + StyleX + cascade layers | shadcn v4 (`base-nova`) + Base UI + Tailwind v4 |
| **Effort** | 12 slices, ~3 semaines | 1 commit scaffold (144 fichiers), routes + mock + tests |
| **Thème brand** | `roxabi.theme.ts` → CLI `theme:build` → `built/` | `forge.css` : mapping direct Forge → variables shadcn |
| **Friction CSS** | Élevée — guerre cascade layers | Faible — `@import "shadcn/tailwind.css"` + `@theme inline` |
| **Composants** | Migration 1:1 non triviale (API ≠ shadcn) | `shadcn add` → 31 primitives (sidebar ~690 LOC) |
| **Bespoke à maintenir** | FilterChip / ListToolbar / SortableTableHeader → remplacés | Rien — kit complet out of the box |
| **Régression visuelle** | Massive (padding 0), gate CI aveugle | Aucune signalée (scaffold neuf) |
| **Dette introduite** | Hybride Astryx/Tailwind, adaptateurs incomplets | Dev-mock large (~535 LOC), sidebar lourde, pas d'auth (#1992) |
| **DX agent** | CLI `astryx component X --props` (output parfois tronqué) | CLI `shadcn add` mature, composants copiés en local |

### Ce que la réinstall shadcn confirme

1. Le problème Astryx n'était pas le brand — `forge.css` mappe les mêmes tokens sans friction ; la douleur venait des cascade layers + contrat API composant.
2. shadcn v4 + Base UI est plug-and-play avec Tailwind v4 et React 19.
3. Les 3 gaps initiaux n'existent plus dans v2 — le kit shadcn v4 les couvre nativement.
4. Le coût réel d'Astryx = apprentissage API + gouvernance CSS layers + wrappers adaptateurs — pas l'install (clean au PoC).

### Ce qu'Astryx apporte malgré tout

- Shell intégré (`AppShell/TopNav/SideNav`) — pas besoin du sidebar shadcn 690 LOC
- CLI agent-ready (`astryx doctor`, `theme:build`, codemods)
- StyleX = isolation CSS par composant (quand les layers sont corrects)
- Meta OSS, aligné design-system agentique

---

## 4. Inventaire git — commits sur `staging`

### Epic & plan

| Commit | Message | Issue / PR |
|---|---|---|
| `e1c77c7bb` | feat(dashboard): Astryx design-system foundation (slice 0) | Closes #2088 · PR #2101 |
| `c41ecb6fb` | docs: promote durable memory-audit findings to permanent docs | `frontend-patterns.md` |
| `fae1bb902` | docs: address code-review blockers on memory-audit promotion | |

### Slices S0–S12 (feat + fix review, sur `staging`)

| Slice | Issue | Commit feat | Commit fix review | PR merge |
|---|---|---|---|---|
| S0 Foundation | #2088 | `e1c77c7bb` | `fafc8e3a3` | #2101 `110bb3235` |
| S1 Theme roxabi | #2089 | `5ec48d359` | `136f2fff3` | #2102 `db29877ac` |
| S2 Shell | #2090 | `8b657ca07` | `69ac69a26` | #2103 `c645016d1` |
| S3 Icons | #2091 | `067d4078f` | `b0415b998` | #2105 `d4232d716` |
| S4 Primitives | #2092 | `2bb40a0a8` | `3231b7b00` | #2107 `5f32beff8` |
| S5 Badge+Banner | #2093 | `e828ce277` | `4c7741fa9` | #2108 `28fc0aa58` |
| S6 Card family | #2094 | `89cb58154` | `4000247b7` | #2110 `d8fa1333e` |
| S7 Inputs | #2095 | `b85099403` | `a24890b57` | #2119 `492c4a694` |
| S8 Dialog | #2096 | `3bef54f18` | `decc01492` | #2122 `ee07116f6` |
| S9 Toast | #2097 | `6b8fc9461` | `6b1ca83a1` | #2124 `afb8d2b61` |
| S10 Table/Toolbar/Chip | #2098 | `c6b7290b3` | `d0a30c445` | #2131 `406f06816` |
| S11 Button+Link | #2099 | `3055297e9` | `fbbef328d` | #2135 `9b33ea9c8` |
| S12 Cutover shadcn | #2100 | `4248c0e5e` | — | #2143 `7acd9c5f5` |

### Correctifs & docs post-migration (sur `staging`)

| Commit | Message | Issue / PR |
|---|---|---|
| `c71be0924` | fix(dashboard): isolate brand reset in @layer reset | #2148 · PR #2152 `d09e6978b` |
| `d68e4b7ea` | docs(dashboard): expand DESIGN.md + quality regression analysis | PR #2151 |
| `d71ac7533` | docs(dashboard): address review findings on DESIGN.md | PR #2155 `d649d971d` |
| `9af0a29a8` | docs(dashboard): UX/UI critical audit — 7 systemic roots | PR #2222 `df72b2341` |
| `eb6e0f7ab` | docs(dashboard): fidelity fixes from code-review | |

### Commits dashboard-v2 / shadcn greenfield (branche `feat/dashboard-v2-greenfield`, **pas sur staging**)

| Commit | Message |
|---|---|
| `87ef81488` | feat(dashboard-v2): greenfield operator dashboard with Forge theme |
| `13ef5dd01` | feat(dashboard-v2): i18n, JetBrains Mono, and Playwright e2e |
| `71645c719` | feat(dashboard-v2): Lot 2 — useChat + AG-UI adapter for chat |
| `7b73dda48` | fix(dashboard-v2): address R3 AG-UI review blockers |
| `caeeecb25` | fix(docker): copy dashboard-v2 package.json for workspace install |

### Probe shadcn isolé (local, non commité sur `staging`)

Répertoire `.scratch/shadcn-probe/` — install template shadcn v4 `base-nova` (2026-07-08), commit local `a5460dc` uniquement.

---

## 5. Branches

| Branche | Statut | Contenu |
|---|---|---|
| `feat/dashboard-v2-greenfield` | **Existe** (`origin/feat/dashboard-v2-greenfield`) | Greenfield shadcn v4 + Forge theme + TanStack Router/Query + dev-mock |
| `feat/astryx-s0` … `feat/astryx-s12` | **Supprimées** (mergées, juil. 2026) | Slices migration Astryx |
| `fix/2148-brand-reset-padding` | **Supprimée** (mergée PR #2152) | Fix cascade reset |
| `docs/2151-quality-doctrine` | **Supprimée** (mergée PR #2155) | DESIGN.md + regression analysis |
| `docs/dashboard-uxui-audit` | **Supprimée** (mergée PR #2222) | Audit UX 03/07 |

---

## 6. Artefacts & preuves visuelles

| Rôle | Chemin |
|---|---|
| Analyse + décision pré-migration | `artifacts/plans/astryx-migration.md` |
| REX qualité post-migration | `artifacts/analyses/astryx-dashboard-quality-regression.claude.md` |
| Audit UX post-migration | `artifacts/analyses/2026-07-03-dashboard-uxui-audit.claude.md` |
| Doctrine forward | `apps/dashboard/DESIGN.md` |
| Gotchas API Astryx | `docs/standards/frontend-patterns.md` |
| Réinstall shadcn (v2) | `apps/dashboard-v2/` + `apps/dashboard-v2/src/theme/forge.css` |
| Review greenfield v2 (scratch) | `.scratch/reviews/dashboard-v2-r1-greenfield.md` |
| Screenshots before/after Astryx | `artifacts/dashboard-redesign/{before,after}/` |

---

## 7. Synthèse en une phrase

L'analyse pré-Astryx disait *« gaps résolus, install clean, go 100 % »* ; le REX dit *« le PoC était juste, l'exécution a sous-estimé cascade layers + contrat API + gate visuel »* ; la réinstall shadcn v2 montre que la même stack brand + Tailwind v4 marche en un scaffold sans les 12 slices ni la régression padding.
