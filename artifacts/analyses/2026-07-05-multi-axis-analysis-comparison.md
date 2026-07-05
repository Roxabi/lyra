# Comparaison — analyse *semctx* vs analyse *Claude* (SSoT multi-axes)

> **Compare :**
> - **A = semctx** — `2026-07-04-multi-axis-comprehension-semctx.md` (analyse initiale, FR)
> - **B = Claude** — `2026-07-05-multi-axis-comprehension-claude.md` (analyse indépendante) + son design spec `2026-07-04-multi-axis-ssot-model.md`
> **Vérif :** faits tranchés par `grep` vs HEAD (2026-07-05), preuve `file:line` en clair.
> **Verdict global :** A = **diagnostic juste, prescriptions partiellement fausses**. 4 recos inversées (vérifiées) + 1 erreur factuelle + 1 étape périmée + 1 reframe conceptuel. Convergence complète sur le constat.

---

## 1. Écarts — réversions de prescription (avec verdict vérifié)

Là où B **inverse** une reco de A — et où grep tranche que A avait tort, pas seulement « superseded » :

| # | Sujet | A — semctx dit | B — Claude dit | Verdict vérifié (preuve) |
|---|---|---|---|---|
| 1 | **`concepts.md`** | Phase A#1 : **créer** `docs/architecture/concepts.md` (registre homonymes) | **CUT** — routeur séparé viole `¬index séparé` ; inline *distinguish-from* + grep + WARN | A **contredit le canon ratifié** (`conventions.ssot.md` § ADR consolidation : « ¬index séparé, titre = contrat ») |
| 2 | **Slots obligatoires** | 4–5 slots **obligatoires** ; inventer `## Concepts` + `## Invariants` | **optionnels-par-besoin** ; **ratifier `## Key invariants`** existant | **✓ grep : `## Key invariants`=8 · `## Invariants`=0 · `## Concepts`=0** — A invente des titres inexistants |
| 3 | **Bannir `## Current state`** | Phase A#2 : **interdit** comme fourre-tout ; gate `>N lignes → FAIL` | **lever le ban** — gate le *contenu mal-altitué*, pas le titre | **✓ 8/16 pages l'utilisent, dont l'exemplaire « doctrine pure » de A : `engineering-standards.md:22`** — le ban flaggerait la page-modèle |
| 4 | **`compteur hors L0 → FAIL`** | gate structurel `check_doc_stratification.py` FAIL | **DELETE** — non décidable par machine | Fondé : aucun lint ne sépare un `13/16` qui pourrit d'un `port 18091` / `ADR-073` / `≤350` stables |
| 5 | **Homonyme `satellite`** | table homonymes : **2 sens** (provider NATS/heartbeat vs `factory-host-sensor` events, ADR-091) | **mono-sens** — ligne **fabriquée** → retirée | **✓ « sens B » absent** du code/docs : le publisher = « host sensor » (`observability.md:31`), jamais « satellite » ; `satellite` = 1 concept (`workers-tooling.md:42` + SDK `roxabi-satellite`) |
| 6 | **Priorité Delta** | Tier-2 `factory-verify-change` (diff→symboles→tests) = **le** build machine principal | **différer en dernier/optionnel** ; MVP = **fan-out-grep** cheap | Repriorisé : A sur-investit l'outil le plus lourd sur le besoin le moins fréquent ; le fan-out vise les **7 incidents fan-out** du MEMORY |
| 7 | **Exemption onboarding `doc_drift`** | Phase B#6 : « **retirer** l'exemption une fois réécrit » | déjà fait | **✓ `check_doc_drift.py:98,113-115` les scanne** (`git fe7664051`, #2201) — étape **périmée** |

---

## 2. Écart conceptuel — le cran au-dessus

Le vrai « on remonte d'un niveau » : A observe les bons axes mais s'arrête à la grille ; B les recatégorise.

| Dimension | A — semctx | B — Claude |
|---|---|---|
| **Nature des 6 axes** | « axes de compréhension » = grille de dimensions/catégories (⇒ tentation de rangement) | **intentions de recherche (colonnes de requête), PAS des dossiers** — intent-as-folder = le piège N×M |
| **Décomposition** | 6 axes × 4 strates traités en pair | **1 décomposition physique** (`altitude × bounded-context`) ; le reste = index projeté / généré / requête |
| **« Ontologie »** | nom de l'axe | **vocabulaire contrôlé / thésaurus** (pas d'axiomes propres) ; `ontologie = taxo + axiomes` |
| **Orthogonalité** | 6 axes orthogonaux | Comportement = *couche d'axiomes du* Glossaire ; Delta = *vue dérivée* — ni l'un ni l'autre orthogonal ⇒ grille vide à dessein |

**En une ligne :** A demande « quels axes de compréhension ? » ; B répond « ce sont des *requêtes*, range-les par altitude et projette le reste ».

---

## 3. Convergences (¬écart — le constat partagé)

Les deux analyses sont **d'accord** sur :

- Problème = **grille de compréhension**, pas volume de doc.
- **4 strates verticales** L0–L3.
- **Retrieval ladder** comme réponse (ADR-086 §47-54, jamais shippé).
- **Homonymes réels** : `plane`, `event`, `axial`, `worker`/`turn`.
- **Politique `artifacts/`** : TTL + graduation obligatoire.
- **Rot anti-corrélé aux gates** (ce qui pourrit = ce qui n'est pas scanné).
- Le pattern « domain page fourre-tout » **existe** (désaccord seulement sur le *remède*).

---

## 4. Erreur factuelle isolée (à corriger dans A)

**`satellite` — ligne homonyme fabriquée.** A affirme 2 sens ; le sens B (« `factory-host-sensor` publie des events, ADR-091 ») n'existe pas textuellement.
- Sens réel unique : *provider self-hosted en process NATS avec heartbeat* — `workers-tooling.md:42`, `packages/roxabi-satellite/AGENTS.md` (« satellite plumbing » SDK), usages `src/factory/nats/*` (`roxabi_satellite.tokens`).
- Le publisher d'events hôte s'appelle **« host sensor »** (`observability.md:31,256`) — jamais « satellite ».
- Risque si non corrigé : un agent crée un faux SSoT (une ligne stale) sur une distinction inexistante — exactement le mal que le registre est censé tuer.

---

## 5. Disposition — quoi faire des prescriptions de A

| Prescription semctx | Disposition | Remplacée par (dans B / design spec) |
|---|---|---|
| Phase A#1 `concepts.md` | **DROP** | inline *distinguish-from* + grep name-as-key + WARN prospectif → `doc_drift` |
| Phase A#2 slots obligatoires | **AMEND** | slots optionnels-par-besoin ; ratifier `## Key invariants` |
| Phase A#2 ban `## Current state` | **DROP** | gate contenu mal-altitué (pointeurs L0) — pas le titre |
| Phase A#3 graduation artifacts | **KEEP** | identique (+ plié dans `debt_expiry` récurrent) |
| Phase B#4 Tier-2 `factory-verify-change` | **DEFER** | Delta MVP fan-out-grep d'abord ; outil lourd optionnel/dernier |
| Phase B#5 `check_doc_stratification.py` (FAILs) | **AMEND** | FAIL seulement sur forme `## ADR archive` + pointeur `artifacts/` ; le reste WARN |
| Phase B#6 retirer exemption onboarding | **DONE** (#2201) | garder seulement le smoke opérateur |
| Phase C#7 retrieval ladder | **KEEP** | matérialisé dans `ARCHITECTURE.md` (row-0 org/repo ajouté) |
| Table homonymes `satellite` | **DROP** | homonymes réels : `plane`/`event`/`axial`/`worker`+`turn` |

**Net :** exécuter la Phase A/B de semctx *telle quelle* aurait créé un `concepts.md` rot-prone, ajouté une cérémonie de slots vides, et shippé un gate qui flagge la meilleure page. Garder de A : le diagnostic, les 4 strates, le ladder, la politique artifacts, les 4 vrais homonymes.

---

## 6. Comment lire les 3 docs ensemble

```
semctx (07-04)          →  claude (07-05)             →  model-spec (07-04)
diagnostic v1              analyse corrigée + reframe     design (grille, gates, lots)
[garder le constat]        [pourquoi + faits vérifiés]    [quoi construire]
     └── corrections (§1,§4,§5) formalisées ici ────────────┘
```

- **semctx** reste utile comme **capture du diagnostic** — pas comme plan d'exécution.
- **claude** = l'analyse à suivre (constat de semctx + reframe + faits grep-vérifiés).
- **model-spec** = le design retenu (critique-durci, 22 attaques FIX/DEFEND en annexe).

> **Suggéré :** poser un bandeau 1-ligne en tête de `…-comprehension-semctx.md` → « Corrections & suite : `…-analysis-comparison.md` » (doctrine redirect-stub, `¬` move sec).
