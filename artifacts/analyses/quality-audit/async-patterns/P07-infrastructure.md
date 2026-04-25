### Summary
The P7 infrastructure and NATS layer demonstrates generally sound async patterns with proper resource lifecycle management (WAL checkpoint tasks, heartbeat loops, NATS connection drain/close). However, there are notable concerns around fire-and-forget task patterns in AuthStore, use of threading.Lock instead of asyncio.Lock in async contexts, and potential race conditions in write-through caches accessed from both sync and async methods.

### Findings
| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| auth_store.py | 109 | Fire-and-forget task pattern: `loop.create_task(self.revoke(...))` without storing reference. Task cannot be cancelled during shutdown. | Medium | Store task reference and cancel in `close()`, or use `asyncio.shield()` with proper cleanup |
| circuit_breaker.py | 26 | Uses `threading.Lock` in async context. While functional for simple ops, not idiomatic for asyncio code. | Low | Consider `asyncio.Lock` for pure async consumers, or document thread-safety guarantee |
| sqlite_base.py | 93 | `asyncio.get_running_loop().create_task()` deprecated pattern vs `asyncio.create_task()`. | Low | Use `asyncio.create_task()` for consistency |
| auth_store.py | 93-114 | Sync `check()` method modifies `_cache` dict and may trigger async `revoke()` task. Potential race if called from multiple contexts. | Medium | Document thread-safety guarantees or add `asyncio.Lock` for cache mutations |
| identity_alias_store.py | 161-165 | `_cache` modified before DB write in `link()`, comment claims it eliminates stale-read window but if DB write fails, cache is inconsistent until next `connect()`. | Low | Consider atomic transaction pattern or document self-healing behavior |
| nats_channel_proxy.py | 69,207-208 | `_active_streams` set modified without synchronization. Atomic swap in `publish_stream_errors()` is good but concurrent `discard()` during exception handling could race. | Low | Document that `_active_streams` is only modified from single async context, or add synchronization |
| worker_registry.py | 104-106 | `_prune()` reassigns `self._workers` dict while `record_heartbeat()` may be modifying it concurrently from heartbeat callbacks. | Medium | Use `asyncio.Lock` for registry mutations or make operations atomic |
| credential_store.py | 71-91 | `_generate_atomic()` uses blocking `os.open`, `os.write`, `os.fsync` - could block event loop on slow filesystem. | Low | Consider `aiofiles` or run in executor for key generation |
| nats_tts_client.py | 50-74 | `_parse_tts_timeout()` reads `os.environ` at call time - fine, but if called frequently could be cached at init. | Info | Cache env var reads at instance init time |
| nats_stt_client.py | 47-74 | Same as TTS - env var read pattern. | Info | Cache env var reads at instance init time |

### Metrics
- Files analyzed: 28
- Race conditions: 3
- Blocking calls: 2
- Resource leaks: 1
- Missing awaits: 0

### Recommendations
1. **AuthStore fire-and-forget task**: Replace the `create_task(self.revoke(...))` pattern with a tracked task that gets cancelled in `close()`. Store the task in a `_pending_revokes` set and cancel/await all during shutdown. This prevents resource leaks and ensures clean teardown.

2. **WorkerRegistry synchronization**: Add an `asyncio.Lock` to protect `_workers` dict modifications. The `_prune()` and `record_heartbeat()` methods are called from async contexts (heartbeat callbacks) and could race. Use `async with self._lock:` around dict mutations.

3. **CircuitBreaker threading vs async**: Either migrate to `asyncio.Lock` (breaking change if used from threads) or document that `NatsCircuitBreaker` is safe for concurrent use from both threads and async contexts due to GIL protection of simple Python operations. The current implementation is functional but the pattern could confuse future maintainers.
