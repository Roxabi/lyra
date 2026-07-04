# Compréhension multi-axes d'une codebase — semctx, factory, et design d'un SSoT AI-compatible

> Synthèse de réflexion — 2026-07-04
> Méthode : analyse `semctx` (clone `~/projects/extarnal_repos/semctx`), comparaison outillage factory, diagnostic doc audit 03/07, extraction stratification corpus, recherche multi-agents (5 angles × web).
> Finalité : proposer un modèle **CCM** (Codebase Comprehension Model) pour simplifier et structurer ce que factory effleure déjà (hexagonal, axial drift, ontologie, topologie, ADR-086).

---

## TL;DR

Factory n'a pas un problème de **volume** de documentation — il a un problème de **grille de compréhension** : plusieurs axes d'observation orthogonaux, chacun avec son SSoT, aujourd'hui mal séparés. Les ADRs sont **multi-niveaux** (intent + inventaire + procédure) et **multi-axes** (messaging, obs, deploy…) dans un même artefact ; trouver la bonne info pour une feature, une simplification ou un refactor est structurellement difficile.

| Constats | Implication |
|----------|-------------|
| **`semctx`** | Une brique sur **un axe** (delta diff → symboles → invariants → tests → PASS/WARN/BLOCK), TypeScript only — **inspiration**, pas adoption |
| **`CURRENT.generated.md`** | Un autre axe (topologie/structure statique, couche L1) — **complémentaire**, pas concurrent |
| **Douleur principale** | **Ontologie + strate verticale** (concepts noyés, homonymies) — pas l'absence de semctx |
| **Déjà en place** | ~80 % d'un CCM industriel : oracle `CodeInventory`, 30+ fitness functions (`stack.yml`), retrieval ladder ADR-086, snapshot Tier-1 (#1532) |
| **Manque** | Registre concepts, contrat de section vertical, axe Delta (Tier-2 Python), point d'entrée agent unifié |

**Réponse cible** : formaliser le **CCM** (5 axes × 4 altitudes × boussole Diátaxis × 1 oracle × 1 orchestrateur `scripts/qg`) — pas adopter semctx ni empiler arc42/Backstage/Structurizr.

---

## 1. semctx — ce que c'est (analyse du dépôt)

**semctx** ([hoklims/semctx](https://github.com/hoklims/semctx)) est un analyseur d'**impact de changement** déterministe, local, sans LLM.

### Pipeline

```
repo  → graphe déterministe (TS Compiler API : symboles, imports/exports, call graph, tests, @markers)
diff  → analyse d'impact → verdict PASS / WARN / BLOCK
```

### `semctx verify diff` produit

- symboles impactés (déclarations touchées par le diff)
- contrats exportés à risque
- invariants à risque (`@invariant` opt-in)
- tests recommandés (arêtes `tested_by`)
- contradictions (sources deprecated)
- unknowns (ce que l'analyse statique ne prouve pas)
- verdict `PASS` / `WARN` / `BLOCK` (BLOCK = exit non-zéro, utilisable en CI/pre-commit)

### Tiers de sévérité

| Tier | Verdict | Déclencheur typique |
|------|---------|---------------------|
| **strict** | `BLOCK` | invariant ou contrat critique modifié **sans test couvrant** |
| **advisory** | `WARN` | contrat exporté sans test direct, ou contradiction non résolue |

### Stack (monorepo Bun)

| Package | Rôle |
|---------|------|
| `@semantic-context/ts-analyzer` | Parsing TS → graphe |
| `@semantic-context/context-engine` | Index, impact, verify |
| `@semantic-context/mcp-server` | `semctx_verify_change`, `semctx_inspect` |
| `apps/cli` | CLI `semctx` |
| `plugins/claude-code` | MCP + skill + hook guarded optionnel |

### Pivot ADR 0005 (interne semctx)

Le retriever `task → ContextPack` a été **retiré** : benchmark 16 commits — perd face à BM25 (R@10 0.31 vs 0.97). Le graphe reste pour **impact analysis**, pas pour la découverte de code. Aligné avec la stratégie factory (CocoIndex pour « où / comment », oracle pour l'exact).

### Limites

- TypeScript only (factory = Python asyncio)
- Call graph best-effort (appels dynamiques ignorés)
- Marqueurs single-line
- Pas publié npm (run from source, Bun ≥ 1.3)

---

## 2. Factory — ce qu'on a déjà (cartographie)

### Tier-1 généré (#1532 — livré)

`tools/generate_architecture_snapshot.py` → `docs/architecture/CURRENT.generated.md` :

- **Layer Map** — contrats `.importlinter`
- **NATS Subjects & Identities** — `deploy/nats/acl-matrix.json`
- **Process Topology** — `deploy/quadlet.toml`

Gate `architecture_snapshot` (pre-push + CI). Déterministe, byte-reproducible.

**Note** : la spec #1532 prévoyait aussi Module/Symbol Inventory via `CodeInventory` — le générateur actuel ne l'inclut plus (layers + NATS + topology seulement).

**Tier-2 explicitement out of scope** (#1532) : call graphs, dependency heatmaps — cœur de `semctx verify diff`.

### Oracle sémantique (ADR-081 absorbé par ADR-086)

`tools/code_inventory.py` : passage AST Python → modules, symboles, sujets NATS. Sert `check_doc_drift.py` — résolution exacte `resolve(token) → {exists, kind}`.

### Enforcement structurel

- `.importlinter` — 15+ contrats (layers, forbidden, independence, stage-axis ADR-073)
- `stack.yml` — ~30 quality gates = **architecture fitness functions** (Neal Ford)
- `axial-review.yml` — label PR cross-layer (signal humain, pas verdict machine)

### Documentation (ADR-086)

| Strate | Rot rate | Artefacts |
|--------|----------|-----------|
| INVENTORY | rapide + silencieux | L0/L1 généré |
| STRUCTURE | moyen | import-linter, snapshot |
| INTENT | lent | domain pages, AGENTS.md, ADRs |

**Retrieval ladder** (ADR-086) :

1. Code — ground truth
2. Tier-1 généré + oracle
3. `ccc` (CocoIndex) — découverte floue
4. AGENTS.md + domain pages — intent only
5. `artifacts/` + issues — deltas only

### Grille factory vs semctx

| Capacité | roxabi-factory | semctx |
|----------|----------------|--------|
| Inventaire statique | `CURRENT.generated.md` | `semctx index` |
| Contrats / invariants | import-linter + ADRs + axial review | `@contract` / `@invariant` + BLOCK rules |
| Gate violation structurelle | import-linter, 16+ gates | `verify diff` |
| **Diff → impact** | ❌ | ✅ |
| Tests recommandés par diff | pytest smoke fixe | `tested_by` |
| Verdict machine sur changement | ❌ | PASS/WARN/BLOCK |
| Langage | Python (+ Bun dashboard) | TypeScript only |
| Recherche sémantique | CocoIndex / `ccc` | Abandonné (ADR 0005) |

**Verdict comparaison** : overlap sur la **forme** (doc déterministe, contrats) ; **orthogonal** sur la **fonction** (snapshot statique vs analyse diff-scoped).

---

## 3. Le problème systémique — multi-axes, multi-niveaux, tout mélangé

### 3.1 Axes de compréhension (questions orthogonales)

| Axe | Question | Rot typique |
|-----|----------|-------------|
| **Ontologie** | Quels concepts, comment se nomment / se distinguent | Lent (lexique) |
| **Topologie** | Quoi tourne où, qui parle à qui | Rapide (deploy) |
| **Structure** | Qui peut importer / dépendre de qui | Moyen |
| **Dynamique** | Quels flux, jobs, états, pipelines | Moyen |
| **Gouvernance** | Quoi doit *rester vrai* (invariants) | Lent |
| **Opérations** | Comment faire X *maintenant* | Moyen (recettes) |
| **Delta** | Ce changement met quoi en danger | Par PR |

Confondre deux axes dans un même paragraphe = le mode par défaut du corpus factory.

### 3.2 Strates verticales (audit doc 03/07 + synthèse industrie)

Modèle **4 couches** (simplification du 0–3 audit + Diátaxis + ADR-086) :

```
L0  GROUND      Code + configs (.importlinter, acl-matrix, quadlet.toml, contracts)
       ↓ génère
L1  STATE       CURRENT.generated.md, oracle CodeInventory, gates CI
       ↓ explique (intent only)
L2  KNOWLEDGE   Domain pages, AGENTS.md, concepts.md, runbooks
       ↓ archive (append-only)
L3  HISTORY     ADRs, artifacts/ — JAMAIS current truth
```

**Règle** : une altitude par section. Inventaire uniquement L0/L1. Jamais de « current truth » en L3.

### 3.3 Boussole Diátaxis (comment chercher)

| Besoin | Forme | Aller vers |
|--------|-------|------------|
| Je découvre | Tutorial | QUICKSTART, golden path |
| Je dois faire X | How-to | `runbooks/` |
| Qu'est-ce qui existe ? | Reference | **L1 uniquement** |
| Pourquoi c'est comme ça ? | Explanation | domain L2 → ADR L3 |

**Un ADR ne doit jamais répondre à « qu'est-ce qui existe ? »** — source principale de la douleur multi-niveaux.

### 3.4 Pattern structurel — domain pages fourre-tout

Squelette récurrent :

```
Scope → Current state → Key invariants → ADR archive
```

`## Current state` = fourre-tout : ontologie + décisions absorbées ADR + impl (paths BFF, units Quadlet, schémas SQLite).

#### Où ça se répète (audit + extraction)

| Page | Verdict audit | Symptôme typique |
|------|---------------|------------------|
| `observability.md` | keep | 4 planes noyés dans paths BFF, units Quadlet |
| `storage.md` | rewrite | Taxonomie L0–L4 + schéma MemoryEntry + endpoints blobstore |
| `messaging.md` | keep | RoutingKey (concept) + catalogue 30+ subjects (inventaire) |
| `adapters.md` | rewrite | Datée ; `telegram_inbound.py`, checklists obsolètes |
| `security-routing.md` | rewrite | Pipeline auth + classes middleware + TOML + shipped checklist |
| `deployment.md` | keep | Mieux (pointe quadlet) mais table containers + hub middleware détaillé |
| `contracts.md` | keep | Pin doctrine + inventaire 6 packages |
| `job-model.md` | keep | `job_id=run` clair mais taxonomy chevauche messaging |
| `llm-streaming.md` | keep | Pipeline hexagonal + edge cases + codec registry |
| `workers-tooling.md` | keep | **Honnête** : « Two altitudes in one page » |
| `engineering-standards.md` | keep | Intent-level — peu d'inventaire |

Même les « keep » mélangent souvent les niveaux.

#### Guides racine

| Fichier | Problème |
|---------|----------|
| `CONFIGURATION.md` | Config + ontologie stores + `FACTORY_VAULT_DIR` fantôme |
| `bot-management.md` | Modèle Bot + schéma SQLite 11 colonnes obsolète |
| `agent-management.md` | Duplique règle TOML-seed (4 endroits) |
| `ARCHITECTURE.md` | Hub + invariants + mini-inventaire jobs |
| `COMMANDS.md` | Taxonomie + chemins modules |

Onboarding (QUICKSTART, GETTING-STARTED, MULTI-BOT) : couche opérateur + modèle données — **faux au runtime** ; **exempté de `doc_drift`**.

#### AGENTS.md, ADR, runbooks

- ~36 paires AGENTS.md : doctrine « invariants, not inventory » globalement OK ; exceptions : racine (compteurs), `obs/AGENTS.md` (scaffolding mort), `scripts/AGENTS.md` (section Inventory)
- 34 ADR actifs : bannière → domain page, mais corps garde souvent détail contrat
- 3 ADR actifs désignent encore `artifacts/` comme current truth — contradiction ADR-086
- Runbooks : frontière floue (`nats-ops.md` = inventaire hand-maintained)

### 3.5 Homonymies (amplificateur)

| Mot | Sens A (ontologie) | Sens B (ailleurs) |
|-----|-------------------|-------------------|
| `satellite` | Provider NATS + heartbeat | factory-host-sensor events (ADR-091) |
| `plane` | 3 planes messaging (ADR-076) | 4 planes observabilité (ADR-091) |
| `event` | `factory.event.*` externe | `RenderEvent` streaming interne |
| `axial` | Stage vs platform (ADR-073) | « axial consolidation » audio (ADR-079) |
| `worker` | Compute instance | WorkerRegistry heartbeat (legacy) |
| `turn` | Unité conversation | ≈ job dans ADR obsolètes |

Sans registre transversal, chaque doc utilise le mot « correctement » dans son contexte — le lecteur ne reconstitue pas la grille.

### 3.6 Exceptions qui fonctionnent

1. `engineering-standards.md` — doctrine pure
2. `CURRENT.generated.md` — inventaire machine gated
3. `workers-tooling.md` § Tool model — deux altitudes avouées
4. `OBSERVABILITY.md` — stub redirect
5. AGENTS.md récents — `grep` / `ls` au lieu d'inventaire

Trait commun : **une altitude par section, pointeur au lieu d'inventaire**.

### 3.7 Trois causes racines

1. Pas de **contrat de granularité par section** (ADR-086 dit « pas d'inventaire » sans slots obligatoires)
2. **Absorption ADR = copier-coller** du détail vers domain page
3. **Gates asymétriques** — fraîcheur symboles ≠ structure sémantique du doc

Audit 30/06 : 16 gates verts, **4 P0 non gate-checkables**.
Audit 03/07 : rot **anti-corrélé** aux gates (onboarding exempté, compteurs, runbooks incident).

---

## 4. CCM — Codebase Comprehension Model (design cible)

Modèle à **2 dimensions** pour simplifier hexagonal + axial + ontologie + topologie + gates existants.

### 4.1 Dimension 1 — Cinq axes (+ Delta + Ops)

| Axe | Question | SSoT machine (L0/L1) | SSoT humain (L2) | Historique (L3) |
|-----|----------|----------------------|------------------|-----------------|
| **Ontologie** | Que signifie X ? | `concepts.json` *(à créer)* | `concepts.md` | ADR qui a nommé le concept |
| **Topologie** | Quoi tourne où ? | `quadlet.toml`, `acl-matrix`, `CURRENT.generated` | `deployment.md` | ADR deploy |
| **Structure** | Qui importe qui ? | `.importlinter`, `CodeInventory` | `engineering-standards.md` | ADR-073 |
| **Dynamique** | Quels flux / jobs ? | `roxabi-contracts`, code | `job-model.md`, `messaging.md` | ADRs flux |
| **Gouvernance** | Quoi reste vrai ? | `stack.yml` gates, tests | §Invariants, `AGENTS.md` | ADR décisionnel |
| **Opérations** | Comment faire X ? | smoke, `factory --help` | `runbooks/` | — |
| **Delta** | Ce diff met quoi en danger ? | *(futur)* `verify_change` | review, axial labels | `artifacts/` |

**Sous-outillés** : Ontologie, Delta. Le reste est déjà solide.

### 4.2 Dimension 2 — Quatre altitudes (L0–L3)

Voir §3.2. Mapping industrie :

| Google / GitLab / Spotify | CCM factory |
|---------------------------|-------------|
| Reference générée | L1 STATE |
| Design doc / explanation | L2 + L3 ADR |
| Handbook procedures | L2 runbooks |
| Catalog / golden paths | `ARCHITECTURE.md` hub + retrieval ladder |

### 4.3 Point d'entrée unique

```
docs/ARCHITECTURE.md          → hub routing (catalog)
    ├── concepts.md/json      → ontologie transversale
    ├── knowledge-ssot.md     → ladder + CCM (à créer — mentionné ADR-086)
    └── 5 axes → domain pages L2
```

### 4.4 Protocole agent (retrieval ladder opérationnel)

```
1. Classifier le besoin (Diátaxis)
2. Concept ambigu → concepts.md
3. « Quoi existe » → L1 (oracle / generated) — JAMAIS artifacts/
4. « Pourquoi » → domain L2 → ADR L3 si bloqué
5. « Où dans le code » → CodeInventory / grep → ccc si flou
6. « Ce diff » → verify_change (futur) + import-linter + gates
7. « Comment faire » → runbook L2 smoke-tested
```

### 4.5 Contrat de section (domain pages)

Remplacer `## Current state` :

```markdown
## Concepts        → liens concepts.json#sense_id
## Invariants      → règles normatives (testables)
## Topology        → pointeur CURRENT.generated §X
## Dynamics        → pointeur contracts / messaging
## Decisions        → tableau ADR archive (status + one-liner)
## Procedures      → runbooks/ uniquement
```

### 4.6 Registre de concepts (MVP)

**Fichiers** : `docs/architecture/concepts.md` + `docs/architecture/concepts.json`

**Entrée minimale** :

```
concept_id, label, sense_id, definition, bounded_context,
canonical_ref, symbols[], adr_refs[], disambiguation, status
```

**P0 homonymes** :

| Terme | sense_id | Canonique |
|-------|----------|-----------|
| plane | `messaging.nats_plane` | messaging.md |
| plane | `observability.signal_plane` | observability.md |
| event | `observability.external_event` | observability.md |
| event | `streaming.render_event` | llm-streaming.md |
| satellite | `workers.provider_satellite` | workers-tooling.md |
| axial | `governance.stage_axis` | ADR-073 |

**Relation ADR ↔ concepts** : ADR = *pourquoi* (L3) ; registre = *que signifie* (L2 ontologie). `adr_refs` dans JSON ; `concepts: [sense_id]` en frontmatter ADR.

### 4.7 Fitness functions taggées (`stack.yml`)

Les ~30 gates **sont** le CCM côté machine. Enrichir metadata :

```yaml
architecture_snapshot:
  ccm_axis: [topology, structure]
doc_drift_bundle:
  ccm_axis: [governance]
import_layers:
  ccm_axis: [structure]
# verify_change:  # futur
#   ccm_axis: [delta]
```

Orchestrateur unique : `scripts/qg`. Pas de tool sprawl.

### 4.8 MCP `factory-context` (transport, pas nouvel outil)

- `resolve_symbol` → CodeInventory
- `architecture_state` → CURRENT.generated (JSON export parallèle au MD)
- `concept_lookup` → concepts.json
- `retrieval_route(question)` → couche + axe
- `gate_status` → scripts/qg (lecture seule)

---

## 5. Carte de couverture — factory vs CCM

```
                    Ontologie  Topologie  Structure  Dynamique  Gouvernance  Ops    Delta
CURRENT.generated      —         ✅         ✅*        —          —          —      —
CodeInventory          ⚠️         —          ✅         ✅         —          —      —
import-linter          —          —          ✅         —          ✅         —      —
doc_drift              —          —          —          —          ✅         —      —
ccc/CocoIndex          —          —          —          ✅         —          —      —
axial-review           —          —          ✅         —          ✅         —      ⚠️
ADR-086 ladder         ✅         ✅         ✅         ✅         ✅         ✅     —
concepts registry      ❌         —          —          —          —          —      —
verify_change          —          —          —          —          —          —      ❌

* layers dans snapshot ; symboles = Tier-2 #1532 non livré
```

**Maturité estimée** : top décile industrie sur oracle + gates + ladder ; gap = ontologie + delta + exécution verticale doc.

---

## 6. Recherche multi-agents — synthèse industrie (2024–2026)

> 5 agents parallèles : frameworks multi-vues, ontologie/AKM, paysage outils/fitness functions, consolidation SSoT/Diátaxis, context engineering agents. Web + alignement repo factory.

### 6.1 Frameworks multi-niveaux

| Framework | Ontologie | Topologie | Structure | Comportement | Delta |
|-----------|-----------|-----------|-----------|--------------|-------|
| **arc42** | §8 concepts, glossaire | §7 deployment | §5 building blocks | §6 runtime | §9 ADR |
| **C4** | Context | Deployment | Container→Component | Dynamic | + ADR externe |
| **Structurizr** | Modèle unique → vues | Dérivé | Hiérarchie C4 | Dynamic views | `!adrs` par niveau |
| **Backstage catalog** | Domain/System | Resource | Component/deps | API | Metadata Git |
| **Nx graph** | Tags | — | Deps calculées | Task graph | `nx affected` |

**Leçon** : modèle unique → vues dérivées (Structurizr) = pattern le plus proche de factory (`quadlet` + ACL + import-linter → `CURRENT.generated`). **Ne pas** ajouter arc42 complet — factory a déjà l'équivalent spécialisé hub-spoke Python/NATS.

### 6.2 ADR — bonnes pratiques

- Une ADR = une décision (Nygard) ; supersede, pas effacer
- Index spatial = **pages domaine**, pas `meta.json` hand-maintained (factory : bon choix suppression 2026-07-02)
- ADR ≠ reference, ≠ runbook, ≠ inventaire
- RFC (Spotify) → ADR pour figer

### 6.3 Ontologie / DDD / AKM

- Ubiquitous language = rigoureux, par bounded context (Evans/Fowler)
- ODA viable en monorepo = **registre léger**, pas OWL complet
- AKM : identification → décision → documentation → enactment → partage
- CodeOntology / graphes : secondaire ; priorité registre + oracle existant

### 6.4 Fitness functions & outillage

| Outil | Axe principal | Vérifiable machine |
|-------|---------------|-------------------|
| import-linter | Structure | ✅ |
| semctx | Delta + invariants inline | ✅ (TS) |
| ArchUnit / dependency-cruiser | Structure | ✅ |
| CodeScene / SonarQube arch | Impact hotspots | ⚠️ advisory |
| Pact | Contrats intégration | ✅ |
| factory `stack.yml` | Multi-axes | ✅ |

**semctx unique** : diff → symboles → `tested_by` → PASS/WARN/BLOCK. **import-linter unique** : état global des frontières. Complémentaires.

**Anti-sprawl** : étendre `CodeInventory` avant d'ajouter semctx/SonarQube/Backstage.

### 6.5 Context engineering pour agents

Industrie (Fowler 2026, LangChain, Anthropic, AGENTS.md standard) :

| Type contexte | Machine vs humain |
|---------------|-------------------|
| Instructions persistantes | AGENTS.md, rules — humain |
| Index exact | AST, oracle — **machine** |
| Index flou | embeddings, ccc — machine |
| Intent | domain pages — humain + pointeurs |
| Delta | diff impact — **machine** (manque factory) |

Échecs contexte (Breunig) : poisoning, distraction, confusion, clash — d'où **ladder explicite** et **une altitude par artefact**.

Factory déjà top décile : réseau AGENTS.md, oracle, ccc, doc_drift, ladder ADR-086. Manque : concepts registry, MCP factory-context, verify_change.

### 6.6 Consolidation SSoT — principes DELETE / MERGE / POINT

| Action | Quoi |
|--------|------|
| **DELETE** | Inventaire hand-typed L2 ; `## Current state` fourre-tout ; artifacts/ comme current truth |
| **MERGE** | Absorption ADR → invariants L2, détail L3 ; runbook dans ADR → runbook L2 |
| **POINT** | Compteurs → CURRENT.generated ; invariant → 1 phrase L2 + lien ADR |

Test : *changer un symbole → combien de fichiers doc à la main ?* → **0** inventaire, **1 max** intent.

---

## 7. semctx dans le CCM — quand oui / non

| Scénario | semctx ? | Alternative factory |
|----------|----------|---------------------|
| Mélange ontologie/impl `observability.md` | Non | Contrat section + concepts.md |
| Homonymies plane/satellite | Non | concepts.json |
| Invariant touché sans test (dashboard TS) | Oui (si `@invariant`) | Marqueurs ou verify_change TS |
| Route sans `Depends(require_operator)` | Non | Règle Tier-2 Python custom |
| Remplacer CocoIndex | Non | ccc + grep (semctx ADR 0005) |
| Blast radius PR Python | Non | `tools/verify_change.py` |

**Verdict** : inspiration sur modèle verdict WARN/BLOCK + `tested_by` ; **pas adoption** du package.

---

## 8. Trois trous structurels (priorisation)

### Trou 1 — Registre ontologique (axe Ontologie)

Sans index transversal, `doc_drift` vérifie l'existence de symboles, pas le sens de « plane » dans un paragraphe.

### Trou 2 — Contrat de section vertical (strate L2)

Sans slots obligatoires, chaque consolidation ADR v2 recrée le fourre-tout.

### Trou 3 — Axe Delta + gates doc structure (Vérifiabilité)

- Pas de diff → symboles → tests
- Gates ne voient pas stratification, homonymes, onboarding (exempté)

---

## 9. Roadmap — 3 vagues

| Vague | Livrables | Débloque |
|-------|-----------|----------|
| **V1 — Nommer** | `concepts.md/json` P0 ; amendement ADR-086 (slots) ; `knowledge-ssot.md` (CCM + ladder) ; tag `ccm_axis` sur gates clés | Boussole humain + agent |
| **V2 — Purger** | 20 drifts high audit 03/07 ; contrat section sur pages « rewrite » ; graduation 3 ADR→artifacts ; retirer exemption onboarding post-smoke | Confiance L2 |
| **V3 — Machine** | `verify_change.py` (CodeInventory + diff + pytest map) ; gate stack.yml ; `check_doc_stratification.py` ; export JSON snapshot ; MCP factory-context | Axe Delta ; agents = même oracle que CI |

**Hors scope immédiat** : Structurizr, Backstage déployé, semctx npm, réécriture horizontale des 16 domain pages.

### Tier-2 Python (spec esquisse)

```
git diff → modules/symboles AST touchés
         → layers (.importlinter) violés ?
         → subjects NATS (oracle) touchés ?
         → invariants (§ Invariants domain ou marqueurs)
         → tests pytest (collect par path)
         → PASS / WARN / BLOCK + preuves file:line
```

Extension naturelle #1532 out-of-scope — pas fork semctx.

### Gates doc complémentaires

`check_doc_stratification.py` (heuristiques) :

- pas de `## Invariants` → WARN
- compteur hardcodé hors L0 → FAIL
- `## Current state` > N lignes → FAIL
- `artifacts/` cité comme current truth → FAIL
- termes ambigus P0 sans qualificateur → WARN

---

## 10. Chemin feature / refactor (opérationnel)

```
Question → ARCHITECTURE.md (routing CCM)
        → Diátaxis (quel type de besoin ?)
        → concepts.md si terme ambigu
        → domain page §Invariants (gouvernance)
        → CURRENT.generated / oracle (état)
        → code / ccc (implémentation)
        → ADR L3 seulement si le « why » bloque
        → verify_change avant merge (futur)
```

Les ADRs ne sont plus l'index, ni l'inventaire, ni la procédure — **archive décisionnelle** liée à un axe et des `concept_id`.

---

## 11. Grille besoin → axe → strate (référence rapide)

| Besoin | Axe | Strate | Où |
|--------|-----|--------|-----|
| Qu'est-ce que c'est ? | Ontologie | L2 | concepts.md |
| Qu'est-ce qui tourne ? | Topologie | L1 | CURRENT.generated |
| Puis-je importer ? | Structure | L0 | import-linter |
| Pourquoi comme ça ? | Gouvernance | L2→L3 | domain → ADR |
| Ce PR casse quoi ? | Delta | L1 | verify_change (futur) |
| Comment faire X ? | Opérations | L2 | runbooks |
| Où est le code ? | — | L0+L1 | grep, CodeInventory, ccc |

---

## 12. Conclusion

| Avant (douleur) | Après (cible CCM) |
|-----------------|-------------------|
| ADR = index + état + procédure + spec | ADR = L3 décision seule |
| 35 AGENTS.md avec inventaire résiduel | AGENTS = invariants + pointeurs axe |
| Multi-SSoT silencieux | 1 hub + 1 ladder + 1 oracle + concepts registry |
| Agent lit au hasard | Diátaxis → couche → axe → fichier |
| semctx comme solution miracle | Tier-2 Python + registre concepts |

Factory a investi dans **fraîcheur des références** et **inventaire machine statique** — excellents sur leurs axes. Il manque l'**indexation des concepts**, la **séparation verticale**, et l'**impact diff-scoped**. Le CCM nomme et relie ce que vous pratiquez déjà (hexagonal, axial ADR-073, ADR-086, snapshot Tier-1) sans ajouter un framework lourd.

**En une phrase** : le CCM factory = **5 axes × 4 altitudes × Diátaxis × oracle × `scripts/qg`** — les briques existent ; il reste à écrire la « constitution » (`concepts` + `knowledge-ssot` + amendement ADR-086) et à livrer l'axe Delta en Python.

---

## 13. Prochaines étapes concrètes

1. Rédiger `docs/architecture/knowledge-ssot.md` — CCM + retrieval ladder + protocole agent
2. Créer `docs/architecture/concepts.md` + `concepts.json` (P0 homonymes) + schéma JSON
3. Amender ADR-086 — slots section obligatoires, lien vers concepts registry
4. Spec issue `verify_change.py` — extension CodeInventory, gate `stack.yml`
5. Issue graduation artifacts — 3 ADR → domain pages (audit 03/07)

Les cinq sont complémentaires et **indépendants de semctx**.

---

## 14. Références

### Interne factory

- `artifacts/analyses/2026-07-03-doc-audit-strategy.claude.md` — couches 0–3, 20 drifts high, séquencement
- `docs/architecture/adr/086-documentation-architecture.mdx` — two-tier SSoT, retrieval ladder
- `artifacts/specs/1532-tier-1-generated-architecture-snapshot-spec.mdx` — Tier-1 livré, Tier-2 out of scope
- `artifacts/analyses/2026-06-30-full-audit/AUDIT-SUMMARY.md` — 16 gates vs P0 non checkables
- `~/projects/extarnal_repos/semctx` — change-impact analyzer (clone local 2026-07-04)

### Industrie — documentation & SSoT

- [Diátaxis](https://diataxis.fr/) — boussole tutorial/how-to/reference/explanation
- [GitLab handbook — SSoT usage](https://handbook.gitlab.com/handbook/about/handbook-usage/)
- [Stripe Markdoc](https://stripe.dev/blog/markdoc) — docs-as-code, validation
- [Google design docs at Google](https://www.industrialempathy.com/posts/design-docs-at-google/)

### ADR & décisions

- [Michael Nygard — Documenting Architecture Decisions](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
- [adr.github.io](https://adr.github.io/)
- [Spotify — When should I write an ADR](https://engineering.atspotify.com/2020/04/14/when-should-i-write-an-architecture-decision-record/)
- [AWS ADR prescriptive guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/architectural-decision-records/welcome.html)
- [Sustainable Architectural Design Decisions (InfoQ)](https://www.infoq.com/articles/sustainable-architectural-design-decisions/)

### Vues architecture

- [C4 model](https://c4model.com/)
- [arc42 overview](https://arc42.org/overview)
- [Structurizr DSL](https://docs.structurizr.com/dsl)
- [Backstage catalog descriptor](https://backstage.io/docs/features/software-catalog/descriptor-format)

### Fitness functions & outillage

- [ThoughtWorks — Architectural fitness function](https://www.thoughtworks.com/radar/techniques/architectural-fitness-function)
- [Building Evolutionary Architectures](https://www.thoughtworks.com/books/building-evolutionary-architectures)
- [import-linter](https://import-linter.readthedocs.io/)
- [semctx](https://github.com/hoklims/semctx)

### Agents & context engineering

- [AGENTS.md standard](https://agents.md/)
- [Martin Fowler — Context engineering for coding agents](https://martinfowler.com/articles/exploring-gen-ai/context-engineering-coding-agents.html)
- [LangChain — Context engineering for agents](https://www.langchain.com/blog/context-engineering-for-agents)
- [Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [Spotify golden paths](https://engineering.atspotify.com/2020/08/how-we-use-golden-paths-to-solve-fragmentation-in-our-software-ecosystem/)

### Ontologie & DDD

- [Ubiquitous Language — Martin Fowler](https://martinfowler.com/bliki/UbiquitousLanguage.html)
