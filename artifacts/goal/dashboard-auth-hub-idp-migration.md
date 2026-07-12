# Migration — Hub IdP + thin BFF (ADR-103 amended)

**Status:** planned
**Target:** ADR-103 Accepted 2026-07-12 (store owner = hub)
**Interim ops:** `[component.dashboard] disabled = true` on factory-hub until Slice 3+ green
**Forbidden bandage:** do **not** mount `factory-data` RW on `factory-dashboard`

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

| Item | Done when |
|------|-----------|
| ADR-103 amended (hub store owner) | ✓ |
| This migration doc | ✓ |
| `security-routing.md` target updated | ✓ |
| `quadlet.toml` `component.dashboard` **disabled** | ✓ |
| M₁: stop + disable unit; confirm hub/NATS green | ✓ |

**Out of scope:** volume mount, partial dual-open “fix”.

---

### Slice 1 — Hub identity RPC surface (no SPA change)

**Goal:** Hub can serve full identity lifecycle over NATS; dashboard code still dual-open until Slice 2.

| Work | Notes |
|------|--------|
| Contracts | `factory.dashboard.auth.*` (or `identity.*`) subjects: login, logout, session.resolve, session.revoke, invite.create/list/revoke/accept, apikey.*, org.* as needed for parity |
| Hub handlers | Use **existing** hub `_control_plane` only; fail-closed; audit events |
| Bootstrap admin | Env on **hub** process (`hub.env` / bootstrap_stores path) — document |
| Principal wire | Spec: BFF will send `user_id` + proof; hub rehydrates roles/orgs (**implement rehydrate in `_wrap` or resolve handler**) |
| Tests | Unit hub RPC: login happy/deny, resolve session, member cannot invite |

**Exit:** hub RPC green in CI; dual-open still present but unused by new path.

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
| `disabled = false` on `component.dashboard` | After Slice 2–3 on staging image |
| Converge M₁ | Observe post-autoupdate; **no** factory-data on dashboard unit |
| Smoke | `auth/me` → 401 unauth; login → 200; hub still sole `auth.db` open |
| SPA | Full login / invite / link against thin BFF |

**Exit:** operator console live; crash-loop gone without volume bandage.

**Depends:** Slice 2 + 3 shipped in `staging-svc`.

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
