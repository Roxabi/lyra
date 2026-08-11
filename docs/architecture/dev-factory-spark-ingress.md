---
id: dev-factory-spark-ingress
title: Dev Factory — Ingress Spark & Outbound
status: reflection
kind: base-de-reflexion
authority: non-normative
date: 2026-08-11
scope: >
  Esquisse connector Ingress Spark (events, dedupe, poll vs webhook) et
  canal Outbound (comments, status). Cas v0 Gosilex. ¬wire figé · ¬ratifié.
parent: dev-factory.md
related:
  - dev-factory.md
  - dev-factory-terminology.md
  - dev-factory-work-contract.md
  - observability.md
---

# Dev Factory — Ingress Spark & Outbound

> **Base de réflexion** — comment Spark parle à factory et inversement.
> S’appuie sur le pattern connector (ADR-096 / `src/factory/ingress/`) sans le spécifier ligne à ligne.
> Parent : [dev-factory.md](./dev-factory.md).

## Intent

| Direction | Brique | Rôle |
|---|---|---|
| Spark → factory | **Ingress** | signaux métier → events normalisés → Work Registry |
| factory → Spark | **Outbound** | comments, patch status ; canal contrôlé |

Spark reste la **vérité métier** pour l’humain (board Pilotage).  
Factory reste la **vérité d’orchestration** dev (Work, steps, jobs).

---

## Périmètre v0 (Gosilex)

| In scope | Out of scope v0 |
|---|---|
| Tickets **Pilotage** (pas board Tâches) | Webhooks multi-tenant génériques |
| Trigger principal : `status → todo` | Sync bidirectionnelle complète de tous les champs |
| Tâches **enfant** (épic = contexte) | Auto-dev sur l’épic entier |
| Links `parent`, `blocked_by`, `blocks` pour readiness | Toutes relations Spark |
| Comment + patch `staging` (pas `done` prématuré) | Notifs client publiques automatiques |

Rappel métier Silex : après merge **staging** → status Spark `staging` ; **`done`** seulement après promote main/prod (éviter faux « Livré »).

---

## Ingress — formes d’entrée

### Option A — Poll (biais v0)

```
cron / factory timer
  → GET tickets search?status=todo&client=…
  → pour chaque ticket : emit event interne
  → Work Registry get_or_create / resume
```

| Pour | Contre |
|---|---|
| Simple, pas de dep webhook Spark | Latence (minute) |
| Facile à dédupliquer | Charge API si large |

### Option B — Webhook

```
Spark status_changed → HTTP ingress factory
  → Connector.spark.verify
  → normalize → LyraEvent / DevFactoryEvent
  → same path que poll
```

| Pour | Contre |
|---|---|
| Temps réel | Exige endpoint + secret + support Spark |
| Moins de poll | Ops (TLS, ACL, replay) |

**Biais réflexion** : poll v0 → webhook quand le signal est stable.

Les deux doivent produire le **même event normalisé**.

---

## Event normalisé (proposition)

```yaml
# conceptuel — pas un schema JSON figé
source: spark
event_type: ticket.status_changed   # ou ticket.todo_observed
occurred_at: iso8601
dedupe_key: "spark:{client}:{ticket_cuid}:todo:{status_version_or_updated_at}"

spark:
  client: metalyde
  ticket_cuid: "clx…"
  ticket_ref: 42
  status: todo
  title: "…"
  body: "…"          # optionnel à l’ingress (fetch lazy OK)
  project_id: "…"
  project_name: "…"
  github_repo: "org/repo"   # si connu
  links:                    # optionnel à l’ingress ; readiness peut re-fetch
    parent: […]
    blocked_by: […]
    blocks: […]
```

### Dedupe

| Règle | |
|---|---|
| Même `dedupe_key` déjà traité récemment | drop |
| Ticket avec Work non-terminal | **resume** (ne pas créer) |
| Ticket terminal `completed` + nouveau `todo` | politique ouverte (W2 work-contract) |

---

## Mapping Ingress → Work

```
event
  → WorkRegistry.get_open(client, cuid)
       ├─ exists → touch, maybe re-queue engine if waiting_* and signal = todo/approve
       └─ none   → create Work { workflow_id: gosilex-dev, step: readiness, status: queued }
  → Engine.advance(work)  # ou enqueue
```

L’Ingress **ne lance pas** readiness lui-même : il assure seulement l’event + Work.

---

## Outbound — opérations

| Op | Usage | Notes |
|---|---|---|
| `comment` | blocked, progress, PR link, HITL prompt | défaut **internal** staff |
| `patch_status` | `staging` post-merge ; éventuellement autres | **pas** `done` auto staging |
| (plus tard) `patch` champs custom | — | — |

### Qui appelle Outbound ?

| Acteur | Autorisé ? |
|---|---|
| Runner `spark.*` | **oui** — chemin nominal |
| Agent Runtime tools | biais **non** (ou allowlist stricte) |
| Engine direct | non — passe par runner |

---

## Auth & secrets

| Secret | Usage |
|---|---|
| Spark PAT / M2M (`spu_…` ou équivalent factory) | API tickets |
| Webhook signing secret | si Option B |
| GitHub token | GitOps (brique séparée ; repo depuis projet Spark) |

Même discipline secrets que le reste factory (pas de PAT dans logs, podman secrets / env scoped).

---

## Connector factory (esquisse placement)

Aligné ADR-096 / `ingress.ports.Connector` :

| Méthode | Spark |
|---|---|
| `name` | `spark` |
| `verify` | signature webhook ou no-op poll interne |
| `parse_external_id` | `ticket_cuid` ou `client:ref` |
| `normalize` | → event ci-dessus |
| `apply_lifecycle` | install/enable tenant (si multi-client plus tard) |

Poll peut **contourner** HTTP et publier en interne le même `normalize` output — important pour un seul chemin engine.

---

## Fetch readiness (lazy)

L’event ingress peut être **mince**. Le step `llm` readiness (ou un pre-runner) fait :

```
tickets get + links + comments (+ parent epic get)
→ prompt structured
→ outputs ready/blocked/tier
→ si blocked : Outbound comment
```

Évite de surcharger l’ingress et garde un seul endroit « vérité ticket ».

---

## Questions ouvertes

| # | Question |
|---|---|
| S1 | Poll interval & multi-client allowlist config |
| S2 | Spark expose-t-il déjà un webhook status ? (à vérifier côté produit) |
| S3 | Signal HITL : comment magic `/approve` vs label vs bouton UI |
| S4 | Idempotence `patch_status` si déjà `staging` |
| S5 | Faut-il un event `ticket.updated` pour re-readiness auto ? |
| S6 | Tenant factory = `spark_client` ou org Gosilex unique v0 |

---

## Graduation

- Connector + tests verify/normalize  
- Runbook `runbooks/ingress-spark.md` (miroir `ingress-webhooks.md`)  
- `status: living` section dans domain page ou merge partiel dans `dev-factory.md`  
- ACL NATS / identity si worker poll dédié  
