# Async Patterns Analysis: Core Hub

### Summary
The core/hub area contains approximately 35 async functions across 29 files. The codebase demonstrates good async practices overall (using `asyncio.to_thread` for blocking I/O, proper task tracking with `_memory_tasks`, timeout guards). However, I found several areas of concern including potential race conditions in pool eviction, unbounded subscriber growth in the event bus, and incomplete cleanup on task cancellation during STT transcription.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| pool_manager.py | 70-77 | Race condition: iterating over `self.pools.items()` while calling `pop()` during eviction. The throttle check is not atomic with the mutation. | Medium | Consider using a lock or snapshotting the keys before mutation. |
| event_bus.py | 38-56 | No unsubscribe mechanism. Subscribers are added via `subscribe()` but never removed, leading to potential memory leaks for long-running processes with dynamic subscribers. | Medium | Add `unsubscribe(queue)` method and call it when consumers shut down. |
| middleware_stt.py | 126-161 | Resource leak potential: temp file created via `asyncio.to_thread()` before try block. If task is cancelled during the `to_thread` call after file creation, the file may not be cleaned up. | Low | Create the temp file inside the try block, or use a context manager pattern. |
| hub_shutdown.py | 86-99 | Incomplete task cleanup: `notify_shutdown_inflight()` uses `asyncio.wait_for` with timeout, but on timeout the tasks are NOT cancelled - they continue running in background. | Medium | Add explicit task cancellation after timeout: `for t in tasks: t.cancel()`. |
| outbound_dispatcher.py | 196-199 | Race condition: `_reap_scope_locks()` and `_reap_circuit_notify_ts()` are called while iterating over `_scope_tasks` in `_worker_loop`. | Low | Move reap calls outside the task iteration or use a copy. |
| hub_shutdown.py | 101-112 | Potential task-store conflict: `shutdown()` closes `_memory`, `_turn_store`, `_message_index` after awaiting `_memory_tasks`, but new tasks could be added during this sequence. | Low | Clear `_memory_tasks` before closing stores, or add a shutdown flag. |
| _dispatch.py | 135-164 | Exception tracking: `_last_exc` is assigned on each retry iteration. If the final attempt succeeds but callback fails, `_last_exc` may not reflect the actual failure. | Low | Consider separating retry exception tracking from post-dispatch exception tracking. |
| hub.py | 207-211 | Message loss on pipeline exception: exceptions are logged but `task_done()` is called, acknowledging the message as processed even though it failed. | Medium | Consider re-queuing failed messages or implementing a dead-letter queue. |
| outbound_router.py | 183-184 | Sequential dispatch without error isolation: `dispatch_response()` calls `dispatch_audio()` after `dispatch_response()`. If audio dispatch fails, the response was already sent but no error handling. | Low | Add try/except around secondary dispatches or make them fire-and-forget with task tracking. |
| TtsDispatch / outbound_tts.py | 77-89 | Fire-and-forget TTS tasks: `dispatch_tts_for_response()` creates background TTS tasks. If the caller's context is cancelled, the TTS task continues independently. | Low | Consider linking TTS task to caller's task group or cancellation scope. |

### Metrics

- Async functions: 35+
- Blocking calls in async: 1 (mitigated with `asyncio.to_thread`)
- Potential race conditions: 3
- Resource leak risks: 2
- Missing await keywords: 0
- Deadlock potential: 0 (no circular lock dependencies identified)

### Recommendations

1. **High Priority**: Add `unsubscribe()` method to `PipelineEventBus` to prevent memory leaks in long-running processes with dynamic event consumers.

2. **High Priority**: Implement explicit task cancellation in `notify_shutdown_inflight()` after timeout to prevent orphaned notification tasks.

3. **Medium Priority**: Add synchronization (lock or atomic snapshot) to pool eviction in `PoolManager._evict_stale_pools()` to prevent race conditions during concurrent pool creation/eviction.

4. **Medium Priority**: Consider implementing a dead-letter queue or retry mechanism for failed pipeline processing in `Hub.run()` to prevent silent message loss.

5. **Low Priority**: Refactor `middleware_stt.py` temp file creation to ensure cleanup on all cancellation paths - consider using an async context manager or moving file creation inside the try block.

6. **Low Priority**: Add shutdown state flag to `Hub` to prevent new task creation during shutdown sequence, ensuring clean teardown order.
