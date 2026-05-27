# Async Patterns Audit — Partition P10: packages/**/*.py

**Date:** 2026-05-27
**Scope:** roxabi-blobs, roxabi-contracts, roxabi-nats (source + test-relevant internals)
**Context:** First full async-patterns review of packages (previously unaudited). Epic #1277 stage-axis refactor is active; prior audit (2026-05-18) left packages untouched.

---

## Summary

- **Blocking file I/O in async paths** is the dominant issue class: `FsBlobStore` performs raw `os.open/write/fsync/close`, `Path.read_bytes()`, and `Path.unlink()` inside `async def` methods without `asyncio.to_thread()`. `nats_connect()` also reads seed/TLS files synchronously within an async bootstrap.
- **Task supervision gaps** exist in `HttpBlobStore` (ASGI lifespan task swallows all exceptions and can deadlock on startup failure) and `NatsAdapterBase` (heartbeat task crashes are silent until shutdown).
- No active race conditions in shared mutable state were found; `FsBlobStore` correctly serializes writes with `asyncio.Lock`, and `NatsDriverBase` dict mutations are GIL-atomic under the single-threaded event-loop assumption.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `roxabi_blobs/http_store.py` | 58-66 | **High** | `_start_asgi_lifespan` wraps the `run()` coroutine in `try/except Exception: pass`, swallowing all startup errors. If the ASGI app crashes before emitting `lifespan.startup.complete`, `await started.wait()` deadlocks forever. | Remove bare `except Exception: pass`; propagate or set an error event. Add a timeout to `await started.wait()`. |
| `roxabi_blobs/fs_store.py` | 215-225 | **Medium** | `_write_blob_file` calls blocking `os.open`, `os.write`, `os.fsync`, and `os.close` inside an `async def`. On slow disks or large shards this blocks the event loop. | Offload the entire file+dir fsync sequence to `asyncio.to_thread()`, or switch to `aiofiles`/`anyio` for async file I/O. |
| `roxabi_blobs/fs_store.py` | 247 | **Medium** | `get()` invokes `Path.read_bytes()` — a blocking read — directly in an `async def`. | Wrap in `asyncio.to_thread(resolved.read_bytes)`. |
| `roxabi_blobs/fs_store.py` | 339 | **Medium** | `delete()` invokes `Path.unlink()` — a blocking unlink — directly in an `async def`. | Wrap in `asyncio.to_thread(resolved.unlink, missing_ok=True)`. |
| `roxabi_blobs/fs_store.py` | 141 | **Medium** | `put()` computes `hashlib.sha256(data).hexdigest()` while holding `asyncio.Lock`. For large payloads this is CPU-bound and stalls all other writers/readers. | Hash the payload in `asyncio.to_thread()` **before** acquiring the lock, then pass the digest into the guarded section. |
| `roxabi_nats/connect.py` | 188-191 | **Medium** | `nats_connect()` (async) calls `_read_nkey_seed()` and `_build_tls_context()`, which perform blocking `os.open`, `os.fstat`, `fdopen`, and `fh.read()` on the event loop. | Pre-read credentials in sync bootstrap, or wrap both helpers with `asyncio.to_thread()`. |
| `roxabi_blobs/http_store.py` | 68-77 | **Medium** | `_stop_asgi_lifespan` sets a `shutdown` event but never feeds `{"type": "lifespan.shutdown"}` into the `receive_queue`. The lifespan task therefore always hits the 2 s timeout and is cancelled rather than exiting gracefully. | Feed the shutdown message to `receive_queue` so the ASGI app can process it; then await the task with a generous timeout. |
| `roxabi_nats/adapter_base.py` | 148, 178 | **Medium** | Heartbeat task is spawned with `asyncio.create_task(self._heartbeat_loop())` in `run()` and `run_embedded()`. An unhandled exception (e.g. `nats.errors.Error` not covered by the inner `try/except`) kills the task silently until shutdown surfaces it. | Add a defensive wrapper around `_heartbeat_loop` that logs exceptions and optionally restarts; or check `task.done() / task.exception()` periodically. |
| `roxabi_nats/driver_base.py` | 173-178 | **Medium** | `_dict_stream_gen` uses `queue.put_nowait(msg)` inside the NATS message callback. When the 512-slot queue fills, chunks are dropped with only a warning log — no backpressure or retry. | Document the drop policy explicitly in the method docstring, or switch to `await queue.put(msg)` with a larger/tunable `maxsize`. |
| `roxabi_nats/adapter_base.py` | 152-153 | Low | `SIGTERM`/`SIGINT` handlers are registered via `loop.add_signal_handler()` in `run()` but never removed in `_shutdown()`. Repeated `run()` calls in long-lived tests or reloaded processes leak handlers. | Store handler references and call `loop.remove_signal_handler()` during `_shutdown()`. |
| `roxabi_nats/adapter_base.py` | 245 | Low | `heartbeat_payload()` calls `socket.gethostname()` on every heartbeat tick (default every 5 s). Under slow DNS or NSS misconfiguration this blocks the event loop. | Cache `socket.gethostname()` once in `__init__` and reuse it. |
| `roxabi_nats/circuit_breaker.py` | 26 | Low | `NatsCircuitBreaker` uses `threading.Lock()` in an async-first package. While harmless in the current single-threaded usage, it is a semantic trap for future threaded callers and contradicts the package's async contract. | Replace with `asyncio.Lock` and make methods `async def`, or add a prominent docstring warning: "sync-thread only — do not call from async contexts". |
| `roxabi_nats/_serialize.py` | 34 | Low | Module-level `_hints_cache` is an unbounded `dict` with no eviction. Long-running adapters that construct many `_TypeHintResolver` instances can grow memory indefinitely. | Convert to `functools.lru_cache` with a bounded maxsize, or add a periodic cleanup guard. |

---

## Metrics

| Metric | Value |
|--------|-------|
| Source `.py` files reviewed | 35 |
| `async def` functions / methods | ~48 |
| **Blocking I/O call sites** | 5 |
| **Fire-and-forget / unawaited task sites** | 3 |
| **Shared-state race-condition sites** | 0 |
| **Resource-leak / cleanup-gap sites** | 3 |
| **Total findings** | 13 |
| High severity | 1 (~8%) |
| Medium severity | 5 (~38%) |
| Low severity | 7 (~54%) |

---

## Recommendations (prioritized)

1. **Fix `HttpBlobStore` ASGI lifespan deadlock (#1)** — High. The bare `except Exception: pass` is a deadlock trap for test suites and any future non-test usage. Add a startup timeout and propagate the failure.
2. **Offload `FsBlobStore` blocking I/O to threads (#2, #3, #4)** — Medium. The `os.*` and `Path.*` calls are the most impactful event-loop blockers in production because blob payloads can be large and fsync latency is unpredictable. `asyncio.to_thread()` is the minimal viable fix.
3. **Move SHA-256 hashing out of the write lock (#5)** — Medium. Lock hold time directly correlates with write-path contention. Pre-computing the digest off-loop before acquiring `asyncio.Lock` is a trivial refactor with immediate throughput benefit.
4. **Add heartbeat task health check (#8)** — Medium. A crashed heartbeat means the hub cannot track worker freshness, yet the adapter continues to run. A lightweight health-check wrapper (log + optional restart) prevents silent degradation.
5. **Clarify `NatsCircuitBreaker` threading contract (#12)** — Low / hygiene. Either async-ify it or fence it with docs so a future developer does not accidentally block a thread-pool worker.
