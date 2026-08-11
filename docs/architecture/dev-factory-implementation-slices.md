---
id: dev-factory-implementation-slices
title: Dev Factory — slices d’implémentation
status: reflection
kind: base-de-reflexion
authority: non-normative
date: 2026-08-11
scope: >
  Découpage proposé des briques en slices livrables (ordre de construction).
  ¬issues GitHub créées · ¬engagement de dates · ¬scope figé.
parent: dev-factory.md
related:
  - dev-factory.md
  - dev-factory-terminology.md
  - dev-factory-work-contract.md
  - dev-factory-runners.md
  - dev-factory-spark-ingress.md
---

# Dev Factory — slices d’implémentation

> **Base de réflexion** — ordre de build pour ne pas commencer par le YAML final.
> À transformer plus tard en epic / issues GH si on lance le chantier.
> Parent : [dev-factory.md](./dev-factory.md).

## Principe

Construire les **contrats de briques** d’abord ; le Workflow YAML riche (F-full, loops) vient quand les runners existent.

Preuve à chaque slice = comportement observable (CLI, test, ticket Spark de dogfood), pas « doc only ».

---

## Carte des docs de réflexion

| Doc | Question |
|---|---|
| [dev-factory-terminology.md](./dev-factory-terminology.md) | Comment on nomme ? (**ratified**) |
| [dev-factory.md](./dev-factory.md) | Comment ça s’assemble ? (**design**) |
| [dev-factory-work-contract.md](./dev-factory-work-contract.md) | Qu’est-ce qu’un Work en data/états ? |
| [dev-factory-runners.md](./dev-factory-runners.md) | Comment un step s’exécute ? |
| [dev-factory-spark-ingress.md](./dev-factory-spark-ingress.md) | Comment Spark entre/sort ? |
| **ce doc** | Dans quel ordre on build ? |

---

## Slices proposées

### Slice 0 — Squelette Work + Engine

| Livrable | Preuve |
|---|---|
| Work Registry (store minimal + get_or_create) | CLI ou test : create/resume unicité ticket |
| Workflow Engine (sequence + terminal + loop counter) | YAML jouet 2–3 steps avance un Work en mémoire/disk |
| Loader YAML + validation types basiques | reject type inconnu |

**Hors slice** : Spark, git, sandbox, agent.

### Slice 1 — Ingress mince + readiness LLM

| Livrable | Preuve |
|---|---|
| Poll Spark `status=todo` (allowlist client) | ticket dogfood → Work créé |
| Event normalisé + dedupe | double poll ≠ double Work |
| Runner `llm` readiness | outputs structured en store |
| Runner `spark.comment` | comment visible si blocked |

**Hors slice** : branch, sandbox, agent.

### Slice 2 — Provision Git

| Livrable | Preuve |
|---|---|
| `github.ensure_issue` | issue GH liée au ticket |
| `git.ensure_branch` | branch `feat/…` poussée depuis staging |
| Update Work `gh_issue`, `branch`, `head_sha` | registry cohérent |

### Slice 3 — Sandbox lifecycle

| Livrable | Preuve |
|---|---|
| Sandbox Manager ensure / hibernate / destroy | cwd stable, idle sweeper unitaire ou manuel |
| Bind Work ↔ sandbox_id | destroy ne casse pas Work/branch |

### Slice 4 — Agent step

| Livrable | Preuve |
|---|---|
| Runner `agent` (backend omp ou clipool) | 1 step (frame **ou** implement S) |
| Commit artefacts via GitOps | fichiers sur la branch |
| Job meta `{ work_id, step_id, attempt }` | observable job-model |

### Slice 5 — PR / CI / loops

| Livrable | Preuve |
|---|---|
| `gh.pr_create` + `gh.wait_checks` | PR + conclusion |
| Loop YAML bornée (ci red → fix/implement) | `max_iters` → fail propre |
| Outbound `staging` post-merge | statut Spark correct (¬done) |

### Slice 6 — HITL + polish

| Livrable | Preuve |
|---|---|
| Gate `waiting_hitl` + signal reprise | approve reprend engine |
| Hibernate sandbox pendant wait | idle respecté |
| (opt) webhook Spark | latence ↓ |
| (opt) Mermaid depuis YAML | doc/dashboard |

---

## Dépendances

```
S0 ──▶ S1 ──▶ S2 ──▶ S3 ──▶ S4 ──▶ S5
                      │              │
                      └──────────────┴──▶ S6 (HITL peut glisser dès S4)
```

S1 peut dogfood métier sans git.  
S4 est le premier « vrai dev ».  
S5 ferme la boucle livrable Silex.

---

## Taille / risque (rough)

| Slice | Risque principal |
|---|---|
| S0 | Sur-designer l’engine avant un YAML réel |
| S1 | Qualité readiness LLM ; flakiness API Spark |
| S2 | Auth GH multi-repo ; conventions branch |
| S3 | Chemins host, perms, fuite disk |
| S4 | Coût tokens, non-déterminisme agent, omp ops |
| S5 | Flaky CI ; boucles mal bornées |
| S6 | UX HITL ambiguë |

---

## Hors backlog réflexion (plus tard)

- Généralisation hors Gosilex  
- Runtime Nika  
- Dashboard Dev Factory dédié  
- Multi-sandbox containers durs  
- Board Spark « Tâches »  

---

## Graduation

Quand on lance le chantier :

1. Epic GH (ou Spark) pointant ces slices  
2. Issues par slice avec AC = colonne « Preuve »  
3. Ce doc → archive ou `status: superseded` par l’epic living  
