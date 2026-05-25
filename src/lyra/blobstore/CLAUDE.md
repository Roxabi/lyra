# src/lyra/blobstore/ — HTTP BlobStore Service

## Purpose

FastAPI service exposing `FsBlobStore` over HTTP on port 8449. Deployed as `lyra-blobstore` Quadlet unit on M₁. All other consumers (same-host or cross-host via Tailnet) use `HttpBlobStore` from `packages/roxabi-blobs`.

## Module placement — Framing B (peer-of-adapters)

`axial-adr-review` flagged this as a potential `target-axis-trap` (ADR-073), recommending `src/lyra/infrastructure/blobstore/`. **Decision: keep peer-of-adapters.** See spec §Context for full rationale.

Short version: `lyra-blobstore` is a **bootable process surface** (typer subcommand → uvicorn → FastAPI), structurally identical to `lyra.adapters.{telegram,discord,clipool}`. `lyra.infrastructure.*` is for store-impl code called by other code in-process (ADR-048). This is a process, not a library.

Three-strikes safeguard: if a 2nd HTTP service lands, the wrong-axis drift surfaces and the refactor is one-shot.

## Invariants

- `auth.py` and `serve.py`: zero `lyra.*` imports beyond `lyra.blobstore.*` siblings.
- Bearer auth uses `hmac.compare_digest` (SC-Code-4 — no `==` comparison).
- Token read once at startup (V1: injected value; T7 adds file-read at startup, no per-request re-read — SC-Code-5).
- `/healthz` and `/metrics` bypass auth (allowlist in `BearerAuthMiddleware`).
- Placeholder `/blobs/{store_key}` GET exists only so the auth test has a path to hit. Real handler lands in T7.

## Slice ownership

| File | Slice |
|------|-------|
| `__init__.py`, `auth.py`, `serve.py`, `cli.py` | S1 (skeleton boot) |
| Real blob handlers (PUT/GET/HEAD/DELETE) | S2 (T7) |
| `audit_sink.py`, NATS wiring | S3 |
| Quadlet unit, secret | S4 |
| This file (full) + ADR-067 amendments | S5 |
