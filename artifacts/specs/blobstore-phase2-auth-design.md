# Blobstore Phase-2 auth — per-identity tokens & scoped grants (design record)

> Extracted 2026-07-02 from ADR-067 (consolidation wave 3): deferred design with no issue filed —
> a design record, not a made decision. Filing trigger = first Phase-2 trigger condition met.
> NOTE (2026-07): written pre-#2121; any implementation must reconcile with `agent_grants` as the
> sole authz SSoT (ADR-090) rather than a parallel grants table.

### Phase 2 — per-identity tokens & scoped grants (deferred, design committed)

**Trigger conditions** (any-of):
- Agents own user-uploaded blobs and cross-agent read isolation is required.
- Per-{agent, user} audit trail of blob access is required (compliance, debugging).
- Untrusted third-party consumers join the Tailnet and need bounded access.
- Quota enforcement per identity is required.

**Token model** (pick one at implementation time; both compatible with the Phase 1 `Authorization: Bearer <tok>` header — server-side upgrade only):

| Backend | Verification | Revocation | Best for |
|---|---|---|---|
| **JWT HS256** (claims: `sub`, `scope`, `prefix`, `iat`, `exp`) | stateless (shared secret) | denylist + short TTL + refresh | cross-machine without DB hit |
| **`~/.lyra/auth.db` lookup** (aligned with project `CLAUDE.md` `A := ~/.lyra/auth.db` grants/identity) | SQLite query per request (cheap WAL; ≤1k rps OK) | instant (delete row) | aligns with existing Lyra identity model |

Recommendation: **`auth.db` lookup** if Lyra continues brokering identity via `auth.db` for adapters; **JWT** if blobstore needs verification without `auth.db` access (e.g., deployed off-M₁).

**Scoping model:**

- **Dimensions:** `read | write | delete` × prefix-or-blob-bound (e.g., `agent/<id>/*`).
- **Token issuance:** handled by `lyra-hub` (or a dedicated auth service later). Endpoint shape:
  ```
  POST /auth/blob-token
    body: { subject: "agent/<id>" | "user/<id>",
            scope: ["read", "write"],
            prefix?: "agent/<id>/*",
            ttl?: "1h" }
    → { token: "<bearer>" }
  ```
- **Server-side enforcement:** blobstore middleware validates `subject` + `scope` + `prefix` against the requested operation BEFORE invoking `FsBlobStore`.

**Ownership model (compat with content-addressed dedup):**

Content-addressed storage means N uploaders can produce the same `sha256` — no natural "owner" on the blob itself. Solution: a separate `blob_grants` table in `index.sqlite`:

```sql
CREATE TABLE blob_grants (
    subject       TEXT NOT NULL,     -- "agent/<id>" or "user/<id>"
    store_key     TEXT NOT NULL,
    perms         TEXT NOT NULL,     -- "r" | "rw" | "rwd"
    created_at    INTEGER NOT NULL,
    PRIMARY KEY (subject, store_key)
);
```

- A new upload publishes a new grant row on the existing-or-new blob — no copy.
- Read check: middleware verifies a `(subject, store_key, perms ⊇ requested)` row exists.
- **Dedup invariant preserved:** bytes live once on disk; grants are the access-control plane.

**Non-goals (Phase 2):**
- mTLS / per-agent certs (complex cert mgmt; doesn't distinguish multiple agents on the same host).
- Per-blob ACL (vs prefix scoping): only if granular sharing requirements emerge — not anticipated.

**Implementation tracking:** no issue filed yet — this section is the design record. Filing trigger = first concrete Phase 2 trigger condition met (see above).
