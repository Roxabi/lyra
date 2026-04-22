# Analysis: Corpus Live Access — Architecture Options

**Issue:** #866
**Date:** 2026-04-22
**Status:** Decision reached

---

## Architecture Overview (chosen path)

```
                    ┌──────────────────────────────────┐
                    │  GitHub Org (Roxabi)              │
                    │  - issues / issue_dependencies    │
                    └──────────────┬───────────────────┘
                                   │ webhook POST
                                   ▼
                    ┌──────────────────────────────────┐
                    │  Cloudflare Edge                  │
                    │  (routes via tunnel, no CF DB)    │
                    └──────────────┬───────────────────┘
                                   │ HTTPS → tunnel
                                   ▼
      M₂ (dev) / M₁ (prod — flip tunnel target)
      ┌───────────────────────────────────────────────┐
      │  cloudflared daemon                            │
      │  (supervisord: cloudflared.conf)               │
      │  outbound tunnel → dashboard.roxabi.dev         │
      └──────────────┬────────────────────────────────┘
                     │ localhost
                     ▼
      ┌───────────────────────────────────────────────┐
      │  FastAPI (roxabi-dashboard repo)               │
      │  - POST /webhook/github  (HMAC-validated)      │
      │  - GET  /api/issues                            │
      │  - GET  /api/issues/:key                       │
      │  - GET  /api/graph                             │
      │  - GET  /api/repos                             │
      │  - GET  /health                                │
      └──────────────┬────────────────────────────────┘
                     │
                     ▼
      ┌───────────────────────────────────────────────┐
      │  ~/.roxabi/corpus.db  (SQLite)                 │
      │  written by: webhook handler + reconciler      │
      │  read by:    API endpoints                     │
      └───────────────────────────────────────────────┘
                     ▲
                     │ GraphQL backfill (hourly + on-startup)
      ┌───────────────────────────────────────────────┐
      │  scripts/corpus/  (existing reconciler)        │
      │  GitHub GraphQL → SQLite upsert                │
      └───────────────────────────────────────────────┘
```

**Write path:** GitHub webhook → FastAPI `/webhook/github` → HMAC validate → upsert SQLite.
**Backfill path:** reconciler runs on startup + periodic cron (e.g. hourly) to heal webhook drops when M₂ is off.
**Read path:** any client → CF Tunnel → FastAPI → corpus.db (read-only query).

---

## Options Ranked

| # | Option | Summary | Decision |
|---|--------|---------|---------|
| 1 | **M₂ + Cloudflare Tunnel** | FastAPI on M₂, `cloudflared` daemon, public custom domain, corpus.db stays local | **Chosen** |
| 2 | Cloudflare Workers + D1 | Serverless Worker, D1 as managed SQLite, no local machine needed | Rejected — see below |
| 3 | Supabase | Managed Postgres + Edge Functions, free tier | Rejected — see below |

---

## Option 1 — M₂ + Cloudflare Tunnel (Chosen)

### Rationale

- Corpus.db already exists and is kept current by `scripts/corpus/`. No schema migration, no replication, no sync lag between a cloud DB and local.
- Cloudflare Tunnel provides public HTTPS with a custom domain (`dashboard.roxabi.dev`) without opening inbound firewall ports. CF handles TLS, routing, and DDoS mitigation for free.
- The `cloudflared` daemon fits naturally into the existing supervisord pattern — one `.conf` file, same `make <svc> {start,stop,status,logs}` grammar.
- M₂ → M₁ migration is: `rsync ~/.roxabi/corpus.db M₁:~/.roxabi/`, deploy same stack on M₁, point tunnel at M₁ `localhost`. No code change.
- FastAPI is familiar Python; no new runtime or language boundary.

### Component shape

| Component | Location | Notes |
|-----------|----------|-------|
| FastAPI app | `roxabi-dashboard` repo | Cross-repo dep; lyra owns corpus.db + reconciler |
| `cloudflared` daemon | `deploy/supervisor/conf.d/cloudflared.conf` | Separate process; not embedded in app |
| corpus.db | `~/.roxabi/corpus.db` | Path via `CORPUS_DB_PATH` env var |
| Reconciler | `scripts/corpus/` (lyra) | Unchanged; adds startup + hourly schedule |
| Webhook secret | env var `GITHUB_WEBHOOK_SECRET` | HMAC-SHA256 against `X-Hub-Signature-256` |

### Migration path M₂ → M₁

1. Confirm stack works on M₂ (dev).
2. `rsync ~/.roxabi/corpus.db roxabituwer:~/.roxabi/`.
3. Deploy same FastAPI + `cloudflared` on M₁ under its supervisord.
4. Update tunnel target in Cloudflare dashboard from M₂ to M₁ local address.
5. Verify `/health` and one API call through tunnel.
6. Done — M₂ instance can be stopped; reconciler on M₁ takes over.

---

## Option 2 — Cloudflare Workers + D1 (Documented, Rejected)

Workers + D1 would put the backend and DB in Cloudflare's edge network, eliminating any local machine dependency.

**Why rejected:**
- D1 is a separate DB from corpus.db. Keeping them in sync requires either: (a) dropping corpus.db and re-pointing all local tooling at D1 (high migration cost, breaks offline tools), or (b) maintaining dual-write (complexity, eventual consistency).
- TypeScript Worker runtime adds a language boundary to a Python-first stack.
- Free tier limits (100K req/day Worker, 5GB D1) are adequate today but create a new ceiling with no escape hatch.
- The existing `scripts/corpus/` reconciler would need a Cloudflare-specific rewrite or a Worker-to-Worker bridge.

**Preserved schema reference** (D1 schema from original analysis — identical to corpus.db and still accurate):

```sql
CREATE TABLE issues (
  key TEXT PRIMARY KEY, repo TEXT NOT NULL, number INTEGER NOT NULL,
  title TEXT NOT NULL, state TEXT NOT NULL, url TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  closed_at TEXT, milestone TEXT, is_stub INTEGER DEFAULT 0
);
CREATE TABLE labels (
  issue_key TEXT NOT NULL, name TEXT NOT NULL,
  PRIMARY KEY (issue_key, name),
  FOREIGN KEY (issue_key) REFERENCES issues(key) ON DELETE CASCADE
);
CREATE TABLE edges (
  src_key TEXT NOT NULL, dst_key TEXT NOT NULL,
  PRIMARY KEY (src_key, dst_key),
  FOREIGN KEY (src_key) REFERENCES issues(key) ON DELETE CASCADE,
  FOREIGN KEY (dst_key) REFERENCES issues(key) ON DELETE CASCADE
);
CREATE TABLE sync_state (
  repo TEXT PRIMARY KEY, last_cursor TEXT, last_synced_at TEXT NOT NULL
);
```

---

## Option 3 — Supabase (Documented, Rejected)

Supabase provides managed Postgres + Edge Functions + Auth on a free tier.

**Why rejected:**
- Postgres vs SQLite — schema migration required; local tooling that reads corpus.db directly would break or need a client swap.
- 50K Edge Function invocations/month is lower than the CF Workers free tier.
- Adds a vendor dependency with less control than self-hosted.
- Same dual-DB sync problem as Option 2.

---

## Auth Decision

**MVP:** public read on all `GET /api/*` endpoints. GitHub issue metadata is already public. Webhook endpoint protected by HMAC-SHA256 only (`X-Hub-Signature-256`). No OAuth, no Cloudflare Access.

Revisit when a write-capable user endpoint lands (e.g. status updates, manual issue edits).

---

## Frontend Strategy

`roxabi-dashboard` is a clean rewrite (tag current state `pre-rewrite` before reset). First view: promote `lyra-v2-dependency-graph-v5.1` (existing artifact) as the first tab, swap static data for API calls, add repo dropdown. No new viz work in MVP.

Single GitHub org (Roxabi) → repo selector is a simple dropdown populated from `GET /api/repos`. No multi-org aggregation layer.

---

## References

- [Cloudflare Tunnel Docs](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)
- [GitHub Webhook Events](https://docs.github.com/en/webhooks/webhook-events-and-payloads)
- [GitHub Webhooks — Validating payloads](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries)
- `scripts/corpus/` — existing GraphQL reconciler (lyra repo)
- `deploy/supervisor/` — supervisord pattern reference
