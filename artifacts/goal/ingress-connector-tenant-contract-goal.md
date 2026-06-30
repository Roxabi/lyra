# /goal — Ingress connector × tenant contract (ADR-096)

> **Issue:** _TBD — create GitHub issue when this goal is approved_
> **ADR:** [`docs/architecture/adr/096-ingress-connector-tenant-registry.mdx`](../../docs/architecture/adr/096-ingress-connector-tenant-registry.mdx)
> **Epic context:** Sentinelle plane ① · `factory-ingress` · multi-tenant factory ops

---

## Goal (one sentence)

Open the **N connectors × M tenants** axes in `factory-ingress` via a **connector plugin registry** and **installation registry** (`ingress.db`), with **tenant-scoped NATS subjects**, while keeping **mono-user (`default`) HTTP behaviour identical** today.

---

## Identity model — users vs `factory_tenant`

| Concept | Storage | Scope |
|---|---|---|
| **Platform user** (`tg:user:…`, `dc:user:…`) | `~/.roxabi/factory/auth.db` — `agent_grants`, pairing (ADR-090) | Who may talk to agents via bots |
| **Agent workspace** | `~/.roxabi/factory/config.db` — agents, bots | Logical agent tenancy (ADR-090) |
| **`factory_tenant`** | `~/.roxabi/factory/ingress.db` — `connector_installations` | Who owns external webhook events (this goal) |

**Centralized user auth:** yes — `auth.db` is the SSoT for grants and identity aliases. **Not** the same table as connector installations.

**V1:** one `factory_tenant = "default"`. **Post-#1992:** dashboard operator workspace → stable `factory_tenant` slug; install flows write `ingress.db` only (authenticated BFF, tenant-admin scoped).

---

## Problem statement

```
            connecteurs (N)  →  GitHub · Cloudflare · Vercel · …
tenants (M) ↓
            nous · user B · user C · …
```

If each **(connector × tenant)** cell is hand-coded, the system does not scale. Current code
already duplicates per-service logic in `routes.py`, `config.py`, `verify.py`, `normalize.py`.

**Trap to avoid:** coding **N×M webhook URLs and HMAC secrets** for every connector. GitHub Apps
and Vercel Integrations impose **one URL + one app secret**; tenant identity lives in the
**payload** + **installation registry**. Cloudflare is the exception (per-account webhook +
`cf-webhook-auth`).

---

## Non-goals (this goal)

- Dashboard onboarding UI (needs auth **#1992**)
- Vercel connector implementation (separate slice after contract lands)
- Cloudflare OAuth auto-provision API (separate slice)
- Hookdeck / Svix gateway
- Sentinelle hub module consumer logic (separate epic)
- Per-tenant Sentinelle Discord routing (separate epic)
- Changing roxabi-live webhook path
- Storing connector installs in `auth.db` or `config.db`

---

## Architecture contract (must hold after all slices)

### Routes

| Route | Family | When |
|---|---|---|
| `POST /webhook/{connector}` | A (centralized app) | GitHub, Vercel |
| `POST /webhook/{connector}/{tenant}` | B (per-account) | Cloudflare (and similar) |
| `POST /webhook/github` | alias | → `connector=github`, `path_tenant=None` |
| `POST /webhook/cloudflare` | alias | → `connector=cloudflare`, `path_tenant=default` |

Static aliases registered **before** generic `/{connector}` routes (no shadowing).

### Orchestrator pipeline (normative)

```
resolve connector
→ [Family B] path_tenant → SecretResolver.get_tenant_secret
→ verify (raises VerificationError → 401 "invalid auth")
→ external_id = connector.parse_external_id(...)
→ factory_tenant = registry.resolve(connector, external_id)  # § resolve rules
→ if None: drop (202 accepted:true, metric, no NATS)
→ [Family B] if factory_tenant != path_tenant: drop
→ lyra = connector.normalize(..., tenant=factory_tenant)
→ publish factory.event.{connector}.{factory_tenant}.{kind}
```

**Published `tenant` always from registry resolve** — never `path_tenant` or raw payload alone.

### Connector plugin interface

```python
# src/factory/ingress/connectors/base.py
class Connector(Protocol):
    name: str
    family: Literal["centralized_app", "per_account"]

    def verify(
        self,
        headers: Mapping[str, str],
        body: bytes,
        secrets: SecretResolver,
        *,
        path_tenant: str | None,
    ) -> None: ...  # raises VerificationError

    def parse_external_id(
        self,
        headers: Mapping[str, str],
        body: dict[str, Any],
        *,
        path_tenant: str | None,
    ) -> str | None: ...

    def normalize(
        self,
        headers: Mapping[str, str],
        body: dict[str, Any],
        *,
        tenant: str,
    ) -> LyraEvent: ...


class SecretResolver(Protocol):
    def get_connector_secret(self, connector: str) -> str: ...
    def get_tenant_secret(self, connector: str, factory_tenant: str) -> str | None: ...


class InstallationRegistry(Protocol):
    def resolve(self, connector: str, external_id: str | None) -> str | None: ...
    def upsert_lifecycle(
        self, connector: str, external_id: str, factory_tenant: str, *, enabled: bool
    ) -> None: ...
```

Port location: `src/factory/ingress/ports.py`. SQLite impl:
`src/factory/infrastructure/stores/ingress/installation_store.py`. Wire in `serve.py`.

### Installation registry (`ingress.db`)

```
~/.roxabi/factory/ingress.db
  connector_installations(
    connector, external_id, factory_tenant, enabled, metadata_json, updated_at
    PRIMARY KEY (connector, external_id)
  )
```

**V1 seed:** env `INGRESS_GITHUB_INSTALLATION_ID` → row `(github, <id>, default, enabled=1)`.
If GH connector enabled and env unset: warn at startup, mono-user fallback for `installation`
absent only. **Never** seed `(github, *, default)`.

**Resolve rules:** see ADR-096 §3.

### Secrets

| Connector | V1 delivery | Scale pattern |
|---|---|---|
| GitHub | Podman `factory-ingress-github-webhook` | Static N (`get_connector_secret`) |
| Cloudflare | Podman `factory-ingress-cloudflare-webhook` for `default` | + `[secret-class.ingress-tenant]` |
| Vercel | (future) Podman `factory-ingress-vercel-webhook` | Static N |

Future dynamic class pattern: `factory-ingress-{connector}-{factory_tenant}-webhook` (ADR-074).

### NATS subjects

```
factory.event.{connector}.{tenant}.{kind}   # ingress only
factory.event.{service}.{kind}                # host/mail/postiz — unchanged
```

- New helper: `per_connector_tenant_event(connector, tenant, kind)` in `roxabi-contracts`
- `LyraEvent.tenant: str = "default"`; `LyraEvent.service` = connector token
- Ingress **stops** 3-segment github publishes after cutover

### Unknown installation policy

1. Verify first (Family A: before any registry branch).
2. Resolve returns `None`.
3. Log `unknown_installation connector=… external_id=… delivery_id=…`.
4. Increment `ingress_unknown_installation_total{connector}`.
5. Return **202** `{"accepted": true}` — identical to success body.
6. No NATS publish.

### Future dashboard handoff (freeze now — implement in #1992)

```
POST /api/bff/connectors/{connector}/installations
  Auth: operator session (#1992)
  Body: { external_id, factory_tenant, metadata? }
  Idempotent on (connector, external_id)
  Authz: caller may only write rows for their own factory_tenant

DELETE /api/bff/connectors/{connector}/installations/{external_id}
  → enabled=false (soft delete)
```

Ingress **never** imports dashboard modules. Dashboard writes `ingress.db` via hub BFF or
shared store — not via webhook handlers (except lifecycle events).

---

## Acceptance criteria (goal complete)

### Functional

- [ ] **AC1** — `ConnectorRegistry` dispatches `github` and `cloudflare`; no `if/elif` in `routes.py`
- [ ] **AC2** — HTTP back-compat: `/webhook/github` and `/webhook/cloudflare` unchanged for providers
- [ ] **AC3** — GitHub events publish to `factory.event.github.default.<kind>` (not 3-segment)
- [ ] **AC4** — `LyraEvent.tenant` field; contracts tests updated
- [ ] **AC5** — `InstallationRegistry` on `ingress.db` with resolve/upsert; RW mount in quadlet
- [ ] **AC6** — GitHub `parse_external_id` reads `installation.id`; unknown id → drop policy
- [ ] **AC6b** — `installation` absent + mono-user → `default` fallback (check_run compat)
- [ ] **AC7** — `ingress.toml` generic `[connector.<name>]`; no hardcoded `IngressConfig.github` fields
- [ ] **AC8** — Unit tests for verify strategies via registry (GH HMAC, CF header)
- [ ] **AC9** — `artifacts/specs/sentinelle-four-planes-spec.mdx` updated (ingress 4-segment)
- [ ] **AC10** — `uv run pytest`, `uv run ruff check .`, `uv run pyright` green
- [ ] **AC11** — Sentinelle trigger patterns tenant-aware (`factory.event.github.>.check_run.completed` or equivalent)
- [ ] **AC12** — `per_service_event` retained for non-ingress; ingress uses `per_connector_tenant_event` only
- [ ] **AC13** — `docs/runbooks/ingress-webhooks.md` + `quadlet-install.md` lists `factory-ingress`
- [ ] **AC14** — `ingress_unknown_installation_total` metric or documented LogQL alert recipe

### Security (AC-S)

- [ ] **AC-S1** — Pipeline order: verify → parse_external_id → registry.resolve → publish
- [ ] **AC-S2** — Published tenant **only** from registry resolve (`enabled=true`)
- [ ] **AC-S3** — Family B: drop if `path_tenant != factory_tenant`; IDOR test
- [ ] **AC-S4** — `factory_tenant` slug validated `^[a-z0-9][a-z0-9_-]{0,62}$`
- [ ] **AC-S5** — Unknown install: HTTP body identical to success; detail in logs/metrics only
- [ ] **AC-S6** — All verify paths use `hmac.compare_digest`; no `==` on secrets
- [ ] **AC-S7** — Secrets never in logs/HTTP errors; test asserts
- [ ] **AC-S8** — Family B: verify failure and unknown tenant → same 401 body
- [ ] **AC-S9** — Max webhook body size (e.g. 5 MiB) → 413
- [ ] **AC-S10** — Document at-least-once delivery; `X-GitHub-Delivery` as `Nats-Msg-Id` (existing)
- [ ] **AC-S11** — Tests: bad sig, unknown install, IDOR path tenant, bad slug, disabled row
- [ ] **AC-S12** — No wildcard `(github, *, default)` in migrations or seeds

---

## Slices (execution order)

### Slice 1 — Contracts + subject helper

| File | Change |
|---|---|
| `packages/roxabi-contracts/src/roxabi_contracts/event/models.py` | Add `tenant: str = "default"` |
| `packages/roxabi-contracts/src/roxabi_contracts/event/subjects.py` | Add `per_connector_tenant_event()`; docstring fork |
| `packages/roxabi-contracts/tests/test_event_subjects.py` | Tenant segment tests |
| `packages/roxabi-contracts/tests/test_event_models.py` | Default tenant |

```bash
uv run pytest packages/roxabi-contracts/tests/test_event_subjects.py packages/roxabi-contracts/tests/test_event_models.py -q
```

---

### Slice 2 — Connector registry + orchestrator + GH/CF plugins

| File | Change |
|---|---|
| `src/factory/ingress/ports.py` | `Connector`, `SecretResolver`, `InstallationRegistry` protocols |
| `src/factory/ingress/connectors/base.py` | `VerificationError` |
| `src/factory/ingress/connectors/github.py` | verify / parse_external_id / normalize |
| `src/factory/ingress/connectors/cloudflare.py` | Family B verify order |
| `src/factory/ingress/connectors/registry.py` | Built-in registration |
| `src/factory/ingress/orchestrator.py` | Pipeline § above |
| `src/factory/ingress/routes.py` | Generic routes + aliases only |
| `src/factory/ingress/secrets.py` | `PodmanSecretResolver` |
| `tests/ingress/` | Route, verify, orchestrator order tests |

```bash
uv run pytest tests/ingress/ -q
```

---

### Slice 3 — Installation registry + deploy mount

| File | Change |
|---|---|
| `src/factory/infrastructure/stores/ingress/installation_store.py` | SQLite `ingress.db` |
| `src/factory/infrastructure/stores/migrations/ingress_store_migrations.py` | Schema |
| `src/factory/ingress/config.py` | Generic connector config from TOML |
| `deploy/quadlet/factory-ingress.container` | RW volume `ingress.db` |
| `deploy/env/ingress.env.example` | `INGRESS_GITHUB_INSTALLATION_ID`, `INGRESS_DB_PATH` |
| `src/factory/ingress/serve.py` | Wire registry + orchestrator |

```bash
uv run pytest tests/ingress/ -q
```

---

### Slice 4 — Publisher + spec migration

| File | Change |
|---|---|
| `src/factory/ingress/publisher.py` | `per_connector_tenant_event` |
| `artifacts/specs/sentinelle-four-planes-spec.mdx` | 4-segment ingress row + triggers |
| `docs/architecture/CURRENT.generated.md` | Regenerate if gate requires |

```bash
uv run pytest packages/roxabi-contracts/tests/test_event_subjects.py tests/ingress/ -q
```

---

### Slice 5 — Lifecycle stubs + metrics

| File | Change |
|---|---|
| `src/factory/ingress/connectors/github.py` | `installation` created/deleted → registry upsert |
| `src/factory/ingress/metrics.py` | `ingress_unknown_installation_total` |
| `deploy/secrets-policy.toml` | Comment `[secret-class.ingress-tenant]` sketch |

```bash
uv run pytest tests/ingress/ -q
uv run ruff check src/factory/ingress/
uv run pyright src/factory/ingress/
```

---

### Slice 5.5 — Ops runbook (deploy)

| File | Change |
|---|---|
| `docs/runbooks/ingress-webhooks.md` | **New** — public URL, secrets, seed, rotation, unknown_installation triage |
| `docs/runbooks/quadlet-install.md` | Add `factory-ingress` to container table |
| `docs/runbooks/secrets-rotation.md` | Ingress webhook secret rotation section |

Runbook must document:

1. GitHub App webhook URL (factory) vs roxabi-live second URL
2. CF Tunnel / Funnel for public HTTPS
3. `INGRESS_GITHUB_INSTALLATION_ID` seed
4. `ingress.db` backup alongside factory data
5. LogQL for `unknown_installation`

---

## User stories (product appendix)

| ID | Story | Covered by |
|---|---|---|
| O1 | Operator keeps existing webhook URLs | AC2 |
| O2 | Operator sees CI events in Sentinelle (same behaviour) | AC3, AC11 |
| O3 | Operator alerted on unknown GitHub installation | AC14, AC-S5 |
| O4 | Operator knows factory vs roxabi-live GitHub URL | Slice 5.5 runbook |
| I1 | Operator manually seeds installation row pre-dashboard | AC5, runbook |
| U1 | Authenticated user installs GitHub App from dashboard | Future #1992 + handoff API |
| U2 | User disconnects → events stop | Lifecycle `enabled=false` (Slice 5) |
| U3 | User never sees another tenant's events on bus | AC-S2, Sentinelle filter (future epic) |

---

## Suggested GitHub issue body (draft)

```markdown
## Summary

Implement ADR-096: connector plugin registry + `ingress.db` installation registry +
tenant-scoped `factory.event.{connector}.{tenant}.{kind}` for factory-ingress.

## Motivation

Scale external observability (Sentinelle plane ①) across N cloud connectors and M factory
tenants without per-cell hardcoding. V1 = mono-user `default`; contract opens M>1 additively.

## Acceptance criteria

See `artifacts/goal/ingress-connector-tenant-contract-goal.md` AC1–AC14, AC-S1–S12.

## References

- ADR-096 (amends ADR-091 ingress subjects)
- ADR-091 (plane ①)
- #1992 (auth — future onboarding)
- #2008 (ingress container — done)

## Labels

`type:feat` `lane:infra` `size:L` `dev-core:axial-adr-review`
```

---

## Dependencies

| Dep | Relation |
|---|---|
| **#1992** auth | Blocks dashboard self-service; links `factory_tenant` ↔ operator workspace |
| **#2008** ingress container | Done |
| **auth.db** | Orthogonal — user grants; not modified by this goal |
| ADR-091 Sentinelle | AC11 spec migration |
| `factory-nats-ingress` ACL | `factory.event.>` publish — OK |

---

## Risk register

| Risk | Mitigation |
|---|---|
| Subject shape breaks exact consumers | AC11; wildcard `github.>` still works |
| `ingress.db` not mounted RW | AC5, Slice 3 quadlet |
| IDOR Family B | AC-S3 |
| Registry wildcard seed | AC-S12 |
| Lifecycle creep into provisioning | Slice 5 registry CRUD only; no OAuth in ingress |
| Silent unknown drops | AC14 metric + runbook |

---

## PR checklist (axial / security)

- [ ] No `if connector == "github"` in `routes.py`
- [ ] Family A: no required `/{tenant}` route
- [ ] Tenant published = `registry.resolve()` only
- [ ] Lifecycle handlers: registry upsert only
- [ ] `hmac.compare_digest` on all secret compares
- [ ] No Option A shapes (per-tenant GitHub URL/secret)

---

## Quality gates

```bash
uv run pytest
uv run ruff check .
uv run pyright
```

---

## Workpack pointer

```bash
cp -r artifacts/plans/TEMPLATE artifacts/plans/<ISSUE-NUMBER>
# Map slices 1, 2, 3, 4, 5, 5.5 → task-0001.md … task-0006.md
```