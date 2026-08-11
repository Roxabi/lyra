---
id: dev-factory-runners
title: Dev Factory — catalogue runners & StepResult
status: reflection
kind: base-de-reflexion
authority: non-normative
date: 2026-08-11
scope: >
  Esquisse du contrat Step Runner / StepResult et d’un catalogue de `type`
  pour le Workflow Engine. ¬implémentation · ¬schéma wire figé · ¬ratifié.
parent: dev-factory.md
related:
  - dev-factory.md
  - dev-factory-terminology.md
  - dev-factory-work-contract.md
  - job-model.md
---

# Dev Factory — runners & StepResult

> **Base de réflexion** — catalogue et contrats d’exécution de steps.
> Pas de code, pas de subjects NATS figés. Amendable librement.
> Parent : [dev-factory.md](./dev-factory.md).

## Intent

L’**Workflow Engine** ne connaît que des `type` de steps.  
Chaque `type` est implémenté par un **Step Runner** enregistré dans le **Runner Catalog**.

Règle d’or (terminology) : **1 exécution de step = 1 Job** (retry = nouveau Job, `attempt+1`).

---

## Contrat générique

### WorkContext (entrée runner)

Données lues par le runner — **lecture** principalement ; mutations Work via engine après `StepResult`.

| Champ (indicatif) | Contenu |
|---|---|
| `work` | snapshot Work (ids, branch, tier, readiness, …) |
| `step_id` | nom du step YAML |
| `step_cfg` | bloc config du step (prompt, paths, skill, …) |
| `attempt` | n° de tentative Job |
| `cwd` | chemin sandbox si ensure déjà fait (sinon null) |
| `secrets` | handles / refs, pas de secrets en clair dans logs |

### StepResult (sortie)

| Champ | Type | Rôle |
|---|---|---|
| `status` | `ok` \| `fail` \| `wait` | pilotage engine |
| `outputs` | object | données pour transitions / steps suivants |
| `message` | string? | résumé humain / Outbound |
| `artifacts` | path[]? | chemins relatifs à commit (si agent/git) |
| `wait_reason` | `hitl` \| `blocked` \| `external`? | si `wait` |
| `error` | string? | si `fail` |
| `touch_sandbox` | bool? | refresh `last_activity_at` |

#### Sémantique `status`

| `status` | Engine |
|---|---|
| `ok` | suit `on.ok` / transition succès du step |
| `fail` | retry policy step **ou** `on.fail` **ou** Work `failed` |
| `wait` | Work → `waiting_hitl` / `waiting_blocked` ; **pas** de step suivant tant que signal de reprise |

Le runner **ne choisit pas** le prochain `step_id` (sauf outputs que le YAML conditionne). L’engine applique le graphe.

---

## Interface runner (pseudo)

```
protocol StepRunner:
  type: str                    # clé catalog
  needs_sandbox: bool          # hint static ou dérivé de step_cfg
  async run(ctx: WorkContext) -> StepResult
```

Enregistrement :

```
RunnerCatalog.register(runner)
RunnerCatalog.get("llm") -> StepRunner
```

Type YAML inconnu → fail validation workflow **avant** exécution (load-time).

---

## Catalogue v0 proposé (types)

### Contrôle de flux (engine-native ou runners triviaux)

| `type` | Rôle | Sandbox |
|---|---|---|
| `sequence` | sous-liste ordonnée (macro-step) | — |
| `switch` | branchement sur expression (`tier`, `ready`, …) | non |
| `terminal` | fin de Work (`completed` / mapping status) | non |

> Alternative : `sequence` / `switch` / `terminal` **hors** catalog, syntaxe engine pure. À trancher (R1).

### Métier & IO

| `type` | Rôle | Sandbox | Outputs typiques |
|---|---|---|---|
| `llm` | call structuré (readiness, classify) | non | object schema-validé |
| `spark.comment` | Outbound commentaire ticket | non | `comment_id`? |
| `spark.patch` | patch status / champs | non | `status` |
| `github.ensure_issue` | create/link issue via Spark ou gh | non | `gh_issue` |
| `git.ensure_branch` | crée/checkout branch | non\* | `branch`, `head_sha` |
| `sandbox.ensure` | alloue / warm sandbox | — | `sandbox_id`, `cwd` |
| `sandbox.hibernate` | force hibernate | — | — |
| `agent` | tour Agent Runtime + tools | souvent oui | `summary`, `artifacts[]` |
| `gh.pr_create` | ouvre PR | non | `pr_url`, `pr_number` |
| `gh.wait_checks` | attend CI | non | `conclusion` green/red |

\* `git.ensure_branch` peut s’exécuter hors sandbox (clone éphémère) ou dans sandbox vide — **à trancher** (R2).

### Runner `agent` — détails

| step_cfg (indicatif) | Rôle |
|---|---|
| `skill` | pack contrat (`frame`, `spec`, `implement`, …) |
| `backend` | `omp-rpc` \| `clipool` \| inherit Work/defaults |
| `commit.paths` | globs à commit post-run si OK |
| `gate` | `hitl` → Result `wait` après commit artefacts ? ou step séparé |

Deux shapes possibles pour HITL :

- **A** : runner `agent` finit `ok` + step suivant `type: gate` / `hitl`  
- **B** : `agent` avec `gate: hitl` renvoie `wait` après artefacts  

Préférence réflexion : **A** (séparation claire) — non figé.

---

## Mapping Job factory

| | |
|---|---|
| Mint | engine crée Job avant `runner.run` |
| Meta | `{ work_id, step_id, attempt, workflow_id, runner_type }` |
| Progress | optionnel pour `agent` long (`factory.job.<id>.progress`) |
| Result | `StepResult` sérialisé dans JobResult / side-channel engine |

Steps purement locaux ultra-courts (`switch`) peuvent **ne pas** mint de Job — exception documentée (R3). Sinon uniformité : tout step = Job.

---

## Erreurs & retries

| Cas | Comportement proposé |
|---|---|
| Erreur transport / 5xx Spark | retry Job (`attempt+1`) borné |
| Validation LLM schema fail | retry ou `fail` selon step_cfg |
| `max_iters` loop workflow | `fail` Work + Outbound (pas retry infini) |
| Agent timeout | `fail` Job ; policy step |

---

## Questions ouvertes

| # | Question |
|---|---|
| R1 | `switch`/`sequence`/`terminal` = engine AST ou runners ? |
| R2 | git hors sandbox ou toujours après `sandbox.ensure` ? |
| R3 | Jobs pour steps triviaux ? |
| R4 | Où vivent les packs `skill` (prompts) : factory repo vs package ? |
| R5 | L’agent a-t-il le droit d’appeler Spark directement, ou seulement via runners Outbound ? (biais : **non**, whitelist tools) |
| R6 | Subjects `factory.jobs.dev.*` vs réutiliser queues existantes |

---

## Graduation

Ratification → :

- interfaces Python dans `src/factory/…`
- table `type` stable (semver breaking si rename)
- tests contrat par runner
- page living ou section de `dev-factory.md` / workers-tooling
