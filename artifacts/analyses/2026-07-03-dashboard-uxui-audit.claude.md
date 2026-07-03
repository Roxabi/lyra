---
title: Dashboard UX/UI — analyse critique & direction cible
date: 2026-07-03
scope: apps/dashboard/ (factory ops cockpit SPA)
method: evidence-first, 48 screenshots × 11 lentilles × 12 écrans, 68 agents, vérif adversariale
status: analysis (pré-spec) — feeds a UX-hardening epic under #2087
---

# Dashboard UX/UI — analyse critique & direction cible

## TL;DR

Le dashboard a **de bonnes fondations et une mauvaise couche de composition**. Le système de
tokens/theming, les landmarks a11y, le focus-ring, la primitive `EmptyState`, la symétrie i18n
EN/FR et le build de thème gated sont solides — la migration Astryx (#2087) a bien posé le socle.
Les défauts ne sont **pas** dans les fondations : ils viennent de **7 primitives/contrats manquants**
au-dessus. 54 constats vérifiés se ramènent à ces 7 racines (+ un reste de polish, cf. §3). Aucun ne demande de réécriture ni de
sortir de la stack.

> **Le setup est à ~80 %. Le 20 % manquant est une fine couche de primitives + adaptateurs Astryx.**

Verdict par lentille (score /max) :

| Lentille | Score | 1 ligne |
|---|---|---|
| ia-nav | 3/5 | nav groupée saine ; **aucun `h1`**, titre = slug brut sur route détail |
| hierarchy-layout-density | 3/5 | densité OK ; couche **Glance faible** (accueil ouvre sur 1 phrase muette) |
| design-tokens | 2/4 | socle fort ; **rôles synonymes** + rampe status **non re-tunée en light (AA fail)** |
| typography | 2/5 | **police head (Outfit) absente** de la couche titres ; échelle de type morte |
| color-contrast | 2/4 | dark passe AA ; **light casse AA** sur tout texte status/erreur (2.0–2.8:1) |
| component-states | 5/12 | **pire surface** : erreur = `<p>` rouge nu, sans retry, parfois silencieuse |
| interaction-motion | 2/4 | motion socle bon ; **CTA icône+label clippés** ; chips sans chrome au repos |
| a11y | 3/5 | landmarks+focus OK ; **surfaces live sans `aria-live`** (WCAG 4.1.3) |
| data-density-tables | 2.5/5 | tables réelles ; affordances tri/filtre invisibles ; **min-width droppé** |
| content-i18n | 2/5 | keysets symétriques ; **formatage suit l'OS pas la langue** ; fuites EN + clé brute |
| responsive | 2/4 | shell collapse OK ; **sous-layouts ignorent les breakpoints** (chat, tables) |

Sévérités vérifiées (grading workflow) : **2 P1 · 29 P2 · 23 P3** ; 2 constats **réfutés**.
Ré-arbitrage (§7) : +1 P1 (échec WCAG AA light = violation objective + critère parité).

---

## 1 · Méthode (traçabilité)

Evidence-first, zéro vibe :
- **Board de preuves** : 48 screenshots réels — 12 écrans × {dark, light} × {desktop 1440, narrow 768},
  capturés en `DASHBOARD_MOCK` (déterministe) via Playwright. Chaque constat cite `écran.png` + `file:line`.
- **Audit** : 11 lentilles (IA · hiérarchie · tokens · typo · couleur/contraste · états · interaction/motion ·
  a11y · densité-tables · contenu/i18n · responsive), chacune balaie **les 12 écrans**, doctrine `/ui`
  encodée (register=**product**, 6-axes + Nielsen H1-H10 + slop-gate 8-bans + WCAG 2.1 AA + personas Alex/Sam/Riley).
- **Vérif adversariale** : chaque constat re-vérifié en lecture seule sur le `file:line` cité → réel vs
  artefact-mock, sévérité honnête, fix in-stack valide. **68 agents, 0 erreur, 4.6M tokens.**
- **Caveat mock** : certaines erreurs de données (jobs/spans/pipeline live) viennent de l'absence
  d'endpoint dans le dev-mock — MAIS le *design* de l'état d'erreur (texte rouge nu, pas de retry,
  non traduit) est un vrai défaut, indépendant des données. Seul 1 constat est marqué `mock:true`
  (fuite de clé brute `imageDigest.undefined`, qui reste un défaut de robustesse à corriger).

---

## 2 · Ce qui est bon (NE PAS toucher)

Nommé pour survivre aux passes futures :

- **Architecture tokens/theming** : autorité unique brand→Astryx→Tailwind (`index.css`), discipline
  cascade-layers, flip light/dark par `[data-theme]`, quasi-zéro hex hardcodé en composant, gate CI
  `theme_build_drift`. Socle réel.
- **IA sidebar** : 4 groupes sensés (Accueil/Opérer/Observer/Administration), active-state correct
  (dark+light), redirect `/admin→/users`, deep-link `Flotte→/ops?container`.
- **a11y de base** : landmarks `main`+`navigation` (Astryx AppShell), **focus-ring 2px accent**
  (`brand/base.css:31`, WCAG 2.4.7 tenu même sur boutons bespoke), opérabilité clavier réelle (composer
  Enter/Shift+Enter, lignes de liste = vrais `button`), dialogs avec `aria-label` (le gap documenté
  dans `frontend-patterns.md` a été **corrigé aux call-sites**), contraste **dark** passe AA (muted 6.96:1).
- **i18n infra** : react-i18next, 8 namespaces, keysets EN/FR **byte-symétriques** (0 clé manquante).
- **Primitive `EmptyState`** réutilisée + skeletons sur Jobs/Fleet ; mono + `tabular-nums` sur valeurs machine.
- **Motion socle** : easings nommés + 3 durées (`motion.css`) câblés dans Astryx, GPU-only avec garde
  `prefers-reduced-motion` **côté Astryx**.

---

## 3 · Les 7 racines systémiques (R₁ — corriger la primitive, pas les 10 symptômes)

L'ordre de valeur : chaque racine est **une** correction architecturale qui éteint N constats.

### S1 · Pas de frontière async-state partagée → ~10 constats  [contient 1× P1]
`EmptyState` existe mais **pas de `ErrorState`/`AsyncState` frère**. Chaque page bricole son cycle
load/error → toutes les divergences sont des instances de cette absence :
- **P1** — l'accueil rend les échecs de fetch en états « vides » calmes (`DashboardHome.tsx:74-89`) :
  **le cockpit affiche "tout va bien" pendant une panne**. Péché cardinal (idle-sain ≡ down).
- `<p>` rouge nu hors du layout de cartes (`JobsPage.tsx:216`), 5 sites sans `role=alert`, pas de retry
  **alors que `common:actions.retry` existe déjà**, contradiction "0 actif" + erreur (`JobsPage:163`),
  erreur EN dans UI FR (`SpansPage:64`), même string ×3 (`IntegrationsPage:152`), spans hand-rolled.
- **Fix** : `components/ui/error-state.tsx` (icône Warning + titre + message + Button `retry`→`refetch()`
  + `role="alert"` intégré), remplacer ~9 sites. Correct par construction partout.

### S2 · Le rendu n'est pas lié à `i18n.language` → ~8 constats
Strings, formatters et décodage d'enum échappent tous à la langue active :
- Littéraux hardcodés (`SpansPage` EN, `OpsPage` presets FR `:20`, dialog `Close`), `toLocaleString(undefined)`
  → dates/tailles suivent l'**OS pas l'app** (`AgentsListPanel:46`, `JobsPage:281`, `OpsPage:233`), enum brut
  imprimé (`Healthy` `FleetPage:212`), **clé brute** (`fleet.imageDigest.undefined` `FleetPage:209`),
  tableaux de labels construits en `const` **hors React** → un switch de langue ne les atteint jamais (`OpsPage:20`).
- **Fix** : `lib/format.ts` (date/nombre/bytes bornés à `i18n.language`) + helper `translateEnum(t,prefix,val)`
  avec fallback + rapatrier les tableaux de labels dans le composant + `t()` obligatoire au render boundary.

### S3 · Pas de composant status/liveness canonique → ~4 constats
Le même concept up/down/health porte **3-4 lexiques** (En ligne/Hors ligne · Actif/Inactif · OK/Stale ·
Healthy · Oui/Non). ⚠️ Nuance (vérif adversariale) : **l'encodage couleur EST cohérent** (variants Astryx
Badge via `job-status.ts`, `FleetPage statusVariant`) — le problème est le **vocabulaire (mots)**, pas les
couleurs, + quelques exceptions de rendu (`FleetPage:212` santé en texte nu vs Badge ailleurs) + `status.css`
= placeholder auto-déclaré (4 tokens quasi-morts).
- **Fix** : une map `value → label(par langue) + Badge variant`. Converger `JobsPage active→online`,
  `FleetPage health→Badge`, retirer les tokens placeholder morts.

### S4 · Échelles de tokens déclarées mais non câblées + light = après-coup → ~6 constats  [1× P1 ré-arbitré]
Type scale, radius scale et rampe couleur util/status existent en CSS brand mais **ne sont pas mappées
dans Tailwind `@theme`** → les defaults Tailwind gagnent en silence :
- **P1 (ré-arbitré)** — rampe `--util-*` déclarée une fois (`:root`, calibrée dark) **jamais re-tunée sous
  `[data-theme=light]`** → **tout texte status/erreur casse WCAG AA en light : 2.0–2.8:1** sur ~12 pages
  (`colors.css:47` → `index.css:59` → `OpsPage:148`). Violation AA objective + critère parité.
- `rounded-xl`→12px (radius s'arrête à `lg`, `--r-xl`=20px inatteignable), `text-sm`=14px pas `--fs-sm`,
  **18× `text-[10px]` arbitraires**, police head Outfit jamais atteinte à la couche titres.
- **Fix** : bridger les échelles brand dans `index.css @theme` (`--text-*`, `--radius-*`), ajouter un bloc
  `[data-theme=light]` pour `--util-red/amber/teal/green`, câbler Outfit sur les titres de carte.

### S5 · Astryx traité comme du HTML stylable, pas comme un contrat de composant → ~6 constats  [1× P1]
Deux modes d'échec, une racine :
- (a) props silencieusement droppées : `min-w-[...]` sur `<Table>` (tables s'écrasent au lieu de scroller,
  `AgentsListPanel:330`), `className` sur `FilterChip` (chips fantômes sans chrome, `:16`), enfant icône+label
  qui clippe faute de prop `icon`. **P1** — CTA `Envoyer/Nouveau/create-user` icône **clippée** contre le
  label (`ChatComposer:40`) → actions primaires paraissent cassées.
- (b) interaction/motion ré-implémentées à la main en contournant les garanties Astryx (transitions Tailwind
  qui ignorent `prefers-reduced-motion`, `AgentsListPanel:280`).
- **Fix** : fine couche adaptateur vers l'API réelle Astryx — `tableProps={{className:'min-w-[N]'}}`,
  `ToggleButton variant='secondary'`, prop `icon` sur `Button`, garde reduced-motion unlayered dans `index.css`.

### S6 · Pas de contrat de titre sémantique / outline document → ~3 constats
Le titre visible vit dans un `<span>` non-heading du shell (`AppTopNav:23`), la promesse in-code « les pages
possèdent leur `h1` » n'a jamais été tenue, tous les titres = `Text as="h3"` → **aucune page n'a de `h1`**,
l'outline ouvre à h3, pas de landmark titre pour AT/navigation-par-titres. Défaut a11y + hiérarchie + typo
d'un seul contrat non-possédé.
- **Fix** : un `h1` par page piloté par `resolvePageTitle(pathname)` (déjà présent, `nav.ts`) dans
  `AppShell`, promouvoir titres de carte h3→h2.

### S7 · Le contrat responsive s'arrête au shell → ~5 constats
La nav collapse en hamburger à `md` (annonce le support narrow) mais chaque sous-layout l'ignore :
- Chat 3-panneaux ne collapse jamais → conversation réduite à **~200px** (`ChatPage:113`), tables s'écrasent
  au lieu de scroller (min-width droppé, S5), préférence cards/table jamais re-clampée au viewport
  (`use-view-preference.ts:29`), actions de ligne restent 28px.
- **Fix** : propager les breakpoints sous le shell — `hidden xl:flex` sur le panneau contexte,
  `tableProps` min-width, `effectiveView = isNarrow ? 'cards' : view`, bumps `max-md:min-h-[44px]`.

**Reste hors racines (polish)** : rôles tokens synonymes `primary≡brand`/`destructive≡status-error`
(`index.css:56`), colonne nom Fleet peinte entièrement en accent qui crie plus fort que les badges
(`FleetPage:195`), grilles ops désalignées (`OpsPage:128`), radius de list-row à 2 valeurs (8/12px).

---

## 4 · Direction cible — 6 principes (ce que "meilleure UX/UI" veut dire ICI)

Register **product** (cockpit mono-opérateur), densité positive, VARIANCE≤7 / MOTION≤6. Mesurable, pas vibe :

1. **Fail loud, never calm.** Sur un cockpit, une panne backend doit rendre **plus fort** qu'un idle sain,
   jamais s'effondrer en état vide/all-clear. Surface d'erreur distincte, annoncée (`role=alert`/`aria-live`),
   récupérable. L'ambiguïté idle-sain ↔ down est le péché cardinal.
2. **Un concept → un mot → un badge.** Chaque valeur liveness/health résout via une map canonique vers
   exactement un label par langue et un encodage visuel (pill Badge — jamais mix texte-coloré/checkmark/Oui-Non).
   Scanner une grille dense ne marche que si "up" se lit à l'identique sur overview/jobs/ops/fleet.
3. **Rendu locale-complet à 100 %.** Toute la surface suit la langue de l'opérateur, pas l'OS. Strings via
   `t()`, dates/nombres/bytes via un formatter lié à `i18n.language`, chaque enum via une map avec fallback
   (jamais de clé brute ni d'enum EN cru). Un switch FR↔EN change aussi les label-arrays module-level.
4. **Densité qui aligne et réserve l'accent.** Les tables denses sont la feature — mais la densité ne paie
   que si ça scanne : colonnes numériques/durée right-align `tabular-nums`, valeurs mono qui ne wrappent
   jamais mid-token (le floor scroll-horizontal doit atteindre le DOM), et l'accent ember dépensé **seulement**
   sur le signal réel (status, action primaire), pas peint sur chaque colonne d'identifiant.
5. **Chaque contrôle annonce qu'il est interactif — au repos, à densité cockpit.** Chip fantôme lisible comme
   label statique, header triable indistinct d'un fixe, filtre dont le seul label est un placeholder qui
   disparaît à la frappe = même violation. Chrome au repos obligatoire ; la densité n'excuse pas de cacher
   l'interactivité.
6. **Le mode de rendu non-défaut est un cas de première classe.** light = dark, EN = FR, l'opérateur
   clavier/lecteur-d'écran (Sam) = le voyant. Light re-tune toute la rampe util/status à AA, chaque claim de
   contraste est mesuré sur la surface réelle, les surfaces temps-réel (chat streamé, flips online/offline,
   compteurs de jobs) vivent en `aria-live`, chaque page possède un `h1`. Un cockpit dark-only/voyant-only/FR-only
   est un demi-cockpit.

---

## 5 · Backlog priorisé (ordre de ship)

Sévérité ré-arbitrée (§7). Effort S(<½j) / M(½-2j) / L(>2j). « Racine » = §3.

### P1 — avant tout (violations objectives / actions primaires cassées)

| # | Constat | Racine | Fix in-stack | Effort |
|---|---|---|---|---|
| P1-1 | Accueil rend les pannes en états « vides » calmes | S1 | `DashboardHome.tsx:74-89` : `isError`→`ErrorState` avant branche vide ; alerte enginesDown indépendante du fetch | M |
| P1-2 | CTA icône+label (Envoyer/Nouveau/create-user) clippés | S5 | prop `icon` sur `button.tsx` + call-sites string-branch (`ChatComposer:40`, `ChatSidebar:90`, `AdminPage:96/124`) | S |
| P1-3 | Light : texte status/erreur casse WCAG AA (2.0–2.8:1) sur ~12 pages | S4 | bloc `[data-theme=light]` dans `colors.css` pour `--util-red/amber/teal/green` (chaque ≥4.5:1 sur blanc) | S |
| P1★ | **Fondation S1** : créer `ErrorState` partagé (débloque ~10 constats) | S1 | `components/ui/error-state.tsx` + brancher `refetch()` + `role=alert` intégré, remplacer ~9 sites | M |

### P2 — avant release (systémiques à fort levier)

| Constat | Racine | Fix | Effort |
|---|---|---|---|
| `min-w-[...]` droppé → tables s'écrasent (11-col agents illisible @narrow) | S5/S7 | `tableProps={{className:'min-w-[N]'}}` sur 4 tables (`AgentsListPanel:330`,`FleetPage:156`,`JobsPage:232`,`AdminPage:135`) | S |
| Formatage dates/bytes suit l'OS pas la langue | S2 | `lib/format.ts` bornée `i18n.language`, 3+1 call-sites | M |
| SpansPage entièrement non traduite (EN dans UI FR) | S2 | `i18n/locales/{en,fr}/spans.json` + `useTranslation('spans')` | S |
| Surfaces live sans `aria-live` (chat streamé, flips online/offline) | a11y | `role="log"`+`aria-live` `MessageList:26` ; `aria-live` `CockpitContextPanel:36` | S |
| Filter-chips sans chrome au repos (fleet/pipeline) | S5 | `ToggleButton variant='secondary'` OU `className` border via cascade-layer, `filter-chip.tsx:17` | S |
| Headers triables indistincts + caret seulement si actif | S5 | glyphe `↕` muté au repos, `sortable-table-header.tsx:37` | S |
| Aucun `h1` ; outline saute à h3 | S6 | `h1` piloté `resolvePageTitle` dans `AppShell` + titres h3→h2 | M |
| Titres de carte en Inter, pas Outfit | S4 | `font-[family-name:var(--font-head)]` sur ~15 titres (cascade-layer) | S |
| Accueil ouvre sur phrase muette, pas de bande KPI | S1/hiérarchie | remplacer l'alerte par 3-up stat-tiles `DashboardHome:126` | M |
| Chat 3-panneaux ne collapse pas (~200px @narrow) | S7 | `hidden xl:flex` contexte + `hidden md:flex` + drawer sessions | M |
| Intégrations : même erreur ×3 | S1 | `ErrorState` page-level unique, gate sections `IntegrationsPage:152` | S |
| `active/inactif` (Jobs) ≠ `en ligne/hors ligne` (Overview) pour le même bool | S3 | converger `JobsPage:359`→`status.online/offline` | S |
| Tagline dupliquée + sur-tronquée `Op…` | S3/typo | `subtitle=""` en vue table `AgentsListPanel:359` | S |
| Presets logs ops hardcodés FR | S2 | déplacer dans `ops.json`, construire dans le composant `OpsPage:20` | S |

_(reste P2 : jobs erreur hors layout, spans hand-rolled, 5 sites sans role=alert — tous absorbés par la fondation S1.)_

### P3 — polish (23 constats)
Résumé : rôles tokens synonymes à trancher (`index.css:56`), accent sur-appliqué colonne Fleet (`:195`),
grilles ops désalignées (`:128`), radius list-row unifier sur `rounded-lg`, `imageDigest` fallback guard,
`fleet.health.*` en Badge traduit, colonnes numériques right-align, échelle de type morte à bridger ou
supprimer, labels visibles spans (`isLabelHidden`), dialog `Close`→`t('common:close')`, actions 28px→44px @narrow,
view-pref re-clampée au viewport, breadcrumb route détail, deep-link `/ops?container` clear-affordance,
motion app-authored qui ignore reduced-motion. Détail complet : `scratchpad/audit-result.json`.

---

## 6 · Vérifié mais NE PAS poursuivre (réfutés)

La vérif adversariale a tué 2 constats — ne pas les re-déposer :

1. **« Bouton primary dark = blanc-sur-orange 3.35:1, fail AA »** → FAUX. Pixel-sampling : le label Astryx =
   `--color-on-accent` = `#171717` sur `#e85d04` = **5.12:1, passe AA** (light 5.18:1). Le constat était
   contradictoire. ⚠️ **MAIS** un vrai white-on-orange existe ailleurs : `MessageBubble.tsx:56` (bulle chat
   user, `bg-brand text-brand-foreground` = `#fafafa` sur `#e85d04` = **3.35:1**) — **re-filé en P2**, replié dans le fix S4 `colors.css` (fix :
   `--accent-on` dark → `#171717`, ne touche que ce composant + swatches déco).
2. **« Vocabulaire couleur status = chaos ad-hoc par page »** → PARTIELLEMENT FAUX. La couleur EST cohérente
   (variants Astryx Badge + helpers centralisés). Le résidu réel est trivial : tokens `status.css` placeholder
   morts + `FleetPage:212` santé en texte nu vs Badge. Le vrai problème status est **le lexique (mots)**, pas
   les couleurs — voir S3.

---

## 7 · Ré-arbitrage de sévérité (transparence)

Le workflow a gradé conservateur (2 P1). J'élève **1 constat P2→P1** : l'échec WCAG AA light-theme
(`--util-*` non re-tuné). Justification : doctrine audit `/ui` = « violation WCAG AA = P1 », c'est objectif
(mesuré 2.0–2.8:1) et touche le critère **parité** du Frame (§ FRAME.md). Tout le reste garde le grading
vérifié. Note : l'opérateur utilise majoritairement le dark (défaut) → impact vécu moindre, mais le critère
parité et la violation restent P1 sur le papier.

---

## 8 · Trous de couverture — passes de suivi recommandées

L'audit a des angles morts honnêtes (critique de complétude) :

- **Opérabilité clavier des widgets custom** (bullseye) : la lentille interaction-motion a livré ~0 sur
  focus-order/Enter/Space/arrows de `PopoverSelect`/`SegmentedControl`/`FilterChip`/sortable-header. Focus-ring
  OK, mais l'opérabilité clavier de ces widgets n'est **pas** vérifiée. → passe a11y clavier dédiée.
- **PipelinePage** : jamais auditée (seulement citée comme site d'erreur). Layout/IA/états à passer.
- **Users/AdminPage** : table + workflows grant/revoke/create/edit/**DELETE** (confirmation destructive) non audités.
- **agents-detail** : corps (persona/voice/passthroughs/model + ses propres états load/empty) non audité.
- **Chat empty-conversation void** : flaggé au SEED, 0 constat produit sur la surface produit principale.
- **Action-feedback / optimistic-UI** sur mutations (launch/cancel job, grant/revoke, create user, connector
  install) : aucun constat sur la confirmation succès/échec. Cockpit qui mute du prod → vrai trou (écho au
  cancel/steer cosmétique connu).
- **Flow re-auth / token expiré** (Integrations:228) et **UX de formulaire** (required, placement erreur,
  submit-disabled sur `UserFormDialog`/`CreateAgentDialog`) : scoppés out, à reprendre.
- **Contraste micro-texte** : les `text-[10px]/[11px]` (18× le premier) jamais mesurés AA à leur taille rendue.

---

## 9 · Prochaines actions proposées

1. **Ouvrir un epic UX-hardening** sous #2087 (Astryx migration) avec 3 lots :
   - Lot A « Primitives manquantes » : S1 `ErrorState` + S2 `lib/format.ts` + S6 `h1` contract + S5 adaptateur Astryx (les 4 primitives qui éteignent ~30 constats).
   - Lot B « Parité » : S4 light-AA + S7 responsive sous-layouts + S3 status map.
   - Lot C « Polish » : les 23 P3.
2. **Passe de suivi ciblée** : a11y-clavier des widgets custom + Pipeline/Users/agents-detail/action-feedback (les trous §8).
3. **Spec** : promouvoir cette analyse en spec sur le Lot A (`/dev` tier F-lite par primitive).

---

## Annexe — index des preuves

- Board screenshots : `<scratchpad>/evidence/` (48 PNG, `{écran}__{dark|light}__{desktop|narrow}.png`)
- Seed observations (baseline lead) : `<scratchpad>/SEED-observations.md`
- Frame (persona/JTBD/critères) : `<scratchpad>/FRAME.md`
- Constats bruts vérifiés (54) + critique + réfutés : `<scratchpad>/audit-result.json`
- Script capture : `<scratchpad>/capture.py` · Script workflow : `workflows/scripts/dashboard-uxui-audit-wf_1ce860b8-41b.js`
- Run workflow : `wf_1ce860b8-41b` (68 agents, 0 err, 4.6M tok, ~27min)

> `<scratchpad>` = session-éphémère ; copier evidence/ + audit-result.json ailleurs si conservation voulue.
