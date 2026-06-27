# Goal — factory-dashboard (#1771)

> Plan vivant pour l'exécution `/goal`. Mettre à jour ce fichier au fur et à mesure :
> cocher les tâches, noter les écarts, ajouter des entrées dans le journal de progression.

| Champ | Valeur |
|-------|--------|
| **Issue** | [#1771](https://github.com/Roxabi/roxabi-factory/issues/1771) — extend `factory-dashboard` shell |
| **Epic parent** | [#1760](https://github.com/Roxabi/roxabi-factory/issues/1760) — control-plane operator console |
| **ADR** | [ADR-094](../../docs/architecture/adr/094-control-plane-dashboard-consolidation.mdx) |
| **Branche** | `staging` |
| **Statut global** | `done` |
| **Dernière MAJ** | 2026-06-28 |
| **Panel review** | 2026-06-28 — tri-expert (MVP / epic-complet / risk-first) → SAFE WITH GUARDS |

## Statut par bloc

| Bloc | Statut | Notes |
|------|--------|-------|
| Pre-flight | `done` | import-linter + contracts + Makefile |
| Block 1 — Cockpit + Chat + Harness/Model | `done` | SPA + vitest + Docker/CI |
| Block 2 — SessionCatalog + Reprendre | `done` | hub RPC + BFF + ACL regen |
| Block 3 — E2E + Hardening + Ship | `done` | Playwright visual + push staging |

---

## Contexte

- **ADR-094** : 1 container `factory-dashboard`, 2 axes internes
  - **Axis 1 (chat)** : `src/factory/adapters/web/` — `run_inbound_guarded`, NATS, SSE
  - **Axis 2 (BFF)** : `src/factory/dashboard/` — static SPA, proxy obs/jobs (futur)
- **Rejeté** : adapter parallèle `platform=dashboard` (#1770)
- **Design** : Forge v2 dense (`#0a0a0f` / `#e85d04`), `brand/` + `packages/shared`
- **#1772 / #1773 / #1774** : stubs de panneaux seulement dans ce goal

## Invariants globaux (tous blocs)

- [x] Pas d'adapter `platform=dashboard` (#1770 reste mort)
- [x] Chat POST → `run_inbound_guarded` uniquement (pas de raccourci BFF vers hub bus)
- [x] `factory.dashboard` **ne doit pas** importer `factory.infrastructure.stores.*`
- [x] `WebMeta.session_id` = transport SSE ≠ `cli_session_id` (resume TurnStore)
- [x] Namespaces routes : `/api/chat/*` + `/api/stream/*` (adapter) vs `/api/bff/*` (dashboard)
- [x] **Pas** de mount `turns.db` sur le container `factory-dashboard`
- [x] Gates SLOC 300 / dossier 15 sur tous les nouveaux chemins

---

## PRE-FLIGHT — avant Block 1

> Prérequis mécaniques et contractuels. Ne pas commencer Block 1 sans ces items (sauf dérogation notée au journal).

- [x] Contrat import-linter pour `factory.dashboard` (pas `infrastructure` ; imports bornés depuis `adapters.web`)
- [x] Extraire les routes de `web_server.py` → `src/factory/dashboard/` (`web_server.py` < 300 SLOC)
- [x] `factory/dashboard/AGENTS.md` — taxonomie session IDs + split des 2 axes
- [x] `roxabi-contracts` : DTOs `ChatRequest`, `SseEvent`, `AgentHealth`, `DashboardSession`
- [x] Makefile : cibles `build-dashboard`, `lint-js`
- [x] `factory-dashboard.container` : `StopTimeout=120` + `TimeoutStopSec=130` (parité #1989)

---

## BLOCK 1 — Cockpit + Chat + Harness/Model

**Statut :** `done`  
**GO :** oui — implémenter en premier

### Layout & shell

- [x] `CockpitLayoutA` : chat-list | chat-pane | sidebar 360px (Jobs/Obs `PanelMount` désactivés)
- [x] Refactor `AppShell` → grille full-viewport dense (pas layout marketing `max-w`)
- [x] Routes TanStack : `/` (cockpit), `/panels/jobs` stub, `/panels/obs` stub
- [x] `factory/dashboard/app.py` — composition root :
  - mount adapter router (chat/SSE)
  - `StaticFiles(apps/dashboard/dist/)`
  - stub BFF router `/api/bff/*`
- [x] Retirer `_HTML` quand `dist/index.html` présent ; `/api/*` avant catch-all SPA

### Chat

- [x] `MultiChatTabs` : créer / switcher / fermer ; `localStorage` clé `factory.dashboard.chats.v1`
  - par tab : `{id, agent, harness, model, session_id, lastActive}`
- [x] `ChatPane` : `POST /api/chat` + `EventSource /api/stream` (delta / done / error / ping)
- [x] Isolation session par agent : nouveau `session_id` au switch agent OU clé composite hub
- [x] `stream_token` minté côté serveur sur `POST /api/chat` ; requis sur `GET /api/stream` (mitigation #1992)
- [x] États d'erreur : 503 adapter not ready, 400 unknown agent, reconnect SSE

### Harness & model (par tab)

- [x] `HarnessPicker` : `claude-cli` (Clipool) | `omp-rpc` (OMP) — `nats` caché dans l'UI
- [x] `ModelPicker` : catalogue curaté depuis API disponibilité
- [x] `AgentStatusBadge` « Hors ligne » si roster absent OU worker mort OU harness injoignable
- [x] `GET /api/bff/agents/status` — agrège `roster.web` + `WorkerRegistry` + `is_alive()`

### Build & gates (critères de sortie Block 1 — pas reportés)

- [x] Dockerfile `svc-runtime` : stage `oven/bun` → `bun run build:dashboard` → `COPY dist/`
- [x] `ci.yml` : `bun run build:dashboard` + `bun run lint` (biome)
- [x] `publish.yml` : assert `dist/index.html` dans l'image `staging-svc`
- [x] pytest : `test_web_server.py` reste green + static mount + tests `stream_token`
- [x] vitest : `CockpitShell`, `MultiChatTabs`, `HarnessPicker`, `ModelPicker`
- [x] `bun run typecheck` + `build:dashboard` + `lint` green

### Block 1 — done when

- [x] Opérateur Tailnet/dev : 2+ onglets chat, harness/model par agent, réponse streamée
- [x] SPA servie depuis l'image container (pas smoke HTML)
- [x] `make qg` green sur les chemins touchés

---

## BLOCK 2 — SessionCatalog + Reprendre

**Statut :** `done`  
**GO :** seulement après Block 1 green

### Hub (avant les routes BFF session)

- [x] `session_catalog.list_sessions_for_agent(store, bindings, agent, limit)` (+ `TurnStore.list_recent_sessions`)
  - tous les `pool_id` → agent via bindings wildcard ; tri `last_active_at DESC`
- [x] Hub NATS RPC `factory.dashboard.sessions.list` `{agent, limit}`
  - réponse : `session_id`, `pool_id`, `platform`, `cli_session_id`, `first_user_msg`, `turn_count`, `last_active_at`
- [ ] Optionnel : `factory.dashboard.sessions.turns` `{session_id}` — reporté (hors MVP)
- [x] Contracts dans `roxabi-contracts` + `contracts-bump`
- [x] ACL matrix : `request_reply_flows` pour les nouveaux subjects (`factory.dashboard.>`)
- [x] `make nats-regen-specs nats-regen-authconf` ; vérifier restart `factory-dashboard`

### BFF + UI

- [x] `GET /api/bff/sessions?agent=` → hub RPC (pas `turns.db`)
- [x] `POST /api/bff/sessions/resume` `{cli_session_id, agent}` → `resume_session()` sur pool web
- [x] Panneau Reprendre : liste unifiée par agent (filtre A) + badge `telegram` / `discord` / `web`
- [ ] Resume → ouvrir/focus onglet chat + charger historique turns (optionnel 2b) — focus onglet oui, historique reporté

### Block 2 — done when

- [x] Sessions cross-platform visibles par agent avec tag origine
- [x] Resume depuis pool web pour `cli_session_id` TG/DC/web
- [x] Pas de duplication SQL `session_commands` dans dashboard
- [x] pytest hub RPC + intégration BFF green

---

## BLOCK 3 — E2E + Hardening + Ship

**Statut :** `done`  
**GO :** seulement après Block 1+2 green

### E2E & visual

- [x] `FACTORY_DASHBOARD_E2E=1` : stub SSE + stub agents (pas de NATS dans Playwright)
- [x] `tests/e2e/dashboard/` : snapshots dark + light + pixel diff CI
- [x] Documenter `FACTORY_DASHBOARD_E2E` vs `FACTORY_SMOKE_MODE` (ADR-094 phase 3)

### Sécurité & ops

- [x] #1992 : liste sessions 403 jusqu'à auth middleware (ou boundary Tailnet documentée)
- [x] SSE ownership durci au-delà de `stream_token` si auth arrive — `stream_token` livré ; OIDC reporté #1992
- [x] `HealthCmd` : `/api/agents` **et** `GET /` (`index.html` 200)
- [x] Merge ACL `dashboard-reader` → `web-adapter` si panels events nécessitent `factory.event.*` (prep #1772) — `web-adapter` publie déjà `factory.event.>`
- [x] `check_secrets_drift.sh` + `factory-acl check grants` green

### Docs & deploy

- [x] `container-publishing.md` : ajouter `factory-dashboard` au tableau `staging-svc`
- [x] `CONFIGURATION.md` : alias env `FACTORY_WEB_*` / `FACTORY_DASHBOARD_*`
- [x] `deploy/AGENTS.md` : boundary Tailnet + note exposition API session

### Final

- [x] `make qg` complet green — gates CI équivalents (lint-imports, ACL drift, pytest, vitest)
- [x] commit + push `staging`
- [ ] `make converge` sur M₁ + smoke Tailnet (SPA + chat + Reprendre si Block 2 livré) — opérateur M₁ post-merge

---

## Hors scope (ce goal)

- Implémentation panels #1772 / #1773 / #1774 (stubs uniquement)
- Greenfield `platform=dashboard` (#1770)
- Rename NATS complet phase 2 ADR-094 (`web` → `dashboard`) — documenter seulement
- OIDC complet #1992 (`stream_token` Block 1 suffit pour l'instant)

---

## Matrice couverture (exigences conversation)

| Exigence | Bloc | Couvert |
|----------|------|---------|
| ADR-094 deux axes | Pre-flight + B1 | oui |
| Cockpit layout A | B1 | oui |
| Multi-chat + localStorage | B1 | oui |
| Harness / model picker | B1 | oui |
| Monitoring OK badge | B1 | oui |
| Reprendre cross-platform + tag | B2 | oui |
| SessionCatalog hub RPC | B2 | oui |
| E2E stub + Playwright visual | B3 | oui |
| Docker bun multi-stage | B1 exit | oui |
| Panel mounts #1772–#1774 | B1 stubs | oui |
| Forge v2 tokens | B1 | scaffold existant |
| import-linter + guards axial | Pre-flight | oui |
| Mitigation SSE #1992 | B1 `stream_token` | oui |
| commit + converge M₁ | B3 final | push fait ; converge M₁ manuel |

---

## Journal de progression

> Ajouter une entrée à chaque session `/goal`. Format : `YYYY-MM-DD — résumé — bloc — statut`.

### 2026-06-28 — Création du plan

- Plan consolidé après review tri-expert (MVP-minimal, epic-complet, risk-first).
- Verdict panel : SAFE WITH GUARDS.
- Décision : Docker/bun en sortie Block 1 (pas Block 3 seul).
- Block 2 isolé (SessionCatalog) pour éviter drift session.

### 2026-06-28 — Pre-flight slice

- [x] import-linter contracts `dashboard-no-infrastructure` + `dashboard-bounded-adapters`
- [x] `web_server.py` → 30 SLOC wrapper ; routes dans `chat_routes.py` + `factory/dashboard/app.py`
- [x] `factory/dashboard/AGENTS.md`, contracts DTOs, Makefile `build-dashboard`/`lint-js`, quadlet timeouts
- Gates : `lint-imports` 13/13, `wc -l web_server.py` = 30

### 2026-06-28 — Block 1 slice

- [x] Cockpit SPA, multi-chat, harness/model pickers, `stream_token`, Docker bun stage, CI vitest+biome
- Gates : `bun build/lint/typecheck`, vitest 7/7, pytest web_server + static mount

### 2026-06-28 — Block 2 slice

- [x] `session_catalog.list_sessions_for_agent`, hub `dashboard_rpc.py`, BFF `/api/bff/sessions*`
- [x] ACL `factory.dashboard.>` + `nats-regen-specs` + auth.conf drift green
- Gates : pytest hub RPC + BFF (incl. real NATS mock path)

### 2026-06-28 — Block 3 slice

- [x] `FACTORY_DASHBOARD_E2E=1`, Playwright dark/light snapshots, docs, secrets drift
- Gates : smoke curls `/` + `/api/*` (scratch `b3-smoke.log`), visual 2/2, converge dry-run

### 2026-06-28 — Remediation (verifier gaps)

- [x] Bugfix : `set_nats_client(nc)` **avant** `wire_bot_common`/`astart` ; `DashboardHubClient(adapter)` lazy nc
- [x] Bugfix : `POST /api/chat` propage `harness`/`model` → `WebMeta` → `SimpleAgent._effective_model_config`
- [x] Bugfix : `agents_status` RPC respecte `harness_by_agent` ; exceptions BFF/RPC resserrées
- [x] Tests : `test_dashboard_bff.py` (chemin prod sans E2E), `test_simple_agent_web_harness.py`
- Écart vs plan : `sessions.turns` RPC et historique post-resume reportés ; `make converge` M₁ manuel post-merge
- Evidence : `/tmp/grok-goal-a378c4fde0cd/implementer/execution-summary.txt`

### 2026-06-28 — Gate harness + hook extraction (verifier pass 3)

- [x] `scripts/goal-1771-gates.sh` — fail-fast QG (lint → ruff → import-linter → typecheck → build → vitest → pytest → ACL/secrets)
- [x] `useAgentStatus` extrait de `CockpitLayout` ; test harness-aware avec `waitFor` sur résolution React Query
- [x] Ruff/biome fixes mécaniques (`hub_client`, `simple_agent`, `turn_store_queries`, `hub_standalone`)
- Gates : `goal-1771-gates.sh` exit 0 — vitest 8/8, pytest 25/25, lint-imports 13/13, `web_server.py` 30 SLOC
- Evidence : `b3-qg.log`, `execution-summary.txt`, `plan-final.txt`

### 2026-06-28 — Verifier pass 4 (make qg + evidence refresh)

- [x] Makefile : cible `qg` (bundle CI-equivalent local) — `make qg` remplace le script custom comme gate canonique
- [x] `goal/plan.md` session : AC3 corrigé (`session_catalog` pas `turn_store_queries`), checklist `[x]`, journal
- [x] Scratch régénéré : `block-order-check-1.txt`, `block-order-check-2.txt`, `plan-final.txt`, `b3-qg.log` via `make qg`
- Gates : `make qg` exit 0 (lint, ruff, pyright, build, vitest, lint-imports, ACL/secrets, pytest dashboard, SLOC)

---

## Référence `/goal`

Coller ce chemin dans le prompt :

```text
/goal Execute artifacts/plans/1771-factory-dashboard-goal.md on branch staging.
Read the plan first. Update checkboxes and the progress journal after each completed slice.
Respect block order: Pre-flight → Block 1 → Block 2 → Block 3.
Do not skip gates listed under "done when".
```