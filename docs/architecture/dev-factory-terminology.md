---
id: dev-factory-terminology
title: Dev Factory — terminologie figée
status: ratified
kind: glossary
authority: normative-for-naming
date: 2026-08-10
updated: 2026-08-11
scope: >
  Usine de développement (cas v0 Gosilex / Spark), runtime roxabi-factory.
  Vocabulaire stable des briques — indépendant du contenu YAML des workflows.
  Normatif pour les *noms* uniquement ; l’archi cible reste design/reflection.
related:
  - dev-factory.md
  - dev-factory-work-contract.md
  - dev-factory-runners.md
  - dev-factory-spark-ingress.md
  - dev-factory-implementation-slices.md
  - job-model.md
---

# Dev Factory — terminologie

Source de vérité des **noms de briques** et de leurs définitions
(`authority: normative-for-naming`).

Architecture & flow cible : [dev-factory.md](./dev-factory.md) (`status: design`).  
Bases de réflexion (non-normatives) : work-contract · runners · spark-ingress · implementation-slices.

Le YAML des workflows est **souple** ; cette terminologie est **stable**.

**Règle d’or** : 1 exécution de step = **1 Job**. Pas de concept séparé « Step run ».

---

## Objets de vie

| Terme | Définition |
|---|---|
| **Work** | Unité durable de développement, liée à un ticket Spark (puis issue GH + branch). Survit aux redémarrages, sandboxes et steps. Identifié par `work_id`. |
| **Step** | Nœud nommé du workflow (ex. `readiness`, `frame`, `implement`). Décrit *quoi* faire à un moment donné du Work. Définition, pas exécution. |
| **Job** | Exécution technique d’un Step pour un Work (aligné job-model factory : open → progress/steer → result). Métadonnées : `{ work_id, step_id, attempt, workflow_id }`. Retry = nouveau Job, même `step_id`, `attempt+1`. |
| **Workflow** | Graphe déclaratif (YAML) : steps, transitions, conditions, boucles, gates. Versionnable, modifiable sans changer les briques. |
| **Tier** | Classe de taille du Work (`S` \| `F-lite` \| `F-full`), issue du readiness ; oriente le sous-chemin du Workflow. |

---

## Briques système

| Brique | Définition |
|---|---|
| **Ingress** | Entrée d’événements. Reçoit les signaux externes (ex. Spark ticket → `todo`), authentifie, normalise, déduplique. Ne décide pas du métier au-delà du signal. |
| **Work Registry** | Stocke et retrouve les Works (état, step courant, branch, liens Spark/GH, sandbox, compteurs de boucle). Garantit create vs resume et unicité *ticket → Work actif*. |
| **Workflow Engine** | Charge un Workflow, avance un Work step par step, applique transitions / switch / loops / gates. Dispatch vers les runners ; pas de logique Spark/Git en dur. |
| **Step Runner** | Implémentation d’un `type` de step (`llm`, `agent`, `spark.comment`, `git.ensure_branch`, …). Contrat : `run(context) → StepResult`. |
| **Runner Catalog** | Registre des `type` connus et de leur runner. Étendre le système = ajouter un type ici. |
| **Sandbox** | Environnement d’exécution isolé (worktree v0, container plus tard) pour **un** Work. Éphémère ; n’est pas la source de vérité du Work. |
| **Sandbox Manager** | Cycle de vie sandbox : `ensure`, `run`, `hibernate`, `destroy` + idle policy. Borne le parallélisme (`max_active`). |
| **GitOps** | Opérations Git/GitHub : branche, commit, push, PR, checks CI. Isolation **1 Work ↔ 1 branch**. |
| **Outbound** | Sortie métier vers Spark (commentaires, statut). Canal contrôlé. |
| **Agent Runtime** | Moteur de tour LLM + tools (omp / clipool). Utilisé par le runner `agent` ; interchangeable. |
| **Artifact** | Fichier produit par un step (frame, spec, plan, code…), **commité sur la branch du Work**. |
| **Gate (HITL)** | Porte d’approbation : Work → `waiting_hitl` ; sandbox peut hiberner ; reprise sur signal humain. |
| **Readiness** | Step (souvent `llm` seul) qui décide si le ticket est prêt, bloqué, et quel Tier — **avant** provision branch/sandbox. |

---

## Relations

```
Ingress  →  Work Registry  →  Workflow Engine
                                  │
                                  ▼
                           Step Runner(s)
                           ├── llm / spark / github  (souvent sans Sandbox)
                           └── agent  →  Agent Runtime + Sandbox
                                          │
                                          ▼
                                       GitOps (branch, Artifacts)
                                          │
                                          ▼
                                       Outbound (Spark)
```

```
Work  ──step courant──▶  Step (définition YAML)
                            │
                            ▼
                          Job  (cette exécution)
```

### Règles d’or

| Règle | |
|---|---|
| 1 ticket Spark en dev | ≤ **1 Work** actif |
| 1 Work | ≤ **1 branch** · ≤ **1 Sandbox** active |
| 1 Step en cours d’exécution | **1 Job** (retry = Job suivant, `attempt+1`) |
| Reprise | **Work + branch (+ `head_sha`)** ; la Sandbox se recrée |
| Workflow YAML | câblage souple ; ne redéfinit pas ces termes |

---

## Termes à éviter / préciser

| Ambigu | Préférer |
|---|---|
| « Session » seule | **Work** (durable) ou **session Agent Runtime** (éphémère) |
| « Pipeline » seule | **Workflow** (définition) ou **Job** (exécution) |
| « Issue » seule | **Spark ticket** vs **GitHub issue** |
| « Step run » | **Job** (fusionné — ne pas réintroduire) |
| « Factory » = le workflow | Factory = **plateforme** ; workflow = fichier + engine |

---

## Liste minimale

1. Ingress  
2. Work / Work Registry  
3. Workflow / Workflow Engine  
4. Step / Job / Step Runner / Runner Catalog  
5. Sandbox / Sandbox Manager  
6. GitOps  
7. Outbound  
8. Agent Runtime  
9. Artifact · Gate · Readiness · Tier  

---

## Hors scope de ce doc

- Contenu exact des YAML de workflow (changeable)
- Choix host M1 vs worker sandbox
- Implémentation détaillée des runners
- Généralisation hors Gosilex
