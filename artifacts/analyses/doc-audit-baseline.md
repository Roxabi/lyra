# Doc Audit — Phase 0 Baseline

> Generated: 2026-06-17  
> Scope: living docs (`docs/`, root `README.md`, `CLAUDE.md` tree) vs code + M₁ truth  
> Method: automated gates + semantic grep + SSH snapshot `roxabituwer`

---

## Executive summary

| Signal | Result |
|--------|--------|
| `check_doc_drift.py` | **PASS** — 0 new violations, burn-down baseline empty |
| `check_architecture_snapshot.sh` | **PASS** — `CURRENT.generated.md` up to date |
| `check_secrets_drift.sh` | **PASS** |
| `check-qg-conf-drift.sh` | **PASS** |
| Semantic drift (living docs) | **20+ findings** — not covered by CI |
| M₁ vs `QUADLET-DEPLOYMENT.md` caveat | **Contradiction majeure** — migration #1710 faite en prod |

**Verdict Phase 0** : les gates mécaniques sont verts, mais la doc **living** est en retard sur l'état prod post-#1670/#1710. Priorité = deploy/ops/onboarding, pas l'architecture domain pages.

---

## M₁ production truth (oracle terrain)

Captured 2026-06-17 via `ssh roxabituwer`.

| Claim | Doc | Reality M₁ |
|-------|-----|------------|
| Unit naming | `QUADLET-DEPLOYMENT.md` L9 : legacy `lyra-*`, migration **pending** | **18 `factory-*` units**, **0 `lyra-*`** |
| Data dir | `~/.lyra` pending move | **`~/.roxabi/factory/`**, `~/.lyra` absent |
| Repo path | `~/projects/lyra` | **`~/projects/roxabi-factory`** (staging @ `8b666aca`) |
| Images | `ghcr.io/roxabi/lyra:staging{,-svc}` | **`ghcr.io/roxabi/factory:staging{,-svc}`** |
| Secrets | `lyra-nats-*` / `lyra-bot-*` | **`factory-nats-*`**, **`factory-bot-*`** (4 bots) |
| hub.env vars | `FACTORY_HEALTH_*` (code + example) | **`FACTORY_HEALTH_*`** (migré 2026-06-17, F18) |
| Containers running | DEPLOYMENT.md : **6** | **9 factory** + voicecli-stt/tts + llmcli (hors scope Lyra core) |
| Health | — | `ok`, NATS ok, 4 adapters, uptime healthy post-restart |

**Action** : caveat L9 remplacé (runbooks) ; F18 host-env migré sur M₁ + machine locale.

---

## Findings by severity

### P0 — Opérationnel / trompeur

| ID | File | Line(s) | Problem | Oracle | Fix |
|----|------|---------|---------|--------|-----|
| F01 | `docs/QUADLET-DEPLOYMENT.md` | 9 | Caveat dit M₁ en legacy `lyra-*`, migration pending #1710 | M₁ SSH | Supprimer caveat ; ajouter état prod actuel |
| F02 | `docs/DEPLOYMENT.md` | 9, 11–19 | « six containers » ; arbre systemd incomplet (manque nats, turn-writer, blobstore, omp) | `deploy/quadlet.toml` (9 components) + M₁ | Réécrire overview → 9 conteneurs |
| F03 | `docs/DEPLOYMENT.md` | 77, 142, 189–193, 208, 340 | `make lyra reload/logs/errors/stop` | `Makefile` — **pas de target `lyra`**, seulement `factory`, `remote` | Remplacer par `make factory` / `make remote factory reload` |
| F04 | `docs/GETTING-STARTED.md` | 393–394, 456–458 | `make lyra status/reload/logs` | `Makefile` | Idem F03 |
| F05 | `docs/MULTI-BOT.md` | 298, 301, 322 | `make lyra reload/logs` | `Makefile` | Idem F03 |
| F06 | `README.md` | 9, 235 | Badge + section **MIT** | `pyproject.toml` + `LICENSE` = **AGPL-3.0-or-later** | Aligner licence |

### P1 — Incohérence inter-docs / CLI rename incomplet

| ID | File | Line(s) | Problem | Oracle | Fix |
|----|------|---------|---------|--------|-----|
| F07 | `docs/QUADLET-DEPLOYMENT.md` | 18–24 | Colonne Image = `lyra` pour 7 services | `deploy/quadlet/*.container` → `ghcr.io/roxabi/factory:*` | `factory:staging` ou `staging-svc` |
| F08 | `docs/COMMANDS.md` | 7–71, 93+ | Section entière « The `lyra` CLI » ; commandes `lyra config validate` etc. | `factory` entrypoint (`pyproject.toml`) | Renommer section ; `factory` primaire, `lyra` alias historique si existe |
| F09 | `docs/QUICKSTART.md` | 24+ | « `lyra` CLI on your PATH » | `uv sync` → `factory` script | `factory` |
| F10 | `docs/ops/container-publishing.md` | 34, 27 | `lyra config validate` dans HEALTHCHECK / svc-runtime | `factory config validate` exists | Renommer |
| F11 | `docs/ROADMAP.md` | 10, 22, 182, 251 | Focus « NATS 4-process » comme état actuel | 9 conteneurs + OMP shipped | Mettre à jour current focus ; last-updated 2026-04-27 |
| F12 | `docs/architecture/tool-architecture.md` | 20, 111 | « parked pending #1670 » | #1670 cutover done (postmortem 2026-06-03) | Requalifier statut implémentation |
| F13 | `docs/ops/container-publishing.md` | 148–154 | Table images : manque **`factory-omp`** | `deploy/quadlet.toml` + M₁ `factory-omp:staging` | Ajouter ligne OMP |

### P2 — Dette documentée / reliquats acceptables

| ID | File | Line(s) | Problem | Verdict |
|----|------|---------|---------|---------|
| F14 | `docs/GETTING-STARTED.md` | 347 | `lyra-monitor` removed | OK — correctly marked removed |
| F15 | `docs/OBSERVABILITY.md` | 124 | `lyra-monitor` removed | OK |
| F16 | `docs/CONFIGURATION.md` | 698 | `lyra-monitor` removed | OK |
| F17 | `docs/architecture/adapters.md` | 47 | mention `make lyra` | Fix in Phase 1 deploy agent |
| F18 | M₁ `hub.env` | — | `LYRA_HEALTH_*` vs code `FACTORY_HEALTH_*` | Host config debt — doc + migrate script |
| F19 | `docs/history/*` | many | `lyra-*`, `~/.lyra` | **KEEP** — historical, do not edit |
| F20 | Product name « Lyra » | many | Brand vs binary `factory` | KEEP product name ; fix **commands/paths/images** only |

---

## Automated gates (detail)

```
check_doc_drift.py     → OK (0 new, baseline empty)
arch snapshot          → OK (CURRENT.generated.md current)
secrets_drift          → OK (quadlet.toml ↔ manifest ↔ acl-matrix)
qg.conf drift          → OK (stack.yml SSoT)
```

**Gap** : aucun gate ne détecte :
- état prod vs runbook (sémantique)
- `make lyra` / `lyra` CLI dans living docs
- compteur conteneurs
- licence README vs pyproject

**Recommandation Phase C** : `tools/check_doc_semantic_drift.sh` avec allowlist `docs/history/`.

---

## Living docs inventory (55 files)

| Bucket | Files | Staleness signal |
|--------|-------|------------------|
| Root guides | `DEPLOYMENT`, `QUADLET-DEPLOYMENT`, `GETTING-STARTED`, `CONFIGURATION`, `COMMANDS`, `QUICKSTART`, `ROADMAP`, `MULTI-BOT` | **High** — deploy/CLI |
| Architecture domain | 15 pages under `docs/architecture/` | **Low–Medium** — mostly 2026-05/06, tool-arch #1670 stale |
| Ops runbooks | 12 under `docs/ops/` | **Medium** — `lyra` CLI refs, GH rotation TODO |
| History | 2 under `docs/history/` | **N/A** — immutable |
| Standards/process | 6 | **Low** |
| Generated | `CURRENT.generated.md` | **OK** |

54/55 living docs newer than `ROADMAP.md` (2026-04-27) — sauf ROADMAP lui-même et `HAPPY-PATHS.md`.

---

## Cross-doc contradictions (same concept, different answer)

| Concept | Doc A | Doc B |
|---------|-------|-------|
| Container count | DEPLOYMENT: **6** | ARCHITECTURE + QUADLET: **9** |
| M₁ naming | QUADLET caveat: **lyra-*** | GETTING-STARTED service list: **factory-*** |
| Make targets | DEPLOYMENT: **`make lyra`** | Makefile: **`make factory`**, **`make remote`** |
| Health env | hub.env.example: **`FACTORY_HEALTH_*`** | M₁ prod: était **`LYRA_HEALTH_*`** → **aligné F18** |
| CLI binary | COMMANDS: **`lyra`** | pyproject scripts: **`factory`** |
| Licence | README: **MIT** | pyproject: **AGPL-3.0-or-later** |

---

## Semantic scan (living docs only, excl. `docs/history/`)

Pattern hits (20 lines):

```
docs/QUADLET-DEPLOYMENT.md:9   — lyra-* caveat (F01)
docs/DEPLOYMENT.md:9           — six containers (F02)
docs/DEPLOYMENT.md             — make lyra ×8 (F03)
docs/GETTING-STARTED.md        — make lyra ×5 (F04)
docs/MULTI-BOT.md              — make lyra ×3 (F05)
docs/architecture/adapters.md:47 — make lyra (F17)
```

Additional `lyra` word counts (not all stale — product name OK):
- `COMMANDS.md`: 18
- `GETTING-STARTED.md`: 23
- `QUICKSTART.md`: 5

---

## Phase 1 agent assignment (from findings)

| Agent | Files | Findings | Est. effort |
|-------|-------|----------|-------------|
| **Deploy** | `DEPLOYMENT.md`, `QUADLET-DEPLOYMENT.md`, `deploy/CLAUDE.md` | F01–F03, F07, F13 | S |
| **Onboarding/CLI** | `COMMANDS.md`, `QUICKSTART.md`, `GETTING-STARTED.md`, `MULTI-BOT.md` | F04–F05, F08–F09 | M |
| **README/Licence** | `README.md` | F06 | XS |
| **Roadmap/Status** | `ROADMAP.md`, `ARCHITECTURE.md` (status section) | F11 | S |
| **Ops** | `docs/ops/container-publishing.md`, `hub.env` migration note | F10, F13, F18 | S |
| **Architecture** | `tool-architecture.md`, spot-check domain pages | F12 | XS |

**Suggested PR stack (Phase 3)** :
1. `fix(docs): align deploy docs with M1 and quadlet.toml` (F01–F03, F07)
2. `fix(docs): replace make lyra and lyra CLI with factory` (F04–F05, F08–F10)
3. `fix(readme): correct AGPL license` (F06)
4. `chore(docs): refresh ROADMAP and tool-arch status` (F11–F12)

---

## Out of scope (Phase 0 confirmed)

- `docs/history/*` — historical, keep as-is
- `docs/architecture/adr/*` — immutable
- `artifacts/*` (sauf ce baseline) — not living SSoT
- `plugins/lyra-ops/` — separate repo/marketplace ; audit Phase 1b
- Claude.md tree (31 files) — deferred Phase 1 ; grep shows low `lyra-*` infra drift

---

## Phase 1 progress

| Finding | Status | PR |
|---------|--------|-----|
| F01 — QUADLET caveat `lyra-*` | ✅ Fixed | deploy PR1 |
| F02 — DEPLOYMENT six→nine containers | ✅ Fixed | deploy PR1 |
| F03 — `make lyra` → `make factory` | ✅ Fixed | deploy PR1 |
| F07 — QUADLET Image column | ✅ Fixed | deploy PR1 |
| deploy/CLAUDE.md lyra relics | ✅ Fixed | deploy PR1 |
| F04 — GETTING-STARTED `make lyra` | ✅ Fixed | onboarding PR2 |
| F05 — MULTI-BOT `make lyra` | ✅ Fixed | onboarding PR2 |
| F08 — COMMANDS.md `lyra` CLI section | ✅ Fixed | onboarding PR2 |
| F09 — QUICKSTART `lyra` CLI / `cd lyra` | ✅ Fixed | onboarding PR2 |
| F06 — README MIT → AGPL | ✅ Fixed | licence PR3 |
| F10 — container-publishing HEALTHCHECK | ✅ Fixed | ops PR4 |
| F11 — ROADMAP current focus | ✅ Fixed | status PR4 |
| F12 — tool-architecture #1670 parked | ✅ Fixed | status PR4 |
| F13 — container-publishing factory-omp | ✅ Fixed | ops PR4 |
| deployment.md 4→9 containers | ✅ Fixed | status PR4 |
| adapters.md make lyra + voice units | ✅ Fixed | ops PR4 |
| F18 — M₁/local `hub.env` `LYRA_HEALTH_*` | ✅ Fixed | host-env 2026-06-17 |

## Next step

**Phase 1 doc audit — closed** (F01–F18 living-doc + host-env items). Phase 2 optionnel : domain pages, ADR archive spot-checks (`LYRA_HEALTH_*` mentions historiques OK).