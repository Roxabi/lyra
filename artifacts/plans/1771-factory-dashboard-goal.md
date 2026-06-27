# Goal — factory-dashboard (#1771)

> Plan vivant pour l'exécution `/goal`. Mettre à jour ce fichier au fur et à mesure :
> cocher les tâches, noter les écarts, ajouter des entrées dans le journal de progression.

| Champ | Valeur |
|-------|--------|
| **Issue** | [#1771](https://github.com/Roxabi/roxabi-factory/issues/1771) — extend `factory-dashboard` shell |
| **Epic parent** | [#1760](https://github.com/Roxabi/roxabi-factory/issues/1760) — control-plane operator console |
| **ADR** | [ADR-094](../../docs/architecture/adr/094-control-plane-dashboard-consolidation.mdx) |
| **Branche** | `staging` |
| **Statut global** | `in_progress` |
| **Dernière MAJ** | 2026-06-28 |
| **Panel review** | 2026-06-28 — tri-expert (MVP / epic-complet / risk-first) → SAFE WITH GUARDS |

## Statut par bloc

| Bloc | Statut | Notes |
|------|--------|-------|
| Pre-flight | `in_progress` | import-linter + contracts
| Block 1 — Cockpit + Chat + Harness/Model | `not_started` | —
| Block 2 — SessionCatalog + Reprendre | `not_started` | —
| Block 3 — E2E + Hardening + Ship | `not_started` | —

---

## Contexte

- **ADR-094** : 1 container `factory-dashboard`, 2 axes internes
  - **Axis 1 (chat)** : `src/factory/adapters/web/` — `run_inbound_guarded`, NATS, SSE
  - **Axis 2 (BFF)** : `src/factory/dashboard/` — static SPA, proxy obs/jobs (futur)
- **Rejeté** : adapter parallèle `platform=dashboard` (#1770)
- **Design** : Forge v2 dense (`#0a0a0f` / `#e85d04`), `brand/` + `packages/shared`
- **#1772 / #1773 / #1774** : stubs de panneaux seulement dans ce goal

## Invariants globaux (tous blocs)

- [ ] Pas d'adapter `platform=dashboard` (#1770 reste mort)
- [ ] Chat POST → `run_inbound_guarded` uniquement (pas de raccourci BFF vers hub bus)
- [ ] `factory.dashboard` **ne doit pas** importer `factory.infrastructure.stores.*`
- [ ] `WebMeta.session_id` = transport SSE ≠ `cli_session_id` (resume TurnStore)
- [ ] Namespaces routes : `/api/chat/*` + `/api/stream/*` (adapter) vs `/api/bff/*` (dashboard)
- [ ] **Pas** de mount `turns.db` sur le container `factory-dashboard`
- [ ] Gates SLOC 300 / dossier 15 sur tous les nouveaux chemins

---

## PRE-FLIGHT — avant Block 1

> Prérequis mécaniques et contractuels. Ne pas commencer Block 1 sans ces items (sauf dérogation notée au journal).

- [ ] Contrat import-linter pour `factory.dashboard` (pas `infrastructure` ; imports bornés depuis `adapters.web`)
- [ ] Extraire les routes de `web_server.py` → `src/factory/dashboard/` (`web_server.py` < 300 SLOC)
- [ ] `factory/dashboard/AGENTS.md` — taxonomie session IDs + split des 2 axes
- [ ] `roxabi-contracts` : DTOs `ChatRequest`, `SseEvent`, `AgentHealth`, `DashboardSession`
- [ ] Makefile : cibles `build-dashboard`, `lint-js`
- [ ] `factory-dashboard.container` : `StopTimeout=120` + `TimeoutStopSec=130` (parité #1989)

---

## BLOCK 1 — Cockpit + Chat + Harness/Model

**Statut :** `in_progress`  
**GO :** oui — implémenter en premier

### Layout & shell

- [ ] `CockpitLayoutA` : chat-list | chat-pane | sidebar 360px (Jobs/Obs `PanelMount` désactivés)
- [ ] Refactor `AppShell` → grille full-viewport dense (pas layout marketing `max-w`)
- [ ] Routes TanStack : `/` (cockpit), `/panels/jobs` stub, `/panels/obs` stub
- [ ] `factory/dashboard/app.py` — composition root :
  - mount adapter router (chat/SSE)
  - `StaticFiles(apps/dashboard/dist/)`
  - stub BFF router `/api/bff/*`
- [ ] Retirer `_HTML` quand `dist/index.html` présent ; `/api/*` avant catch-all SPA

### Chat

- [ ] `MultiChatTabs` : créer / switcher / fermer ; `localStorage` clé `factory.dashboard.chats.v1`
  - par tab : `{id, agent, harness, model, session_id, lastActive}`
- [ ] `ChatPane` : `POST /api/chat` + `EventSource /api/stream` (delta / done / error / ping)
- [ ] Isolation session par agent : nouveau `session_id` au switch agent OU clé composite hub
- [ ] `stream_token` minté côté serveur sur `POST /api/chat` ; requis sur `GET /api/stream` (mitigation #1992)
- [ ] États d'erreur : 503 adapter not ready, 400 unknown agent, reconnect SSE

### Harness & model (par tab)

- [ ] `HarnessPicker` : `claude-cli` (Clipool) | `omp-rpc` (OMP) — `nats` caché dans l'UI
- [ ] `ModelPicker` : catalogue curaté depuis API disponibilité
- [ ] `AgentStatusBadge` « Hors ligne » si roster absent OU worker mort OU harness injoignable
- [ ] `GET /api/bff/agents/status` — agrège `roster.web` + `WorkerRegistry` + `is_alive()`

### Build & gates (critères de sortie Block 1 — pas reportés)

- [ ] Dockerfile `svc-runtime` : stage `oven/bun` → `bun run build:dashboard` → `COPY dist/`
- [ ] `ci.yml` : `bun run build:dashboard` + `bun run lint` (biome)
- [ ] `publish.yml` : assert `dist/index.html` dans l'image `staging-svc`
- [ ] pytest : `test_web_server.py` reste green + static mount + tests `stream_token`
- [ ] vitest : `CockpitShell`, `MultiChatTabs`, `HarnessPicker`, `ModelPicker`
- [ ] `bun run typecheck` + `build:dashboard` + `lint` green

### Block 1 — done when

- [ ] Opérateur Tailnet/dev : 2+ onglets chat, harness/model par agent, réponse streamée
- [ ] SPA servie depuis l'image container (pas smoke HTML)
- [ ] `make qg` green sur les chemins touchés

---

## BLOCK 2 — SessionCatalog + Reprendre

**Statut :** `not_started`  
**GO :** seulement après Block 1 green

### Hub (avant les routes BFF session)

- [ ] `session_catalog.list_sessions_for_agent(store, bindings, agent, limit)` (+ `TurnStore.list_recent_sessions`)
  - tous les `pool_id` → agent via bindings wildcard ; tri `last_active_at DESC`
- [ ] Hub NATS RPC `factory.dashboard.sessions.list` `{agent, limit}`
  - réponse : `session_id`, `pool_id`, `platform`, `cli_session_id`, `first_user_msg`, `turn_count`, `last_active_at`
- [ ] Optionnel : `factory.dashboard.sessions.turns` `{session_id}` — reporté (hors MVP)
- [ ] Contracts dans `roxabi-contracts` + `contracts-bump`
- [ ] ACL matrix : `request_reply_flows` pour les nouveaux subjects (`factory.dashboard.>`)
- [ ] `make nats-regen-specs nats-regen-authconf` ; vérifier restart `factory-dashboard`

### BFF + UI

- [ ] `GET /api/bff/sessions?agent=` → hub RPC (pas `turns.db`)
- [ ] `POST /api/bff/sessions/resume` `{cli_session_id, agent}` → `resume_session()` sur pool web
- [ ] Panneau Reprendre : liste unifiée par agent (filtre A) + badge `telegram` / `discord` / `web`
- [ ] Resume → ouvrir/focus onglet chat + charger historique turns (optionnel 2b) — focus onglet oui, historique reporté

### Block 2 — done when

- [ ] Sessions cross-platform visibles par agent avec tag origine
- [ ] Resume depuis pool web pour `cli_session_id` TG/DC/web
- [ ] Pas de duplication SQL `session_commands` dans dashboard
- [ ] pytest hub RPC + intégration BFF green

---

## BLOCK 3 — E2E + Hardening + Ship

**Statut :** `not_started`  
**GO :** seulement après Block 1+2 green

### E2E & visual

- [ ] `FACTORY_DASHBOARD_E2E=1` : stub SSE + stub agents (pas de NATS dans Playwright)
- [ ] `tests/e2e/dashboard/` : snapshots dark + light + pixel diff CI
- [ ] Documenter `FACTORY_DASHBOARD_E2E` vs `FACTORY_SMOKE_MODE` (ADR-094 phase 3)

### Sécurité & ops

- [ ] #1992 : liste sessions 403 jusqu'à auth middleware (ou boundary Tailnet documentée)
- [ ] SSE ownership durci au-delà de `stream_token` si auth arrive — `stream_token` livré ; OIDC reporté #1992
- [ ] `HealthCmd` : `/api/agents` **et** `GET /` (`index.html` 200)
- [ ] Merge ACL `dashboard-reader` → `web-adapter` si panels events nécessitent `factory.event.*` (prep #1772) — `web-adapter` publie déjà `factory.event.>`
- [ ] `check_secrets_drift.sh` + `factory-acl check grants` green

### Docs & deploy

- [ ] `container-publishing.md` : ajouter `factory-dashboard` au tableau `staging-svc`
- [ ] `CONFIGURATION.md` : alias env `FACTORY_WEB_*` / `FACTORY_DASHBOARD_*`
- [ ] `deploy/AGENTS.md` : boundary Tailnet + note exposition API session

### Final

- [ ] `make qg` complet green — gates CI équivalents (lint-imports, ACL drift, pytest, vitest)
- [ ] commit + push `staging`
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

### 2026-06-28 — Pre-flight slice (in_progress)

- Statut Pre-flight → `in_progress`


---

## Référence `/goal`

Coller ce chemin dans le prompt :

```text
/goal Execute artifacts/plans/1771-factory-dashboard-goal.md on branch staging.
Read the plan first. Update checkboxes and the progress journal after each completed slice.
Respect block order: Pre-flight → Block 1 → Block 2 → Block 3.
Do not skip gates listed under "done when".
```