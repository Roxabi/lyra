### Summary
- 10 findings across 44 files: 1 Medium (fire-and-forget supervision gap), 9 Low (stale-read windows, refcount races, missing subscription cleanup, non-transactional writes).
- Zero blocking synchronous calls (no `time.sleep`, `requests`, or unwrapped file I/O).
- Three race-condition patterns in shared mutable state; two un-awaited coroutine sites without task supervision; four resource-leak or cleanup-gap sites.

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/infrastructure/stores/auth_store.py` | 106–112 | Medium | `check()` fires `loop.create_task(self.revoke(identity_key))` as fire-and-forget for expired-grant eviction. Task exceptions are never retrieved or logged; silent failure if DB is locked. | Add a done-callback to the created task that logs exceptions, or accumulate tasks in a weak set and drain them periodically. |
| `src/lyra/infrastructure/stores/agent_store.py` | 181–207 | Low | `upsert()` does `await db.commit()` **before** updating `_agents` cache. During the `await`, another coroutine can call `get()` and read stale cache data. `BotAgentMapStore` avoids this by updating cache first. | Reorder to update `_agents` before `await db.commit()`, or hold an `asyncio.Lock` across DB write + cache update. |
| `src/lyra/infrastructure/stores/identity_alias_store.py` | 184–199 | Low | `unlink()` commits to DB then updates `_cache`/`_reverse`. During the `await`, `resolve_aliases()` (sync, no I/O) reads the still-present stale alias. `link()` updates cache first; `unlink()` does not. | Update caches before `await db.commit()` to match the `link()` pattern, or guard with a lock. |
| `src/lyra/transport/typing_publisher.py` | 27–49 | Low | `_refcount` is incremented in `publish_started()` then `await _publish()` suspends. `publish_ended()` can run mid-flight, decrement to zero, delete the key, and emit `ended` before `started` finishes, producing out-of-order events. | Serialize per-key publish logic with an `asyncio.Lock`, or decide whether to wire-emit before entering the `await` boundary. |
| `src/lyra/infrastructure/turn_writer/writer.py` | 94–103 | Low | `stop()` cancels the consume task but never calls `sub.unsubscribe()` / `sub.drain()` on the JetStream pull subscription. Pending `fetch()` futures or internal NATS client state may leak until process exit. | Add `await self._sub.unsubscribe()` (or `drain()` if supported by nats-py) inside `stop()` before nulling `_task`. |
| `src/lyra/infrastructure/stores/turn_store.py` | 185–212 | Low | `_log_turn()` issues three `execute` calls (insert turn, insert-or-ignore session, update session) then a single `commit`. No explicit `BEGIN` wrapper; an exception mid-batch leaves an open transaction that relies on aiosqlite implicit rollback at close. | Wrap the three statements in an explicit `BEGIN IMMEDIATE` … `COMMIT` block, or `ROLLBACK` in the `except` path. |
| `src/lyra/infrastructure/audit/jetstream_sink.py` | 32 | Low | Docstring states `emit()` is "called via `asyncio.create_task()` in `_spawn()`". The sink provides no supervision; if `emit()` raises, the task exception is never retrieved and is silently swallowed. | Document caller responsibility, or export a supervised `spawn_emit()` helper that adds a done-callback logging `__cause__`. |
| `src/lyra/transport/nats_request_response.py` | 71–93 | Low | `open_inbox()` yields an `InboxStream` with an internal `_messages()` async generator. If the consumer breaks early, `sub.unsubscribe()` runs in the context-manager `finally`, but a pending `sub.next_msg()` inside the generator may not be cleanly cancelled depending on nats-py internals. | Wrap `sub.next_msg()` in its own `try/finally` inside `_messages()`, or document that nats-py handles cancellation on unsubscribe. |
| `src/lyra/infrastructure/stores/sqlite_base.py` | 23–36 | Low | `close_all_sqlite_stores()` catches `Exception` broadly and logs at `debug` level. A failing `close()` during test teardown can mask a connection leak. | Log at `warning` level, or collect exceptions and raise an `ExceptionGroup` after attempting all closes. |
| `src/lyra/nats/nats_bus.py` | 229–268 | Low | `_handle_nats_message()` returns early on JSON/validation errors without explicit `msg.ack()` or `msg.nak()`. nats-py defaults to auto-ack, but if the subscription is ever switched to manual ack, failed messages would redeliver indefinitely. | Add explicit `await msg.ack()` on success and `await msg.nak()` on all error paths, guarded by a `manual_ack` flag if needed. |

### Metrics

| Metric | Value |
|---|---|
| Files scanned | 44 |
| Files with findings | 10 |
| Total findings | 10 |
| Medium severity | 1 |
| Low severity | 9 |
| Blocking sync calls | 0 |
| Un-awaited / fire-and-forget | 2 |
| Race conditions in shared state | 3 |
| Resource leaks / cleanup gaps | 4 |
| async for / generator cleanup | 1 |

### Recommendations (prioritized)

1. **Supervise fire-and-forget tasks** — AuthStore revoked-grant eviction and JetStreamAuditSink `emit()` both spawn detached tasks. Add a lightweight `Task` registry with exception-logging done-callbacks, or document the caller's obligation to attach error handlers. This is the only Medium-severity item and the most likely source of silent runtime degradation.

2. **Unify cache-update ordering in write-through stores** — `AgentStore.upsert()`, `IdentityAliasStore.unlink()`, and `BotAgentMapStore.set_bot_agent()` use inconsistent ordering (DB-then-cache vs cache-then-DB). Pick one convention (cache-then-DB is safer for stale reads) and apply it everywhere, or introduce an `asyncio.Lock` per store to close the window entirely.

3. **Add subscription cleanup in TurnWriter** — `TurnWriter.stop()` should explicitly unsubscribe or drain the JetStream pull subscription. This prevents leaked fetch futures and reduces noise in NATS server-side consumer monitoring during rolling restarts.

4. **Make TurnStore turn logging atomic** — Wrap `_log_turn()`'s three writes in an explicit SQLite transaction (`BEGIN IMMEDIATE` + `COMMIT`/`ROLLBACK`). This guarantees that a session row and its activity timestamp are never persisted without the matching turn row.

5. **Document or remove the ignored `timeout` in `stream_request()`** — `WorkerPoolClient.stream_request()` accepts a `timeout` parameter but immediately discards it. Either implement a total-stream timeout wrapper (e.g. `asyncio.timeout`) or rename the parameter to `_timeout` with a `# noqa` and a code comment explaining the design choice, to prevent caller confusion.
