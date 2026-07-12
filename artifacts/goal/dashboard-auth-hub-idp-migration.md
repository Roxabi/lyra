# Migration — Hub IdP + thin BFF (ADR-103 amended)

**Status:** planned
**Target:** ADR-103 Accepted 2026-07-12 (store owner = hub)
**Interim ops:** prod stays `disabled = true` until **Slice 4 exit** (Slice 3 green is necessary but not sufficient)
**Forbidden bandage:** do **not** mount `factory-data` RW on `factory-dashboard`
**Predecessor:** [`dashboard-auth-identity-org-goal.md`](dashboard-auth-identity-org-goal.md) (Blocks 0–14 dual-open debt)

### V1 identity RPC defaults (lock for Slice 1)

| Choice | V1 default |
|--------|------------|
| Session durability | Opaque server session rows in hub `sessions` table |
| Edge cookie | Session id / opaque token only (**no roles**) |
| RPC stamp | `user_id` + `session_id` (or api_key id); hub validates + **rehydrates** roles/orgs |
| Subject namespace | `factory.dashboard.auth.*` (reuse `factory.dashboard.>` ACL) |
| Edge cache | Optional TTL `session→user_id` only |
| Signed JWT/HMAC | Deferred (not parallel V1) |

---

## North star

```text
Browser → dashboard (thin BFF + SPA + chat) → NATS → hub sole ControlPlaneStore
                no auth.db open on dashboard
```

| Layer | Owner |
|-------|--------|
| Durable identity (users, sessions, invites, keys, orgs, links) | **Hub only** |
| HTTP cookie / login UX | **Dashboard BFF** (façade) |
| Authz (`authorize`, jobs/agents/admin) | **Hub** (rehydrate principal) |
| Chat grants / dual-link gate | **Hub** (unchanged plane) |

---

## PR slices (ordered, each mergeable)

### Slice 0 — Ops quarantine (this change set)

| Item | Scope | Done when |
|------|--------|-----------|
| ADR-103 amended (hub store owner) | git | ✓ |
| This migration doc | git | ✓ |
| `security-routing.md` target updated | git | ✓ |
| `quadlet.toml` `component.dashboard` **disabled** | git SSoT | ✓ this PR |
| M₁ host: stop/mask unit; hub/NATS green | **ops host** (not git) | ✓ one-time ops |
| Post-merge: converge does not re-enable dashboard | gate | after merge |

**Slice 0 CI exit:** docs + `disabled` flag only — no identity RPC expected green.

**Out of scope:** volume mount, partial dual-open “fix”.

**Gate (until Slice 4):** no new dual-open surface; existing dual-open code = known debt until Slice 2.

---

### Slice 1 — Hub identity RPC surface (no SPA change)

**Goal:** Hub can serve full identity lifecycle over NATS; dashboard code still dual-open until Slice 2.

| Work | Notes |
|------|--------|
| Contracts | Subjects under **`factory.dashboard.auth.*`** (+ request/response types in roxabi-contracts) |
| Hub handlers | Use **existing** hub `_control_plane` only; fail-closed; audit events |
| Bootstrap admin | Env on **hub** process; document rename plan (`FACTORY_CP_BOOTSTRAP_*` alias later) |
| Principal wire (design) | `user_id` + `session_id`/api_key id; **rehydrate design** documented (implement fail-closed on all business RPCs in Slice 3) |
| Data | Same `auth.db` path hub already uses — no bulk data migration if path identical |
| Tests | Unit hub RPC: login happy/deny, resolve session, member cannot invite |

**Exit:** hub identity RPC green in CI; dual-open code may still exist but new path does not depend on dashboard store open.

**Depends:** Slice 0.

---

### Slice 2 — Thin BFF: stop local store open

| Work | Notes |
|------|--------|
| Remove `open_control_plane_store()` from `WebAdapter.astart` | Inject nothing or a **RPC-backed** directory port |
| `require_principal` / auth routes | Call hub RPC for resolve/login/logout/invite |
| Cookie | Set/clear from hub-minted opaque token or session id |
| Optional edge cache | TTL cache `session_token → user_id` only (not roles) |
| Delete dual-open code paths | No ControlPlaneStore in dashboard process |
| Tests | BFF TestClient with mocked hub RPC; no real auth.db in web adapter |

**Exit:** dashboard process has **zero** SQLite identity open; still works against hub in integration tests.

**Depends:** Slice 1.

---

### Slice 3 — Wire security: rehydrate + health

| Work | Notes |
|------|--------|
| Hub `_wrap` | Rehydrate principal from store by `user_id` (+ validate proof); **ignore** client roles |
| HealthCmd dashboard | Public `/healthz` (or cmdline probe); **never** `/api/agents` 200 |
| Public routes | login, accept-invite, health, static login shell only |
| Runbooks | Bootstrap on hub; dashboard has no BOOTSTRAP_* store open |

**Exit:** forgery class (trusted wire roles) closed; healthchecks auth-safe.

**Depends:** Slice 2 (or parallel if hub rehydrate independent).

---

### Slice 4 — Re-enable dashboard on M₁

| Work | Notes |
|------|--------|
| `disabled = false` on `component.dashboard` | After Slice 2–3 on `staging-svc` image |
| If host was masked | `systemctl --user unmask factory-dashboard.service` **before** converge |
| Converge M₁ | `make converge`; confirm unit file reinstalled; **no** factory-data on dashboard unit |
| Smoke | unauth `auth/me` → 401; login → 200; hub sole `auth.db` open; HealthCmd public |
| SPA | Full login / invite / link against thin BFF |

**Exit:** operator console live; crash-loop gone without volume bandage.

**Depends:** Slice 2 + 3 shipped in `staging-svc` (Slice 3 green necessary, not sufficient alone).

---

### Slice 5 — Cleanup debt (optional same train)

| Work | Notes |
|------|--------|
| Remove `identity_cache_rewarm` if link writes only on hub | Or keep as no-op safety |
| Org routes: no local authorize fork | Prefer hub RPC for mutations |
| Docs | deployment.md volumes table stays hub-only for identity |
| ADR-094 soft note | One Tailnet surface; IdP DB on hub |

---

### Slice 6 — C-lite (optional, not committed)

Only if measured: move BFF route modules into hub process; dashboard reverse-proxies `/api/bff`.
SPA + chat stay edge. **Not** required for IdP uniqueness.

---

## Explicit non-goals

| Non-goal | Why |
|----------|-----|
| Mount `factory-data` on dashboard | #1721 / dual-writer / blast radius |
| SPA inside hub container | Blast radius + release cadence |
| External OIDC V1 | Deferred multi-product |
| Per-platform auth policy | Axial trap |

---

## Gate checklist (every slice)

- [ ] `qg` / pre-push green
- [ ] No new dual open of ControlPlaneStore
- [ ] importlinter `dashboard-no-infrastructure`
- [ ] Hub fail-closed without principal / invalid proof
- [ ] Health public
- [ ] M₁ hub/NATS/adapters healthy when dashboard still disabled (Slices 0–3)

---

## Rollback

| Slice | Rollback |
|-------|----------|
| 0 | Re-enable component only after Slice 4 criteria |
| 1–3 | Feature flag hub RPC vs legacy dual-open **only in non-prod**; prod stays dashboard-disabled until 4 |
| 4 | `disabled = true` again |

---

## Success metric

- One process opens durable CP identity: **hub**
- Dashboard container RO, **no** identity volume
- Unauth `/api/bff/auth/me` → **401** (not 404, not crash)
- Operator login works end-to-end on Tailnet
