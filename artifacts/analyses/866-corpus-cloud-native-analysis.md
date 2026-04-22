# Analysis: Corpus Cloud-Native Architecture Options

**Issue:** #866
**Date:** 2026-04-22
**Status:** Draft — pending investigation

---

## Architecture Overview

```
GitHub Org (Roxabi)
      │
      │ webhook
      ▼
┌─────────────────────────────────────────┐
│  Cloudflare Worker (free)               │
│  - POST /webhook/github                 │
│  - GET  /api/issues                     │
│  - GET  /api/issues/:key                │
│  - GET  /api/graph                      │
└─────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────┐
│  Cloudflare D1 (SQLite, free)           │
│  - issues table                         │
│  - labels table                         │
│  - edges table                          │
│  - sync_state table                     │
└─────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────┐
│  Frontend (TBD)                         │
│  - Cloudflare Pages?                    │
│  - Local web app?                       │
│  - None (API only)?                     │
└─────────────────────────────────────────┘
```

---

## Free Stack Options

### Cloudflare (Recommended)

| Service | Free Tier | Use Case |
|---------|-----------|----------|
| **Workers** | 100K req/day | Webhook + API backend |
| **D1** | 5GB SQLite | Database |
| **Pages** | Unlimited hosting | Frontend (static) |
| **Pages Functions** | 100K req/day | Server-side rendering |
| **Access** | 50 users | Zero-trust auth (optional) |

**Total cost: $0/month**

### Alternative: Supabase

| Service | Free Tier |
|---------|-----------|
| Postgres DB | 500MB |
| Edge Functions | 50K invocations/month |
| Auth | 50K monthly active users |
| Storage | 1GB |

**Cons:** Postgres vs SQLite (schema migration), lower function limits

---

## Backend: Cloudflare Worker

### Endpoints

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `POST` | `/webhook/github` | Receive GitHub events | Signature validation |
| `GET` | `/api/issues` | List issues (filterable) | TBD |
| `GET` | `/api/issues/:key` | Single issue | TBD |
| `GET` | `/api/graph` | Edge list | TBD |
| `GET` | `/api/repos` | Repo list + sync status | TBD |
| `GET` | `/health` | Status check | Public |

### Worker Code Skeleton

```typescript
// worker.ts
interface Env {
  DB: D1Database;
  WEBHOOK_SECRET: string;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    // Webhook endpoint
    if (url.pathname === "/webhook/github" && request.method === "POST") {
      return handleWebhook(request, env);
    }

    // API endpoints
    if (url.pathname.startsWith("/api/")) {
      return handleApi(request, env);
    }

    return new Response("Not found", { status: 404 });
  }
};

async function handleWebhook(request: Request, env: Env): Promise<Response> {
  // 1. Validate X-Hub-Signature-256
  const sig = request.headers.get("X-Hub-Signature-256");
  const body = await request.text();
  const expected = `sha256=${hmacSha256(env.WEBHOOK_SECRET, body)}`;
  if (sig !== expected) {
    return new Response("Unauthorized", { status: 401 });
  }

  // 2. Parse event
  const event = request.headers.get("X-GitHub-Event");
  const payload = JSON.parse(body);

  // 3. Upsert to D1
  if (event === "issues") {
    await upsertIssue(env.DB, payload);
  } else if (event === "issue_dependencies") {
    await upsertEdge(env.DB, payload);
  }

  return new Response("OK", { status: 200 });
}
```

---

## Database: Cloudflare D1

### Schema (same as local corpus.db)

```sql
CREATE TABLE issues (
  key TEXT PRIMARY KEY,
  repo TEXT NOT NULL,
  number INTEGER NOT NULL,
  title TEXT NOT NULL,
  state TEXT NOT NULL,
  url TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  closed_at TEXT,
  milestone TEXT,
  is_stub INTEGER DEFAULT 0
);

CREATE TABLE labels (
  issue_key TEXT NOT NULL,
  name TEXT NOT NULL,
  PRIMARY KEY (issue_key, name),
  FOREIGN KEY (issue_key) REFERENCES issues(key) ON DELETE CASCADE
);

CREATE TABLE edges (
  src_key TEXT NOT NULL,
  dst_key TEXT NOT NULL,
  PRIMARY KEY (src_key, dst_key),
  FOREIGN KEY (src_key) REFERENCES issues(key) ON DELETE CASCADE,
  FOREIGN KEY (dst_key) REFERENCES issues(key) ON DELETE CASCADE
);

CREATE TABLE sync_state (
  repo TEXT PRIMARY KEY,
  last_cursor TEXT,
  last_synced_at TEXT NOT NULL
);
```

---

## Frontend Options

### Option A: Cloudflare Pages (Recommended)

| Aspect | Detail |
|--------|--------|
| Hosting | Free, unlimited |
| Framework | React, Vue, Svelte, Astro, etc. |
| Build | Automatic on git push |
| API calls | Fetch from Worker endpoints |
| Auth | Can integrate GitHub OAuth |

**Pros:** Same ecosystem as Worker/D1, zero cost, global CDN

### Option B: Local Web App

- Run locally, points to cloud API
- No auth needed if API is public read
- Useful for development/debugging

### Option C: API Only (No Frontend)

- Minimal approach
- CLI tools and dep-graph consume API directly
- Add frontend later if needed

---

## Authentication

### Option A: Public Read, Webhook-Only Write

| Endpoint | Auth |
|----------|------|
| `GET /api/*` | Public (no auth) |
| `POST /webhook/*` | HMAC signature |

**Pros:** Simplest, no user management
**Cons:** Anyone can read corpus (may be fine — it's public GitHub data)

### Option B: GitHub OAuth

```
User ──▶ Frontend ──▶ "Login with GitHub" ──▶ GitHub OAuth
                                                    │
                                                    ▼
                                            Access Token
                                                    │
                                                    ▼
                                        Frontend calls API with token
                                                    │
                                                    ▼
                                        Worker validates token via GitHub API
```

**Pros:** Fine-grained access control, user identity
**Cons:** More complex, token management

### Option C: Cloudflare Access

- Zero-trust auth layer
- Can restrict to `@roxabi.com` emails
- Integrates with GitHub as IdP

**Pros:** Enterprise-grade, managed
**Cons:** Requires Cloudflare Access setup

---

## Local Sync Strategy

### Option A: Cloud-Only

- D1 is SSoT
- Local tools call API
- No local DB needed

**Pros:** Simple, single source of truth
**Cons:** Requires network access, API dependency

### Option B: Cloud Primary + Local Cache

```
Webhook → D1
             │
             └──▶ Periodic export → local corpus.db
```

**Pros:** Local tools work offline, cloud tools use API
**Cons:** Two DBs to sync, eventual consistency

### Option C: Dual Write

```
Webhook → D1
         → Forward to local via tunnel → corpus.db
```

**Pros:** Real-time sync both places
**Cons:** Complexity, tunnel dependency

---

## Migration Path

1. **Phase 4a:** Deploy Worker + D1, wire org webhook
2. **Phase 4b:** Verify real-time sync works
3. **Phase 4c:** Add API endpoints
4. **Phase 4d:** (Optional) Deploy frontend
5. **Phase 4e:** Migrate local tools to API consumers (gradual)

---

## Open Questions for Investigation

| # | Question | Decision Needed |
|---|----------|-----------------|
| 1 | Frontend needed? If yes, Cloudflare Pages or local? | User preference |
| 2 | Auth model: public read, GitHub OAuth, or Cloudflare Access? | Security posture |
| 3 | Keep local corpus.db or go cloud-only? | Offline needs |
| 4 | Use existing D1 schema or evolve? | Compatibility |
| 5 | How to handle initial backfill (existing issues)? | Migration strategy |

---

## References

- [Cloudflare Workers Docs](https://developers.cloudflare.com/workers/)
- [Cloudflare D1 Docs](https://developers.cloudflare.com/d1/)
- [Cloudflare Pages Docs](https://developers.cloudflare.com/pages/)
- [GitHub Webhook Events](https://docs.github.com/en/webhooks/webhook-events-and-payloads)
- [GitHub OAuth Apps](https://docs.github.com/en/apps/oauth-apps)
