---
id: dev-factory
title: Dev Factory — architecture & flow
status: design
kind: architecture-target
authority: non-normative
date: 2026-08-10
updated: 2026-08-11
scope: >
  Usine de développement sur roxabi-factory. Cas v0 = Gosilex / Spark
  (tickets Pilotage → readiness → branch GitHub → pipeline dev).
  Vocabulaire : dev-factory-terminology.md (ratified).
  ¬SSoT runtime actuel — cible de design.
related:
  - dev-factory-terminology.md
  - dev-factory-work-contract.md
  - dev-factory-runners.md
  - dev-factory-spark-ingress.md
  - dev-factory-implementation-slices.md
  - job-model.md
  - messaging.md
---

# Dev Factory — architecture & flow

> Status: **DESIGN** — cible à implémenter. Pas l’état runtime actuel (`authority: non-normative`).
> Terminologie figée : [dev-factory-terminology.md](./dev-factory-terminology.md).
> Job technique : [job-model.md](./job-model.md).
>
> **Bases de réflexion** (détail amendable, `status: reflection`) :
> [work-contract](./dev-factory-work-contract.md) ·
> [runners](./dev-factory-runners.md) ·
> [spark-ingress](./dev-factory-spark-ingress.md) ·
> [implementation-slices](./dev-factory-implementation-slices.md).

## Thèse

Factory devient le **runtime d’une usine de développement** en plus du hub chat multi-bot.

- **Spark** = source métier (tickets, liens, statuts) et canal de feedback humain.
- **GitHub** = isolation code (**1 Work ↔ 1 branch**) + artefacts reviewables.
- **Workflow YAML** = câblage déclaratif (souple).
- **Briques ci-dessous** = stable ; on étend le catalogue de runners, pas le moteur.

Cas v0 : **Gosilex uniquement**. Généralisation multi-produit plus tard.

---

## Objectifs

| Objectif | Comment |
|---|---|
| Déclencher le dev depuis le métier | Ingress Spark (`status → todo` sur ticket Pilotage) |
| Ne pas coder un ticket pourri | **Readiness** (LLM, sans sandbox) avant provision |
| Isoler et paralléliser | 1 Work = 1 branch = ≤1 Sandbox active |
| Multi-étapes + loops | Workflow Engine + transitions / `max_iters` |
| Artefacts durables | commits sur la branch (frames, specs, code) |
| Coût compute borné | Sandbox hibernate / destroy sur idle |
| Lisible / modifiable | Workflow en YAML (esprit Nika) ; graphe Mermaid = phase 2 |

Non-objectifs v0 : board Spark « Tâches » (≠ Pilotage), multi-clients hors allowlist Gosilex, runtime Nika AGPL, dashboard dédié parfait.

---

## Architecture logique

```
                         ┌──────────────────────────────────────┐
   Spark webhook/poll ──▶│              INGRESS                 │
                         │  verify · normalize · dedupe         │
                         └──────────────────┬───────────────────┘
                                            │ event
                         ┌──────────────────▼───────────────────┐
                         │           WORK REGISTRY              │
                         │  get_or_create · resume · state      │
                         └──────────────────┬───────────────────┘
                                            │ Work
                         ┌──────────────────▼───────────────────┐
                         │         WORKFLOW ENGINE              │
                         │  load YAML · advance · gates · loops │
                         └──────────────────┬───────────────────┘
                                            │ dispatch Step → Job
              ┌─────────────────────────────┼─────────────────────────────┐
              ▼                             ▼                             ▼
     ┌────────────────┐           ┌────────────────┐           ┌────────────────┐
     │ Step Runners   │           │ Sandbox Mgr    │           │ GitOps         │
     │ (catalog)      │──────────▶│ ensure/hibernate│◀─────────│ branch/PR/CI   │
     │ llm agent …    │           │ destroy/idle   │           │                │
     └───────┬────────┘           └────────────────┘           └───────┬────────┘
             │ agent                                                    │
             ▼                                                          │
     ┌────────────────┐                                                 │
     │ Agent Runtime  │  omp-rpc / clipool                              │
     └────────────────┘                                                 │
             │                                                          │
             └──────────────────────┬───────────────────────────────────┘
                                    ▼
                         ┌────────────────┐
                         │   OUTBOUND     │  Spark comment / status
                         └────────────────┘
```

### Placement dans factory (hexagone)

| Couche | Contenu Dev Factory |
|---|---|
| **Ingress / adapters** | Connector Spark ; GitOps (gh/git) ; Outbound Spark ; Sandbox (FS/process) |
| **Application** | Work Registry · Workflow Engine · Runner Catalog |
| **Jobs / workers** | Chaque exécution de Step = **Job** (job-model) ; runner `agent` → omp / clipool |
| **Config** | Fichiers Workflow YAML versionnés dans le repo (ou config déployée) |

Principe : pas de logique Spark dans le core engine — seulement des **ports** + runners typés.

---

## Briques et responsabilités

| Brique | Responsabilité | Ne fait pas |
|---|---|---|
| **Ingress** | Signal → event normalisé | Décider readiness / tier |
| **Work Registry** | Cycle de vie multi-jours du Work ; unicité ticket→Work | Exécuter des steps |
| **Workflow Engine** | Interpréter le YAML ; avancer ; boucles ; gates | Appeler Spark/Git en dur |
| **Step Runner** | `run(ctx) → StepResult` pour un `type` | Connaître le graphe global |
| **Runner Catalog** | Map `type` → runner | Contenir le métier |
| **Sandbox Manager** | Isoler le FS/process d’un Work | Être SSoT de reprise |
| **GitOps** | Branch, commit, push, PR, checks | Orchestrer le workflow |
| **Outbound** | Feedback Spark contrôlé | Remplacer le Work store |
| **Agent Runtime** | Tour LLM + tools | Porter l’état workflow |

Détail des définitions : [dev-factory-terminology.md](./dev-factory-terminology.md).

### Work vs Job

| | **Work** | **Job** |
|---|---|---|
| Durée | jours | secondes → heures d’un step |
| Portée | tout le développement d’un ticket | une exécution d’un Step |
| SSoT reprise | Work + branch + `head_sha` | JobResult terminal |
| Cardinalité | 1 par ticket actif | N par Work (un par step / retry) |

```
Work meta job : { work_id, step_id, attempt, workflow_id }
```

Retry d’un step = **nouveau Job**, même `step_id`, `attempt+1`.

---

## Identités et isolation

| Identité | Rôle |
|---|---|
| `work_id` | Clé primaire pipeline |
| Spark `client` + `ticket_cuid` / ref | Lien métier ; clé d’unicité ingress |
| GitHub issue number | Créée/liée via Spark `github-create` |
| `branch` | `feat/{spark_ref}-{slug}` (convention v0) |
| `sandbox_id` | Handle éphémère (nullable) |
| `head_sha` | Dernier commit connu du Work |
| `job_id` | Run d’un step (job-model) |

### Règles d’or (parallélisme)

1. **≤ 1 Work actif** par ticket Spark en dev  
2. **1 Work ↔ 1 branch ↔ ≤ 1 Sandbox active**  
3. **1 Step en cours d’exécution ↔ 1 Job**  
4. Re-trigger `todo` sur le même ticket → **resume** Work, pas second Work  
5. N tickets en parallèle = N Works indépendants  

---

## Flow cible (Gosilex)

### Vue d’ensemble

```
[Spark] ticket enfant status → todo
        │
        ▼
[Ingress] normalize + dedupe
        │
        ▼
[Work Registry] get_or_create(Work)
        │
        ▼
[Engine] step = readiness          ←── pas de sandbox, pas de branch
        │
        ├─ blocked / immature ──▶ Outbound comment Spark
        │                         Work = waiting_blocked / terminal soft
        │
        └─ ready + tier ────────▶ provision
                                      │
                        github.ensure_issue
                        git.ensure_branch
                        sandbox.ensure
                                      │
                                      ▼
                        pipeline dev (selon Tier)
                        S      : implement → pr → ci → review ⇄ fix
                        F-lite : frame → spec → plan → implement → …
                        F-full : frame → analyze → spec → plan → …
                                      │
                        artefacts commités sur la branch
                        gates HITL → Work waiting_hitl
                                      │
                                      ▼
                        merge staging → Outbound status=staging
                        sandbox hibernate/destroy
                        Work = completed
```

### Phase 1 — Readiness (avant dev)

| | |
|---|---|
| Trigger | Ticket Pilotage (tâche enfant) passe `todo` |
| Inputs | ticket body, comments, **links** (`parent`, `blocked_by`, `blocks`) |
| Runner | `llm` (structured) |
| Sandbox / branch | **non** |
| Sorties | `ready`, `blocked`, `blockers[]`, `tier`, `missing[]`, `rationale` |
| Si KO | Outbound comment sur Spark ; Work en attente (re-trigger possible) |
| Si OK | continuer → provision |

L’**épic parent** informe le contexte ; ce n’est pas le Work de code (sauf décision contraire ultérieure).

### Phase 2 — Provision

| Step type (indicatif) | Effet |
|---|---|
| `github.ensure_issue` | Spark `github-create` ou reuse link existant |
| `git.ensure_branch` | Branch depuis `staging` (convention Silex) |
| `sandbox.ensure` | Worktree (v0) checkout branch |

À partir d’ici : isolation Git active → parallélisable.

### Phase 3 — Développement

- Chemin choisi par **Tier** (switch workflow).
- Steps `agent` s’appuient sur des **packs de contrats** alignés dev-core (frame / spec / plan / implement / review / fix) — pas sur le plugin Claude interactif `/dev`.
- Chaque step réussi qui produit des fichiers → **Artifact** commité via GitOps.
- **Gate HITL** (typiquement F-lite / F-full sur frame/spec/plan) :
  - Work → `waiting_hitl`
  - Sandbox → hibernate possible
  - Reprise sur signal (comment Spark, label, event dédié)

### Phase 4 — PR, CI, loops

```
implement ──▶ pr ──▶ ci
               │       │
               │       ├─ red  ──▶ (fix_ci | implement)  [max_iters]
               │       └─ green ──▶ review
               │                      │
               │                      ├─ changes_requested ──▶ fix ──▶ review  [max_iters]
               │                      └─ approved ──▶ done_staging
```

Boucles **bornées** dans le Workflow (`max_iters`) ; dépassement → fail explicite + Outbound.

### Phase 5 — Clôture

| Action | Détail |
|---|---|
| Outbound status | `staging` après merge sur staging — **pas** `done` trop tôt (règle Silex : `done` = main/prod) |
| Comment | lien PR / résumé |
| Sandbox | hibernate puis destroy selon policy |
| Work | `completed` (ou `failed` / `cancelled`) |

---

## Lifecycle Sandbox

```
                 ensure (premier step qui en a besoin)
                        │
                        ▼
                     ACTIVE ── touch à chaque Job sandbox
                        │
            idle > T1 (ex. 15–30 min)
                        ▼
                   HIBERNATED   (process off ; Work + branch restent)
                        │
            prochain step / approve HITL
                        ▼
                     ensure (warm) → ACTIVE
                        │
     Work terminal  OR  idle > T2 (ex. 7j)  OR  cancel
                        ▼
                    DESTROYED
```

| Règle | |
|---|---|
| SSoT de reprise | Work Registry + GitHub branch + `head_sha` |
| Session Agent Runtime | nice-to-have ; **non** requise pour resume |
| Pool | `max_active` sandboxes ; excès → Work `queued` |

---

## Workflow déclaratif (rôle, pas le fichier figé)

Le **Workflow** est un YAML versionné :

- steps nommés + `type` (catalog)
- transitions `on:`
- `switch` (tier, ready, ci…)
- loops + `max_iters`
- gates `hitl`
- policy sandbox idle (defaults)

**Modifiable sans redéployer l’architecture.**  
Le premier fichier n’a pas besoin d’être final : un graphe minimal suffit pour valider les briques.

Phase 2 possible : générer un graphe Mermaid depuis le YAML (doc / dashboard).

Inspiration modèle : Nika (Intent-as-Code) — **pas** adoption du runtime Nika en v0.

---

## Mapping job-model factory

| Concept Dev Factory | Job-model / platform |
|---|---|
| Job (exécution step) | `job_id` = run ; subjects `factory.job.<id>.*` |
| Dispatch runner long | `factory.jobs.<name>` (ex. `dev.agent`, `dev.git`) |
| Work (multi-jours) | **Nouveau** store (SQLite/KV) — distinct du registry jobs TTL court |
| Steer mid-step | existant Shape-D si step agent long |
| Agent backend | `omp-rpc` et/ou clipool via Agent Runtime pluggable |
| Ingress | pattern connector (ADR-096) — nouveau connector `spark` |

Le Work **n’est pas** un job longue durée unique : c’est une **machine à états** qui enchaîne des jobs courts/moyens.

---

## États Work (indicatif)

```
queued → running ⇄ waiting_hitl
                 ⇄ waiting_blocked
                 ⇄ queued (pool sandbox plein)
        → completed | failed | cancelled
```

Transitions exactes = responsabilité du Workflow + Engine ; la liste ci-dessus est le vocabulaire d’observabilité.

---

## Sécurité & secrets (cadre)

| Sujet | V0 |
|---|---|
| Spark API | PAT staff / secret connector (même famille que secrets factory) |
| GitHub | token repo cible (projet Spark `githubEnabled`) |
| Sandbox | pas de secrets host globaux montés en vrac ; injection scoped |
| Multi-tenant | allowlist clients Gosilex ; un Work ne voit que son repo/branch |

Détail ACL NATS : [security-routing.md](./security-routing.md) + matrix existante (à étendre pour identities `dev-*` si workers dédiés).

---

## Découpage d’implémentation (ordre des briques)

L’ordre construit les **contrats**, pas le YAML final.

| # | Livrable | Preuve |
|---|---|---|
| 1 | Work Registry + CLI/show | create/resume, unicité ticket |
| 2 | Workflow Engine minimal (sequence, switch, terminal, loop counter) | YAML jouet avance un Work |
| 3 | Ingress Spark (poll v0 OK) | event `todo` → Work |
| 4 | Runners `llm` + `spark.comment` | readiness E2E sans git |
| 5 | GitOps ensure_branch + `github.ensure_issue` | branch isolée |
| 6 | Sandbox Manager ensure/hibernate | cwd stable pour agent |
| 7 | Runner `agent` (omp) | frame ou implement S + commit Artifact |
| 8 | PR + wait CI + loops | cycle review/fix borné |
| 9 | Outbound status `staging` | boucle métier fermée |

---

## Décisions ouvertes (ne bloquent pas le doc)

| # | Sujet | Biais actuel |
|---|---|---|
| D1 | Webhook Spark vs poll | poll v0, webhook ensuite |
| D2 | Host sandboxes (M1 vs runner dédié) | orchestrateur factory ; compute sandbox peut être ailleurs |
| D3 | HITL exact (comment `/approve`, label, UI) | comment Spark d’abord |
| D4 | Packs prompts : in-repo factory vs package partagé | in-repo v0 |
| D5 | Nom stream/subjects `factory.jobs.dev.*` | à figer à l’implémentation |

---

## Documents liés

| Doc | Rôle | Authority |
|---|---|---|
| [dev-factory-terminology.md](./dev-factory-terminology.md) | Noms & définitions | **ratified** |
| [dev-factory-work-contract.md](./dev-factory-work-contract.md) | Champs & états Work | reflection |
| [dev-factory-runners.md](./dev-factory-runners.md) | Runners + StepResult | reflection |
| [dev-factory-spark-ingress.md](./dev-factory-spark-ingress.md) | Ingress / Outbound Spark | reflection |
| [dev-factory-implementation-slices.md](./dev-factory-implementation-slices.md) | Ordre de build | reflection |
| [job-model.md](./job-model.md) | Jobs techniques factory | living |
| [messaging.md](./messaging.md) | NATS / dispatch | living |
| ADR-096 | Ingress connector / tenant | ADR |
| ADR-084 | WorkEnvelope / job_id (≠ **Work** Dev Factory) | ADR |

> **Homonyme** : dans le job-model historique, « work » apparaît via WorkEnvelope. Ici **Work** = unité durable Dev Factory. En cas de doute dans le code, préférer `DevWork` / `work_id` documenté dans ce domaine. <!-- drift-ignore -->
