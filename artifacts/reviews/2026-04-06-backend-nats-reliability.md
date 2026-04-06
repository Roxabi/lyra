# Backend Review — NATS Reliability — 2026-04-06

Reviewer: Claude Sonnet 4.6 (backend-dev)
Commits: 1bc363e (queue groups) · 453b8fa (startup ordering) · 4ec2417 (stream_error) · ec90576 (publish-only)
Sources read: all modified files + related tests

---

## Overall Verdict

**WARN** — Solid implementation across all four features with one unambiguous
discrepancy between the commit diff and the current tree (readiness responder
missing `inbound_audio_bus`), one non-deterministic eviction in `remember_terminated`,
and one latent ordering concern in `publish_stream_errors` shutdown. No data-loss
bugs. No crashes. Actionable items are localized and low-risk to fix.

---

## Queue Groups

### Design

`src/lyra/nats/queue_groups.py` is the single source of truth.

| Name | Value | Used at |
|------|-------|---------|
| `HUB_INBOUND` | `"hub-inbound"` | `NatsBus` for `InboundMessage` in hub |
| `HUB_INBOUND_AUDIO` | `"hub-inbound-audio"` | `NatsBus` for `InboundAudio` in bootstrap_wiring |
| `adapter_outbound(plat, bid)` | `"adapter-outbound-{platform}-{bot_id}"` | `NatsOutboundListener` |

### Findings

**Correct:** Hub-side `NatsBus` instances receive `queue_group=HUB_INBOUND` /
`HUB_INBOUND_AUDIO` in both `hub_standalone.py` and `bootstrap_wiring.py`. The
`NatsBus._make_handler` passes `queue=self._queue_group` to `nc.subscribe()`
at line 289 of `nats_bus.py`. `NatsOutboundListener.start()` passes
`queue=self._queue_group` at line 91 of `nats_outbound_listener.py`. Adapter
outbound listeners receive `queue_group=adapter_outbound(platform_enum, bot_id)`
at lines 119 and 254 of `adapter_standalone.py`.

**Correct:** Publish-only adapter-side `NatsBus` instances are intentionally
left without a queue group — they never subscribe, so a group name would be
meaningless.

**Correct:** Token validation (`validate_nats_token`, `_validate.py:8`) guards
both `NatsBus.__init__` (line 107) and `NatsOutboundListener.__init__` (line 54)
against injection via group name.

**Multi-hub consideration (not a bug, but a note):** `HUB_INBOUND` and
`HUB_INBOUND_AUDIO` are constants, not per-instance values. All hub instances
in a cluster join the same queue group, which is the correct behavior for
load-balanced dedup. No issue.

---

## Startup Ordering + Readiness Probe

### Mechanism

- **Layer 1 (supervisor):** `lyra_hub.conf` has `priority=100`, `startsecs=10`;
  adapter confs have `priority=200`, `startsecs=5`. Supervisor starts programs
  in priority order with `autostart=true`; however, `autostart=false` on all
  three programs means `supervisorctl start all` (or `start.sh --all`) respects
  priority order at launch. The 10 s `startsecs` on hub gives it a head start
  before adapters are considered "started" and their own `startsecs=5` timer
  begins.

- **Layer 2 (probe):** `wait_for_hub(nc)` called in `adapter_standalone.py` at
  lines 131 and 270, after all `astart()` calls (outbound listeners live) but
  before poll tasks are spawned. Probe loops on `nc.request("lyra.system.ready", …)`
  with 500 ms interval and 30 s total timeout.

- **Responder placement:** `start_readiness_responder()` is called in
  `hub_standalone.py:435` after `hub.inbound_bus.start()` and all dispatcher
  starts, but before `asyncio.create_task(hub.run())`. This is the correct
  placement: the hub will not reply until its subscriptions are live.

### Findings

**BUG (W1) — Readiness responder missing `inbound_audio_bus`:**
The commit diff for 453b8fa shows:
```python
readiness_sub = await start_readiness_responder(
    nc, [hub.inbound_bus, hub.inbound_audio_bus]
)
```
The current file at `hub_standalone.py:435-437` reads:
```python
readiness_sub = await start_readiness_responder(
    nc, [hub.inbound_bus]
)
```
`inbound_audio_bus` was dropped, likely during the Slice 1 refactor (commit
520c33b "unify InboundAudio into InboundMessage"). The audio bus is now managed
via `InboundAudioLegacyHandler` / `bootstrap_wiring.py`, not a bare `NatsBus`
on the hub. The reported `buses` count in the readiness reply is 1 lower than
expected if audio subscriptions were intended to be counted. This is a **cosmetic
inconsistency**, not a functional failure — the probe still returns `status: ready`
and the hub still processes audio messages. The `buses` field in the JSON reply
will reflect one fewer subscription than the full picture. Severity: low.

**Correct:** `wait_for_hub` returns `False` on timeout and the adapter proceeds
with a WARNING (`readiness.py:103-108`). No hard-block on hub unreachability.

**Correct:** Empty `msg.reply` guard in `_handler` (`readiness.py:53`) prevents
a `BadSubject` crash from stray publishes.

**Correct:** `readiness_sub.unsubscribe()` is called in the hub teardown block
at `hub_standalone.py:486`.

**Note — supervisor priority only applies to grouped start:** The `priority=100`
/ `priority=200` ordering is only meaningful when supervisord starts all programs
together (e.g. `supervisorctl start all`). Individual `supervisorctl start
lyra_adapter_telegram` calls ignore priority. This is expected behavior and
mitigated by the NATS probe (Layer 2).

---

## stream_error Crash Recovery

### Flow

```
Hub crash mid-stream
  → NatsChannelProxy.send_streaming() catches exception
    → publishes {"type": "stream_error", "stream_id": X, "reason": "streaming_exception"}
    → drains the remaining iterator
    → discard X from _active_streams (finally block)

Adapter receives stream_error envelope
  → NatsOutboundListener._handle() routes on data.get("type") == "stream_error"
  → _handle_stream_error_impl(listener, data)
    → if queue exists: enqueue poison pill {"event_type": "stream_error", "done": True}
                        call remember_terminated(listener, X)
    → if no queue but known state: tombstone + evict cache/outbound/tasks/queues
    → if unknown stream_id: log + return (no state mutation)

Drain loop in decode_stream_events
  → reads poison pill from queue
  → event_type == "stream_error" → break (terminates generator cleanly)
```

Hub shutdown path:
```
_bootstrap_hub_standalone teardown
  → for proxy in proxies: await proxy.publish_stream_errors("hub_shutdown")
  → atomic swap: _active_streams swapped with empty set before iterating
  → one stream_error per active stream_id
```

### Findings

**Correct:** The `type` field on the stream_error envelope routes via the
top-level `data.get("type")` check in `_handle()`. No risk of it being mistaken
for a chunk (chunks lack `"type"`; stream_error lacks `"seq"`).

**Correct:** Tombstone (`_terminated_streams`) is set before cache pops in the
no-queue branch (`nats_stream_decoder.py:142`), so a late chunk arriving
between tombstone and cache eviction is rejected by `_handle_chunk`'s
`stream_id in self._terminated_streams` guard at `nats_outbound_listener.py:205`.

**Correct:** `try/finally` in `send_streaming` ensures `_active_streams.discard`
always runs (`nats_channel_proxy.py:174-177`).

**Correct:** Atomic swap in `publish_stream_errors` (`nats_channel_proxy.py:187-188`)
eliminates the race window between snapshot and clear.

**BUG (W2) — Non-deterministic eviction in `remember_terminated`:**
`nats_stream_decoder.py:91` calls `listener._terminated_streams.pop()` to
evict one entry when the set reaches `_MAX_TERMINATED_STREAMS = 500`.
`set.pop()` removes an **arbitrary** element — there is no LRU or insertion-order
guarantee. An active tombstone protecting a live stream_id could be accidentally
evicted while stale entries remain. The window is narrow (requires 500 concurrent
streams + a new stream_error arriving at the exact same moment), but the eviction
order is wrong in principle. A simple `collections.OrderedDict` keyed on
`stream_id` with value `None` would give insertion-order eviction at identical
overhead, or a `list`-backed FIFO.

**Correct:** `render_event_codec.py:124` marks `stream_error` as terminal in
`is_terminal()` — belt-and-suspenders on top of the `decode_stream_events` early
break at `nats_stream_decoder.py:77-78`.

**WARN (W3) — `publish_stream_errors` called after tasks cancelled but
before NATS client closed:**
In `hub_standalone.py`, the shutdown sequence is:
1. Cancel and gather all tasks (line 484-485)
2. `readiness_sub.unsubscribe()` (line 486)
3. `teardown_buses` (line 489)
4. `for proxy in proxies: await proxy.publish_stream_errors("hub_shutdown")` (line 491-492)

Tasks include `hub.run()`. When `hub.run()` is cancelled mid-stream, there is a
brief window where `_active_streams` still holds IDs that the in-flight
`send_streaming` coroutine's `finally` block has not yet discard'd (the task was
cancelled before reaching its finally). The atomic swap in `publish_stream_errors`
correctly captures these IDs, so the stream_error envelope is published. The NATS
client is still alive at this point. This is the intended behavior. No bug —
noting it explicitly for clarity.

---

## Publish-Only Mode

### Design

`NatsBus(…, publish_only=True)`:
- `start()` sets `_started = True` then returns immediately — no subscriptions created.
- `stop()` iterates `_subscriptions` (empty) — natural no-op.
- `get()` raises `RuntimeError` immediately.
- `put()` and `register()` unchanged.
- Double-start raises `RuntimeError` via `_started` flag.
- `register()` after `start()` raises `RuntimeError` via `_started` flag.

### Findings

**Correct:** All 4 adapter-side `NatsBus` construction sites use
`publish_only=True` (`adapter_standalone.py:96, 103, 230, 237`). Telegram
and Discord, both inbound and inbound_audio buses.

**Correct:** The `_started` flag properly guards both double-start and
post-start registration — the previous `_subscriptions`-based guard had a
silent bypass on publish-only buses (since `_subscriptions` remains empty
after start).

**Correct:** `subscription_count` returns `len(self._subscriptions)` which is
always 0 for publish-only buses. The readiness responder `sum(b.subscription_count
for b in buses)` correctly sees 0 for adapter-side buses (not passed anyway,
but if it were).

**Note:** `stop()` resets `_started = False` (`nats_bus.py:169`). On publish-only
buses this means the bus can be re-started, which is symmetric with normal buses
and intentional per the docstring.

---

## Edge Cases & Failure Modes

| Scenario | Behavior | Assessment |
|----------|----------|------------|
| NATS goes down while hub subscribing | `nc.subscribe()` raises; `_bootstrap_hub_standalone` propagates uncaught → process exits | Acceptable — supervisor restarts |
| NATS goes down mid-stream (hub) | `nc.publish()` inside `send_streaming` raises inner exception → `stream_error` envelope publish attempted → if that also fails, swallowed with `log.warning` | Correct |
| NATS goes down mid-probe (adapter) | `nc.request()` raises `nats.errors.TimeoutError` or `Exception` → loop continues → eventually times out → adapter proceeds with WARNING | Correct |
| NATS reconnects mid-session | `nats.connect()` handles reconnection transparently (NATS client library); subscriptions are re-created by the client. `_subscriptions` dict holds stale `Subscription` objects. | Potential issue: stale subscriptions after reconnect — not introduced by these commits, pre-existing |
| Queue full on stream_error poison pill | `asyncio.QueueFull` caught at `nats_stream_decoder.py:114-119`; logs WARNING but does NOT add tombstone | Issue: if poison pill is dropped due to full queue, the drain loop never sees the termination signal and will block on `asyncio.wait_for(q.get(), timeout=120s)` until the 120 s chunk timeout fires. Stream eventually terminates but 2 min late. |
| Graceful shutdown with active streams | `publish_stream_errors` covers all active IDs via atomic swap | Correct |
| `inbound_audio_bus` stop not called | `legacy_audio_handler` has no `stop()` in Slice 1; NATS client teardown closes subscriptions implicitly | Documented; acceptable for Slice 1 |
| Multiple hub restarts without adapter restart | Adapter probes via `wait_for_hub()` only once, at startup. If hub crashes and restarts while adapter stays up, adapter does not re-probe. Messages published by adapter are buffered in NATS until hub re-subscribes. At-most-once delivery: messages published during the gap may be dropped | Pre-existing; not introduced by these commits |

---

## Test Coverage

| Area | Files | Assessment |
|------|-------|------------|
| Queue groups (constants + distribution) | `tests/nats/test_nats_bus.py` (73 lines added) | Real nats-server distribution test; validates actual delivery contract, not just internals |
| Queue group on NatsOutboundListener | `tests/adapters/test_nats_outbound_listener.py:415-447` | Verifies `nc.subscribe()` call arg |
| Readiness probe | `tests/nats/test_readiness.py` (304 lines) | Covers: reply, timeout, concurrent start, unexpected error, empty buses |
| stream_error — all paths | `tests/adapters/test_nats_outbound_listener.py:451-600` + `tests/nats/test_nats_channel_proxy.py:473-636` | Covers: poison pill, no-queue cleanup, missing stream_id, unknown stream_id, active_streams tracking, shutdown loop, publish failure |
| publish-only mode | `tests/nats/test_nats_bus.py` (162 lines added) | Covers: start no-op, stop no-op, get raises, put publishes, double-start raises, register-after-start raises, stop does not touch NATS |
| `remember_terminated` eviction at limit | Not directly tested | The `_MAX_TERMINATED_STREAMS` boundary is untested; the non-deterministic pop() issue (W2) has no regression test |
| Queue-full poison pill drop | Not tested | The scenario where the stream queue is full when stream_error arrives (nats_stream_decoder.py:114) is not covered — drain loop blocks for 120 s |

---

## Bugs / Issues Found

Ranked by severity:

**W2 (Medium) — Non-deterministic tombstone eviction in `remember_terminated`**
- File: `src/lyra/adapters/nats_stream_decoder.py:91`
- `set.pop()` removes an arbitrary entry. An active tombstone for a live stream
  could be evicted while stale ones remain, allowing a late chunk to slip through
  the terminated-stream guard.
- Fix: replace `set` with `collections.OrderedDict` (insertion-order FIFO eviction).

**W3 (Low) — Queue-full causes poison pill to be silently dropped, blocking drain for 120 s**
- File: `src/lyra/adapters/nats_stream_decoder.py:114-119`
- When `q.put_nowait()` raises `asyncio.QueueFull`, the poison pill is lost.
  `remember_terminated` is still called (line 120), so future chunks are blocked.
  But the already-running `_drain_stream` task blocks on `asyncio.wait_for(q.get(),
  timeout=120.0)` and only exits after 2 minutes, keeping the stream_id in
  `_stream_tasks` / `_stream_queues` for the duration.
- Fix: after `QueueFull`, consider `q.get_nowait()` + retry, or at minimum
  document that the 120 s timeout is the recovery path.

**W1 (Low/Cosmetic) — `start_readiness_responder` missing audio bus count**
- File: `src/lyra/bootstrap/hub_standalone.py:435-437`
- The `buses` field in the readiness reply is 1 short if audio subscriptions were
  intended to be included. Does not affect probe outcome — `status: ready` still
  returned. Operator tooling reading `buses` will see an undercount.
- Fix: either remove the `buses` field from the reply entirely (it has no
  functional gate), or document that it only counts the primary inbound bus.

---

## Recommended Actions

| Priority | Action | Location |
|----------|--------|----------|
| Medium | Replace `set` with `collections.OrderedDict` for `_terminated_streams` eviction in `remember_terminated` | `src/lyra/adapters/nats_stream_decoder.py:88-92` and the corresponding `_terminated_streams` init in `nats_outbound_listener.py:67` |
| Low | Document or handle the queue-full poison-pill drop scenario; add a test asserting drain exits within bounded time when queue is full at stream_error arrival | `src/lyra/adapters/nats_stream_decoder.py:114-119` |
| Low | Align `hub_standalone.py` readiness call with intended bus list or add comment explaining the intentional omission of `inbound_audio_bus` | `src/lyra/bootstrap/hub_standalone.py:435-437` |
| Low | Add test for `remember_terminated` at `_MAX_TERMINATED_STREAMS` boundary asserting FIFO eviction order once the data structure is fixed | `tests/adapters/test_nats_stream_decoder.py` (new) |
