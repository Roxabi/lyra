# Async Patterns Audit — P02 Inbound (`src/lyra/inbound/**/*.py`)

Date: 2026-05-27 | Scope: stage-axis inbound pipeline (parse → route → session → dispatch)
Prior audit: 2026-05-18 (hexagonal / duplication / dead-code) — no regression observed on those axes.

---

### Summary

- **No blocking calls** in the inbound directory: all I/O is async-native (aiosqlite, NATS JetStream publish, discord.py/aiogram Gateway events).
- **No un-awaited coroutines** inside P02: every `async def` in the pipeline is awaited by its caller.
- **Two race-condition patterns** in shared mutable state (`thread_sessions_cache` and `owned_threads`) and **one resource-leak vector** in a dependency directly referenced by `DispatchCtx` (`TypingTaskManager`).

---

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `session_builder.py` | 93–98 | **Medium** | `_build_turnstore_path` catches only `sqlite3.Error` + `RuntimeError`. If `TurnStore.get_last_session` raises an unanticipated exception (e.g. `TypeError`, `ValueError`, `CancelledError`), it propagates uncaught through `InboundPipeline.run` and out of the adapter handler. Telegram's `handle_message` has no top-level boundary, so a crash triggers Telegram webhook retry storms. | Add a broad `Exception` catch in `SessionBuilder` that logs and falls back to `_prior_session_id = None`, or wrap `_pipeline.run()` in the adapter with a boundary that always returns normally. |
| `session_builder.py` | 151–169 | **Low** | `thread_sessions_cache` is a mutable `dict` shared by reference across concurrent inbound messages. The sequence `_cache.get(...) → await th.get_session(...) → _cache[...] = ...` is not atomic across the `await` boundary. Two concurrent tasks for the same `thread_id` can both miss the cache, both query `ThreadStore`, and both write back. | Add `asyncio.Lock` around cache read-miss-write and eviction paths, or document the race as acceptable (soft cache, CPython dict ops are GIL-atomic individually). |
| `session_builder.py` | 196–211 | **Low** | The `_thread_update_fn` closure (captured in `InboundMessage.session_update_fn`) mutates the same shared `_cache` dict later in the turn lifecycle. If `_session_persisted` is reset and multiple pools share the same adapter `_thread_sessions`, concurrent invocations could race on `len(_cache) >= 500 → del _cache[_oldest]`. | Same as above: add `asyncio.Lock` around cache mutation, or accept the soft-bound semantics with a comment. |
| `context.py` | 54–55 | **Low** | `RouterCtx.owned_threads` is a mutable `set[int]` shared by reference. `_discord_pre_route_hook` does `await adapter._thread_store.is_owned(...)` then `ctx.router.owned_threads.add(...)`. Between the `await` and the `add`, another task could have added the same thread (set semantics make this harmless, but the pattern is a race). | Document as an eventual-consistency warm-up contract (already partially documented) or wrap mutation in an `asyncio.Lock` if strict ordering is ever required. |
| `adapters/shared/_shared.py` | 173–187 | **Medium** | `TypingTaskManager` (referenced via `DispatchCtx.typing`) stores `asyncio.Task` objects in `self._tasks` but never removes tasks that complete naturally. Only `cancel()` and `cancel_all()` pop entries. In long-running adapters with many unique channel IDs, done tasks accumulate as a slow memory leak. | Add `task.add_done_callback(lambda t, tgt=target: self._tasks.pop(tgt, None))` in `start()`, or reap done tasks in `cancel_all()`. |
| `infrastructure/stores/sqlite_base.py` | 83–88 | **Low** | `SqliteStore._open_db` creates a background `_checkpoint_task` with `create_task()`. Exceptions from `_run_periodic_checkpoint` (other than `CancelledError`) are never retrieved, which causes Python asyncio to log an *"Task exception was never retrieved"* warning on task finalisation. | Attach a `done_callback` that calls `task.exception()` and logs any unexpected failure. |

> **Note on out-of-partition files:** `adapters/shared/_shared.py` and `infrastructure/stores/sqlite_base.py` are outside `src/lyra/inbound/` but are directly wired into the inbound pipeline via `DispatchCtx.typing` and `SessionCtx.turn_store` / `thread_store`. The findings above are included because they affect the async correctness of the pipeline.

---

### Metrics

| Metric | Value |
|--------|-------|
| Files scanned | 9 |
| Async functions in partition | 5 (`pipeline.run`, `dispatcher.dispatch`, `session_builder.build`, `_build_turnstore_path`, `_build_thread_path`) |
| `await` expressions in partition | 15 |
| Blocking calls (`time.sleep`, `requests`, sync file I/O) | 0 |
| Un-awaited coroutines / fire-and-forget (partition) | 0 |
| Race conditions in shared mutable state | 2 (`thread_sessions_cache`, `owned_threads`) |
| Resource leaks (unretrieved tasks / unclosed subscriptions) | 1 in partition-adjacent code (`TypingTaskManager`) |
| `async for` / generator cleanup issues | 0 |

---

### Recommendations (prioritized)

1. **Harden `SessionBuilder` exception boundaries** — Broaden the two `except (sqlite3.Error, RuntimeError)` blocks to `except Exception` with graceful fallback, or add a top-level try/except in `telegram_inbound.handle_message` that always returns normally to the aiogram handler. Prevents retry storms from unanticipated store errors.

2. **Fix `TypingTaskManager` task leak** — Add a done-callback in `TypingTaskManager.start()` that removes completed tasks from `_tasks` so the dict does not grow without bound. This is the highest-impact concrete leak found.

3. **Protect `thread_sessions_cache` with `asyncio.Lock`** — Wrap the read-miss-write and eviction sequences in `SessionBuilder._build_thread_path` and the `_thread_update_fn` closure with a lock keyed by the cache instance. Low severity because the cache is soft-bound, but it removes a latent race under high concurrency or non-CPython runtimes.

4. **Add exception-retrieval callback to `SqliteStore` checkpoint task** — Prevents asyncio unretrieved-task warnings and surfaces any unexpected WAL-checkpoint failures that could indicate disk pressure or DB corruption.

5. **Document the `owned_threads` race contract explicitly** — The frozen-container/mutable-contents contract is already documented in `context.py`, but add a note that `owned_threads.add()` after an `await` is an intentional eventual-consistency warm-up and that `Router.decide` may observe a stale negative for one turn before the hot set is updated.
