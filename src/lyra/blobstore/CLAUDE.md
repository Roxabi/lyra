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
`src/lyra/infrastructure/blobstore/`. **Decision: keep peer-of-adapters.**

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
audit sink logs to `lyra.security` logger, `blobstore.ready` is not published, uvicorn
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
`"unauthorized"`). If NATS publish fails, sink degrades to `lyra.security` logger.

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

## Polymorphic DELETE path

URL path argument is tested against `^\d+$`:
- Numeric → `blob_ref_id` used directly.
- Non-numeric → SQLite `store_path` lookup to resolve `blob_ref_id`.

Protocol signature uses `blob_ref_id`; HTTP wire identifier is `store_key`.

## HEAD handler dual lookup

`handle_head` in `src/lyra/blobstore/_handlers.py` performs two sequential database lookups:

1. **`store_key` lookup** — primary path for callers that pass an opaque `store_key` (the wire path on disk); the handler resolves it directly via `store_path` in the manifest.
2. **`content_hash` fallback** — secondary path for callers like `HttpBlobStore.exists` that pass a `content_hash` directly (see `packages/roxabi-blobs/CLAUDE.md §HttpBlobStore.exists` for the call shape); the symmetry between the two lookup paths is asserted via the existing inline comment at `_handlers.py:181-182`.

Both `content_hash` and `store_key` must be handled because the `BlobStore` Protocol allows either opaque identifier to act as an existence key. The dual-lookup design keeps the server handler generic without requiring callers to pre-resolve which form they hold.

## Reference pointers

- `docs/QUADLET-DEPLOYMENT.md` — install runbook, secret rotation, backup procedures
- `docs/architecture/adr/067-blobstore-abstraction-flat-fs-content-addressed.mdx` — Protocol
  contract, HTTP API mapping, auth plane decisions
- `artifacts/specs/1330-v8-http-fronted-blobstore-spec.mdx` — V8 full spec
