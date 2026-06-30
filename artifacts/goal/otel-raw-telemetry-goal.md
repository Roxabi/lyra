# /goal — OTel raw telemetry (workers + satellites, sans Langfuse v1)

> **Issue:** [#2069](https://github.com/Roxabi/roxabi-factory/issues/2069) — `feat(obs): OTel raw telemetry — adapter hooks + collector JSONL + dashboard raw` (parent : [#1760](https://github.com/Roxabi/roxabi-factory/issues/1760) control-plane · [#1759](https://github.com/Roxabi/roxabi-factory/issues/1759) engines)  
> **ADR cibles :** amendement [ADR-092](../../docs/architecture/adr/092-observability-architecture.mdx) · [ADR-094](../../docs/architecture/adr/094-control-plane-dashboard-consolidation.mdx) · nouveau **ADR-097** (otel-raw store)  
> **Références :** [ADR-068](../../docs/architecture/adr/068-ecosystem-service-plane.mdx) · [ADR-073](../../docs/architecture/adr/073-axial-stage-of-pipeline-decomposition.mdx) · [ADR-084](../../docs/architecture/job-model.md) · [runbook otel-traces](../../docs/runbooks/otel-traces.md)  
> **Statut global :** `phase_1_done` — Blocks 0–5 + 8 implémentés sur `feat/2069-otel-raw-telemetry` (2026-06-30) ; Blocks 6–7 satellites + Block 9 ingress différés

---

## Goal (one sentence)

Collecter des **spans OTel structurés et corrélés** (`pool_id`, `job_id`, skill, modèle, `blob_ref`) depuis **tous les workers NATS** (clipool, omp, voice, image, llm) et **llmcli proxy**, les stocker en **brut** (JSONL + index queryable), **sans Langfuse v1**, pour post-traitement et affichage dashboard raw plus tard.

---

## Démarrage rapide (humain)

```text
/goal Execute artifacts/goal/otel-raw-telemetry-goal.md
```

**Avant (optionnel)** :

```bash
git fetch origin staging && git checkout staging
git checkout -b feat/2069-otel-raw-telemetry
```

---

## Instructions agent `/goal` (lire en premier)

Ce fichier est **SSoT** pour l'exécution end-to-end. L'agent doit :

1. **Lire ce plan en entier** avant de coder.
2. **Phase 0 (design-only)** : rédiger ADR-097 + specs + amendements ADR — **aucun code instrumenté** tant que Phase 0 non approuvée.
3. **Phase 1+** : branche `feat/2069-otel-raw-telemetry` depuis `staging` — jamais commit direct sur `staging`.
4. **Ordre strict** : `Pre-flight → Block 0 → Block 1 → … → Block 8` — chaque bloc : **done when** + `make qg` avant le suivant.
5. **Livraison** : `/pr --base staging` → code-review → `/fix` → `make qg` → `/ci-watch`.
6. **Repos externes** (voiceCLI, imageCLI, llmCLI) : PRs séparées **après** merge contracts + `roxabi-nats` + `roxabi-otel` (Blocks 5–7).
7. **Journal** : mettre à jour § Statut par bloc + Journal de progression en fin de session.

### Verdict revue design (2026-06-30)

| Rôle | Verdict |
|------|---------|
| **Product** | GO conditionnel — besoin utilisateur aligné ; P0 specs attributs + corrélation |
| **Architect** | NO-GO implémentation — GO design ; OTel **pas** dans `roxabi-nats` deps |
| **Axial Drift** | GO axial — instrumentation une fois dans `NatsAdapterBase` via **hook** ; **F1 codec SSoT bloquant** |
| **DevOps** | GO Phase 1 conditionnel — collector JSONL + quadlet fix ; dashboard Phase 2 |

### Invariants non négociables

| Règle | Valeur |
|-------|--------|
| **Bytes** | `blobstore` uniquement — spans portent `blob_ref`, jamais audio/image/base64 |
| **Texte conversation** | `turns.db` — pas de duplication dans OTel |
| **Métadonnées timeline** | `otel-raw` (JSONL + index) — SSoT v1 pour post-traitement |
| **Langfuse v1** | **Hors chemin critique** — pas d'exporter Langfuse dans collector config prod v1 |
| **OTel SDK** | **Interdit** dans `packages/roxabi-nats` core — hook `MessageLifecycleHooks` (contracts) + impl `roxabi-otel` |
| **`roxabi-satellite`** | Inchangé — validation/blobs seulement ; pas de logique OTel |
| **Instrumentation workers** | **Une fois** via `NatsAdapterBase._dispatch()` — pas N×M par CLI (ADR-073) |
| **Hub codecs** | **Zéro** `uuid4()` local pour `trace_id`/`job_id` sur chemins work — helper SSoT (F1) |
| **PII** | Interdit dans spans — pas `OTEL_LOG_TOOL_DETAILS=1` ; attributs scrubbed Factory |
| **llmcli HTTP** | OTel LiteLLM séparé (`LITELLM_OTEL_V2`) — pas via `NatsAdapterBase` |
| **Clipool** | Double couche : span NATS (hook) + spans Claude Code OTel (existant) |
| **Platform adapters** | Telegram/Discord instrumentés via **hub** `TraceMiddleware` — pas `NatsAdapterBase` |

### Commandes gates

```bash
make qg
uv run pytest packages/roxabi-contracts/tests/ -q
uv run pytest packages/roxabi-nats/tests/test_adapter_base.py -q
uv run pytest src/factory/nats/ -q
uv run pytest src/factory/obs/ -q  # après Block 3
cd apps/dashboard && bun run build && bun run lint && bun run typecheck && bun run test  # Block 8
```

---

## Contexte

### Problème

L'opérateur veut savoir **quel skill, quel modèle, quand** — pour CliPool/Claude, OMP, LiteLLM, voiceCLI, imageCLI. Les **logs seuls** (Loki/journald) ne suffisent pas : pas de structure queryable cross-service. Langfuse self-hosted (6 containers) est **lourd** et **non exercé** ; le besoin immédiat est **collecter proprement maintenant, post-traiter plus tard**.

### État actuel codebase (vérifié)

| Élément | État |
|---------|------|
| `deploy/observability/otel-collector-config.yml` | Export **unique** → Langfuse |
| `factory-langfuse-*` (×6) + collector | Quadlets installés, **inactive/dead** sur dev |
| `factory-clipool.container` | OTel → `factory-otel-collector:4317` ; `TOOL_DETAILS=0` |
| `NatsAdapterBase` | Pas d'OTel ; `_dispatch()` → `handle()` |
| Hub codecs (`stt`, `tts`, `image`) | `trace_id=str(uuid4())`, `job_id=new_job_id()` — **décorrélé** du tour hub |
| `WorkEnvelope` | `job_id` + `trace_id` ; **pas** `pool_id` |
| `src/factory/obs/` | `ObservabilityProvider` scaffolding — **0 consommateur** |
| `ops_proxy.py` | Health Langfuse — **pas** de query traces |
| Satellites (voice/image/llm) | `NatsAdapterBase` + `roxabi-satellite` ; **pas** d'OTel |

### Direction validée (panel 2026-06-30)

| Décision | Choix |
|----------|-------|
| Protocole | **OTel OTLP** → `factory-otel-collector` |
| Sink v1 | **JSONL** `~/.local/state/factory/otel/` + **index SQLite** `otel-raw.db` (query dashboard) |
| Langfuse | **Différé** — retirer du collector v1 ; quadlets Langfuse **disabled** ou hors manifest |
| Instrumentation | **`MessageLifecycleHooks`** dans `NatsAdapterBase` (callback, zero dep OTel) |
| Package OTel | Nouveau **`packages/roxabi-otel`** (ou module dédié — **pas** `roxabi-obs` plan ③ fleet) |
| Hook domaine | **`telemetry_attributes(payload, result) -> dict`** optionnel par worker — défaut `{}` |
| Couverture skill | Si absent → `roxabi.skill=unknown` (explicite, pas silence) |
| Trois magasins | blobstore (bytes) · turns.db (texte) · otel-raw (métadonnées) |
| Dashboard v1 | **Raw viewer** — filtres `pool_id`, `job_id`, `component` — pas UI Langfuse |
| Cross-NATS nesting | **Différé** #1623 — v1 accepte spans siblings si spec parent-enfant clipool documentée |

---

## Non-goals (ce goal)

- Langfuse UI / ingestion ClickHouse en prod v1
- Post-traitement ETL / agrégats coût / ranking skills (pipeline batch futur)
- Cross-NATS W3C propagation complète (#1623 / harness #1490)
- Remplacer Loki/Promtail (logs texte restent parallèles)
- Dupliquer bytes blobstore dans spans OTel
- `OTEL_LOG_TOOL_DETAILS=1` pour skills (PII)
- Panels obs avancés #1774 (coût, Langfuse drill-down) — stub raw seulement
- Instrumentation Sentinelle `factory.event.*` (plane ① distinct)
- M₂ collector local (M₂ → M₁ tailnet uniquement en v1)

---

## Architecture contract (must hold after all blocks)

### Trois magasins complémentaires

```
┌─────────────────────────────────────────────────────────────────┐
│ BLOBSTORE          bytes (audio, images, PJ)                    │
│   ↔ spans: roxabi.blob_ref.in / roxabi.blob_ref.out only       │
├─────────────────────────────────────────────────────────────────┤
│ turns.db           texte conversation (L1 audit)              │
│   ↔ jointure via job_id / pool_id — pas de duplication OTel    │
├─────────────────────────────────────────────────────────────────┤
│ otel-raw           timeline métadonnées (spans, attrs, durées)   │
│   JSONL archive + SQLite index pour BFF dashboard               │
└─────────────────────────────────────────────────────────────────┘
```

### Flux télémétrie

```
INGRESS (Telegram/Discord/Web)
  TraceMiddleware hub → trace_id + job_id + pool_id
  mint_work_envelope_fields()  ← SSoT (Block 1)
        │
        ├─► clipool worker ──┐
        ├─► omp worker     ──┤
        ├─► voice-stt/tts  ──┼─► NatsAdapterBase._dispatch()
        ├─► image-worker   ──┤      → MessageLifecycleHooks (roxabi-otel)
        └─► llm-worker     ──┘      → telemetry_attributes() optional
        │
        └─► llmcli proxy HTTP ──► LITELLM_OTEL_V2 (séparé)
                │
                ▼
        factory-otel-collector (M₁)
                │
                ▼
        ~/.local/state/factory/otel/spans-*.jsonl
        ~/.roxabi/factory/otel-raw.db
                │
                ▼
        factory-dashboard BFF GET /api/bff/spans?...
```

### Package boundaries (normatif)

| Package | Contenu OTel |
|---------|----------------|
| `roxabi-contracts` | `MessageLifecycleHooks` Protocol · `telemetry/attrs.py` registry · `pool_id` optionnel sur `WorkEnvelope` (si arbitré Block 0) |
| `roxabi-nats` | `lifecycle_hooks: MessageLifecycleHooks \| None` sur `NatsAdapterBase` · appels best-effort dans `_dispatch()` · **zero** `import opentelemetry` |
| `roxabi-otel` (**nouveau**) | `OtelLifecycleHooks` · `NoopHooks` · export OTLP · `InMemorySpanRecorder` tests |
| `roxabi-satellite` | **Aucun changement OTel** |
| `src/factory/obs/` | Composition root wiring · décision `ObservabilityProvider` (fusionner ou déprécier) |
| `src/factory/nats/` | `mint_work_envelope_fields()` helper · codecs migrés (Block 1) |

### `MessageLifecycleHooks` (interface)

```python
# packages/roxabi-contracts — zero dep OTel
class MessageLifecycleHooks(Protocol):
    def on_work_start(
        self, *, trace_id: str, job_id: str, parent_job_id: str | None,
        pool_id: str | None, subject: str, envelope_name: str, queue_group: str,
    ) -> None: ...
    def on_work_end(
        self, *, trace_id: str, job_id: str, duration_ms: float,
        error: BaseException | None = None,
    ) -> None: ...
    def record_domain_attrs(self, attrs: Mapping[str, str | int | float | bool]) -> None: ...
```

**Invariants hooks :** ne lèvent jamais vers `handle()` · `try/except` + compteur drop · pas de PII.

### Hook domaine optionnel (workers)

```python
# Sur sous-classe NatsAdapterBase (voice/image/llm/clipool/omp)
def telemetry_attributes(self, payload: dict, result: object | None) -> dict[str, Any]:
    return {}  # défaut — attrs standard depuis envelope déjà dans la base
```

### Registry attributs span (SSoT — `roxabi.contracts.telemetry.attrs`)

| Attribut | Obligatoire | Source |
|----------|-------------|--------|
| `roxabi.trace_id` | work | `WorkEnvelope` (mapping OTel à spec Block 0) |
| `roxabi.job_id` | work | `WorkEnvelope` |
| `roxabi.parent_job_id` | si sub-job | `WorkEnvelope` |
| `roxabi.pool_id` | si présent wire | hub / envelope |
| `roxabi.component` | oui | `queue_group` |
| `roxabi.envelope_name` | oui | adapter config |
| `roxabi.subject` | oui | NATS subject |
| `roxabi.model` | si applicable | hook / LiteLLM / Claude |
| `roxabi.skill` | si applicable | hook ; sinon `unknown` |
| `roxabi.engine` | si applicable | voice/image hook |
| `roxabi.blob_ref.in` | si applicable | STT ingress |
| `roxabi.blob_ref.out` | si applicable | TTS/image output |
| **Interdit** | — | prompts, messages[], bytes, base64, secrets, chemins home |

### Décision `pool_id` (à trancher Block 0)

| Option | Recommandation panel |
|--------|---------------------|
| **A** — `pool_id: str \| None` sur `WorkEnvelope` | Préféré Product/Axial si bump contracts acceptable |
| **B** — `pool_id` attribut OTel hub-only (`session.id`) | Fallback si éviter bump multi-repo |

**Règle v1 :** si présent sur wire → **obligatoire sur span** ; si absent → pas de filtre pool sur ce hop.

### Corrélation IDs (Block 0 spec obligatoire)

| ID | Sémantique Factory | OTel |
|----|-------------------|------|
| `trace_id` wire | Arbre requête (ADR-084) | Trace ID OTel (mapping hex32 à figer) |
| `job_id` | Run / span identity | Span name ou `roxabi.job_id` attr |
| `pool_id` | Session mailbox ≈ conversation | `roxabi.pool_id` ou `session.id` |

**Clipool dual-layer :** spec lien span NATS job ↔ root trace Claude subprocess (même `roxabi.trace_id` wire minimum).

### Collector + Quadlet (cible v1)

| Changement | Détail |
|------------|--------|
| `otel-collector-config.yml` | Exporter `file` JSONL + **retirer** `otlphttp/langfuse` |
| `factory-otel-collector.container` | Retirer `After` Langfuse · volume RW `~/.local/state/factory/otel` · health `13133` publié localhost · OTLP tailnet M₂ + auth |
| `deploy/quadlet.toml` | Langfuse components → `disabled` ou section commentée + doc migration |
| Rétention | 7j ou 5 Go — rotation `logrotate` systemd timer |
| Env rollback | `ROXABI_OTEL_ENABLED=0` sur workers |

### Satellites externes (intégration)

| Repo | Mécanisme | Bloc |
|------|-----------|------|
| **voiceCLI** | `SttNatsAdapter` / `TtsNatsAdapter` : passer `OtelLifecycleHooks` au constructeur + `telemetry_attributes` | Block 5 |
| **imageCLI** | `ImageNatsAdapter` : idem + `blob_ref.out`, engine, dimensions | Block 6 |
| **llmCLI worker** | `LlmNatsAdapter` : idem + model, tokens | Block 7 |
| **llmCLI proxy** | `LITELLM_OTEL_V2` → collector M₁ tailnet | Block 4 |
| **Tous** | Bump `roxabi-nats` + `roxabi-otel` après publish factory | Post-merge |

---

## Acceptance criteria (goal complete)

### Functional

- [ ] **AC1** — ADR-097 + amendement ADR-092/094 mergés ; `docs/OBSERVABILITY.md` aligné
- [ ] **AC2** — `mint_work_envelope_fields()` : zéro `uuid4()` local trace_id sur codecs hub work paths
- [ ] **AC3** — `NatsAdapterBase` appelle hooks start/end ; tests passent avec `NoopHooks`
- [ ] **AC4** — `roxabi-otel` exporte spans testables (`InMemorySpanRecorder`) sans réseau
- [ ] **AC5** — Collector écrit JSONL sous `~/.local/state/factory/otel/` ; rotation documentée
- [ ] **AC6** — Index SQLite `otel-raw.db` queryable par `trace_id`, `job_id`, `pool_id`, `ts`
- [ ] **AC7** — E2E papier validé : message hub path → span JSONL avec `job_id` cohérent
- [ ] **AC8** — Clipool : span hook + spans Claude ; même `roxabi.trace_id` wire sur les deux
- [ ] **AC9** — llmcli proxy M₂ → collector M₁ avec auth ; span `gen_ai.request.model` visible
- [ ] **AC10** — voiceCLI STT span : `blob_ref.in`, model, duration (après PR satellite)
- [ ] **AC11** — imageCLI span : `blob_ref.out`, engine (après PR satellite)
- [ ] **AC12** — Dashboard BFF `GET /api/bff/spans` — JSON brut paginé, filtres pool/job/component
- [ ] **AC13** — Aucun span ne contient bytes/base64/prompt (test négatif CI)
- [ ] **AC14** — Langfuse quadlets non requis au démarrage collector ; runbook mis à jour

### Non-functional

- [ ] **AC-N1** — `ROXABI_OTEL_ENABLED=0` désactive export sans redeploy image
- [ ] **AC-N2** — Hooks ne bloquent jamais `handle()` (test fault injection)
- [ ] **AC-N3** — Partition alerte si `otel/` > seuil (script ou doc runbook)
- [ ] **AC-N4** — `make qg` vert sur tous les PRs

---

## Statut par bloc

| Bloc | Statut | Notes |
|------|--------|-------|
| Pre-flight | `done` | Issue [#2069](https://github.com/Roxabi/roxabi-factory/issues/2069) créée |
| Block 0 — Design artifacts | `done` | ADR-097, specs, amendements, OBSERVABILITY.md |
| Block 1 — Hub codec SSoT | `done` | `mint_work_envelope_fields` + codecs STT/TTS/image/socialmedia |
| Block 2 — contracts + hooks Protocol | `done` | `MessageLifecycleHooks`, attrs registry ; pool_id via TraceContext (Option B) |
| Block 3 — roxabi-otel + NatsAdapterBase wiring | `done` | Noop default, `ROXABI_OTEL_ENABLED` |
| Block 4 — factory-otel + quadlet | `done` | Replaced otel-collector; Langfuse decoupled |
| Block 5 — Factory workers (clipool, omp) | `done` | telemetry_attributes + bootstrap wiring |
| Block 6 — voiceCLI PR | `pending` | repo externe |
| Block 7 — imageCLI + llmCLI PRs | `pending` | repos externes |
| Block 8 — Dashboard raw BFF + UI | `done` | `GET /api/bff/spans` + `/spans` page |
| Block 9 — Hub TraceMiddleware + client spans | `done` | mint_work on LLM codecs, hub_tracer client/ingress spans |

---

## Blocks (execution order)

### Pre-flight

| Tâche | Done when |
|-------|-----------|
| Issue GitHub [#2069](https://github.com/Roxabi/roxabi-factory/issues/2069) | ✅ créée 2026-06-30 |
| Lier branche `feat/2069-otel-raw-telemetry` | `gh issue develop 2069` ou panneau Development |
| Lire revue design § Verdict + Invariants | Agent confirme |

---

### Block 0 — Design artifacts (NO CODE)

**Bloquant — livrables docs uniquement**

| Fichier | Contenu |
|---------|---------|
| `docs/architecture/adr/097-otel-raw-telemetry-store.mdx` | Context, decision 3 stores, collector JSONL, Langfuse deferred |
| Amendement `docs/architecture/adr/092-observability-architecture.mdx` | Trace plane v1 = otel-raw ; Langfuse optional later |
| Amendement `docs/architecture/adr/094-control-plane-dashboard-consolidation.mdx` | Engine `otel-raw` ; BFF raw viewer |
| `artifacts/specs/otel-raw-store-spec.mdx` | JSONL schema, SQLite schema, retention, ADR-068 access pattern |
| `artifacts/specs/otel-correlation-ids-spec.mdx` | trace_id wire ↔ OTel ; job_id ↔ span ; clipool dual-layer |
| `artifacts/specs/otel-span-attributes-spec.mdx` | Registry obligatoires/interdits ; skill=unknown |
| `docs/OBSERVABILITY.md` | Réaligner Phase 1 (plus « Langfuse only ») |

**Décision PO requise :** `pool_id` Option A vs B (voir § Architecture)

```bash
# Gate Block 0
# Revue humaine : cocher AC1 specs présentes ; pas de pytest
```

**Done when :** 4 specs + ADR-097 en PR design ; panel sign-off ; § Statut Block 0 = `done`

---

### Block 1 — Hub codec SSoT (F1 axial)

| Fichier | Change |
|---------|--------|
| `src/factory/nats/envelope_fields.py` (nouveau) | `mint_work_envelope_fields(trace_id, job_id, parent_job_id, pool_id?)` |
| `src/factory/nats/stt/nats_stt_codec.py` | Consommer helper + `TraceContext` |
| `src/factory/nats/audio/nats_tts_codec.py` | Idem |
| `src/factory/nats/image/nats_image_codec.py` | Idem |
| `src/factory/nats/llm/` codecs | Idem |
| `src/factory/core/trace.py` | Documenter source `trace_id` |
| `tests/factory/nats/test_envelope_fields.py` | Pas de remint uuid4 sur work paths |

```bash
uv run pytest tests/factory/nats/ src/factory/nats/ -q
```

**Done when :** grep `trace_id=str(uuid4())` absent des codecs hub work ; tests verts

---

### Block 2 — Contracts + hooks Protocol

| Fichier | Change |
|---------|--------|
| `packages/roxabi-contracts/src/roxabi_contracts/telemetry/` | `hooks.py`, `attrs.py` |
| `packages/roxabi-contracts/src/roxabi_contracts/envelope.py` | `pool_id` si Option A |
| `packages/roxabi-nats/src/roxabi_nats/adapter_base.py` | `lifecycle_hooks` param · calls in `_dispatch` |
| `packages/roxabi-nats/tests/test_adapter_base.py` | NoopHooks · fault injection |

```bash
uv run pytest packages/roxabi-contracts/tests/ packages/roxabi-nats/tests/test_adapter_base.py -q
```

**Done when :** adapter appelle hooks ; handle() inchangé fonctionnellement ; 0 import otel dans roxabi-nats

---

### Block 3 — Package `roxabi-otel`

| Fichier | Change |
|---------|--------|
| `packages/roxabi-otel/pyproject.toml` | deps `opentelemetry-*` optional extra |
| `packages/roxabi-otel/src/roxabi_otel/hooks.py` | `OtelLifecycleHooks`, `NoopHooks`, `InMemorySpanRecorder` |
| `src/factory/bootstrap/` ou `obs/` | Wire hooks au démarrage workers factory |
| `src/factory/obs/base.py` | Décision fusion/dépréciation documentée |

```bash
uv run pytest packages/roxabi-otel/tests/ -q
```

**Done when :** span mémoire en test unitaire ; export OTLP derrière `OTEL_EXPORTER_OTLP_ENDPOINT`

---

### Block 4 — Collector + quadlet

| Fichier | Change |
|---------|--------|
| `deploy/observability/otel-collector-config.yml` | `file` exporter JSONL ; remove langfuse |
| `deploy/quadlet/factory-otel-collector.container` | Volume RW, deps, ports, health 13133 |
| `deploy/quadlet.toml` | Langfuse disabled / documented |
| `deploy/scripts/bootstrap-otel-raw.sh` (nouveau) | Dirs + permissions |
| `docs/runbooks/otel-traces.md` | Réécriture otel-raw centric |
| `docs/data-dirs.md` | `~/.local/state/factory/otel/` |

```bash
# Dev smoke
systemctl --user start factory-otel-collector
curl -sf http://127.0.0.1:13133/ && echo ok
```

**Done when :** collector écrit JSONL ; pas de dépendance Langfuse au start ; runbook à jour

---

### Block 5 — Factory workers (clipool + omp)

| Fichier | Change |
|---------|--------|
| `src/factory/adapters/clipool/clipool_worker.py` | `telemetry_attributes` : pool_id, agent, skill scrubbed |
| `src/factory/adapters/omp/omp_worker.py` | model, runtime=omp |
| `deploy/quadlet/factory-clipool.container` | Vérifier OTEL endpoint inchangé |

```bash
uv run pytest tests/adapters/test_clipool_worker.py tests/adapters/omp/ -q
```

**Done when :** E2E dev : clipool job → ligne JSONL avec `roxabi.job_id`

---

### Block 6 — voiceCLI (repo externe)

| Repo | Change |
|------|--------|
| `~/projects/voiceCLI` | Bump roxabi-nats + roxabi-otel ; `telemetry_attributes` STT/TTS |
| PR séparée | Après merge factory Blocks 2–4 |

**Done when :** STT request → span avec `blob_ref.in` + model

---

### Block 7 — imageCLI + llmCLI (repos externes)

| Repo | Change |
|------|--------|
| `imageCLI` | telemetry_attributes : engine, blob_ref.out |
| `llmCLI` worker NATS | telemetry_attributes : model |
| `llmCLI` proxy | `LITELLM_OTEL_V2` + endpoint M₁ tailnet |

**Done when :** OMP path → span llmcli visible dans JSONL

---

### Block 8 — Dashboard raw BFF + UI

| Fichier | Change |
|---------|--------|
| `src/factory/dashboard/routes/bff.py` | `GET /api/bff/spans` |
| `src/factory/dashboard/otel_raw_reader.py` (nouveau) | SQLite + tail JSONL |
| `apps/dashboard/src/pages/` | SpansRawPage ou panneau #1774 réduit |
| `src/factory/dashboard/ops_proxy.py` | Langfuse health → `deferred` |

```bash
cd apps/dashboard && bun run test && bun run build
```

**Done when :** UI liste spans filtrés ; JSON brut au clic

---

### Block 9 — Hub ingress spans (complément)

| Fichier | Change |
|---------|--------|
| `src/factory/core/trace.py` / middleware | Span parent ou event structuré dispatch |
| `src/factory/nats/*_client.py` | Client spans vers workers (prep #1623) |

**Done when :** hub → worker même `roxabi.trace_id` sur papier E2E Telegram

---

## Bloquants P0 (rappel panel)

| ID | Bloquant | Bloc résolution |
|----|----------|-----------------|
| B1 | ADR-092/094 vs code Langfuse | Block 0 |
| B2 | Codecs remintent trace_id | Block 1 |
| B3 | Mapping trace_id Factory ↔ OTel | Block 0 spec |
| B4 | Collector quadlet incompatible | Block 4 |
| B5 | Rétention disque JSONL | Block 0 spec + Block 4 |
| B6 | OTLP M₂ auth/bind | Block 4 |
| B7 | pool_id propagation | Block 0 decision + Block 1 |
| B8 | Hub hors NatsAdapterBase | Block 9 |
| B9 | Clipool dual-layer | Block 0 spec + Block 5 |
| B10 | OTel SDK dans roxabi-nats | Block 2 (interdit) |

---

## Risques 3 mois (surveillance)

| Risque | Mitigation dans ce goal |
|--------|-------------------------|
| JSONL disque plein | Rétention Block 0/4 |
| skill=unknown partout | Hook optionnel + métrique couverture dashboard |
| Deux traces clipool orphelines | Spec corrélation Block 0 |
| PII dans spans | Registry interdits + CI test |
| Langfuse fantômes | quadlet.toml Block 4 |
| Post-process jamais fait | JSONL + SQLite = consumable ; ETL hors scope |

---

## Workflow PR

```mermaid
flowchart TD
  A[Block N done] --> B[make qg]
  B --> C[/pr base staging]
  C --> D[code-review]
  D --> E{findings?}
  E -->|oui| F[/fix]
  F --> D
  E -->|non| G[/ci-watch]
```

- **PR design** : Block 0 seul (docs)
- **PR factory** : Blocks 1–5 + 4 + 8 (selon phasage PO)
- **PRs satellites** : Blocks 6–7 après merge packages

---

## Journal de progression

| Date | Agent / humain | Bloc | Note |
|------|----------------|------|------|
| 2026-06-30 | Grok | — | Goal créé ; design validé panel 4 rôles ; impl NO-GO |
| 2026-06-30 | Grok | Pre-flight | Issue [#2069](https://github.com/Roxabi/roxabi-factory/issues/2069) ouverte |
| 2026-06-30 | Grok | Blocks 0–5,8 | Impl factory : ADR-097, roxabi-otel, collector JSONL, BFF spans |
| 2026-06-30 | Grok | Fix round | Hook job_id correlation, deferred clipool/omp hooks, dashboard otel mounts, 7 evidence logs, make qg green |
| 2026-06-30 | Grok | Block 4b | factory-otel maison (blobstore-style); otel-collector disabled; runbook + satellite rollout spec |
| 2026-06-30 | Grok | Block 9 | P0 LLM codec propagation, P1 WorkerPoolClient spans, P2 hub ingress span |

---

## Références revue design

Consolidation conversation + subagents :

- **Product** : besoin logs+stats+skills ; post-process ; blob_ref not bytes ; GO conditionnel P0 specs
- **Architect** : `roxabi-otel` package ; hook not SDK in nats ; NO-GO impl sans ADR-097
- **Axial** : NatsAdapterBase hook = stage-axial OK ; F1 codec SSoT bloquant ; GO sous 7 correctifs
- **DevOps** : collector JSONL ; quadlet fix ; M₂ tailnet ; Phase 1 GO si B1–B8 ; dashboard Phase 2

---

## Checklist avant `/goal` implémentation

- [x] Issue GitHub [#2069](https://github.com/Roxabi/roxabi-factory/issues/2069) créée
- [x] Block 0 mergé (design artifacts)
- [x] Décision `pool_id` — **Option B** (TraceContext + domain models ; pas de champ `WorkEnvelope` — conflit pyright/cli)
- [ ] Invariants § relus par l'agent
- [ ] `Statut global` → `phase_0_done` puis `in_progress`