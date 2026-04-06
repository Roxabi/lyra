# Crash Recovery Review — 2026-04-06

Commits reviewed:
- `4ec2417` — stream_error envelope (NatsChannelProxy + NatsOutboundListener)
- `ec90576` — publish-only mode for adapter-side NatsBus (#541)
- `aa57879` — outbound listener protocol (#529)

## Verdict: WARN

Two low-severity issues and one documentation gap. No correctness bugs, no
data loss paths, no shutdown safety holes. All issues are bounded in blast
radius; none block merge.

---

## stream_error Envelope

### How hub signals mid-stream crash

Two distinct paths, both route to the adapter via the outbound NATS subject:

```
Path A — exception during streaming:
  NatsChannelProxy.send_streaming()
    inner try/except catches any Exception
    → publishes {type: "stream_error", stream_id: ..., reason: "streaming_exception"}
    → drains remaining iterator to avoid generator leak
    → finally: _active_streams.discard(stream_id)

Path B — hub shutdown with active streams:
  publish_stream_errors("hub_shutdown") called in teardown
  → atomic swap: stream_ids = _active_streams; _active_streams = set()
  → publishes {type: "stream_error", stream_id, reason: "hub_shutdown"} for each
```

The atomic swap in `publish_stream_errors` (reassign `_active_streams` to a new
empty set before iterating the snapshot) correctly eliminates the race between
the shutdown loop and a concurrent `finally: discard()` firing on another task.

### Adapter reaction

`NatsOutboundListener._handle()` routes `msg_type == "stream_error"` to
`_handle_stream_error()`, which delegates to `handle_stream_error()` in
`nats_stream_decoder.py`. Two sub-paths:

```
Active stream (queue exists):
  → put_nowait({event_type: "stream_error", done: True}) into stream queue
  → remember_terminated(stream_id)
  decode_stream_events() sees event_type == "stream_error" → breaks
  _drain_stream.finally cleans all state + discards tombstone

No queue (race: error arrived before first chunk, or after stream_end):
  → checks known (in _cache / _stream_outbound / _stream_tasks)
  → unknown → log + drop (no tombstone pollution)
  → known → remember_terminated first, then pop all state
```

The tombstone-first ordering in the no-queue/known path is correct: it rejects
any late chunks that arrive between the `remember_terminated` call and the
`_cache.pop`.

### Message loss risk

Scenario: hub crashes, stream_error published, but adapter has already started
`_drain_stream` and the queue is full (`_MAX_QUEUE_SIZE = 256`).

`handle_stream_error` calls `q.put_nowait()` which raises `asyncio.QueueFull`
when the queue is at capacity. The code catches this, logs a warning, and still
calls `remember_terminated`. The drain loop will continue consuming chunks until
it hits the timeout (120 s) or a natural terminal — the stream_error pill is
lost. The tombstone does prevent *new* chunks from entering the queue, but the
drain task has no way to know it should abort early.

**Assessment:** bounded degradation. The stream will drain up to 256 queued
chunks then hang for 120 s before timing out. No crash, no data corruption. But
the user sees a stalled partial response for up to 2 minutes on a full-queue
crash, rather than a clean abort.

---

## Publish-Only Mode

### Does adapter NatsBus avoid wrong subscriptions?

Yes. The fix is complete and correct:

- `NatsBus.__init__` adds `_publish_only: bool` and `_started: bool`
- `start()` sets `_started = True` then early-returns before the
  subscription loop when `_publish_only` is True
- `register()` now guards on `_started` instead of `_subscriptions`, which
  means it correctly rejects post-start registration even when no subscriptions
  were created
- `stop()` sets `_started = False`; the `_subscriptions` loop is a natural
  no-op because publish-only start() never populates `_subscriptions`
- `get()` raises `RuntimeError("publish-only bus never consumes")` — explicit
  and clear

The old guard (`if self._subscriptions`) was neutered for publish-only buses
because `_subscriptions` stays empty — the `_started` flag closes this gap.

### Missed-message risk

None. Adapter-side buses are producers only on `lyra.inbound.*` subjects. The
hub-side `NatsBus` (not publish-only) subscribes to those same subjects. The
`test_publish_only_adapter_bus_roundtrip` integration test verifies the
end-to-end path with a real NATS server.

### Regression guards

Double-start and post-start register are both tested explicitly in
`TestPublishOnlyMode` (`test_publish_only_double_start_raises`,
`test_publish_only_register_after_start_raises`). The `TestPublishOnlyInvariants`
mock-based test catches future `stop()` refactors that bypass the
`_subscriptions` invariant.

---

## Outbound Listener Protocol

### Definition

`src/lyra/adapters/outbound_listener.py` defines:

```python
class OutboundListener(Protocol):
    def cache_inbound(self, msg: "InboundMessage | InboundAudio") -> None: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

Structural (not `@runtime_checkable`), consistent with `ChannelAdapter` in
`core/hub/hub_protocol.py`. Surface matches exactly what adapter code calls.

### Coverage

| Call site | Method called | Covered by protocol |
|-----------|--------------|---------------------|
| `telegram_inbound.py`, `discord_inbound.py`, `discord_audio.py` | `cache_inbound()` | Yes |
| `telegram.py:235-236`, `discord.py:161-162` | `start()` | Yes |
| `telegram.py:240-241`, `discord.py:168-169` | `stop()` | Yes |

All three adapter-side call sites are covered. Internal methods
(`_handle_*`, `_drain_stream`, `_reap_stale`) correctly remain outside the
protocol.

### Consistency

`NatsOutboundListener` satisfies the protocol structurally without inheritance.
Both `telegram.py` and `discord.py` already use `OutboundListener | None` in
their `TYPE_CHECKING` blocks (verified against live source). Bootstrap
(`adapter_standalone.py`) still assigns a concrete `NatsOutboundListener`
instance — no change needed there.

---

## Shutdown Safety

### NatsChannelProxy / stream_error on shutdown

Both bootstrap paths call `publish_stream_errors("hub_shutdown")` after
cancelling tasks and before closing NATS:

- `hub_standalone.py:491-492`: `for proxy in proxies: await proxy.publish_stream_errors(...)`
- `bootstrap_lifecycle.py:106-107`: `for proxy in proxies or []: await proxy.publish_stream_errors(...)`

The NATS connection is still open when `publish_stream_errors` runs (connection
close happens after the teardown block in `hub_standalone.py`), so the publish
can succeed.

`publish_stream_errors` swallows individual publish failures (`except Exception:
log.warning`) — shutdown does not propagate NATS errors, which is correct.

### NatsBus stop() on publish-only

`stop()` sets `_started = False` and clears `_subscriptions` (empty for
publish-only). No NATS calls are made — verified by `test_stop_does_not_touch_nats`.
The NATS connection lifetime is owned by the caller, not `NatsBus`.

### Reaper task

`NatsOutboundListener.stop()` cancels `_reaper_task` and awaits cancellation
before unsubscribing. Correct ordering.

### Stream tasks on stop()

`NatsOutboundListener.stop()` cancels all in-flight `_stream_tasks` and clears
both `_stream_tasks` and `_stream_queues`. Tasks are cancelled but not awaited —
`asyncio.CancelledError` may surface in the drain loop after `stop()` returns.
This is a pre-existing pattern, not introduced by these commits.

---

## Test Coverage

| Scenario | Test |
|----------|------|
| stream_error enqueues poison pill (active stream) | `test_stream_error_enqueues_poison_pill` |
| stream_error with no stream_id is no-op | `test_stream_error_missing_stream_id_is_noop` |
| stream_error with no queue cleans cache | `test_stream_error_no_queue_cleans_cache` |
| stream_error with unknown stream_id is no-op | `test_stream_error_unknown_stream_id_is_noop` |
| `is_terminal` returns True for stream_error | `test_is_terminal_stream_error` |
| active_streams tracked during streaming | `test_active_streams_tracked_during_streaming` |
| publish_stream_errors publishes per active stream | `test_publish_stream_errors_publishes_for_active` |
| publish_stream_errors swallows NATS failure | `test_publish_stream_errors_swallows_nats_failure` |
| publish_stream_errors no-op when no active streams | `test_publish_stream_errors_noop_when_no_active_streams` |
| send_streaming exception publishes stream_error | `test_send_streaming_exception_publishes_stream_error` |
| shutdown loop pattern | `test_shutdown_loop_calls_publish_stream_errors_on_each_proxy` |
| publish-only start is no-op | `test_publish_only_start_noop` |
| publish-only stop is no-op | `test_publish_only_stop_noop` |
| publish-only get raises | `test_publish_only_get_raises` |
| publish-only put still publishes | `test_publish_only_put_still_publishes` |
| publish-only double-start raises | `test_publish_only_double_start_raises` |
| publish-only register after start raises | `test_publish_only_register_after_start_raises` |
| stop does not touch NATS (mock) | `test_stop_does_not_touch_nats` |
| adapter/hub roundtrip (integration) | `test_publish_only_adapter_bus_roundtrip` |

Missing: no test for the `QueueFull` path in `handle_stream_error` (the
poison-pill is silently dropped when the queue is full). The tombstone is still
written, but the drain loop is not aborted — the test gap means the stall
behaviour is not documented as intentional.

---

## Issues

| # | File:line | Severity | Description |
|---|-----------|----------|-------------|
| I-1 | `src/lyra/adapters/nats_stream_decoder.py:112-120` | LOW | `handle_stream_error`: when `q.put_nowait` raises `QueueFull`, the poison pill is lost. The tombstone is recorded, so new chunks are rejected, but the active drain loop has no signal to abort. The stream stalls until the 120 s chunk timeout fires. No test documents this degraded behaviour. |
| I-2 | `src/lyra/adapters/nats_stream_decoder.py:89-92` | INFO | `remember_terminated` uses `set.pop()` for eviction. `set.pop()` removes an arbitrary element (CPython happens to pop a deterministic element but this is not guaranteed). A comment says "arbitrary entry is evicted" which is accurate, but if ordered eviction (LRU or insertion-order) ever matters, this will silently do the wrong thing. No functional issue today. |
| I-3 | `src/lyra/adapters/nats_stream_decoder.py:14` / import split | INFO | `decode_stream_events` and `handle_stream_error` are imported in two separate `from lyra.adapters.nats_stream_decoder import (...)` blocks in `nats_outbound_listener.py`. Both are from the same module; they can be merged into one import block. Cosmetic only — ruff does not flag it because each block is syntactically valid. |

---

## Actions

| # | Action | Priority |
|---|--------|----------|
| A-1 | Add test for `QueueFull` path in `handle_stream_error` — assert that when the queue is full the tombstone is still written and no exception is raised, and add a comment in the code stating the 120 s timeout is the recovery path for this case. | SHOULD |
| A-2 | Merge the two `nats_outbound_listener.py` import blocks for `nats_stream_decoder` into one. | NICE-TO-HAVE |
| A-3 | (Pre-existing, not introduced here) `NatsOutboundListener.stop()` cancels stream tasks but does not await them — consider collecting them with `asyncio.gather(*tasks, return_exceptions=True)` to ensure clean cancellation before clearing the dicts. | NICE-TO-HAVE |
