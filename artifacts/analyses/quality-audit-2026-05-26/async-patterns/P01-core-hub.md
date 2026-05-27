# Async Patterns Audit — P01 core/hub

**Scope:** `src/lyra/core/hub/**/*.py` (31 files)
**Date:** 2026-05-27
**Epic:** #1277 stage-axis refactor
**Prior audit:** 2026-05-18 (hexagonal/mutualization/dead-code) — no regression of those findings observed.

---

### Summary

- `PoolManager` uses `threading.Lock` in a single-threaded asyncio context; safe today because no `await` occurs inside locked sections, but it is a brittle anti-pattern that blocks the event loop if future edits introduce async I/O under the lock.
- `OutboundDispatcher._worker_loop` has a narrow race between `asyncio.create_task` and `self._scope_tasks.add(task)` that can leak scope tasks during `stop()`.
- `dispatch_outbound_item` drains streaming iterators on `Exception` paths but re-raises `BaseException` (e.g. `CancelledError`) without draining, leaving async generators open.

---

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `pipeline/pool_manager.py` | 33 | **Medium** | `threading.Lock()` guards `OrderedDict` mutations in `PoolManager`. All current locked sections are sync-only, but `flush_pool`, `set_debounce_ms`, and `set_cancel_on_new_message` are async methods. A future refactor adding `await` inside a locked section would block the event loop thread. | Replace with `asyncio.Lock()`. Convert `flush_pool` and the setters to `async with self._lock`. |
| `outbound/outbound_dispatcher.py` | 179–195 | **Medium** | Race window: `asyncio.create_task` (L179) happens before `self._scope_tasks.add(task)` (L195). If `stop()` cancels the worker between those lines, the scope task is not tracked and leaks; `stop()` will not gather it. | Add a helper that registers a placeholder in `self._scope_tasks` before `create_task`, then atomically replaces it with the real task. |
| `outbound/_dispatch.py` | 124–138 | **Medium** | `BaseException` handler (L133) re-raises `CancelledError` / `KeyboardInterrupt` immediately without draining streaming/async iterators (`payload`). The `Exception` path (L172–178) does drain, but `BaseException` bypasses it. | Wrap the `await adapter.send_streaming` block in a nested `try/finally` that drains `payload` on all exit paths. |
| `outbound/_dispatch.py` | 76–78, 87–89 | **Low** | Iterator drain on routing-mismatch / circuit-open uses `async for _ in payload: pass`. If `payload` is a tee generator and the event loop is heavily loaded, a cancellation arriving mid-drain will skip the `finally` of the tee. | Acceptable given caller already handles suppression; noted for awareness only. |
| `pool/pool_processor.py` | 42–43 | **Low** | `if pool._inbox.empty(): break` is a classic read-then-act race: a message may arrive after `empty()` returns `True`, causing `submit()` to spawn an extra `process_loop` that immediately exits. Harmless but wastes a task. | Remove the guard; `_debouncer.collect` already blocks or returns; let `submit()` be the single spawner of `process_loop`. |
| `pool/pool_processor_exec.py` | 141 | **Low** | `importlib.import_module("lyra.core.processors")` performs blocking file-system I/O and module execution on the event loop on every command-bearing message. Python caches the result, but the first call stalls all coroutines. | Move the import to module top-level with a lazy `sys.modules` guard, or pre-import at bootstrap. |

---

### Metrics

| Metric | Value |
|--------|-------|
| Files scanned | 31 |
| Async findings | 6 |
| Medium severity | 3 |
| Low severity | 3 |
| Blocking calls in hot path | 1 (`importlib.import_module`) |
| Un-awaited `create_task` (no tracking) | 0 |
| Race conditions | 2 (scope-task creation, inbox-empty guard) |
| Resource leaks (undrained iterators / leaked tasks) | 2 |

---

### Recommendations (prioritized)

1. **Migrate `PoolManager` lock to `asyncio.Lock`** — Eliminates the only threading primitive in the hub. All call sites (`flush_pool`, `set_debounce_ms`, `set_cancel_on_new_message`, `pools` property) must become `async with`.
2. **Close the scope-task creation race in `OutboundDispatcher`** — Use a pre-registration pattern or wrap task creation + tracking in a small synchronous helper so `stop()` always sees the task.
3. **Harden `dispatch_outbound_item` against `BaseException`** — Add a `try/finally` around the `adapter.send_streaming` call that drains `payload` even when `CancelledError` is in flight.
4. **Pre-import `lyra.core.processors` at bootstrap** — Move the `importlib.import_module` out of the per-message hot path. The existing comment about caching is true for subsequent calls, but the first call blocks.
5. **Remove `pool._inbox.empty()` guard from `PoolProcessor.process_loop`** — Rely on `submit()` as the exclusive task spawner. The debouncer handles idleness via its own timeout or the inbox blocks until a message arrives.
