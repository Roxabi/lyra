### Summary

- **BlobStore V8 (`src/lyra/blobstore/`) is the only significant new code** since 2026-05-18; it introduces an encapsulation break by reaching into `FsBlobStore._conn` to run raw SQLite SQL for `store_key` lookups and row counts.
- **Importlinter: 8/8 contracts kept, 0 circular dependencies** — P09 boundary modules remain cleanly isolated from bootstrap/adapters/infrastructure per `shared-modules-upper-boundary` and `shared-modules-independence`.
- **Unaudited gap closed**: `src/lyra/tools/gh_token/` — 0 layer violations, clean self-contained helper with no `lyra.*` cross-imports beyond its own subpackage.

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/blobstore/_handlers.py` | 79-82, 168-188, 229-265 | **Medium** | `handle_head` and `handle_delete` access `FsBlobStore._conn` directly to run raw SQLite queries for `store_key` resolution, `blob_ref_id` lookup, and existence checks. This bypasses the public `BlobStore` Protocol and couples the adapter-layer HTTP service to the package's internal schema. | Extend `BlobStore` Protocol / `FsBlobStore` with `head(store_key) -> bool` and `resolve(store_key) -> int` (or equivalent); remove all `_conn` access from handlers. |
| `src/lyra/blobstore/serve.py` | 39-48 | **Medium** | `_blob_count()` reaches into `FsBlobStore._conn` to execute `SELECT COUNT(*) FROM blobs`. Same encapsulation break as handlers. | Add `FsBlobStore.count() -> int` to the public API; replace raw SQL in `_blob_count()`. |
| `src/lyra/blobstore/auth.py` | 44 | **Low** | Unauthorized audit events hardcode `contract_version="1"` instead of importing `roxabi_contracts.envelope.CONTRACT_VERSION`. Creates drift if the contract version is bumped. | Import `CONTRACT_VERSION` from `roxabi_contracts.envelope` and use it in `BlobAuditEvent` construction. |
| `src/lyra/blobstore/_handlers.py` | 203-206 | **Low** | `handle_head` deliberately avoids `store.exists()` to save a DB round-trip; the inline comment admits this is a pre-collapse performance shortcut that forces raw SQL. | Once `FsBlobStore.head(store_key)` exists, replace the dual-lookup SQL with a single Protocol call and remove the workaround comment. |
| `src/lyra/blobstore/audit_sink.py` | 53-74 | **Info** | `BlobAuditSink.emit()` duplicates the degraded-mode fallback pattern (publish → catch `nats.errors.Error` → log to `lyra.security`) already present in `JetStreamAuditSink` (`lyra.infrastructure.audit`). | Extract a shared `JetStreamEmitter` helper in `roxabi-nats` or `lyra.infrastructure.audit` to own the degrade-and-logic; both sinks can delegate to it. |

### Metrics

| Metric | Value |
|---|---|
| Python files analyzed | 27 |
| Files changed since 2026-05-18 | 11 (blobstore 6, monitoring 4, tools/gh_token 1) |
| Importlinter contracts | 8 / 8 kept |
| Circular dependencies | 0 |
| New layer violations | 1 (blobstore `_conn` SQL access) |
| New contract drifts | 1 (blobstore auth `contract_version` hardcoding) |
| ADR-048 stores in P09 scope | 0 (N/A — P09 is boundary/utility, not store layer) |
| Prior-audit findings regressed | 0 |
| `lyra.obs` runtime consumers | 0 (unchanged) |
| `lyra.monitoring` cross-layer imports | 1 (`core.logging_setup` — unchanged) |

### Recommendations (prioritized)

1. **Extend `BlobStore` Protocol and eliminate `_conn` access** — Add `head(store_key: str) -> bool` and `count() -> int` to `roxabi-blobs` public API; remove all raw-SQL `_conn` access from `blobstore/_handlers.py` and `blobstore/serve.py`. This is the highest-impact fix because it decouples the HTTP adapter from the SQLite schema.
2. **Fix contract-version drift in `auth.py`** — Import `CONTRACT_VERSION` from `roxabi_contracts.envelope` instead of hardcoding `"1"` in `blobstore/auth.py`. Zero-risk single-line change.
3. **Add `DEBT:` annotations at `_conn` sites** — If recommendation #1 is deferred, annotate each `_conn` access with a `DEBT:` comment linking to the tracking issue so future schema changes are not missed.
4. **Deduplicate JetStream degrade-and-emit logic** — Extract a shared helper from `BlobAuditSink` and `JetStreamAuditSink` so the degraded-publish → fallback-to-logger pattern lives in one place.
5. **Keep `gh_token` isolation pattern** — The `src/lyra/tools/gh_token/` subpackage is a clean reference for future in-container helpers: zero cross-package `lyra.*` imports, typed boundaries, and injected dependencies. Replicate this structure for any new sidecar utilities rather than allowing hub/core leakage.
