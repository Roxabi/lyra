# Goal — Persona / soul blobstore + dashboard agent config

> Plan vivant pour l'exécution `/goal`. Mettre à jour ce fichier au fur et à mesure :
> cocher les tâches, noter les écarts, ajouter des entrées dans le journal de progression.
>
> **Statut :** préparé — **ne pas implémenter** avant lancement explicite `/goal` par l'opérateur.

| Champ | Valeur |
|-------|--------|
| **Issue** | [#2058](https://github.com/Roxabi/roxabi-factory/issues/2058) |
| **Epic parent** | [#1760](https://github.com/Roxabi/roxabi-factory/issues/1760) — control-plane operator console |
| **Consensus** | [persona-soul-harness-parity-consensus.mdx](../analyses/persona-soul-harness-parity-consensus.mdx) (2026-06-29) — **à mettre à jour** pour blobstore |
| **ADR** | [ADR-029](../../docs/architecture/adr/029-db-first-agent-config-and-hot-reload.mdx), [ADR-073](../../docs/architecture/adr/073-axial-stage-of-pipeline-decomposition.mdx), [ADR-094](../../docs/architecture/adr/094-control-plane-dashboard-consolidation.mdx) |
| **Base PR** | `staging` |
| **Branche de travail** | feature locale dédiée (ex. `feat/<issue>-persona-soul-blobstore`) — **pas** de commit direct sur `staging` |
| **Statut global** | `in_review` — impl complete, PR pending |
| **Panel review** | 2026-06-29 — 5 experts (produit, architecte, devops, sécurité, axial-drift) → **CONDITIONAL GO** |
| **Dernière MAJ** | 2026-06-29 (session 5 — workflow PR validé) |
| **Workflow PR** | Branche feature → `/goal` → `/pr` → review/fix loop → CI green → `reviewed` + `/ci-watch` |
| **Décision opérateur** | **AgentSoul v1** : 5 sections dans 1 `soul.md` blob + envelope DB ; `backend`+`model` = defaults ; memory **provision only** ; vault personas **supprimé** |
| **Issue GitHub** | [#2058](https://github.com/Roxabi/roxabi-factory/issues/2058) |

## Statut par bloc

| Bloc | Statut | Notes |
|------|--------|-------|
| Pre-flight | `done` | Contrats + import-linter ; consensus blobstore mis à jour session 6 |
| Block 1 — Migration stockage soul | `done` | Schéma, backfill, cache, hub blobstore, sweep exempt |
| Block 2 — Hub centralisation prompt | `done` | `resolve_effective_system_prompt` ; jobs.launch bypass fermé |
| Block 3 — OMP V2 parité harness | `done` | `set_system_prompt` au acquire ; log V1 retiré |
| Block 4 — Dashboard agents UI + BFF | `done` | `/agents`, BFF, RPC, chat defaults DB |
| Block 5 — Hardening + docs | `done` | Runbooks, docs, consensus ; drop `persona_json` **différé** |

---

## Glossaire — vocabulaire factory (éviter la confusion)

> Le nom `config.db` est **trompeur** (héritage migration #417 depuis `auth.db`). Ce n'est pas « toute la config » ni « le fichier de config » — c'est le **registre opérateur runtime** du hub.

### Fichiers & stores (`~/.roxabi/factory/`)

| Nom usuel | Fichier / service | Contenu réel | Rôle |
|-----------|-------------------|--------------|------|
| **Registre hub** (≠ « config » au sens TOML) | `config.db` | `agents`, `bots`, `bot_agent_map`, `agent_runtime_state`, `user_prefs` (+ `bot_secrets` legacy) | Pointeurs, scalaires, relations — **pas** le texte long de la soul |
| **Auth / identités** | `auth.db` | `grants`, `users`, `platform_identities`, `identity_aliases`, `agent_grants` | Qui peut faire quoi sur quelle plateforme |
| **Conversations** | `turns.db` | `conversation_turns`, `pool_sessions` | Historique messages (ADR-075) |
| **Discord threads** | `discord.db` | `discord_threads` | Adapter Discord |
| **Câblage instance** | `config.toml` | bots seed, monitoring, defaults machine | Seed + wiring — **pas** SQLite |
| **Contenu long immuable** | blobstore HTTP (`:8449`) | bytes adressés `sha256:<hex>` | `soul.md`, pièces jointes… |
| **Mémoire longue durée** (futur) | roxabi-cortex | graphe, assemble | Hors scope V1 ; pas roxabi-vault personas |

### Termes agent / prompt

| Terme | Signification | Où ça vit |
|-------|---------------|-----------|
| **Agent** | Cerveau IA (model, soul, tools) | row `agents` dans `config.db` |
| **Bot** | Identité plateforme (token TG/DC) | row `bots` + `bot_agent_map` |
| **Binding** | `(platform, bot_id) → agent_name` | `bot_agent_map` — résolu par le **hub**, pas TG/DC |
| **Harness / backend** | Même concept runtime : `claude-cli`, `omp-rpc`, `nats` | `agents.backend` |
| **Soul** (produit) | Document entier AgentSoul v1 (`soul.md`, 5 sections) | blobstore + pointeur DB |
| **persona_json** | **Legacy** — JSON inline pré-migration | colonne `agents` (à dropper Block 5) |
| **system_prompt** | String **composée** opaque envoyée aux harnesses | mémoire hub `Agent.config` ; jamais authoring |
| **soul_meta_json** | Envelope **non-prompt** : display_name, memory provision, cortex ext | colonne `agents` |
| **soul_document_blob_ref** | Pointeur `sha256:…` vers `soul.md` | colonne `agents` |

### Qui fait quoi (TG/DC vs hub)

```
Adapter TG/DC  = transport message uniquement
Hub            = bot_agent_map → agent → backend/model/soul → pool → harness
Web dashboard  = override harness/model par onglet (localStorage) — à aligner sur defaults DB
```

### Nommage public V1 (panel ratifié)

- API / BFF / NATS : préfixe **`soul`** (`soul.put`, `/api/bff/agents/{name}/soul`)
- Legacy : `persona_json`, routes `/persona` — **deprecated** pendant transition
- Code interne : module `persona.py` (nom fichier inchangé) ; fonctions `compose_soul_document()`, `parse_soul_markdown()`

---

## Workflow Git / PR — **validé opérateur** (2026-06-29)

> Toute l'implémentation se fait sur une **branche feature locale dédiée** (worktree recommandé via `/setup-worktree` une fois l'issue créée en fin de goal, ou branche ad hoc avant issue). La PR cible **`staging`**. Pas de push direct sur `staging`.

### Séquence

```
1. /goal … sur branche feature
2. Implémentation + make qg local par bloc
3. /pr  (create/update PR → staging ; rebase inclus si besoin)
4. /dev-core:code-review  (ou /code-review sur la PR)
5. /fix  si findings
6. ⟲ boucle 4→5 tant que review demande des changements
7. CI PR verte ?  (checks GitHub sur la PR)
   ├─ non → /fix + ⟲ retour 4 si nécessaire
   └─ oui → label reviewed + /ci-watch
```

### Détails par étape

| Étape | Skill / action | Notes |
|-------|----------------|-------|
| Branche | `/setup-worktree --issue N --slug persona-soul-blobstore` (post-issue) | Pattern `feat/N-slug` ; base = `staging` |
| Implémentation | `/goal` + checkboxes plan | `make qg` green à chaque bloc « done when » **avant** PR |
| PR | `/pr` | Guard : refuse commit sur `staging`/`main` ; rebase sur base post-create |
| Review | `/dev-core:code-review` ou `/code-review #N` | Verdict Approve requis pour sortir de la boucle |
| Fix | `/fix` | Applique findings review ; re-push ; re-review |
| CI | Status checks PR (`gh pr checks` / UI) | Tous verts avant label `reviewed` |
| Merge path | `reviewed` + `/ci-watch` | Watch dashboard ; auto-merge si configuré sur repo |

### Invariants workflow

- [ ] **Une PR** pour ce goal (épic #1760) — pas de stack multi-PR sauf découpage explicite noté au journal
- [ ] **Block order** respecté dans la branche feature avant ouverture PR (ou PR draft early si long — opérateur choisit)
- [ ] **Label `reviewed`** seulement après : review Approve **et** CI verte
- [ ] **`/ci-watch`** en dernière étape — surveille run + auto-merge éligible (`reviewed` + CI green)

```mermaid
flowchart TD
  A[Branche feature locale] --> B[/goal implémentation]
  B --> C[make qg par bloc]
  C --> D[/pr vers staging]
  D --> E[/code-review]
  E --> F{Findings?}
  F -->|oui| G[/fix]
  G --> E
  F -->|non Approve| H{CI verte?}
  H -->|non| G
  H -->|oui| I[label reviewed]
  I --> J[/ci-watch]
```

---

## Panel review — 2026-06-29 (5 experts)

| Expert | Verdict | Point bloquant principal |
|--------|---------|--------------------------|
| **Produit** | Presque GO | Block 4 = checklist ingénieur ; manque spec UX (5 onglets, lag, defaults chat) |
| **Architecte** | Conditional GO | **Sync load vs async blob** ; fetcher hors `core/` ; nommage soul incohérent |
| **DevOps** | Conditional GO | **Sweep blobstore 30j** vs refs immortelles ; runbooks migration/rollback absents |
| **Sécurité** | Conditional GO | PUT soul sans auth app (#1992) ; hub blobstore token non câblé ; jobs.launch bypass |
| **Axial-drift** | Approve with gates | import-linter + contract tests pas encore dans repo ; jobs.launch 2e résolveur defaults |

**Verdict global : CONDITIONAL GO** — architecture AgentSoul v1 validée ; intégrer les gardes ci-dessous **avant** `/goal` ou en Pre-flight / Block 1.

### Gardes obligatoires intégrées au plan

1. **Sync load contract** (Block 1) — write-through cache au `soul.put` ; `agent_row_to_config` ne fait pas `await` blob
2. **Parse/compose** dans `core/persona.py` seulement ; **fetch** dans bootstrap/infrastructure via `BlobStorePort`
3. **Sweep / rétention** — pin ou exempt soul refs du sweep 30j (`factory-blobstore-sweep`)
4. **Hub blobstore** — token + `init_blobstore()` sur factory-hub (Block 1)
5. **Block 2 avant B4 mutating** — fermer `jobs.launch` `system_prompt` + `model_cfg` depuis agent registry
6. **import-linter + contract tests** — Pre-flight stubs
7. **Runbooks** — migration, rollback, opérateur (Block 5)
8. **Threat model** — soul editor = tout membre Tailnet tant que #1992 hors scope

### Dissents enregistrés (non bloquants)

- Produit : masquer ligne mémoire V1 plutôt que badge « désactivé »
- Produit : onglets horizontaux pour 5 sections = **V1 required**
- Architecte : dériver `display_name` depuis `## Identity` à la save (éviter drift header vs section)
- Sécurité : editor soul interdit sur Tailnet partagé jusqu'à #1992 Phase 2
- Axial : `agent_refiner` patch `persona_json` = dette active jusqu'à follow-up

---

## Récap conversation (2026-06-29)

### Demande initiale

Interface dashboard pour configurer chaque agent et ses **défauts** :

- harness
- model
- voice
- prompt / soul spécifique

Question : la soul n'est injectée que dans `claudeHarness` ?

### Ce qu'on a découvert (état actuel, non modifié)

| Sujet | Réalité codebase |
|-------|------------------|
| Vocabulaire | Pas de type `soul` — équivalent = `persona_json` → `system_prompt` |
| SSoT runtime | `~/.roxabi/factory/config.db` → table `agents`, colonne `persona_json` (TEXT JSON) |
| Composition | `src/factory/core/persona.py` → `compose_system_prompt_from_json()` — appelé depuis `agent_db_loader.py` uniquement |
| Runtime hub | `Agent.config.system_prompt` (string composée au load) |
| Cache session | `pool._system_prompt` rempli au **1er tour** via `Agent._ensure_system_prompt()` |
| Mémoire optionnelle | `IDENTITY_ANCHOR` via roxabi-vault (`memory_namespace`) |
| Limite | `MAX_PROMPT_BYTES = 64 KiB` sur le prompt **composé** (`config/limits.py`) |
| Voice | `voice_json` inline en DB (`{"tts":{...},"stt":{...}}`) — pas encore blobstore |
| Model | `backend` + `model` en DB — déjà SSoT agent |
| Harness default | **`agents.backend`** en DB (`claude-cli` \| `omp-rpc` \| `nats`) — mais le dashboard **ignore** ça et hardcode `claude-cli`/`sonnet` dans `chats-storage.ts` + override par onglet `localStorage` |
| Dashboard (#1771) | Cockpit chat livré ; **pas** de page config agent ; BFF = status/sessions/jobs/ops |
| Édition aujourd'hui | `factory agent show/edit/patch`, plugin `refine-agent` |
| TOML | Seed seulement — seeder n'importe pas `persona_json` depuis TOML |

### Harness & injection soul

| Harness | Comportement soul |
|---------|-------------------|
| **claude-cli** (Clipool) | Injectée au **spawn** subprocess via `--system-prompt-file`. Tours suivants = stdin user seulement. Respawn si `entry.system_prompt` change (`cli_pool_send.py`, `cli_pool_streaming.py`) |
| **omp-rpc** (OMP) | `system_prompt` dans l'envelope mais **non appliqué** V1 — log `(system_prompt V1: not applied)` dans `omp_worker.py` |

**Invariant accepté :** la soul est **session-scoped**, pas message-scoped. Un edit persona **ne met pas à jour** les sessions actives (lag explicite jusqu'à nouveau pool / `/reset`).

### Clarifications opérateur (jargon)

1. **Contrat + RPC** → types partagés `packages/roxabi-contracts` ; hub communique via NATS request-reply (pattern `dashboard_rpc.py`)
2. **BFF + SPA** → React dashboard appelle FastAPI BFF (`/api/bff/*`) qui proxy vers hub NATS — **pas** d'accès direct `config.db` depuis le dashboard
3. **Avertissement OMP** → solution UI temporaire rejetée — l'opérateur veut la **parité OMP**, pas un band-aid

### Consensus panel (architect + devops + axial-drift)

Document : `artifacts/analyses/persona-soul-harness-parity-consensus.mdx` (commit `27170ae2`).

**Ratifié à l'unanimité :**

1. **Un seul composeur** : `core/persona.py` — pas de composition dashboard, OMP, clipool
2. **Hub résout le prompt effectif** : créer `resolve_effective_system_prompt(agent, pool) -> str`
3. **Harness = application seulement** : reçoivent `system_prompt: str` au bornage session, jamais `persona_json`
4. **Soul session-scoped** : lag accepté ; pas d'invalidation agressive de `pool._system_prompt` au hot-reload
5. **OMP V2** : parité CliPool (apply au `new_session`/`acquire`, stocker prompt sur entry, comparer/respawn)

**Rejeté :**

- Composition soul dans le SPA TypeScript
- `persona_json` dans `JobEnvelope`
- OMP qui parse/composer la persona
- Injection soul à chaque message
- Fichiers locaux comme SSoT (position finale opérateur)
- Avertissement UI seul à la place de corriger OMP

### Architecture AgentSoul v1 — **RATIFIÉ** (session 3, 2026-06-29)

#### Pourquoi `config.db` + blobstore (pas « tout dans le blob ») ?

Il n'y a **qu'une seule DB agent** : `~/.roxabi/factory/config.db`, table `agents`. Le blobstore n'est pas une base relationnelle — c'est un **magasin de bytes immuables** indexés par `sha256:`.

| Rôle | `config.db` | Blobstore |
|------|-------------|-----------|
| **Identifier** l'agent | `name` PK, `bot_agent_map` | — (pas de concept « agent lyra ») |
| **Scalars** queryables | `backend`, `model`, `voice_json`, `effort`… | — |
| **Pointer** version courante | `soul_document_blob_ref`, `updated_at` | bytes du doc |
| **Envelope** (non-prompt) | `soul_meta_json` : display_name, memory provision, cortex ext | — |
| **Relations** | `bot_agent_map`, `agent_runtime_state` | — |
| **Hot-reload** ADR-029 | `updated_at` poll → reload row | fetch si ref change |

Sans la row DB, le hub ne sait pas quel `sha256` est la soul active de `lyra`. Sans blobstore, on remet du TEXT long dans SQLite (row bloat). **Complémentaires, pas redondants.**

#### TG/DC = transport ; hub = résolution

```
Telegram/Discord adapter → run_inbound_guarded() → hub
Hub → bot_agent_map (platform, bot_id) → agent_name
Hub → agents.* depuis config.db → backend, model, soul composée
```

Seul override harness/model : **web** via `WebMeta` + `localStorage` (à corriger : lire defaults DB).

#### Modèle 5 sections + 1 document blob

**Soul** = le document entier. **5 sections** long-form (textarea), une question chacune :

| Section UI | Clé `##` | Question | Contient | Ne contient pas |
|------------|----------|----------|----------|-----------------|
| Identité | `Identity` | Qui suis-je ? | Rôle, mission, audience | Ton, procédures |
| Personnalité | `Personality` | Comment je sonne ? | Ton, style, humour | Mission, expertise |
| Valeurs | `Values` | Pourquoi / jamais ? | Éthique, limites | Procédures « comment » |
| Expertise | `Expertise` | Que sais-je ? | Domaines, profondeur | Traits de caractère |
| Directives | `Guidelines` | Comment j'agis ? | Heuristiques, patterns | Mission (→ Identity) |

**SSOT authoring** — un fichier markdown canonique :

```markdown
## Identity
…

## Personality
…

## Values
…

## Expertise
…

## Guidelines
…
```

Stockage :
```
config.db agents:
  soul_meta_json          -- envelope only (voir ci-dessous)
  soul_document_blob_ref  -- sha256:<hex>
  soul_document_bytes     -- metadata
  backend, model, voice_json, effort, …  -- scalaires
  updated_at              -- hot-reload

blobstore:
  soul.md (markdown) — SEUL artefact long, édité par dashboard / CLI / cortex (via hub)
```

`soul_meta_json` (envelope, **pas** du contenu prompt dupliqué) :

```json
{
  "schema_version": 1,
  "header": { "display_name": "Lyra", "tagline": "…" },
  "memory": { "enabled": false, "namespace": null, "source": "cortex", "sync_mode": "none" },
  "extensions": { "cortex": { "entity_slug": null, "sync_mode": "none" } }
}
```

Dashboard : **5 éditeurs** en onglets horizontaux (Identity … Guidelines) ; save = hub merge sections → ordre canonique → `soul.put` atomique (PUT blob → PATCH ref).

**Envelope UI (hors 5 sections)** : `display_name`, `tagline` éditables ; dérivés de `## Identity` à la save si vides (éviter drift).

Compose (`compose_soul_document()` dans `persona.py`, ordre fixe) → `system_prompt` → harness.

#### Limites

| Gate | Limite |
|------|--------|
| Document brut (`soul.md`) | **48 KiB** à la save |
| Prompt composé | **64 KiB** (`MAX_PROMPT_BYTES`) |
| Warnings par section | soft caps dashboard (optionnel V1) |

#### Mémoire — provision only V1

- **Ne pas** appeler `hub.set_memory()` (déjà 0 call site prod).
- **Ne pas** identity anchor / recall roxabi-vault dans ce goal.
- `memory.enabled: false` dans envelope ; cortex Layer B plus tard (ADR-010).
- À terme : cortex écrit le **même** `soul.md` via hub RPC — pas un 2e SSOT.

#### Vault personas — supprimé

Retirer `~/.roxabi-vault/personas/` du refine-agent.

#### Cortex (futur, hors scope impl)

Écriture via hub uniquement : `PUT soul.md` → ref bump → hot-reload. Jamais compose côté cortex.

> **Note :** mettre à jour `persona-soul-harness-parity-consensus.mdx` en Block 5.

---

## Contexte technique

### Couches prompt (cible)

```
Layer A — Authoring (SSoT)
  soul_document_blob_ref → fetch soul.md → compose_soul_document() [seul composeur, persona.py]
  soul_meta_json = envelope only (display_name, memory provision)
  → Agent.config.system_prompt (matérialisé au load / hot-reload)

Layer B — Session context (pool boundary) — V1 sans mémoire
  _ensure_system_prompt(pool) — 1er tour : pool._system_prompt = config.system_prompt
  (memory / cortex recall = epic futur, memory.enabled=false)

Layer C — Turn resolution (hub stage)
  resolve_effective_system_prompt(agent, pool) → str opaque
  → JobEnvelope.payload.system_prompt
  → harness APPLY au session boundary uniquement
```

### Fichiers clés

| Fichier | Rôle |
|---------|------|
| `src/factory/core/persona.py` | Composeur persona (SSoT composition) |
| `src/factory/core/agent/agent_db_loader.py` | Load agent + compose (à adapter blobstore) |
| `src/factory/core/agent/agent.py` | `_ensure_system_prompt`, memory anchor |
| `src/factory/core/agent/schema/agent_schema.py` | Schéma SQLite agents |
| `src/factory/infrastructure/stores/registry/agent_store.py` | CRUD agents |
| `src/factory/adapters/omp/omp_worker.py` | Gap OMP V1 |
| `src/factory/adapters/clipool/` | Référence parité session-boundary |
| `src/factory/bootstrap/factory/dashboard_rpc.py` | Pattern RPC hub existant |
| `src/factory/dashboard/routes/bff.py` | BFF proxy |
| `src/factory/blobstore/` + `packages/roxabi-blobs` | HTTP blobstore |
| `apps/dashboard/` | SPA React |
| `packages/roxabi-contracts/src/roxabi_contracts/dashboard/` | DTOs dashboard |

## Invariants globaux (tous blocs)

- [ ] **Un seul module composeur** : `core/persona.py` — `parse_soul_markdown()`, `merge_soul_sections()`, `compose_soul_document()` ; legacy `compose_system_prompt_from_json()` jusqu'à drop `persona_json` (B5)
- [ ] **Pas de fetch blob dans `core/`** — `BlobStorePort` injecté depuis bootstrap/infrastructure ; loader lit cache sync write-through
- [ ] **Pas de `persona_json` / blob bytes dans `JobEnvelope`** — seulement `system_prompt: str`
- [ ] **Harnesses ne parsent pas la persona** — executors dumb
- [ ] **Dashboard ne touche pas `config.db`** — BFF → hub NATS RPC uniquement
- [ ] **Dashboard ne compose pas** — preview via hub RPC
- [ ] **Soul session-scoped** — lag documenté ; runbook opérateur
- [ ] **ADR-094** : pas d'adapter `platform=dashboard` ; namespaces `/api/bff/*`
- [ ] **import-linter** : `factory.adapters` ↛ `core.persona` ; `factory.dashboard` ↛ `core.persona` / `agent_db_loader`
- [ ] Gates SLOC 300 / dossier 15 sur nouveaux chemins
- [ ] `make qg` green à chaque bloc « done when »

---

## PRE-FLIGHT — avant Block 1

> Prérequis décisionnels et spikes. Ne pas commencer Block 1 sans ces items (sauf dérogation notée au journal).

- [x] **AgentSoul v1** : 5 sections + 1 `soul.md` blob + `soul_meta_json` envelope
- [x] **Limites** : doc ≤ 48 KiB, composé ≤ 64 KiB
- [x] **Harness default** : `agents.backend` + `agents.model` (pas de nouvelle colonne)
- [x] **Memory** : provision `soul_meta_json.memory` ; pas de `set_memory()` V1
- [ ] **Contrats** : DTOs `AgentSoulDocument`, `SoulMetaEnvelope`, `AgentConfigGet/Update`, `SoulPreviewRequest/Response` — **GO bloquant Block 4 SPA**
- [ ] **import-linter stubs** : `adapters-no-persona`, `dashboard-no-persona` (voir Block 2)
- [ ] **Consensus Layer A** : mise à jour blobstore **avant** `/goal` (ou première slice B1) — éviter deux vérités
- [ ] **OMP Block 3** : introspection API au début du bloc (pas pre-flight bloquant)
- [ ] **Issue GitHub** : créée en fin de goal (après doc)
- [ ] **Threat model** : soul editor = contrôle plane ; Tailnet-only tant que #1992 hors scope

---

## BLOCK 1 — Migration stockage soul (DB maigre + blobstore)

**Statut :** `not_started`  
**GO :** après Pre-flight

### Schéma DB

- [ ] Migration SQLite : ajouter `soul_meta_json`, `soul_document_blob_ref`, `soul_document_bytes` à `agents`
- [ ] `soul_meta_json` : envelope only (header, memory provision, extensions.cortex) — pas de contenu section dupliqué
- [ ] `soul_document_*` : ref blobstore vers `soul.md` markdown — cap 48 KiB à l'écriture
- [ ] Garder `persona_json` temporairement pour rollback / backfill

### Backfill

- [ ] Script one-shot : pour chaque agent avec `persona_json` non vide
  - mapper legacy → sections AgentSoul v1 (voir table migration ci-dessous)
  - assembler `soul.md` avec headers `## Identity` … `## Guidelines`
  - PUT blobstore → PATCH `soul_document_blob_ref` + `soul_meta_json.header.display_name`
- [ ] Idempotent ; log agents migrés / skipped / erreurs
- [ ] Test : agent sans persona → envelope minimale + ref NULL OK

**Mapping legacy `persona_json` → AgentSoul v1 :**

| Legacy | Section cible |
|--------|---------------|
| `identity.*` (prose) | `## Identity` |
| `personality.*` (prose) | `## Personality` |
| éthique / limites (si présents) | `## Values` |
| `expertise.areas` | `## Expertise` |
| `expertise.instructions` | `## Guidelines` |

### Sync load contract (architecte — bloquant)

- [ ] **Write-through cache** : au `soul.put` / backfill, hub fetch+compose et peuple cache sync (`ref → bytes`, optionnel `ref → composed_prompt`)
- [ ] `agent_row_to_config()` lit **cache uniquement** (pas `await` blob) — compatible ADR-029 per-message reload
- [ ] Cache miss → fallback `persona_json` + log/metric `soul_cache_miss`

### Loader hub

- [ ] `parse_soul_markdown()` + `compose_soul_document()` + `merge_soul_sections()` dans `core/persona.py` (pur, sans I/O)
- [ ] `SoulDocumentFetcher` dans bootstrap/infrastructure : `BlobStorePort.get()` + LRU par `sha256`
- [ ] Adapter `agent_db_loader.py` : cache bytes → parse → compose ; fallback `persona_json`
- [ ] Hot-reload ADR-029 : `updated_at` / ref change → invalider cache agent (pas `pool._system_prompt`)
- [ ] Sections allowlist : exactement `Identity`, `Personality`, `Values`, `Expertise`, `Guidelines` ; sections inconnues rejetées à la save
- [ ] Append `_VOICE_TRANSCRIPT_INSTRUCTION` dans compose (inchangé) ; 64 KiB sur prompt **complet**

### Hub blobstore + rétention (devops + sécurité)

- [ ] Câbler `factory-hub` : `factory_blobstore_token`, `init_blobstore()`, fail-closed si blobstore down sur `soul.put`
- [ ] Politique sweep : pin / exempt refs `source=soul` du sweep 30j — refs actives ne doivent pas disparaître
- [ ] `soul.put` atomique : PUT → verify sha256 → PATCH ref + `updated_at` ; runbook si PATCH échoue (orphan blob)

### Consommateurs legacy

- [ ] `bot_display_name` → `soul_meta_json.header.display_name` (+ fallback `persona_json`)
- [ ] `agent_refiner` : documenter dette — patch `persona_json` invalide post-migration ; follow-up issue

### CLI / refine-agent

- [ ] `factory agent patch` : flow PUT `soul.md` → PATCH ref (hub-side ou CLI avec blob client)
- [ ] Plugin `refine-agent` : **supprimer** vault personas ; édition via même flow soul document

### Block 1 — done when

- [ ] Tous les agents prod migrés (ou script documenté + exécuté en staging)
- [ ] `agent_row_to_config` compose depuis blobstore
- [ ] pytest : migration, loader, cache, fallback inline
- [ ] Pas de régression `make qg` sur chemins agents

---

## BLOCK 2 — Hub centralisation prompt

**Statut :** `not_started`  
**GO :** après Block 1 green

### Primitive core

- [ ] Ajouter `resolve_effective_system_prompt(agent, pool) -> str` dans `core/` (nom final TBD)
  - appelle `_ensure_system_prompt(pool)` si besoin
  - retourne `pool._system_prompt or agent.config.system_prompt`
  - futur : fold `RuntimeConfig.overlay()` ici
- [ ] Remplacer call sites dispersés vers cette primitive (grep `pool._system_prompt`, `config.system_prompt` dans turn path)
- [ ] Appel depuis `SimpleAgent.process()` une fois par tour (après ensure)

### Metadata envelope (devops nuance)

- [ ] Ajouter `prompt_sha256` + `agent_updated_at` (ou `persona_blob_ref`) sur `JobEnvelope` metadata — pas dans payload persona

### Drift gates

- [ ] import-linter contracts (voir invariants)
- [ ] Contract test : `persona_json` ∉ `JobEnvelope.payload` keys
- [ ] Contract test : `system_prompt` type str only

### Bypass existant (bloquant avant B4 mutating)

- [ ] `DashboardJobsLaunchRequest.system_prompt` : **ignorer** valeur client ; hub résout depuis agent registry
- [ ] `jobs.launch` `model_cfg` : depuis `agents.backend` + `agents.model` (pas heuristique `job_name`)
- [ ] Primitive hub : `resolve_agent_runtime_defaults(agent_name) -> {backend, model}` (évite N×M dashboard)

### Block 2 — done when

- [ ] Un seul chemin turn-stage pour le prompt effectif
- [ ] Gates importlinter + contract tests green
- [ ] pytest turn-path avec mock pool

---

## BLOCK 3 — OMP V2 parité harness

**Statut :** `not_started`  
**GO :** après Block 2 green

> **Note OMP (pas de spike pre-/goal)** : le wire existe déjà (`OmpRpcDriver.complete()` envoie `system_prompt` ; `omp_worker` le lit mais ne l'applique pas — gap V1 **documenté** dans le plan #1813, V2 prévu). La parité est un **travail de câblage** (Clipool = référence), pas une inconnue architecturale. Première tâche Block 3 : introspection `omp_rpc.RpcClient` (~30 min) pour choisir le hook session (`new_session` / autre) — puis implémenter.

### Implémentation

- [ ] Introspection `omp_rpc` : méthode d'apply `system_prompt` au session boundary (première tâche du bloc)
- [ ] `omp_worker` : passer `system_prompt` à `acquire` / `bridge.run` ; appliquer au cold session
- [ ] Stocker `system_prompt` sur état session-scoped (miroir `_ProcessEntry.system_prompt` Clipool)
- [ ] Comparer au boundary : si changement → nouvelle session / respawn équivalent
- [ ] Retirer log `(system_prompt V1: not applied)` quand V2 actif

### Tests

- [ ] Parité sémantique avec tests Clipool respawn
- [ ] Test : même string soul claude-cli vs omp-rpc pour un agent donné
- [ ] Test : changement persona → nouvelle session OMP reçoit nouvelle soul

### Block 3 — done when

- [ ] OMP applique soul au session boundary
- [ ] pytest adapters/omp green
- [ ] Pas d'avertissement UI requis pour OMP (parité réelle)

---

## BLOCK 4 — Dashboard agents UI + BFF

**Statut :** `not_started`  
**GO :** après Block 1 green ; **routes mutating** (PUT/PATCH soul) seulement après **Block 2 green** (fermeture bypass + gates)

### Hub NATS RPC (nouveaux subjects)

- [ ] `factory.dashboard.agents.list` — liste agents + metadata (sans bytes soul)
- [ ] `factory.dashboard.agents.get` — config agent : harness default, model, voice_json, persona ref, format, bytes, updated_at
- [ ] `factory.dashboard.agents.patch` — update champs scalaires + voice_json
- [ ] `factory.dashboard.agents.soul.put` — `soul.md` bytes → PUT blobstore → PATCH ref (atomique côté hub)
- [ ] `factory.dashboard.agents.soul.preview` — compose preview (hub `compose_soul_document`) — **pas de compose SPA**
- [ ] `factory.dashboard.agents.soul.get` — parse sections pour les 5 éditeurs
- [ ] ACL matrix + `nats-regen-specs` + `factory-acl check grants`
- [ ] DTOs dans `roxabi-contracts` + `contracts-bump`

### BFF routes

- [ ] `GET /api/bff/agents` → list
- [ ] `GET /api/bff/agents/{name}` → get (enregistrer `/agents/status` **avant** `/{name}`)
- [ ] `PATCH /api/bff/agents/{name}` → patch scalaires
- [ ] `PUT /api/bff/agents/{name}/soul` → soul.put
- [ ] `GET /api/bff/agents/{name}/soul` → soul.get (sections parsées)
- [ ] `POST /api/bff/agents/{name}/soul/preview` → preview
- [ ] Pas d'import `infrastructure.stores` dans `factory.dashboard`

### SPA (`apps/dashboard/`)

- [ ] Route `/agents` (+ `/agents/:name` détail)
- [ ] Liste agents avec statut (réutiliser pattern `AgentStatusBadge`)
- [ ] Formulaire édition :
  - **backend** (harness default — `HarnessPicker`, `claude-cli` \| `omp-rpc` seulement)
  - **model** (`ModelPicker` / colonne `model` + `backend`)
  - **voice** (éditeur JSON guidé ou champs TTS/STT depuis `voice_json`)
  - **envelope** : `display_name`, `tagline` (champs courts)
  - **soul** : 5 onglets + textarea long-form par section + compteur bytes + preview (troncature 8k chars)
  - **memory** : ligne masquée V1 (ou tooltip info seul — dissent produit)
- [ ] **Lag UX** : callout persistant onglet Soul + toast post-save (« sessions en cours inchangées »)
- [ ] **Dirty state** : bandeau « modifications non enregistrées » + confirm navigation
- [ ] Sauvegarde : PATCH scalaires + PUT soul atomique via BFF
- [ ] Erreur si doc > 48 KiB ou composé > 64 KiB (message actionnable)
- [ ] Nouvel onglet chat : pré-remplir harness/model depuis defaults agent DB (remplacer hardcode `chats-storage.ts`)

### Intégration chat existant

- [ ] `newTab(agent)` : `GET agents/{name}` → `backend`, `model` (fallback hardcode + toast warning)
- [ ] HarnessPicker : indicateur si valeur ≠ défaut agent DB ; pas de sync auto onglets existants (documenter)
- [ ] Secret lint optionnel sur soul save : warn `sk-`, `ghp_`, `Bearer `, PEM

### Block 4 — done when

- [ ] Opérateur édite soul + harness + model + voice depuis dashboard
- [ ] Preview compose affiche le prompt effectif (tronqué si > N chars UI)
- [ ] vitest : pages agents + formulaires
- [ ] pytest BFF + hub RPC green
- [ ] `bun run typecheck` + `build:dashboard` green

---

## BLOCK 5 — Hardening + docs

**Statut :** `not_started`  
**GO :** après Blocks 1–4 green

### Ops

- [ ] Runbook `docs/runbooks/persona-soul-migration.md` : backup → migrate → backfill → vérif SQL
- [ ] Runbook `docs/runbooks/persona-soul-rollback.md` : revert ref sha256 ; backup pairé config.db+blobstore
- [ ] Runbook `docs/runbooks/persona-soul-operator.md` : edit → lag → `/reset` ; OMP vs clipool
- [ ] Étendre `blobstore-backup-restore.md` : soul refs + politique sweep
- [ ] Index dans `docs/runbooks/README.md`
- [ ] Audit operator : log structuré + event sur `soul.put` / patch agent
- [ ] Métriques : `soul_blob_fetch_errors`, `soul_compose_rejected_bytes`

### Docs

- [ ] Mettre à jour `docs/agent-management.md` (blobstore, plus inline-only)
- [ ] Mettre à jour `persona-soul-harness-parity-consensus.mdx` Layer A → blobstore
- [ ] Optionnel : snippet ADR ou appendix ADR-073

### Cleanup migration

- [ ] Slice finale : drop colonne `persona_json` (seulement quand backfill 100 % + staging validé)
- [ ] Retirer fallback inline dans loader

### Block 5 — done when

- [ ] Docs à jour
- [ ] `make qg` complet green
- [ ] PR mergée vers `staging` (via workflow review + `reviewed` + `/ci-watch`)
- [ ] smoke Tailnet : edit soul dashboard → nouveau chat → soul appliquée clipool + omp

---

## Hors scope (ce goal)

- Migration `voice_json` vers blobstore (reste inline DB pour ce goal)
- OIDC / auth middleware dashboard (#1992)
- Panels jobs/obs (#1772–#1774)
- Injection soul à chaque message
- Fichiers locaux par agent (`~/.../soul.md`) comme SSoT
- Rename NATS `web` → `dashboard` (ADR-094 phase 2)
- Merge `agent_refiner` meta-prompt dans `persona.py`

---

## Matrice couverture (exigences conversation)

| Exigence | Bloc | Couvert |
|----------|------|---------|
| UI config agent (harness, model, voice, soul) | B4 | oui |
| Soul pas seulement claude-cli (OMP parité) | B3 | oui |
| Composition centralisée hub | B2 | oui |
| Lag session/soul accepté | invariants + B4 UX | oui |
| DB maigre + blobstore (pas fichier local) | B1 | oui |
| BFF + SPA, pas accès direct DB | B4 | oui |
| Contrat + RPC NATS | Pre-flight + B4 | oui |
| Panel consensus ratifié | récap + B5 doc | oui |
| Cache hub persona | B1 | oui |
| `refine-agent` / CLI compat | B1 | oui |
| `backend`+`model` defaults agent (DB existant, UI + chat) | B4 | oui |
| AgentSoul v1 (5 sections + 1 soul.md) | B1 | oui |
| Memory provision only | B1+B4 | oui |
| Suppression vault personas refine-agent | B1 | oui |
| OMP spike non bloquant pre-goal | B3 note | oui |

---

## Risques & mitigations

| Risque | Mitigation |
|--------|------------|
| OMP API hook inconnu | Introspection première tâche B3 ; Clipool reste référence |
| Soul > 64 KiB composée | Validation hub ; warning dashboard ; revue limite |
| Blobstore indisponible | Cache write-through + fallback `persona_json` ; fail-closed sur `soul.put` |
| Sweep 30j supprime soul | Pin / exempt `source=soul` ; Block 1 done-when |
| Drift compose SPA | import-linter + preview uniquement via hub |
| Migration partielle | Fallback `persona_json` jusqu'à B5 cleanup |
| Tailnet non authentifié | Threat model #1992 ; pas d'usage multi-principal |
| jobs.launch bypass | Block 2 gate avant B4 mutating |
| Sync/async loader trap | Write-through cache Block 1 |

---

## Journal de progression

> Ajouter une entrée à chaque session `/goal`. Format : `YYYY-MM-DD — résumé — bloc — statut`.

### 2026-06-29 — Création du plan

- Recap conversation complet + décision migration blobstore validée par opérateur.
- Consensus harness parity existant (`persona-soul-harness-parity-consensus.mdx`) — blobstore pas encore reflété.
- **Aucune implémentation** — attente `/goal` explicite.

### 2026-06-29 — Décisions session 2–3

- `backend` = harness default ; hub résout tout (TG/DC = transport).
- **AgentSoul v1 ratifié** : 5 sections + 1 `soul.md` blob ; `soul_meta_json` = envelope only.
- Limites : doc 48 KiB, composé 64 KiB.
- Memory : provision only, `set_memory()` hors scope.
- Vault personas : deprecated.
- OMP spike : downgraded → première tâche Block 3, pas pre-flight.
- Issue GitHub : en fin de documentation.

### 2026-06-29 — Session 4 : glossaire + panel review (5 experts)

- Ajout glossaire (`config.db` = registre hub, pas TOML).
- Verdict **CONDITIONAL GO** : gardes sync cache, sweep, hub blobstore, Block 2 avant B4 mutating.
- Nommage unifié `soul` (BFF `/soul`, plus `/persona`).
- Spec UX Block 4 enrichie (onglets, lag, dirty state, display_name).
- Runbooks migration/rollback/operator en Block 5.

### 2026-06-29 — Session 5 : workflow PR validé

- Implémentation sur **branche feature dédiée** (pas `staging` direct).
- Boucle : `/pr` → `/code-review` → `/fix` (loop) → CI verte → `reviewed` → `/ci-watch`.
- Block 5 done-when : merge via PR, plus « push staging » direct.

### 2026-06-29 — Session 6 : `/goal` exécution

- Branche `feat/019f14c9-persona-soul-blobstore` (base `staging`).
- Blocks 1–4 implémentés : soul blobstore, hub prompt centralisation, OMP V2, dashboard agents UI.
- Block 5 : runbooks migration/rollback/operator, `agent-management.md`, consensus Layer A blobstore, refine-agent vault personas retiré.
- `make qg` green ; contract test `persona_json` ∉ golden JobEnvelope payload.
- **Écarts notés :** drop `persona_json` + retrait fallback loader différé (staging validation) ; `prompt_sha256` envelope metadata non ajouté (P2) ; smoke Tailnet manuel post-merge ; test OMP parity dédié clipool vs omp minimal (wiring seulement).

---

## Référence `/goal`

Coller ce chemin dans le prompt :

```text
/goal Execute artifacts/plans/persona-soul-blobstore-dashboard-goal.md on a dedicated feature branch (base staging).
Read the plan first. Update checkboxes and the progress journal after each completed slice.
Respect block order: Pre-flight → Block 1 → Block 2 → Block 3 → Block 4 → Block 5.
Do not skip gates listed under "done when".
Do not start implementation until this command is issued.

After goal complete: /pr → /code-review → /fix (loop) → CI green → label reviewed → /ci-watch
```