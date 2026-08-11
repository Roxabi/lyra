---
id: dev-factory-work-contract
title: Dev Factory — contrat Work (champs & états)
status: reflection
kind: base-de-reflexion
authority: non-normative
date: 2026-08-11
scope: >
  Esquisse du modèle de données et de la machine d’états du Work.
  Cas de réflexion v0 Gosilex/Spark. ¬SSoT runtime · ¬ratifié · ¬gate CI.
parent: dev-factory.md
related:
  - dev-factory.md
  - dev-factory-terminology.md
  - job-model.md
---

# Dev Factory — contrat Work

> **Base de réflexion** — pas une vérité runtime, pas un contrat wire figé.
> Sert à discuter champs / états avant implémentation.
> Vocabulaire : [dev-factory-terminology.md](./dev-factory-terminology.md).
> Cadre : [dev-factory.md](./dev-factory.md).

## Intent

Le **Work** est l’unité durable multi-jours. Ce doc propose :

1. un **schéma de champs** (registre)
2. une **machine d’états** indicative
3. les **invariants** à préserver à l’implémentation

Tout est amendable tant que `status: reflection`.

---

## Identité

| Champ | Type indicatif | Rôle |
|---|---|---|
| `work_id` | uuid | PK interne |
| `workflow_id` | string | ex. `gosilex-dev` |
| `workflow_version` | int \| semver | pin du YAML au moment du create (reprise stable) |

### Liens métier (Spark)

| Champ | Type | Rôle |
|---|---|---|
| `spark_client` | string | slug client |
| `spark_ticket_cuid` | string | id stable API |
| `spark_ticket_ref` | string \| int | ref affichable (`42`) |

**Unicité proposée (v0)** : au plus un Work *non-terminal* par `(spark_client, spark_ticket_cuid)`.

### Liens code (GitHub)

| Champ | Type | Rôle |
|---|---|---|
| `gh_repo` | `owner/name` | repo du projet Spark |
| `gh_issue` | int \| null | issue liée (après provision) |
| `branch` | string \| null | ex. `feat/42-slug` |
| `base_branch` | string | défaut `staging` |
| `head_sha` | sha \| null | dernier commit connu du Work |

### Exécution

| Champ | Type | Rôle |
|---|---|---|
| `step_id` | string \| null | step YAML courant |
| `status` | enum | voir machine d’états |
| `tier` | `S` \| `F-lite` \| `F-full` \| null | post-readiness |
| `sandbox_id` | string \| null | handle Sandbox Manager |
| `loop_counts` | map step→int | compteurs de boucles (`review_fix`, …) |
| `last_job_id` | string \| null | dernier Job step |
| `last_error` | string \| null | dernier échec lisible |
| `readiness` | object \| null | snapshot structured (ready, blockers, missing, rationale) |

### Horodatage / activité

| Champ | Rôle |
|---|---|
| `created_at` | création Work |
| `updated_at` | toute mutation registre |
| `last_activity_at` | dernière activité step/sandbox (idle policy) |
| `terminal_at` | si status terminal |

---

## Machine d’états (indicative)

```
                    ┌─────────┐
           create → │ queued  │  (pool sandbox plein, ou attente engine)
                    └────┬────┘
                         │ schedule
                         ▼
                    ┌─────────┐
          ┌────────▶│ running │◀────────┐
          │         └────┬────┘         │
          │              │              │
          │    ┌─────────┼─────────┐    │
          │    ▼         ▼         ▼    │
          │ waiting_  waiting_  (step    │
          │  hitl     blocked   ok →     │
          │    │         │     next)     │
          │    │         │              │
          └────┴─────────┴──────────────┘
                         │
           fail / cancel / success
                         ▼
              completed | failed | cancelled
```

| Status | Signification | Sandbox typique |
|---|---|---|
| `queued` | accepté, pas encore de Job actif (ou pool saturé) | absente ou hibernated |
| `running` | un Job de step est en cours (ou engine enchaîne) | active si step l’exige |
| `waiting_hitl` | Gate humaine ; pas d’auto-advance | hibernate OK |
| `waiting_blocked` | Readiness KO ou dépendance Spark | pas de sandbox dev |
| `completed` | terminal OK | destroy policy |
| `failed` | terminal erreur (loops max, crash non retriable) | destroy policy |
| `cancelled` | terminal opérateur / abandon ticket | destroy |

### Transitions à figer plus tard

- `todo` Spark répété sur Work `waiting_*` → **resume** (`running`), pas nouveau Work  
- `todo` sur Work `completed` → politique ouverte (noop vs nouveau cycle) — **à trancher**  
- timeout HITL → `failed` vs reste `waiting_hitl` — **à trancher**

---

## Invariants proposés

1. **¬deux Works non-terminaux** pour le même ticket Spark.  
2. Si `branch` set → `gh_repo` set.  
3. Si `status ∈ {running}` et step `needs_sandbox` → `sandbox_id` non null *ou* Job d’ensure en cours.  
4. `head_sha` mis à jour seulement après commit GitOps réussi.  
5. `tier` immuable après premier readiness OK (ou versionné explicitement si re-readiness).  
6. Compteurs `loop_counts` monotones ; dépassement `max_iters` → `failed` + Outbound.

---

## Relation au Job

```
Work.step_id = "frame"
  → mint Job { work_id, step_id: "frame", attempt: N }
  → JobResult
  → engine met à jour Work (step suivant | waiting_* | failed)
```

Le Work **n’est pas** un job longue durée unique (voir job-model).

---

## Questions ouvertes

| # | Question |
|---|---|
| W1 | Store : SQLite local factory vs KV NATS vs les deux ? |
| W2 | Re-readiness autorisée après `waiting_blocked` sans reset branch ? |
| W3 | Champ `attempt` global vs seulement par step dans `loop_counts` / jobs ? |
| W4 | Exposition dashboard / CLI (`factory work list`) — MVP champs minimaux ? |
| W5 | Homonyme code : type `DevWork` vs `Work` pour éviter collision WorkEnvelope ? | <!-- drift-ignore -->

---

## Graduation

Quand ratifié → merger le schéma dans une page living (ou ADR + store) et :

- `status: living` (ou retirer ce fichier au profit du domain page)
- `authority: normative`
- lier les gates / tests d’invariants
