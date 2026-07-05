# Compréhension multi-axes d'une codebase — analyse indépendante (Claude)

> **Statut :** analyse (L3 / TTL — sujette à la même politique artifacts que ce qu'elle décrit).
> **Relation :** compagnon *indépendant* de `2026-07-04-multi-axis-comprehension-semctx.md` — mêmes symptômes observés, lecture d'un cran au-dessus. Diagnostic partagé, **prescriptions divergentes** (détail → `2026-07-05-multi-axis-analysis-comparison.md`).
> **Solution détaillée :** design spec `2026-07-04-multi-axis-ssot-model.md` (grille complète, contrat de section, budget de gates, lots d'adoption). Ce doc reste à l'altitude *analyse* et pointe vers le spec pour le *design*.
> **Méthode :** workflow 31 agents — 16 angles de recherche web + 3 grounding repo + panel design 4 candidats + 3 juges + critique adversariale/complétude (`wf_d1b6b726`). Faits empiriques **grep-vérifiés vs HEAD (2026-07-05)**, cités `file:line`.

---

## TL;DR

- Même douleur que semctx : pas un problème de **volume** de doc, un problème de **grille de compréhension**. Mais le nœud est un cran plus haut :
- **Les 6 axes sont des *intentions de recherche* (colonnes de requête), pas des dossiers de rangement.** Les matérialiser en hiérarchies physiques **EST** le piège N×M.
- **Une seule décomposition physique : `altitude (L0→L3) × bounded-context (L1)`.** Tout le reste (topologie, structure, glossaire, delta…) = index projeté / artefact généré / requête dérivée.
- Le « registre de concepts » n'est **pas un fichier** — c'est `grep` name-as-key + note inline *distinguish-from* + un WARN homonyme prospectif.
- semctx = **bon diagnostic** ; l'écart est dans ses prescriptions Phase A/B (4 à ne pas exécuter telles quelles).

---

## 1. Le cran au-dessus : axe = intention, pas dossier

semctx pose une grille correcte (6 axes × 4 strates) mais la **traite comme des catégories de rangement**. Or bâtir les 6 axes en 6 hiérarchies physiques produit **deux arbres co-égaux** — `src/` par couche **et** docs par axe — dont chacun est forcé d'énumérer l'autre → chacun dérive seul. C'est exactement l'état mesuré par l'audit 03/07 (compteurs faux `9/7/10/16` vs réels `19/13/13/19/36`).

**Cure généralisée :** choisir **une** décomposition physique ; projeter toute autre intention en **index ou requête** ; épingler chaque fait à **l'altitude dont un gate peut faire respecter la fraîcheur**. « Réduire le drift » devient alors une propriété de gate, pas un espoir de diligence.

| Intention (ex-« axe ») | Question | Genre épistémique | Fraîcheur naturelle | Home |
|---|---|---|---|---|
| Glossaire | c'est quoi X / quel sens ? | vocabulaire contrôlé | lente | page propriétaire + `grep` |
| Topologie | tourne-où / parle-à-qui ? | méréologie + graphe orienté | générée-fraîche | `CURRENT.generated.md` |
| Structure | qui peut / qui importe qui ? | poset + règle déontique | générée + lente | `.importlinter` (MAY) + graphe (DOES) |
| Comportement | quoi doit rester vrai ? | axiomes | loi (durable) | `## Key invariants` + le gate |
| Delta | mon changement met quoi en danger ? | vue dérivée | à la demande | **aucun** — requête/outil |
| Procédure | comment je fais X maintenant ? | genre documentaire | par changement | `docs/runbooks/` |

---

## 2. Correction épistémique (la rigueur que semctx effleure)

`ontologie = taxonomie + axiomes`. La plupart des « axes » de semctx sont **en-dessous** d'une ontologie ou ne sont **pas des classifications** du tout — d'où le mal à trouver la bonne info : on cherche un genre au mauvais rayon.

| Axe (nom semctx) | Vrai type KO | Pourquoi pas son nom |
|---|---|---|
| **Ontologie** | **vocabulaire contrôlé / thésaurus** (terme préféré + « distinguer du voisin ») | il n'a **pas d'axiomes propres** — les axiomes SONT l'axe Comportement |
| **Topologie** | **méréologie** (containment, transitif) **+ graphe orienté** (`talks-to`, intransitif) | deux algèbres de relation cachées dans un mot ; ne jamais chaîner `talks-to` à travers `contient` |
| **Structure** | **poset** (ordre de couches) **+ couche déontique** (MAY) | le *DOES-import* est un fait extensionnel (généré) ; le *MAY-import* est une loi intensionnelle — deux strates |
| **Comportement** | **axiomes** (la couche qui hisse la taxo du glossaire en ontologie) | **¬orthogonal** au Glossaire : c'est *sa* couche d'axiomes |
| **Delta** | **vue dérivée** (pas une dimension) | calculable depuis Topologie+Structure ∩ Comportement — échoue au test d'orthogonalité |
| **Procédure** | **genre documentaire** (DITA « task ») | c'est un genre, pas une classification du code |

**Conséquence :** chaque intention s'effondre sur l'un de {type-de-nœud, type-d'arête, axiome-sur-un-nœud, genre-sur-un-nœud, requête-dérivée}. La grille 6×4 est donc **vide à dessein** — une cellule n'a un home que si l'intention en exige un durable.

---

## 3. La décomposition unique — `altitude × bounded-context`

C'est LA décision fondatrice (l'« axe de décomposition »), en amont de tout le reste.

- **Vertical (altitude) :** L0 généré/gated · L1 intent/invariants · L2 procédure · L3 résidu. Chaque fait à l'altitude dont un gate peut tenir la fraîcheur.
- **Horizontal (L1 seulement) : bounded-context.** L'asymétrie structurelle porteuse est hexagonale **inside/outside** (core n'importe jamais adapter — déjà le trigger de `axial-review.yml`). Une page = une projection de context-map.
- **Ratifier le gagnant, pas en inventer un :** la zone saine trouvée par l'audit (`docs/architecture/` 13/16 keep, ≤350 l., réseau AGENTS.md ~95 % exact) **EST déjà** des pages bounded-context + AGENTS.md sole-home.
- **Two-tier anti-récurrence :** org-wide → `~/projects/ssot/*.ssot.md` ; repo-local → `docs/architecture/*.md` ; arbitrage = **row-0 du ladder** + convention « une page locale qui instancie une règle globale nomme la shard gouvernante » (grep-trouvable ↔). Deux homes L1 **avec** règle de routage ¬ piège N×M ; deux **sans** = le piège.

---

## 4. Faits vérifiés cette session (preuves — pas opinion)

Quatre prescriptions de semctx reposent sur des affirmations empiriques ; grep vs HEAD les tranche :

| Affirmation semctx | Réalité (grep 2026-07-05) | Verdict |
|---|---|---|
| `## Current state` = fourre-tout → **interdire** | **8/16** pages l'utilisent, dont l'**exemplaire « doctrine pure »** de semctx : `engineering-standards.md:22` | ban **injustifié** — le défaut est le *contenu mal-altitué*, pas le titre |
| Créer des slots **obligatoires** `## Concepts` / `## Invariants` | `## Key invariants`=**8** · `## Invariants`=**0** · `## Concepts`=**0** | **ratifier** l'existant `## Key invariants` ; ¬inventer des titres qui n'existent nulle part |
| `satellite` = homonyme (2 sens) | mono-sens : provider self-hosted à heartbeat (`workers-tooling.md:42`) + SDK `roxabi-satellite`. Le « sens B » host-sensor est **absent** — ce publisher s'appelle « host sensor » (`observability.md:31`), jamais « satellite » | ligne homonyme **fabriquée** → à retirer |
| Phase B : « retirer l'exemption onboarding de `doc_drift` » | **déjà fait** : `check_doc_drift.py:98,113-115` les scanne (`git fe7664051`, #2201) | étape **périmée** |

*(Note connexe vérifiée : `WorkerRegistry` = dette-de-nommage assumée du registre consommateur de heartbeats, `workers-tooling.md:45,170` — pas un 2ᵉ concept valide, donc pas un homonyme mais un misnomer.)*

---

## 5. Ce que le SSoT doit être (bref — détail → design spec)

- **Un point d'entrée :** le **retrieval ladder** (question-lecteur → 1 home) matérialisé dans `docs/ARCHITECTURE.md` existant (ADR-086 §47-54 l'a *spécifié*, jamais *shippé*). Aucun nouveau fichier.
- **Contrat de section :** slots **optionnels-par-besoin** tirés d'un vocabulaire fixe, chacun épinglé à une altitude ; gate uniquement sur *l'ordre* + la *forme de `## ADR archive`* + le pointeur-`artifacts/`. **Pas** 6 slots obligatoires (= fourre-tout renommé + cérémonie de slot vide).
- **Tueur d'homonymes sans fichier :** note inline « distinguish from <voisin> → <page> » (bidirectionnelle) + `grep` name-as-key (slug = identifiant de code verbatim) + **WARN homonyme prospectif** plié dans `doc_drift`. **Pas** de `concepts.md` (un routeur séparé viole le canon ratifié `¬index séparé` / `titre = contrat`).
- **Budget d'enforcement :** **1 BLOCK neuf** (AST P0 : routes dashboard `Depends(require_operator)` + requêtes mémoire portant `user_id` — gagne son slot en fermant un trou IDOR/auth *live*) + **4 WARN/fold** (Delta MVP = fan-out-grep visant les 7 incidents fan-out du MEMORY ; smoke opérateur ; homonyme-prospectif → `doc_drift` ; artifacts-TTL → `debt_expiry`) + 1 advisory (registre de tags d'invariants). **Pas** de pile de gates parallèle.
- **Delta = requête dérivée, jamais un doc stocké.** MVP cheap d'abord (fan-out-grep), outil lourd `factory-verify-change` en dernier/optionnel — l'inverse de la priorité semctx.

→ Grille axe×strate complète, avant/après, lots d'adoption, sources (60 URLs) : **`2026-07-04-multi-axis-ssot-model.md`**.

---

## 6. Où semctx voit juste (convergences)

semctx est un **diagnostic solide** ; l'écart n'est **pas** dans le constat mais dans les prescriptions. Convergences :

- **4 strates verticales** L0–L3 (le cœur du modèle).
- **Retrieval ladder** comme réponse (ADR-086 §47-54) — jamais matérialisé, à lander.
- **Homonymes réels** : `plane`, `event`, `axial`, `worker`/`turn` (⇐ ceux-là, oui).
- **Politique `artifacts/`** : TTL + graduation obligatoire (le vrai gisement, −44 % du markdown).
- **Rot anti-corrélé aux gates** : ce qui pourrit = ce qui n'est pas scanné.
- Le pattern « domain page fourre-tout » **existe** — mais se corrige en gérant l'altitude du *contenu*, pas en bannissant un *titre*.

---

## Sources (extrait — set complet dans le research-digest)

- Fitness functions / gouvernance : archunit.org · import-linter.readthedocs.io · nx.dev/features/enforce-module-boundaries
- Code-as-data / name-as-key : engineering.fb.com (Glean) · kythe.io · github.blog (stack-graphs)
- Modélisation / topologie : c4model.com · structurizr · martinfowler.com/bliki/BoundedContext.html · alistair.cockburn.us/hexagonal-architecture
- Épistémique KO : en.wikipedia.org/wiki/Mereology · berkeley faceted-classification · taxonomy-vs-thesaurus-vs-ontology
- Genres doc / living docs : diataxis.fr · living-documentation (Martraire) · Parnas software-aging
- Contexte agents : anthropic.com/engineering/effective-context-engineering-for-ai-agents · von Mayrhauser (program comprehension)
