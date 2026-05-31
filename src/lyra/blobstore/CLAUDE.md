# src/lyra/blobstore/ — HTTP BlobStore Service

## Purpose

HTTP-fronted BlobStore service: FastAPI process that exposes `FsBlobStore` over HTTP on
port 8449. Peer-of-adapters per Framing B (spec §Context). Receives blob writes and reads,
backs them with `FsBlobStore` on `~/.lyra/blobs/`, and is the only process that touches
`FsBlobStore` directly. All other consumers (same-host or cross-host) use `HttpBlobStore`
from `packages/roxabi-blobs`.

Used cross-host over Tailnet by M₂ workers (llm-worker, image-worker, future voice-worker).
Entry point: `lyra blobstore serve`.

## Module placement — Framing B (peer-of-adapters)

`axial-adr-review` flagged this as a potential `target-axis-trap` (ADR-073), recommending
src/lyra/infrastructure/blobstore/. **Decision: keep peer-of-adapters.**

Short version: `lyra-blobstore` is a **bootable process surface** (typer subcommand →
uvicorn → FastAPI), structurally identical to `lyra.adapters.{telegram,discord,clipool}`.
`lyra.infrastructure.*` is for store-impl code called by other code in-process (ADR-048).
This is a process, not a library.

Three-strikes safeguard: if a 2nd HTTP service process lands, wrong-axis drift surfaces
and the refactor is one-shot. Acknowledged residual axial finding documented in spec §Context
so the next reviewer sees the explicit choice.

## Host topology

Runs on M₁ (`lyra-hub` role) only. M₂ workers connect via:

```
http://roxabituwer.goose-logarithm.ts.net:8449
```

## Image

`ghcr.io/roxabi/lyra:staging-svc`. Three-strikes rule defers a dedicated
`ghcr.io/roxabi/blobstore` image to ≥3 cross-repo consumers (#1334 + ADR-073).

## Boot order invariant

NATS connect (best-effort) → `BlobAuditSink.provision(nc)` → KV announce
(`blobstore.ready=true`) → uvicorn start on `:8449`.

**NATS-connect failure does NOT abort startup** (degraded mode). If NATS is unreachable:
audit sink logs to lyra.security logger, `blobstore.ready` is not published, uvicorn
starts normally and accepts HTTP traffic. No in-process reconnect loop — failure mode is
intentionally loud; re-provision deferred to next container restart.

## Token semantics

- Bearer token read **once at startup** from `/run/secrets/lyra_blobstore_token`
  (Podman `type=mount` secret, ADR-054).
- Stored in memory for the process lifetime. Re-read requires container restart — NOT a
  `HUP`. Sending `SIGHUP` does NOT rotate the in-memory token.
- `BearerAuthMiddleware` uses `hmac.compare_digest` (¬ `==` comparison — SC-Code-4).
- Rotation runbook: `docs/QUADLET-DEPLOYMENT.md` (5-step: write source → secret create →
  restart → verify → rollback). Same-host client adapters pick up the new token on their
  own next restart; operator coordinates the window.

## Auth boundary

| Phase | Scope | Status |
|-------|-------|--------|
| Phase 1 (V8) | Single shared bearer token | Shipped |
| Phase 2 | Per-identity tokens + scoped grants | #1334 — roadmap |

Auth middleware **allowlist** (bypass bearer check): `/healthz`, `/metrics`.

## Audit semantics

Every op (PUT / GET / HEAD / DELETE) emits a `BlobAuditEvent` on
`lyra.audit.blobs.{op}` — **including 401s** (subject `"anonymous"`, result
`"unauthorized"`). If NATS publish fails, sink degrades to lyra.security logger.

`BlobAuditEvent` defined in `packages/roxabi-contracts/src/roxabi_contracts/audit/blobs.py`.
At time of S3 wiring, the `_emit_audit` stub in `_handlers.py` is replaced by the real sink.

## Error mapping invariants

- Path traversal (store_key resolves outside blob root): **404**, not 400 — no oracle leak
  (FsBlobStore._safe_resolve_in_root).
- `BlobNotFoundError` on GET / HEAD / DELETE: **404**.
- `BlobWriteError` (PUT / DELETE): **500** with `{"detail": "blob write failed"}` — static
  message, no exception text echoed.
- `BlobConsistencyError`: **500**, `error_code:"blob-consistency"`.
- All unhandled exceptions: **500** with `{"detail": "internal error"}` — no schema or
  traceback leaked to the caller.

## Oversized-blob handling (max_bytes decision — S2)

`FsBlobStore` accepts an optional `max_bytes` ceiling (env-configurable). **No pre-read 413
gate exists at the FastAPI layer.** The handler reads the full body into memory first
(`await request.body()`), then calls `FsBlobStore.put()`. If `put()` raises `BlobWriteError`
due to size, the response is **500** (not 413). Callers cannot distinguish oversized from
other write failures via HTTP status; the audit event carries `error_code:"blob-write"`.

Pre-read 413 enforcement (reject before loading body) is deferred: it would require
reading `Content-Length` and enforcing before `request.body()`. V8 ships the simpler path.

## Wire key semantics (content address)

The HTTP wire `store_key` is `sha256:<hex>` (content address). The on-disk `store_path`
(absolute FS path in `blobs.store_path`) is **internal only** and never emitted to callers.

- **PUT** returns `store_key = "sha256:<hex>"` in the JSON body and `Location: /blobs/sha256:<hex>`.
- **GET / HEAD / DELETE** accept `sha256:<hex>` as the path argument and resolve it to
  `store_path` via the manifest before any FS operation.
- Legacy `store_path` keys (absolute path, no prefix) are accepted on GET/HEAD/DELETE
  for back-compat with any in-flight refs predating this change.
- Key resolution lives in `src/lyra/blobstore/_keys.py` (`resolve_wire_key`), imported
  by `_handlers.py` to keep the handler file under the 300-line gate.

## Polymorphic DELETE path

URL path argument is tested in order:
1. Numeric (`^\d+$`) → `blob_ref_id` used directly.
2. `sha256:<hex>` prefix → content_hash lookup: `SELECT r.id FROM blob_refs WHERE r.content_hash = ? ORDER BY ingested_at DESC LIMIT 1`.
3. Other (legacy store_path) → `store_path` JOIN lookup.

Protocol signature uses `blob_ref_id`; HTTP wire identifier is `store_key`.

## HEAD handler dual lookup

`handle_head` performs two sequential database lookups:

1. **`store_path` lookup** — primary path for legacy keys (bare FS path).
2. **`content_hash` fallback** — bare hex or `sha256:<hex>` prefix (prefix stripped via
   `removeprefix`); covers `HttpBlobStore.exists` (passes content_hash directly) and
   callers with canonical wire keys.

≤2 SELECTs total, 0 calls to `store.exists()` (consensus T3).

## In-process consumers (ADR-082)

In-process callers (audio paths, inbound attachment ingest) do NOT import this package directly. They consume the service through:

- Port: `core.ports.BlobStorePort` (`@runtime_checkable` Protocol, wire `roxabi_contracts.BlobRef`)
- Adapter: `infrastructure.blobstore_adapter.HttpBlobStoreAdapter` (injected by bootstrap)
- Composition root: `init_blobstore()` in `bootstrap/factory/voice_overlay.py` (reads URL+token once; restart-not-HUP)

The bare per-call `get_blobstore_client()` factory has been removed (ADR-082). `HttpBlobStoreAdapter` is the sole injection point; adapters and stages receive it as `BlobStorePort`.

## Reference pointers

- `docs/QUADLET-DEPLOYMENT.md` — install runbook, secret rotation, backup procedures
- `docs/architecture/adr/067-blobstore-abstraction-flat-fs-content-addressed.mdx` — Protocol
  contract, HTTP API mapping, auth plane decisions
- `docs/architecture/adr/082-blobstore-driven-port.mdx` — driven-port pattern + injection wiring
- `artifacts/specs/1330-v8-http-fronted-blobstore-spec.mdx` — V8 full spec
