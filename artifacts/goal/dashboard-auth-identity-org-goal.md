# /goal — Dashboard auth + identité unifiée + orgs + link TG/DC

> **2026-07-12 supersession (store ownership):** Blocks 0–14 dual-open path = **shipped debt**.
> Execution SSoT for **hub sole IdP + thin BFF** =
> [`dashboard-auth-hub-idp-migration.md`](dashboard-auth-hub-idp-migration.md).
> [ADR-103](../../docs/architecture/adr/103-dashboard-auth-user-org-platform-link.mdx) = **Accepted** (amended).
> This file = historical product ACs + journal; **do not** re-open dual-open as target.
>
> **Issue (à ouvrir / lier) :** epic control-plane auth — suite #2262 · #1992
> **ADR cibles :** ADR-103 (Accepted) · living [security-routing.md](../../docs/architecture/security-routing.md) § control-plane · companion [phase0 schema/threat](../specs/dashboard-auth-phase0-schema-threat.md)
> **Références panel :** audit L2 · décisions 2026-07-11 (product) + 2026-07-12 (hub IdP)
> **Statut global :** product Blocks 0–14 merged; store placement **amended** → hub-idp migration

---

## Goal (one sentence)

Remplacer la confiance Tailnet / token opérateur optionnel par un **modèle full-auth** : tout acte (HTTP BFF et messages NATS control-plane) porte un **principal authentifié** ; signup **invite-only** ; **orgs** sans default ; **TG/DC** disponibles seulement après invite **et** link des comptes ; auth = **module Python dans le process dashboard (BFF)**, hub = SSoT authz.

---

## Démarrage rapide (humain)

```text
/goal Execute artifacts/goal/dashboard-auth-identity-org-goal.md
```

**Avant (optionnel) :**

```bash
git fetch origin staging && git checkout staging
git checkout -b feat/dashboard-auth-identity-org
```

---

## Instructions agent `/goal` (lire en premier)

Ce fichier est **SSoT** pour l’exécution end-to-end. L’agent doit :

1. **Lire ce plan en entier** avant de coder.
2. **Phase 0 (design-only)** : ADR + schéma store + amendements security-routing — **aucun code auth de prod** tant que Phase 0 non approuvée.
3. **Phase 1+** : branche `feat/dashboard-auth-identity-org` depuis `staging` — jamais commit direct sur `staging`.
4. **Ordre strict** : `Pre-flight → Block 0 → Block 1 → …` — chaque bloc : **done when** + `make qg` (ou gates listés) avant le suivant.
5. **Livraison** : `/pr --base staging` (slices) → code-review → `/fix` → `make qg` → `/ci-watch`.
6. **Axis** : authz métier **une fois** au hub (control-plane gate) ; BFF = garant authn ; workers **dumb** ; adapters TG/DC thin (link check + principal, pas policy org).
7. **Journal** : mettre à jour § Statut par bloc + Journal de progression en fin de session.

### Invariants non négociables

| Règle | Valeur |
|-------|--------|
| **Tailnet ≠ auth** | Réseau ignoré pour la confiance. Full auth quelle que soit l’origine (y compris localhost). |
| **Principal obligatoire** | Tout RPC `factory.dashboard.*` mutatif/sensible et tout inbound chat “produit” portent un principal authentifié d’origine — sinon **refus**. |
| **Garants** | TG/DC adapter = garant platform id **lié** ; BFF = garant session/API key → user ; Hub scheduler = `sys:…` si un jour ; workers **ne** re-auth **pas**. |
| **Ops n’est pas une identité** | Rôle / permission sur un **User** (`admin`, caps org, …). |
| **Auth runtime** | **Python dans le process dashboard** (équivalent BetterAuth *minimal*) — **pas** service BetterAuth TS, **pas** BFF TS. |
| **Signup** | **Invite-only** — pas d’open registration. |
| **Org** | **Aucune org par défaut.** User sans org OK. Partage = org explicite. |
| **Visibilité** | Admin global → tout. Member → **own** + **org-shared** (tous membres de l’org voient les ressources `org_id`). |
| **TG/DC** | Disponibles pour un user **seulement** s’il est **invité** **et** a **lié** Telegram **et** Discord. **Admin inclus** pour interagir sur ces canaux. |
| **Console sans link** | Admin (et users) peuvent utiliser la **console** avec session dashboard **sans** link ; le **chat** TG/DC exige le link. |
| **API keys** | Appartiennent à un User ; mêmes permissions (ou subset) ; header Bearer ; org via membership + header/contexte actif. |
| **Planes séparés** | Control-plane (dashboard/org/roles) ≠ chat-plane (`agent_grants` USE). Link unifie le **principal** ; ne fusionne pas les matrices sans ADR. |
| **Contracts security-bearing** | Principal / org sur wire dashboard = **champs dashboard** (mixin), **pas** `ContractEnvelope` global (ADR-049). MAJOR dashboard si breaking. |
| **BFF store-free métier** | `factory.dashboard` ↛ `infrastructure.stores` (importlinter). Auth store via ports + impl bootstrap/infra ; BFF appelle ports injectés ou hub RPC selon slice. |
| **Hub authoritative** | BFF authn + stamp ; hub **refuse** principal absent/invalide et applique authz. Seed NKey `web-adapter` = canal process, **pas** super-admin anonyme. |
| **Workers** | Exécuteurs ; **zéro** checks operator/org dans clipool/omp/image. |
| **Health** | Probe non authentifiée (`/` ou `/healthz`) — ne pas casser HealthCmd Quadlet. |
| **E2E** | Bypass auth **explicite** (`FACTORY_DASHBOARD_E2E=1` / test bootstrap) — **pas** “token unset = open”. |

### Commandes gates

```bash
make qg
uv run pytest tests/dashboard/ tests/ -q -k 'auth or dashboard or grant' --maxfail=20
uv run lint-imports
# après UI
cd apps/dashboard-v2 && bun run typecheck && bun run test && bun run build
# secrets / deploy si Block secrets
scripts/qg run secrets_drift secrets_source
```

---

## Contexte

### Problème

1. Control-plane BFF largement **ouvert** (sauf connectors `require_operator` optionnel fail-open).
2. Identité opérateur = env / token partagé — **pas** un user.
3. Hub RPC dashboard **sans** principal → seed `web-adapter` = pouvoir total sur `factory.dashboard.>`.
4. Pas d’orgs, pas d’invite, pas de link TG/DC → multi-user réel impossible.
5. Confiance Tailnet documentée comme residual — **rejetée** comme modèle cible.

### État actuel codebase (vérifié 2026-07-11)

| Élément | État |
|---------|------|
| BFF | Python FastAPI, process `factory-dashboard` (ADR-094) |
| SPA | TS Vite `apps/dashboard-v2` — static mount |
| `require_operator` | Fail-open si token unset ; **connectors only** |
| Token | Commenté `web.env.example` ; **pas** secrets-policy |
| `hub_client` | JSON nu, pas d’actor |
| `dashboard_rpc._wrap` | Parse/respond, **0 authz** |
| Admin grants | `granted_by="dashboard"` statique |
| Jobs launch/steer | Sans `launched_by` / org |
| Chat inbound | ADR-090 grants USE + ban — **orthogonal**, sain |
| Web smoke | `user_id="smoke"` hardcodé |
| `factory_tenant` | Connectors only — précurseur org à migrer |
| BetterAuth | **Non** — stack BFF Python |

### Direction validée (session produit + panels)

| Décision | Choix |
|----------|-------|
| Auth stack | **Python minimal “BetterAuth-like” dans BFF process** |
| Signup | Invite-only |
| Org default | **Aucune** |
| Visibilité org | Ressource `org_id` → tous membres org |
| TG/DC | Invite + **link TG et DC** requis pour chat (admin inclus) |
| Console | Session user OK sans link |
| Ops identity | **Drop** — roles/permissions sur User |
| Workers authz | **Drop** |
| NATS TLS #2264 | Track **séparé** (non-bloquant ce goal) |

---

## Non-goals (ce goal)

- Service BetterAuth / Node auth sidecar
- BFF réécrit en TypeScript
- Open signup / self-serve multi-tenant SaaS
- Org créée automatiquement pour chaque user
- OIDC/SAML/passkeys/2FA (V2+)
- Authz dans workers (clipool/omp/image)
- Remplacer `agent_grants` par org matrix (chat reste ADR-090 ; link unifie le principal)
- NATS TLS / bind 4222 (#2264)
- Multi-factory cross-host IdP Roxabi global
- Job scheduler sys (réserver `sys:scheduler` dans le modèle, **pas** implémenter le cron)

---

## Architecture contract (must hold after all blocks)

### Plans d’identité

```
┌──────────────────────────────────────────────────────────────────┐
│ User (dashboard)                                                 │
│   id, credentials, roles[]  (admin | member | …)                 │
│   memberships[] → Org                                            │
│   platform_links → tg:user:… , dc:user:…  (both required for chat)│
├──────────────────────────────────────────────────────────────────┤
│ Organization                                                     │
│   id, name, members[]                                            │
│   Resources with org_id visible to all members                   │
├──────────────────────────────────────────────────────────────────┤
│ Principal (sur chaque acte)                                      │
│   user_id | sys:…                                                │
│   org_id? (contexte actif, membership checked)                   │
│   via: session | api_key | platform_link | sys                   │
└──────────────────────────────────────────────────────────────────┘
```

### Flux authn / authz

```
Browser / API client
  → BFF: session cookie | API key  →  Principal(user, orgs, roles)
  → stamp Principal (+ org) on factory.dashboard.* RPC
  → Hub _wrap: reject if missing/invalid
  → Hub authorize: admin | owner | org member
  → handler business
  → (jobs) publish JobEnvelope{launched_by, org_id?} → worker (dumb)

Telegram / Discord
  → adapter: resolve platform id → platform_links
  → if not linked: refuse / onboarding message (no agent turn)
  → if linked: InboundMessage.user_id = canonical user (or linked platform id per ADR choice)
  → Hub: ResolveIdentity + AuthorizeAgent (USE grants) as today
```

### Placement code (axis)

| Concern | Where |
|---------|--------|
| HTTP authn (login, session, API key, invite accept, link OAuth/pairing) | `factory.dashboard` edge (+ thin routes) |
| Pure types / authorize(resource) | `factory.core.auth` (ports) — **pas** agent_grants overload |
| Store impl | `factory.infrastructure.stores.identity` (or sibling) |
| Wire stamp | `DashboardHubClient` injects principal from request context |
| Control-plane gate | `dashboard_rpc._wrap` **only** verify + attach principal |
| Resource authz helper | one `require_access(principal, resource)` — handlers thin |
| SPA | login, accept-invite, link accounts, org switcher, no token-on-Integrations-only |

### Visibilité (règle produit)

| Principal | Voit |
|-----------|------|
| `role=admin` | Tout |
| member | `owner_user_id = me` **∪** `org_id ∈ my_orgs` |
| non authentifié | Rien (sauf health + pages login/invite publiques) |

---

## Pre-flight

| Check | Done when |
|-------|-----------|
| Lire ce goal + L2 audit § trust | ☐ |
| Confirmer branche depuis `staging` | ☐ |
| `make qg` baseline green (ou dette connue listée) | ☐ |
| Issue epic créée + labels size/priority | ☐ |
| Phase 0 ADR draft path décidé | ☐ |

---

## Phase 0 — Design only (GO before code)

### Block 0 — ADR + domaine

| | |
|--|--|
| **Do** | Rédiger ADR : Dashboard auth Python BFF ; User/Org/Membership/Invite/ApiKey/PlatformLink ; hub gate ; chat link gate ; non-goals |
| | Amendement living : `security-routing.md` § control-plane (remplacer “Tailnet residual only”) |
| | Schéma tables + états invite (pending/accepted/revoked) |
| | Décision SSoT principal chat post-link : `dash:user:…` vs keep platform id + alias table (reco : **alias table**, grants migrables) |
| **Done when** | ADR reviewable ; diagrammes flux ; critères acceptation produit listés |
| **Tests** | n/a |
| **Out** | Code prod |

### Block 0b — Threat model & secrets

| | |
|--|--|
| **Do** | Documenter : session secret, password hashing, API key hashing, cookie flags, seed NKey ≠ auth user |
| | Plan secrets-policy pour session signing key (Podman secret) si besoin |
| **Done when** | Runbook draft rotation session secret |
| **Out** | Impl secrets |

---

## Phase 1 — Fondation identité (backend)

### Block 1 — Store + ports

| | |
|--|--|
| **Do** | Ports `UserDirectory`, `OrgDirectory`, `InviteService`, `SessionStore`, `ApiKeyStore`, `PlatformLinkStore` |
| | Impl SQLite (auth.db extension or dedicated) + migrations |
| | Bootstrap : create first admin user (seed from config/env once) + role admin — **no default org** |
| **Done when** | Unit tests store CRUD ; bootstrap idempotent |
| **Axis** | infra store + core ports ; composition bootstrap |

### Block 2 — Authn BFF (session + API key)

| | |
|--|--|
| **Do** | Login (invite-accepted users only), logout, session cookie httpOnly |
| | `require_principal` FastAPI dep (fail-closed) — remplace fail-open `require_operator` |
| | API key create/revoke (user-scoped) ; Bearer resolve → same Principal |
| | Public routes : health, login, accept-invite ; tout le reste auth |
| | Router-level dependencies sur `/api/bff` mutateurs + lectures sensibles |
| | E2E flag explicite |
| **Done when** | Tests 401 sans auth ; 200 avec session ; API key ; health sans auth |
| **Out** | Org UI ; link TG/DC |

### Block 3 — Invites

| | |
|--|--|
| **Do** | Admin crée invite (email/token, expiry) ; accept flow → user actif |
| | Invite-only enforcement (reject register) |
| **Done when** | Tests invite lifecycle ; no open signup path |

---

## Phase 2 — Orgs + control-plane principal sur le bus

### Block 4 — Organizations

| | |
|--|--|
| **Do** | Create org (authenticated user) ; add/remove members ; list my orgs |
| | **No** auto org on signup |
| | Active org context (header or session field) ; membership check |
| **Done when** | Tests membership visibility rules (unit) |

### Block 5 — Stamp + hub gate

| | |
|--|--|
| **Do** | Dashboard request models : principal fields (security-bearing) |
| | `hub_client` always stamps from Principal (never client-spoofed raw) |
| | `_wrap` : reject missing principal ; bind frozen Principal into handlers |
| | `granted_by` / audit = real user id |
| | Connectors `factory_tenant` → migrate toward `org_id` (compat period documented) |
| **Done when** | Integration test : NATS RPC without principal denied ; with principal ok |
| **Axis** | single gate `_wrap` ; handlers business-only |

### Block 6 — Authz resource (own + org + admin)

| | |
|--|--|
| **Do** | Helper `authorize(principal, action, resource)` |
| | Apply to admin users, agents list/patch/soul, jobs list/launch/steer/cancel, connectors |
| | Admin sees all ; member own + org_id ∈ memberships |
| **Done when** | Matrix tests (admin / owner / org-peer / outsider) |

### Block 7 — Jobs identity

| | |
|--|--|
| **Do** | `launched_by` + optional `org_id` on launch path / catalog |
| | List/steer/cancel filtered by authz rules |
| | Permission `jobs.steer_any` for admin (or explicit cap) |
| **Done when** | User A cannot steer User B private job ; org mates can if org-scoped |

---

## Phase 3 — Link TG/DC + chat gate

### Block 8 — Platform link

| | |
|--|--|
| **Do** | Link flows Telegram + Discord (pairing code / OAuth / bot deep-link — choisir une mécanique en Phase 0) |
| | Require **both** links for “chat ready” status |
| | Unlink + re-link edge cases |
| **Done when** | User can complete dual link ; status visible in UI |

### Block 9 — Inbound gate non-lié

| | |
|--|--|
| **Do** | TG/DC normalize path : if platform id not linked → **no agent turn** ; user-visible refusal (pull) |
| | Linked → resolve to dashboard user (or alias) → existing AuthorizeAgentMiddleware |
| | Admin without link : console OK, chat refuse |
| | Retire or quarantine hard-coded web `smoke` for prod path |
| **Done when** | E2E or integration : unlinked message dropped/refused ; linked works with grant |
| **Axis** | thin adapter + one hub/inbound helper — **not** per-platform policy copies |

### Block 10 — agent_grants alignment

| | |
|--|--|
| **Do** | Document + implement how USE grants attach to linked user |
| | Migrate admin_rpc user/grant UI to new User directory |
| | Deprecate `granted_by="dashboard"` |
| **Done when** | Grant to linked user controls TG+DC access for that human |

---

## Phase 4 — UI + deploy

### Block 11 — SPA

| | |
|--|--|
| **Do** | Login, accept-invite, session handling, 401 recovery |
| | Org switcher ; create org ; members |
| | Link TG/DC pages ; “chat ready” badge |
| | Unify API client Bearer/cookie (retire dual connectorFetch/adminFetch/bffFetch mess) |
| | Remove Integrations-only operator token as primary auth |
| **Done when** | `bun` typecheck/test/build green ; manual smoke checklist |

### Block 12 — Deploy / secrets / docs

| | |
|--|--|
| **Do** | Session signing secret in secrets-policy + install if needed |
| | Fail-closed boot if auth misconfigured in container |
| | HealthCmd remains green |
| | Update security-routing, data-dirs, runbooks, AGENTS residual |
| | Close/reframe #2262 against this goal |
| **Done when** | Deploy gates green ; runbook “bootstrap admin + invite + link” |

---

## Phase 5 — Hardening (optional same epic or follow-up)

### Block 13 — Audit + rate limits

| | |
|--|--|
| **Do** | `factory.audit.security.*` on login fail, link, grant, deny RPC |
| | Basic rate limit login/invite |
| **Done when** | Events visible in stream/logs |

### Block 14 — Cleanup

| | |
|--|--|
| **Do** | Remove fail-open token path ; remove `FACTORY_DASHBOARD_AUTH_REQUIRED` hard-403 stub |
| | Debt markers ; doc drift |
| **Done when** | Grep clean for old operator token primary path |

---

## Acceptance criteria (operator / product)

1. **Sans session/API key** : pas d’accès console sensible ni RPC (401/redirect login) — y compris hors Tailnet.
2. **Invite-only** : pas de self-register.
3. **Admin bootstrap** voit tout ; member ne voit que own + org.
4. **Pas d’org default** : user invité sans org jusqu’à création/adhésion.
5. **Ressource org** visible par tous les membres de l’org.
6. **API key** permet d’appeler le BFF en tant que ce user.
7. **Sans link TG+DC** : pas de tour agent sur ces canaux (message clair).
8. **Avec link + grant USE** : chat TG/DC fonctionne pour ce user.
9. **Admin** doit lier pour chatter TG/DC ; console admin OK avant link.
10. **Hub** refuse dashboard RPC sans principal.
11. **Health** Quadlet OK.
12. **Workers** inchangés côté authz user.

---

## Order of PRs (suggested)

| PR | Blocks | Base |
|----|--------|------|
| PR0 | Phase 0 ADR/docs only | staging |
| PR1 | Blocks 1–3 (store, authn, invite) | staging |
| PR2 | Blocks 4–7 (org, stamp, authz, jobs) | after PR1 |
| PR3 | Blocks 8–10 (link + inbound gate + grants) | after PR2 |
| PR4 | Blocks 11–12 (UI + deploy) | after PR2/3 as deps allow |
| PR5 | Blocks 13–14 | cleanup |

Prefer **stacked PRs** rather than one mega-PR.

---

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Scope creep BetterAuth clone | Strict V1 feature table in ADR |
| Dual principal chat vs dashboard | Alias table + one authorize path documented |
| Breaking existing smoke/e2e | Explicit E2E bypass ; migrate tests per block |
| importlinter dashboard→stores | Ports + inject ; hub keeps heavy mutations if needed |
| Seed web-adapter still powerful | Hub principal required ; later optional dedicated NATS identity |
| Link UX friction | Clear onboarding ; admin runbook |

---

## Journal de progression

| Date | Bloc | Note |
|------|------|------|
| 2026-07-11 | — | Goal draft from product/arch session (full auth, Python BFF, invite, no default org, dual link) |
| 2026-07-11 | 0 / 0b | ADR-103 Proposed ; security-routing § control-plane ; companion schema/threat ; **GO humain requis** |
| 2026-07-12 | GO | Phase 0 **approuvée** — defaults § companion open items 1–5 locked ; start Blocks 1–3 |
| 2026-07-12 | 1–3 | Store+ports ControlPlaneStore ; BFF login/session/API key/`require_principal` ; invites ; tests green ; importlinter 15 kept |
| 2026-07-12 | 4–7 | Orgs + org routes ; principal stamp hub `_wrap` fail-closed ; authorize + agents admin-only ; jobs meta launched_by/org_id |
| 2026-07-12 | 8–10 | dash_link_codes + BFF links ; PlatformLinkMiddleware TG/DC ; grant_human multi-platform keys |
| 2026-07-12 | 11–12 | SPA auth pages + org/link UX ; bffFetch credentials ; web.env + bootstrap runbook |
| 2026-07-12 | 13–14 | security audit log + rate limit login/invite ; fail-closed operator token ; drop AUTH_REQUIRED |

---

## Statut par bloc

| Bloc | Statut |
|------|--------|
| Pre-flight | done (goal + baseline design) |
| 0 ADR | **done (Proposed)** — `docs/architecture/adr/103-dashboard-auth-user-org-platform-link.mdx` |
| 0b threat/secrets | **done (draft)** — `artifacts/specs/dashboard-auth-phase0-schema-threat.md` |
| 1 store | **done** (ControlPlaneStore + protocol + bootstrap admin) |
| 2 authn BFF | **done** (session cookie + API key + require_principal ; fail-closed when CP wired) |
| 3 invites | **done** (create/accept/revoke ; no open register) |
| 4 orgs | **done** (create/members/list ; principal.org_ids) |
| 5 stamp+hub gate | **done** (`_wrap` principal required ; hub_client stamp) |
| 6 resource authz | **done** (authorize helper ; agents write admin-only) |
| 7 jobs identity | **done** (dash_job_launches ; list/steer/cancel filter) |
| 8 platform link | **done** (link codes + BFF /auth/links + bot `/link`) |
| 9 inbound gate | **done** (PlatformLinkMiddleware dual-link) |
| 10 grants align | **done** (grant_human + admin_rpc platform keys) |
| 11 SPA | **done** (login/invite/links/org switcher + gate) |
| 12 deploy/docs | **done** (web.env.example + runbook bootstrap) |
| 13 audit | **done** (`factory.audit.security.*` + rate limit) |
| 14 cleanup | **done** (fail-closed token; AUTH_REQUIRED removed) |

---

*SSoT exécution : ce fichier. Ne pas implémenter hors ordre des blocs. Phase 0 d’abord.*
