# Réconciliation — CCM ⋈ model-spec → un seul modèle

> **Compare :** `2026-07-04-multi-axis-comprehension-semctx.md` (**CCM**, doc vivant de l'opérateur) ⟷ `2026-07-04-multi-axis-ssot-model.md` (**SPEC**, écrit par l'assistant).
> **Méthode :** workflow 6 agents (`wf_0413b2da`) — steelman CCM + steelman SPEC + **map neutre** + **ground-check grep** → réconciliation → **bias-check adversarial**. Faits `file:line` vérifiés vs HEAD.
> **Statut :** analyse L3 / TTL. Sortie durable = ladder → `docs/ARCHITECTURE.md` + contrat de section + **1 ADR** (gate P0). Le reste (CCM, SPEC, ce doc) expire.

---

## 0. Honnêteté méthodo (à lire avant les verdicts)

Le réconciliateur = l'auteur du SPEC. Un **bias-check adversarial** (modèle indépendant) a tourné exprès et a **corrigé 6 biais pro-SPEC** — appliqués ci-dessous (§5). Deux garde-fous :

- **Ne pas lire « SPEC gagne 12/14 » comme une victoire.** C'est le **taux d'auto-victoire attendu** quand un auteur juge son doc contre un rival. Chaque verdict ci-dessous tient **sur la preuve grep indépendante** — ou est rétrogradé/fusionné.
- **Le bandeau errata du CCM (2026-07-05) ¬ preuve indépendante** — je l'ai écrit. Les verdicts ne s'appuient QUE sur le grep repo, pas sur « le CCM concède ».

**Résultat net : ¬ « un doc gagne ». Une fusion.** Les deux sont **la même lecture du même diagnostic** ; ils divergent sur ~14 points, dont une poignée tranchés par grep, plusieurs vraies fusions où le CCM apporte du réel, et **1 (le gate sécurité) que j'ai surestimé et rétrograde**.

---

## 1. Ce sur quoi les deux convergent (le gros — ~90 %)

| Convergence |
|---|
| Défaut structurel = le fourre-tout `## Current state` (inventaire généré + invariant durable + impl + histoire à une altitude) |
| **Une altitude par section** ; aucun slot ne mélange les rot-rates |
| **Une seule décomposition physique**, projeter le reste en index/requête/artefact généré (pattern Structurizr « 1 modèle → N vues ») |
| **Générer, jamais maintenir** un inventaire/graphe/compteur — et **gater** le généré |
| ADR = L3 provenance seule ; jamais current-truth/index/inventaire ; `titre = contrat` |
| `artifacts/` jamais current-truth (mêmes 3 ADR fautifs à graduer) |
| **Delta = requête dérivée**, pas un doc blast-radius stocké |
| **semctx = inspiration** (verdict PASS/WARN/BLOCK + `tested_by`), ¬ adoption (TS-only, ADR-0005 retire le retriever) |
| Factory déjà **top-décile** (oracle + fitness gates + ladder ADR-086) ; gap = glossaire/ontologie + delta + stratification verticale |
| **Un seul ladder question-lecteur** en point d'entrée (ADR-086 §47-54, jamais shippé) |
| Homonymes réels (`plane`, `event`, `axial`) = amplificateur |
| **Bounded-context / hexagonal inside-outside** (core ¬import adapter, trigger axial-review) = l'asymétrie porteuse |
| Anti-sprawl : étendre `CodeInventory`/`doc_drift` avant d'ajouter un outil ; ¬arc42/Backstage/Structurizr/semctx-npm déployés |
| Endpoint Delta = `verify_change` Python (diff→symboles→layers→subjects→invariants→pytest→verdict), hors #1532, ¬fork semctx |

→ Deux lectures du même diagnostic, pas deux camps.

---

## 2. Verdict par divergence (corrigé par le bias-check)

| # | Sujet | CCM | SPEC | **DÉCISION** | Rationale (grep-indépendant) |
|---|---|---|---|---|---|
| 0a | Jeu d'axes 6-vs-7 | 7 col. (incl. **Dynamique**) | 6 intentions, 3 renames | **merge** | 3 renames adoptés ; Dynamique **¬ droppée** → intention nommée-mais-fine avec home (voir §3, correction bias-check) |
| 0b | Altitudes L0/L1/L2 | lignée : code=L0, généré=L1, intent+proc=L2 | classe-de-fraîcheur : généré=L0, intent=L1, proc=L2, hist=L3 | **merge** | Bandes SPEC plus saines (1 mode enforce/rot chacune) ; **greffer** l'insight CCM : nommer code+config = source génératrice (pré-L0) |
| 1 | Registre concepts = fichier séparé | `concepts.md`+`.json` (sense_id) | inline + grep name-as-key + WARN | **adopt-SPEC + restaurer index généré différé** | `¬index séparé` (`conventions.ssot.md:21`) : le *sens* est **irréductiblement hand-authored** = un `meta.json`-bis → inline. **Mais** un index *généré* concept→pages/symboles reste un artefact **différé** légitime (T5 du SPEC) — ¬ « inutile vs grep » |
| 2 | Ban `## Current state` | ban → slots fixes | garder, gater l'altitude du contenu | **adopt-SPEC** | grep : **8/16** pages saines incl. exemplaire `engineering-standards.md:22` ; défaut = contenu mal-altitué, ¬ le titre |
| 3 | Homonyme `satellite` | Sens A vs B (ADR-091) | drop — mono-sens | **drop-both** | grep : sens unique (GPU-worker NATS, `workers-tooling.md:42`) ; producteur ADR-091 = « host sensor » (`observability.md:31`), jamais « satellite ». **Ligne fabriquée** → supprimer |
| 4 | Slots obligatoires vs optionnels | 6 présence-checks | menu optionnel-par-besoin | **adopt-SPEC** | 6-présence ¬ décidable machine, fabrique la cérémonie de slot vide ; gater seulement la forme décidable |
| 5 | Nom heading invariants | `## Invariants` | ratifier `## Key invariants` | **adopt-SPEC** | grep : `## Key invariants`=**8**, `## Invariants`=**0** — le CCM gate-failerait 8 pages saines pour un titre jamais adopté |
| 6 | Budget enforcement | V3 : verify_change + stratif-FAIL + ccm_axis + MCP + JSON | 1 BLOCK + 4 WARN/fold | **merge** | Budget SPEC en base (`counter-hors-L0→FAIL` ¬décidable) ; **greffer** `ccm_axis` (+consommateur) + export JSON |
| 7 | Séquence Delta MVP | `verify_change.py` d'abord | fan-out-grep WARN d'abord | **merge** | Même endpoint ; cheap-first vise la **classe d'échec #1** (7 incidents fan-out MEMORY) à coût du jour |
| 8 | Le 1 BLOCK neuf | 1 règle Tier-2 parmi d'autres | gate AST P0 sécu (operator-dep + user_id) | **merge — justification RÉTROGRADÉE** | ⚠ **¬ « IDOR live confirmé »** : le hub ré-authentifie (ADR-090, live @ idx7 ~2026-07-01) ; les routes session ont un guard env-gated (#1992). Gap réel = **défense-en-profondeur** sur `bff_admin.py`, **à tracer** avant de le traiter en trou (§7) |
| 9 | MCP factory-context | ajouter | omettre | **merge (différer)** | Omettre en v1 OK ; transport machine sert le north-star AI-first → **lot différé** post-v1, gaté sur ladder matérialisé |
| 10 | Entrée : fichier neuf vs hub | `knowledge-ssot.md` | ladder dans `ARCHITECTURE.md` | **adopt-SPEC (maintenant)** | ADR-086 fixe la current-truth dans `ARCHITECTURE.md` ; `knowledge-ssot.md` sanctionné **mais différé** (¬ fabriqué) |
| 11 | Axes = SSoT-d'observation vs intentions | « chaque axe son SSoT » | intentions, jamais dossiers | **adopt-SPEC (nuancé)** | Le framing intention-¬-dossier prévient le piège N×M. **Crédit** : le CCM §4.1 **home déjà** les axes dans des fichiers existants (¬ 6 dossiers neufs) — la critique porte sur la *formule* « son SSoT », pas sur des folders |
| 12 | Altitude `.importlinter` | L0 GROUND (config=ground) | MAY-rule=L1 intensionnel ; graphe does-import=L0 extensionnel | **adopt-SPEC** | Règle déontique hand-authored ≠ fait généré — rot-rate/nature ≠ |
| 13 | Boussole Diátaxis explicite | 1er pas « classifier » | fold implicite + ladder question-keyed | **merge** | Genre aide l'humain, question-key aide l'agent sans vocabulaire — **layerable** ; garder la boussole genre comme **couche humaine explicite** (¬ droppée) |
| 14 | Dynamique axe first-class | ligne + SSoT propre | flows = Topology+Behavior | **merge (¬ adopt-SPEC)** | Correction bias-check : une **state-machine** (job queued→running→cancelled ; séquence turn-stream) n'est **ni une arête `talks-to` ni un axiome** → besoin réel d'un **home explicite**. → intention nommée avec home = sections state-machine de `job-model.md`/`llm-streaming.md` |

---

## 3. Le modèle réconcilié

**Intentions de recherche** (colonnes de requête, jamais dossiers ; chacune → type KO le plus faible suffisant) :

| Intention | Question lecteur | Type KO / home | Note |
|---|---|---|---|
| Glossaire | « que veut dire X ici ? » | vocabulaire contrôlé — inline page propriétaire | ← Ontologie (ontologie = taxo + axiomes ; les axiomes = Comportement) |
| Topologie | « tourne où / parle à qui ? » | méréologie (containment, transitif) + graphe orienté (`talks-to`, intransitif) | |
| Structure | « importe qui ? » | graphe does-import = **L0 fait** ; `.importlinter` MAY-rule = **L1 loi** | règle ≠ fait |
| Comportement | « quoi reste vrai ? » | axiomes de contrat + `## Key invariants` | ← Gouvernance ; **c'est la couche d'axiomes du Glossaire**, co-localisée |
| **Dynamique** | « quel est le cycle de vie job/turn ? » | **home explicite** = sections state-machine `job-model.md`/`llm-streaming.md` | intention nommée-mais-fine (comme Delta) ; largement une *vue* sur Topologie+Comportement, **mais** la séquence/état a un home désigné (correction bias-check) |
| Delta | « mon diff casse quoi ? » | requête dérivée (Topo∩Struct∩Comport) | pas de doc blast-radius stocké |
| Procédure | « comment je fais X ? » | pointeur → `runbooks/<x>` | ← Ops (DITA task) |

**Une seule échelle d'altitude — classe de fraîcheur + source génératrice (greffe CCM) :**

```
pré-L0  RÉFÉRENT   code + config          ← le DÉCRIT, ¬ une strate doc ; ccc/grep, ¬home normatif
                       │ régénère ↓ (byte-diff gate)
L0      GÉNÉRÉ      CURRENT.generated, CodeInventory, graphe does-import, artefacts ACL   (extensionnel, machine-frais)
L1      INTENT+INV  intent domain-page, ## Key invariants, .importlinter MAY-rule          (intensionnel, loi humaine, durable)
L2      PROCÉDURE   runbooks/ (pointeur-only depuis les domain pages, ¬shell inline)       (par-changement, smoke-testé)
L3      HISTOIRE    ADRs, artifacts/ (incl. CE doc, CCM, SPEC)                              (append-only ; jamais current-truth)
```
Résout le mismatch : CCM-L0(code)→pré-L0 référent ; CCM-L1(généré)→L0 ; **CCM-L2 dé-lumpé** en L1(intent)/L2(procédure) — rot-rates ≠ (signal ground-check : les fix runbook = « ce step exact était faux », `nkey-rotation`/`identity-lifecycle step 7`). Insight CCM préservé = l'arête `pré-L0 → L0`.

**Contrat de section** (ratifier le corpus, gater le décidable) :
- Menu de slots **optionnel-par-besoin** ; **¬** présence obligatoire.
- Ratifier les headings existants : `## Current state`, `## Key invariants`. **¬ inventer** `## Invariants`/`## Concepts`/`## Dynamics` (grep : 0 usage).
- Une-altitude-par-section : gater le **placement du contenu**, ¬ les titres (¬ inventaire/compteur généré hors pointeur L0).
- Forme machine-gatée = **colonnes table ADR-archive + ordre des sections** (le seul contrat décidable).
- Sections Procédure = **pointeur-only** → `runbooks/`.

**Registre de concepts** (résout `concepts.json` vs inline) :
- **Sens (intensionnel)** = irréductiblement hand-authored → **note inline `distinguish-from` sur l'UNIQUE page propriétaire** (¬ routeur séparé ; `titre=contrat` satisfait par le contexte de la page), co-localisée avec les axiomes Comportement du terme. Gaté par review + **WARN homonyme prospectif** (grep name-as-key plié dans `doc_drift`). Seed = homonymes grep-vérifiés (`plane`, `event`, `axial`) ; **¬ satellite**.
- **Index (extensionnel)** concept→pages/symboles = **artefact généré, différé** (T5 du SPEC restauré) — navigation machine, ¬ SSoT du sens, ¬ hand-maintained. Construire quand la douleur de cache apparaît.

**Budget enforcement :**

| Tier | Item | Justification |
|---|---|---|
| **1 BLOCK** (neuf) | gate AST : routes dashboard portent `Depends(require_operator)` ; requêtes mémoire/session scopées `user_id` | **défense-en-profondeur** (¬ « trou live confirmé » — cf. §7) ; les routes BFF devraient porter le guard même si le hub enforce aussi |
| WARN (fold) | homonyme-prospectif (grep name-as-key) → `doc_drift` | flag collisions non-désambiguïsées |
| WARN (fold) | ordre-section + forme ADR-archive → `doc_drift` | l'unique contrat de section décidable |
| WARN (fold) | **fan-out-grep Delta MVP** : ACL-key/host-role/quadlet-unit changé → grep manifeste sibling-consumer → WARN si non-touché | vise les 7 incidents fan-out MEMORY |
| CHEAP (greffe CCM) | tag `ccm_axis:[...]` sur les ~48 gates **+ 1 consommateur coverage-report** | requête couverture-par-axe ; consommateur obligatoire sinon décoratif |
| NEAR-TERM (greffe CCM) | **`architecture_state.json`** export // `CURRENT.generated.md` | snapshot machine-lisible pour agents/MCP — utile seul, ¬ noyer dans le lot MCP |
| DIFFÉRÉ | `verify_change.py` (endpoint Delta) | hors #1532 |
| DIFFÉRÉ | MCP `factory-context` (`resolve_symbol`/`gate_status`/`retrieval_route`) | north-star AI-first ; gaté sur ladder + JSON |
| DROP | CCM `counter-hors-L0→FAIL` / `check_doc_stratification.py` FAIL | ¬ décidable (rotting 13/16 vs stable :18091 indistinguables) — reproduit le défaut de gate asymétrique dans le remède |

**Entrée unique** : ladder question-lecteur dans `docs/ARCHITECTURE.md` (ADR-086) + **ligne Dynamique** (→ state-machine `job-model.md`/`llm-streaming.md`) + **boussole Diátaxis** implicite (what-is=reference, why=explanation, how=procedure) comme couche humaine, sans étape de routage imposée aux agents.

---

## 4. Ce que chaque doc apporte (crédit honnête — c'est une fusion)

| SPEC apporte | CCM apporte |
|---|---|
| Rigueur KO épistémique (ontologie=taxo+axiomes ; Comportement=couche d'axiomes du Glossaire ; Delta=vue dérivée) | **Recherche primaire semctx** (clone, ADR-0005 R@10 0.31 vs 0.97) — le vrai apport durable |
| Intentions-¬-dossiers (prévient N×M) | **Survol frameworks** arc42/C4/Structurizr/Backstage/Nx (table §6.1) |
| Altitudes classe-de-fraîcheur (split intent/procédure) | **Tag `ccm_axis`** sur gates (couverture-par-axe requêtable) |
| Optionnel-par-besoin + ratifier les headings existants | **`architecture_state.json`** export machine-lisible |
| Traitement concepts doctrine-consistant (¬index séparé) | **Design MCP `factory-context`** (transport, ¬ nouvel outil) |
| Fan-out-grep Delta MVP (classe d'échec #1) | **Boussole Diátaxis** comme couche humaine explicite |
| Le flag gate P0 sécu (défense-en-profondeur) | **Insight lignée** : nommer code+config = source génératrice |
| | **Surfacer Dynamique** comme besoin-lecteur réel (state-machine) |

→ Le CCM n'est **pas** « corrigé » — il contribue **8 éléments réels** au modèle final.

---

## 5. Corrections bias-check appliquées (trail de transparence)

| Le bias-check a attrapé | Correction appliquée |
|---|---|
| Gate P0 = « IDOR live CONFIRMÉ » **surestimé** (routes session ont guard #1992 ; hub ré-auth ADR-090 non-tracé ; « missing-auth » ≠ IDOR) | Rétrogradé **CONFIRMÉ → PLAUSIBLE** ; recadré défense-en-profondeur ; trace hub requise (§7) |
| Dynamique droppée trop vite (state-machine ¬ arête ¬ axiome ; arc42/C4/Structurizr la traitent first-class) | **Restaurée** en intention nommée avec home state-machine explicite (§3) |
| « le CCM concède » cité comme preuve **circulaire** (errata self-authored) | Retiré de tous les rationales ; **grep indépendant seul** |
| « SPEC gagne 12/14 » présenté en vindication | Recadré : taux d'auto-victoire attendu, ¬ preuve (§0) |
| Index concept généré tué « inutile vs grep » — plus dur que le T5 du SPEC (qui le diffère) | **Restauré** en artefact généré différé (§3) |
| N×M attribué au CCM = strawman (§4.1 home dans fichiers existants) | Crédité ; critique recentrée sur la *formule* « son SSoT » |
| Diátaxis droppée via « ¬ dans le repo » (non-sequitur — c'est un ajout net) | Fusionnée sur le mérite ; boussole genre créditée comme couche humaine |

---

## 6. Deltas concrets (recommandés — à valider)

**DANS le CCM** (ton doc vivant — ¬ touché sans ton go) :
- Passer les points concédés du bandeau au **corps en strikethrough** : ligne `satellite` (§3.5/§4.6), schéma `concepts.json` (§4.6), ban `## Current state` + 6-slots (§4.5), heading `## Invariants` + sa règle WARN (§9).
- §4.3 « 5 axes chacun son SSoT » → **6 intentions + Dynamique(home state-machine)** ; noter « intentions, ¬ dossiers ».
- Rétrograder §4.8 MCP + §9 `verify_change`/`check_doc_stratification` de **V3-inconditionnel → différé** ; supprimer `counter-hors-L0→FAIL`.
- **Garder** la recherche primaire (semctx, ADR-0005, table frameworks) = **appendice L3** cité par le modèle fusionné.

**DANS le SPEC** :
- Greffer : (a) tag `ccm_axis` + consommateur coverage ; (b) sharpen L0 = nommer **code+config source génératrice** (pré-L0→L0 byte-gaté) ; (c) **`architecture_state.json`** near-term ; (d) MCP `factory-context` **différé post-v1**.
- Ajouter la **ligne Dynamique** au ladder (→ `job-model.md`/`llm-streaming.md` state-machine).
- **Rétrograder** la justification du gate P0 : CONFIRMÉ → **PLAUSIBLE défense-en-profondeur** (§7).

**Graduation durable** (les 3 docs = L3/TTL) : sortie vivante = ladder → `docs/ARCHITECTURE.md` + contrat de section sur domain pages + **1 ADR** (décision gate P0). Le reste expire.

---

## 7. Action hors-doc — le gap auth (⚠ à vérifier, ¬ confirmé)

Le bias-check + mémoire projet (ADR-090 authz **live @ hub idx7** depuis ~2026-07-01) dégonflent le « P0 IDOR confirmé » :

- **Constat grep :** `bff_admin.py:25/35/49` (GET /admin/access, POST /admin/users, PATCH /admin/users/{id}) **¬ `Depends(require_operator)`** ; `bff.py:86/108` (sessions/turns) 401 seulement si `FACTORY_DASHBOARD_AUTH_REQUIRED` (**default off**), sans scope ownership.
- **Mais :** le hub **ré-authentifie chaque inbound** (le modèle SPEC le dit lui-même) via `middleware_authz.py` (ADR-090, enforce @ idx7). Si `DashboardHubClient` route les appels admin par le hub → **guardé au hub**, gap BFF = défense-en-profondeur, **probablement ¬ exploitable**.
- **Non tracé :** le chemin `DashboardHubClient` → hub authz. **→ tracer end-to-end avant de qualifier.** Connexe : ADR-090 matrix, GuardChain unwired, IDOR #2153.
- **Reco :** ouvrir une issue « BFF admin routes ¬ operator-guard (défense-en-profondeur) », **¬ « P0 IDOR »**, découplée du rollout doc-stratégie. Vérifier d'abord le trace hub.

---

## Provenance

Workflow `wf_0413b2da-eca` (6 agents : steel:ccm opus · steel:spec sonnet · map:neutral opus · ground:check sonnet · synth:reconcile opus · adversarial:biascheck opus). Ground-check + bias-check grep-vérifiés vs HEAD 2026-07-05.
