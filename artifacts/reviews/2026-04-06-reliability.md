# Reliability Review — 2026-04-06

## Verdict: WARN

Two confirmed bugs (one moderate, one low-severity) plus two test coverage gaps.
No data-loss risk at current single-hub scale. Issues compound under horizontal scaling.

---

## Queue Groups

### What was added

`src/lyra/nats/queue_groups.py` defines canonical names:

| Constant / helper | Value | Used on |
|---|---|---|
| `HUB_INBOUND` | `"hub-inbound"` | Hub's `NatsBus[InboundMessage]` |
| `HUB_INBOUND_AUDIO` | `"hub-inbound-audio"` | Hub's `NatsBus[InboundAudio]` |
| `adapter_outbound(platform, bot_id)` | `"adapter-outbound-{platform}-{bot_id}"` | `NatsOutboundListener` per bot |

Both `NatsBus` and `NatsOutboundListener` forward the group name to `nc.subscribe(queue=...)`.

### Consistency across adapters

`adapter_outbound` is called identically for both Telegram
(`src/lyra/bootstrap/adapter_standalone.py:119`) and Discord
(`src/lyra/bootstrap/adapter_standalone.py:253`). The function produces a
deterministic name from `(platform.value, bot_id)` — any two adapter processes
for the same bot join the same NATS queue group automatically. Consistent.

### Behavior with multiple hub instances

Hub-side queue groups are correct in isolation: `HUB_INBOUND` and
`HUB_INBOUND_AUDIO` are single fixed strings. Under NATS queue group semantics,
all hub instances that subscribe to the same subject with the same group name
form a single load-balanced pool — each inbound message is delivered to exactly
one hub. This is the desired behavior for horizontal scaling.

**BUG (moderate) — `hub_standalone.py` passes only `inbound_bus` to
`start_readiness_responder`**

`hub_standalone.py:435-436`:
```python
readiness_sub = await start_readiness_responder(
    nc, [hub.inbound_bus]
)
```

The `inbound_audio_bus` is never passed. The `buses` field in readiness replies
will therefore always under-count by the number of audio subscriptions. An
adapter waiting for `buses >= expected_threshold` (if such logic is ever added)
would stall. Currently the probe only checks for `status == "ready"` — so this
is not yet a runtime failure, but it is an incorrect invariant in the payload.

`bootstrap_wiring.py` correctly sets `queue_group=HUB_INBOUND_AUDIO` on the
audio bus (lines 73-76, 190-193), confirming the intent was to track it.
`unified.py` also sets `HUB_INBOUND_AUDIO` on its audio bus. Neither feeds the
bus into a readiness responder call — `hub_standalone.py` is the only file that
calls `start_readiness_responder`, and it is missing the audio bus.

---

## Startup Ordering + Readiness Probe

### How the probe works

1. Hub boots, subscribes all buses and dispatchers, then calls
   `start_readiness_responder(nc, buses)` which subscribes a reply handler on
   `lyra.system.ready`.
2. Adapter process boots, subscribes `NatsOutboundListener` and inbound buses,
   then calls `wait_for_hub(nc)` before starting the polling loop.
3. `wait_for_hub` sends `nc.request("lyra.system.ready", b"", timeout=per_call)`
   in a retry loop. `per_call = min(0.5, remaining)`. On `TimeoutError` or
   `NoRespondersError` it sleeps 0.5 s and retries.
4. On success, polling tasks are created and the adapter enters its run loop.

### Timeout

- Per-attempt timeout: `min(PROBE_INTERVAL_S=0.5, remaining)` — effectively 0.5 s
  per request call.
- Total timeout: `PROBE_TIMEOUT_S = 30.0` seconds.
- On expiry: logs `WARNING`, returns `False`, adapter continues anyway
  (graceful degradation). Correct — avoids hard deadlock if hub is slow.

### Adapter blocked from publishing until hub is ready

`wait_for_hub` is called **after** `adapter.astart()` and outbound listener
`.start()` but **before** the Telegram/Discord polling tasks are created (lines
131, 140-146 for Telegram; lines 267, 276-282 for Discord). Polling — the
entrypoint for user-facing traffic — is correctly gated behind hub readiness.

Outbound listener (hub → adapter) is subscribed before the probe. This is safe:
NATS will buffer messages in the subscription's internal queue; no messages are
lost if the hub sends an outbound during the probe window.

Inbound buses (adapter → hub) are `publish_only=True` on the adapter side. They
are started before the probe. The first user message could theoretically arrive
and be published to NATS before the hub is ready. The hub's own subscriptions
exist before it starts the responder — the race window is only between hub
subscription and responder registration (microseconds in single-process boot).
This is acceptable.

---

## Race Conditions

### Identified races

| # | Race | Severity | Protected? |
|---|---|---|---|
| R1 | Adapter publishes inbound before hub's NATS subscription is active | Low | Hub subscribes buses before registering responder — window is sub-millisecond on same NATS server |
| R2 | Hub crashes after responder but before dispatcher start | Low | Supervisor `autorestart=true`; adapter probe would retry on reconnect |
| R3 | Two adapter processes for same bot restart simultaneously | Safe | NATS queue group ensures exactly one delivery per message regardless |
| R4 | `wait_for_hub` returns `False` (timeout) and polling starts without hub | Low | Hub's NATS subscriptions remain live; messages buffer in NATS; hub processes them when it recovers |

### Supervisor priority ordering

`lyra_hub.conf` sets `priority=100`, both adapters `priority=200`. Supervisord
starts lower-priority number first. Hub gets a head start, but this is a
best-effort hint — supervisord does not wait for `startsecs` of one process to
complete before starting the next priority group. The `wait_for_hub` probe
exists precisely to handle this gap. Ordering is correct and well-motivated.

`startsecs=10` for hub (up from 5) gives supervisor 10 seconds to declare the
hub "running" before counting as a failed start. Combined with the 30-second
probe, adapters have a realistic window.

**BUG (low) — double sleep on `NoRespondersError` path**

In `wait_for_hub` (lines 89-101):

```python
per_call = min(PROBE_INTERVAL_S, remaining)
try:
    await nc.request(..., timeout=per_call)
    return True
except nats.errors.TimeoutError:
    pass
except nats.errors.NoRespondersError:
    pass
except Exception:
    log.exception(...)

await asyncio.sleep(PROBE_INTERVAL_S)   # <-- always runs
```

`nc.request()` with `timeout=0.5` on `TimeoutError` consumes ~0.5 s.  
On `NoRespondersError`, NATS responds immediately (no waiting). The `asyncio.sleep(0.5)`
then adds another 0.5 s of delay after the instant failure, doubling the
per-attempt cycle to ~1.0 s instead of the intended ~0.5 s for the
`NoRespondersError` case. This is not a correctness issue — the 30-second budget
is still consumed correctly — but the probe takes up to 2x longer during the
period before the hub subscribes, and the comment says "Brief sleep ... so we
don't hammer NATS on NoRespondersError" which confirms the intent. The fix would
be to only sleep on `NoRespondersError`, not after every attempt (or to account
for time already spent inside the request call).

---

## Test Coverage

### Covered

- `NatsOutboundListener` subscribes with queue group (unit, mock nc)
- `NatsBus` default queue group empty string (unit)
- `NatsBus` queue group distributes messages across two subscribers (integration, `@requires_nats_server`)
- `start_readiness_responder` reply shape, bus count sum, empty buses (integration)
- `wait_for_hub` returns `True` on success, `False` on timeout, logs warning (integration)
- Concurrent startup race — responder starts 200ms after probe begins (integration)
- Unexpected error path — `BrokenNats` fake (unit, no NATS needed)

### Not covered

| Gap | Risk | Recommended test |
|---|---|---|
| `validate_nats_token` with invalid characters | Low — constructor-time crash is loud; no silent corruption | Unit test in `test_nats_bus.py` or a dedicated `test_validate.py`: verify `ValueError` on e.g. `"hub inbound"` (space), `"hub>inbound"` (wildcard), empty string without `allow_empty` |
| `adapter_outbound` name format | Low | Unit test in a `test_queue_groups.py`: assert `adapter_outbound(Platform.TELEGRAM, "bot1") == "adapter-outbound-telegram-bot1"` |
| `start_readiness_responder` with stray publish (no reply subject) | Low — handler early-returns, but untested | Unit or integration: publish (not request) to `READINESS_SUBJECT`; assert no exception, no reply |

---

## Issues (file:line, severity)

| ID | File | Line | Severity | Description |
|---|---|---|---|---|
| I1 | `src/lyra/bootstrap/hub_standalone.py` | 435-436 | MODERATE | `start_readiness_responder` called with only `[hub.inbound_bus]`; `inbound_audio_bus` missing from readiness payload |
| I2 | `src/lyra/nats/readiness.py` | 100-101 | LOW | `asyncio.sleep(PROBE_INTERVAL_S)` runs unconditionally; on `NoRespondersError` (instant) this doubles the per-attempt wait to ~1.0 s instead of intended ~0.5 s |
| I3 | `src/lyra/nats/_validate.py` | 6 | LOW | `_NATS_IDENT` regex uses `[A-Za-z0-9_.\-]+` — the `.` inside a character class matches literal dot, which is correct. However the hyphen `\-` at the end is correctly escaped. Note: regex does not reject multi-segment NATS subjects (dots allowed), which is intentional per the docstring, but a caller passing a full subject `"lyra.inbound"` to `validate_nats_token(kind="queue_group")` would pass silently. Currently no such misuse exists; worth documenting. |
| I4 | Tests | — | LOW | No tests for `validate_nats_token` edge cases or `adapter_outbound` name format (see coverage gaps above) |

---

## Actions

| Priority | Action | Owner |
|---|---|---|
| P1 | Fix I1: pass `hub.inbound_audio_bus` (or the constructed `inbound_audio_bus`) as second element in `start_readiness_responder` call in `hub_standalone.py:436` | backend-dev |
| P2 | Fix I2: track time spent inside `nc.request()` and deduct from sleep, or only sleep on `NoRespondersError` and not after `TimeoutError` | backend-dev |
| P3 | Add unit tests for `validate_nats_token` (invalid input) and `adapter_outbound` name format | tester |
| P4 | Add unit test for stray-publish path in `start_readiness_responder` (no reply subject) | tester |
