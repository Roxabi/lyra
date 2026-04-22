# Async Patterns Analysis: Infrastructure + NATS

### Summary

The infrastructure and nats modules contain 89 async functions across 14 files. The codebase demonstrates mature async patterns overall with proper use of aiosqlite, NATS async client, and context managers. However, I identified 8 issues ranging from a race condition in the voice worker registry to potential resource leaks in NATS clients and silent error swallowing in session management.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/nats/voice_health.py` | 108-113 | **Race condition**: `alive_workers()` iterates over `self._workers.values()` while `record_heartbeat()` can mutate the dict concurrently, risking `RuntimeError: dictionary changed size during iteration` | Medium | Protect `_workers` with `asyncio.Lock` or use copy before iteration |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_stt_client.py` | 99-104 | **Resource leak**: `start()` creates NATS subscription but no `stop()` method to unsubscribe; `_hb_sub` never closed | Medium | Add `stop()` method to unsubscribe heartbeat subscription |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_tts_client.py` | 48-53 | **Resource leak**: Same as STT client - no `stop()` method to close heartbeat subscription | Medium | Add `stop()` method to unsubscribe heartbeat subscription |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_image_client.py` | 157-162 | **Resource leak**: Same pattern - no `stop()` method for heartbeat subscription | Medium | Add `stop()` method to unsubscribe heartbeat subscription |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/turn_store.py` | 190-196 | **Silent error swallowing**: `log_turn()` catches `Exception`, logs, and returns without re-raising; callers unaware of failure | Medium | Re-raise exception or return `bool` success indicator |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/turn_store_session.py` | 76, 103, 120 | **Silent error swallowing**: Multiple methods catch `Exception`, log, and return None without re-raising | Medium | Re-raise or document intentional suppression; consider return type change |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/identity_alias_store.py` | 161-174 | **Cache-DB ordering**: `link()` updates cache before DB commit; on failure, cache inconsistent until reconnect | Low | Move cache update after commit or use compensating action on rollback |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/auth_store.py` | 107-109 | **Fire-and-forget task**: `create_task()` without reference; task failures silently swallowed | Low | Add error callback or use `asyncio.TaskGroup` for structured concurrency |

### Metrics

- Async functions: **89** (62 infrastructure + 27 nats)
- Blocking calls in async: **0** (good - uses `asyncio.to_thread` at nats_stt_client.py:153)
- Potential race conditions: **1** (VoiceWorkerRegistry dict iteration)
- Resource leaks: **3** (NATS client heartbeat subscriptions)
- Silent error swallowing: **4** (turn_store and turn_store_session methods)
- Missing `await` keywords: **0** (none detected)

### Recommendations

1. **High Priority - Fix race condition in VoiceWorkerRegistry**
   - Add `asyncio.Lock` to protect `_workers` dict access
   - Or use `self._workers.copy()` before iteration in `alive_workers()`

2. **High Priority - Add lifecycle management to NATS clients**
   - Implement `stop()` methods in `NatsSttClient`, `NatsTtsClient`, `NatsImageClient`
   - Unsubscribe heartbeat subscriptions on shutdown

3. **Medium Priority - Fix silent error handling**
   - In `turn_store.py:log_turn()`: re-raise exception after logging
   - In `turn_store_session.py`: document intentional suppression or change return type to `bool`

4. **Low Priority - Improve fire-and-forget task handling**
   - In `auth_store.py:check()`: add done callback to log any exceptions
   - Consider using `asyncio.TaskGroup` for structured concurrency where applicable

5. **Low Priority - Fix cache-DB ordering**
   - In `identity_alias_store.py:link()`: move cache update after successful commit
   - Or add try/except with cache rollback on DB failure
