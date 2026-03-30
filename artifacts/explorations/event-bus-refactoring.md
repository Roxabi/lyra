# Exploration: Event Bus Refactoring

> **Type:** Architectural Exploration
> **Status:** Research only — no code changes
> **Date:** 2026-03-30
> **Author:** Architect (Claude Sonnet 4.6)

---

## 1. What the current system actually does

Before designing an alternative, we need to understand the current system precisely. The mental model of "sequential if/return chain" is accurate but undersells how much is actually happening.

### 1.1 The full current flow

```
Adapter (Telegram / Discord)
  │ normalize(raw_message) → InboundMessage | InboundAudio
  │
  ├── text → InboundBus.put(platform, msg)         # bounded per-platform queue
  └── audio → InboundAudioBus.put(platform, audio) # separate bounded queue

InboundBus (feeder tasks per platform → staging queue)
  │  Each platform has its own asyncio.Queue(maxsize=100).
  │  One feeder task per platform drains into a shared staging queue (maxsize=500).
  │  Backpressure: put_nowait raises QueueFull → adapter drops + logs.
  │
Hub.run()  ← single asyncio coroutine consuming staging queue
  │  while True: msg = await inbound_bus.get(); pipeline.process(msg)
  │
  └── MessagePipeline.process(msg)
        1. _validate_platform(msg)          → DROP on unknown platform
        2. _check_rate_limit(msg, key)      → DROP on rate exceeded
        3. _resolve_binding(msg, key)       → DROP if no (platform, bot_id, scope_id) binding
        4. _lookup_agent(binding, key)      → DROP if agent not in registry
        5. get_or_create_pool(...)          → creates Pool if new conversation scope
        6. CommandParser.parse(msg.text)    → attach CommandContext if slash command
        7. router.prepare(msg)              → URL rewriting (#99)
        8. router.is_command(msg)?
           ├─ YES → _dispatch_command()    → router.dispatch() → PipelineResult(COMMAND_HANDLED)
           └─ NO  → _submit_to_pool()
                      ├── adapter registered check
                      ├── circuit_breaker_drop()   → fast-fail if Anthropic CB open
                      ├── _resolve_context()        → session resume (3 paths)
                      └── PipelineResult(SUBMIT_TO_POOL, pool=pool)
  │
Hub._dispatch_pipeline_result(msg, result)
  ├── COMMAND_HANDLED → dispatch_response(msg, result.response)
  └── SUBMIT_TO_POOL  → pool.submit(msg)
        └── Pool._inbox.put_nowait(msg)
              └── pool._current_task = create_task(PoolProcessor.process_loop())
                    └── debounce → cancel-in-flight → _guarded_process_one()
                          └── agent.process(msg, pool) → Response | AsyncIterator[RenderEvent]
                                └── dispatch_response() / dispatch_streaming()
                                      └── OutboundDispatcher.enqueue*()
                                            └── _worker_loop() → per-scope lock → adapter.send*()

AudioPipeline.run()  ← separate asyncio coroutine (runs concurrently with Hub.run)
  │  while True: audio = await inbound_audio_bus.get(); _process_audio_item(audio)
  │
  └── _process_audio_item(audio)
        1. platform validation + trust check
        2. rate limit check (mirrors text pipeline)
        3. STT configured? else → dispatch error reply
        4. write temp file → stt.transcribe() → delete temp file
        5. noise check → dispatch stt_noise reply
        6. slash-injection guard → dispatch stt_invalid reply
        7. echo transcript to user
        8. construct InboundMessage from audio envelope
        9. inbound_bus.put(platform, msg)  ← RE-INJECTS into text bus
```

### 1.2 What the current architecture actually guarantees

**Ordering:** Messages from a given user arrive at their Pool in strict FIFO order. The staging queue serializes across platforms, and Pool._inbox is a single-consumer asyncio.Queue. This is a hard guarantee today.

**Isolation:** Each platform has its own bounded queue. A Telegram flood cannot fill the Discord queue. Pool processing is isolated per conversation scope.

**Backpressure:** Explicit: `put_nowait` raises `QueueFull` at the platform queue boundary. The adapter owns the drop decision. There is no backpressure from Pool back to InboundBus.

**Error containment:** Pipeline exceptions are caught in Hub.run() and logged; the loop continues. Pool exceptions are caught in `_guarded_process_one`. Outbound exceptions are caught in `OutboundDispatcher._dispatch_item`. Layers do not cascade.

**Session state:** The Pipeline's `_resolve_context` runs three resume paths (reply-to, thread-session, last-active) before pool submission. This must happen after binding resolution but before pool submission. It is currently sequenced correctly and atomically relative to the pool's idle state check.

---

## 2. The proposed event bus architecture

### 2.1 Core concept

Replace the sequential pipeline with a typed event envelope that evolves through processing phases. Multiple independent consumers subscribe to the bus, each consuming events at the phase they care about and emitting enriched events back.

```
InboundMessage (raw)
    │
    ▼
EventBus  ← central pub/sub, typed by MessageEvent
    │
    ├── SecurityConsumer     (validates platform, rate limit, trust)
    ├── RoutingConsumer      (resolves binding + agent + pool)
    ├── AudioConsumer        (STT transcription, re-emits as text phase)
    ├── CommandConsumer      (detects + dispatches slash commands)
    ├── ContextConsumer      (session resume logic)
    ├── PoolConsumer         (submit to pool for LLM dispatch)
    └── OutboundConsumer     (routes Response → adapter)
```

### 2.2 The MessageEnvelope — the event type

The key insight is that the "message" is no longer a passive data struct that pipeline stages transform. It becomes an event envelope with a **phase** that determines which consumers will pick it up.

```python
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from lyra.core.message import InboundMessage
from lyra.core.hub.hub_protocol import Binding


class MessagePhase(enum.Enum):
    """The processing phase a MessageEnvelope is currently in.

    Consumers filter on phase. A consumer consumes an event, does work,
    and emits a new event with the next phase (or drops it).
    """
    # Input phases (raw arrival)
    AUDIO_RAW        = "audio_raw"        # InboundAudio just arrived
    TEXT_RAW         = "text_raw"         # InboundMessage just arrived (or re-injected from audio)

    # Guard phases
    SECURITY_PASSED  = "security_passed"  # platform valid, not rate-limited, not blocked
    ROUTING_RESOLVED = "routing_resolved" # binding + agent + pool resolved

    # Dispatch phases
    COMMAND_DETECTED = "command_detected" # slash command identified, ready for dispatch
    CONTEXT_READY    = "context_ready"    # session resume complete, ready for pool
    LLM_SUBMITTED    = "llm_submitted"    # submitted to Pool._inbox

    # Outbound phases
    RESPONSE_READY   = "response_ready"   # agent produced a response
    DISPATCHED       = "dispatched"       # response sent to adapter

    # Terminal phases
    DROPPED          = "dropped"          # message discarded (with reason)


@dataclass
class MessageEnvelope:
    """Mutable event wrapper that evolves through processing phases.

    The original InboundMessage is immutable (frozen dataclass). The
    envelope carries mutable routing context accumulated by consumers.
    """
    msg: InboundMessage
    phase: MessagePhase = MessagePhase.TEXT_RAW

    # Accumulated by consumers
    binding: Binding | None = None
    pool_id: str | None = None
    agent_name: str | None = None
    drop_reason: str | None = None

    # Carry-forward metadata for outbound routing
    metadata: dict[str, Any] = field(default_factory=dict)
```

### 2.3 The EventBus implementation

```python
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

log = logging.getLogger(__name__)

# Filter predicate: returns True if this consumer should receive the event
FilterFn = Callable[[MessageEnvelope], bool]
# Handler coroutine: receives envelope, does work, emits zero or one new event
HandlerFn = Callable[[MessageEnvelope, "EventBus"], Coroutine[Any, Any, None]]


class Subscription:
    def __init__(self, filter_fn: FilterFn, handler: HandlerFn) -> None:
        self.filter_fn = filter_fn
        self.handler = handler


class EventBus:
    """Pub/sub bus for MessageEnvelope events.

    Publish: bus.publish(envelope) → fans out to all matching subscribers
    Subscribe: bus.subscribe(filter_fn, handler)
    Emit (from handler): bus.publish(new_envelope) re-enters the fan-out

    This is NOT a queue. publish() is an async call that runs all matching
    handlers concurrently (asyncio.gather). There is no buffering at the bus
    level — backpressure must be handled by individual consumers via their
    own internal queues.
    """

    def __init__(self) -> None:
        self._subscriptions: list[Subscription] = []

    def subscribe(self, filter_fn: FilterFn, handler: HandlerFn) -> None:
        self._subscriptions.append(Subscription(filter_fn, handler))

    async def publish(self, envelope: MessageEnvelope) -> None:
        """Fan out to all matching subscribers concurrently."""
        matching = [
            sub for sub in self._subscriptions
            if sub.filter_fn(envelope)
        ]
        if not matching:
            log.debug(
                "EventBus: no consumer for phase=%s msg_id=%s",
                envelope.phase.value,
                envelope.msg.id,
            )
            return
        await asyncio.gather(
            *(sub.handler(envelope, self) for sub in matching),
            return_exceptions=True,
        )
```

### 2.4 The consumers

Each current pipeline stage becomes a consumer. Here are the concrete implementations:

#### SecurityConsumer (was: _validate_platform + _check_rate_limit)

```python
class SecurityConsumer:
    """Validates platform and enforces rate limits.

    Subscribes to: TEXT_RAW
    Emits:         SECURITY_PASSED | DROPPED
    """

    def __init__(self, hub: Hub) -> None:
        self._hub = hub

    def filter(self, env: MessageEnvelope) -> bool:
        return env.phase == MessagePhase.TEXT_RAW

    async def handle(self, env: MessageEnvelope, bus: EventBus) -> None:
        try:
            Platform(env.msg.platform)
        except ValueError:
            env.phase = MessagePhase.DROPPED
            env.drop_reason = f"unknown_platform:{env.msg.platform}"
            await bus.publish(env)
            return

        if self._hub._is_rate_limited(env.msg):
            env.phase = MessagePhase.DROPPED
            env.drop_reason = "rate_limited"
            await bus.publish(env)
            return

        env.phase = MessagePhase.SECURITY_PASSED
        await bus.publish(env)
```

#### RoutingConsumer (was: _resolve_binding + _lookup_agent + get_or_create_pool)

```python
class RoutingConsumer:
    """Resolves binding, agent, and pool for a message.

    Subscribes to: SECURITY_PASSED
    Emits:         ROUTING_RESOLVED | DROPPED
    """

    def __init__(self, hub: Hub) -> None:
        self._hub = hub

    def filter(self, env: MessageEnvelope) -> bool:
        return env.phase == MessagePhase.SECURITY_PASSED

    async def handle(self, env: MessageEnvelope, bus: EventBus) -> None:
        binding = self._hub.resolve_binding(env.msg)
        if binding is None:
            env.phase = MessagePhase.DROPPED
            env.drop_reason = "no_binding"
            await bus.publish(env)
            return

        agent = self._hub.agent_registry.get(binding.agent_name)
        if agent is None:
            env.phase = MessagePhase.DROPPED
            env.drop_reason = f"no_agent:{binding.agent_name}"
            await bus.publish(env)
            return

        env.binding = binding
        env.pool_id = binding.pool_id
        env.agent_name = binding.agent_name
        env.phase = MessagePhase.ROUTING_RESOLVED
        await bus.publish(env)
```

#### CommandConsumer (was: router.is_command + _dispatch_command)

```python
class CommandConsumer:
    """Detects and dispatches slash commands.

    Subscribes to: ROUTING_RESOLVED (when message is a command)
    Emits:         RESPONSE_READY | CONTEXT_READY (fallthrough)

    Note: only consumes when is_command() returns True. Non-command messages
    at ROUTING_RESOLVED are picked up by ContextConsumer.
    """

    def __init__(self, hub: Hub) -> None:
        self._hub = hub
        self._parser = CommandParser()

    def filter(self, env: MessageEnvelope) -> bool:
        if env.phase != MessagePhase.ROUTING_RESOLVED:
            return False
        if env.binding is None:
            return False
        agent = self._hub.agent_registry.get(env.binding.agent_name)
        if agent is None:
            return False
        router = getattr(agent, "command_router", None)
        if router is None:
            return False
        # Parse and attach command context so router.is_command() can check it
        cmd_ctx = self._parser.parse(env.msg.text)
        if cmd_ctx is not None:
            import dataclasses
            env.msg = dataclasses.replace(env.msg, command=cmd_ctx)
        if hasattr(router, "prepare"):
            env.msg = router.prepare(env.msg)
        return router.is_command(env.msg)

    async def handle(self, env: MessageEnvelope, bus: EventBus) -> None:
        # ... dispatch command, emit RESPONSE_READY or fall through
        ...
```

#### AudioConsumer (was: AudioPipeline.run)

```python
class AudioConsumer:
    """Transcribes audio via STT and re-emits as TEXT_RAW.

    Subscribes to: AUDIO_RAW
    Emits:         TEXT_RAW (on success) | DROPPED (on failure)

    Has an internal asyncio.Queue for backpressure — STT is slow (100-500ms)
    and we don't want the bus blocked while waiting for Whisper.
    """

    def __init__(self, hub: Hub, queue_maxsize: int = 50) -> None:
        self._hub = hub
        self._queue: asyncio.Queue[tuple[MessageEnvelope, EventBus]] = (
            asyncio.Queue(maxsize=queue_maxsize)
        )
        self._worker: asyncio.Task | None = None

    async def start(self) -> None:
        self._worker = asyncio.create_task(self._process_loop())

    def filter(self, env: MessageEnvelope) -> bool:
        return env.phase == MessagePhase.AUDIO_RAW

    async def handle(self, env: MessageEnvelope, bus: EventBus) -> None:
        """Accept without blocking (fire into internal queue)."""
        try:
            self._queue.put_nowait((env, bus))
        except asyncio.QueueFull:
            log.warning("AudioConsumer queue full — audio %s dropped", env.msg.id)

    async def _process_loop(self) -> None:
        while True:
            env, bus = await self._queue.get()
            try:
                await self._transcribe_and_emit(env, bus)
            except Exception:
                log.exception("AudioConsumer._transcribe_and_emit failed")
            finally:
                self._queue.task_done()

    async def _transcribe_and_emit(
        self, env: MessageEnvelope, bus: EventBus
    ) -> None:
        # ... STT transcription logic (same as current AudioPipeline._process_audio_item)
        # On success: construct InboundMessage, set env.msg, env.phase = TEXT_RAW
        # On failure: env.phase = DROPPED, dispatch error reply
        await bus.publish(env)
```

---

## 3. What changes, what stays, what breaks

### 3.1 What stays

- `InboundMessage` and `InboundAudio` frozen dataclasses — unchanged.
- `Pool`, `PoolProcessor`, `PoolObserver` — unchanged. Pool is already decoupled via `PoolContext`.
- `OutboundDispatcher` — unchanged. It is already a queue-backed worker.
- `ChannelAdapter` protocol — unchanged.
- `RoutingKey`, `Binding` — unchanged.
- `RateLimiter` — unchanged, consumed by SecurityConsumer.
- `CircuitBreaker` — unchanged, consumed by ContextConsumer / PoolConsumer.
- The per-platform isolation in `InboundBus` — this feeds the bus with `TEXT_RAW` events.

### 3.2 What changes

**Hub.run() disappears.** Replaced by `EventBus` fan-out. Adapters publish `TEXT_RAW` events directly to the bus (or through a thin adapter shim that wraps `InboundBus` → `EventBus`).

**MessagePipeline disappears.** Its stages become consumers. The `PipelineResult` / `Action` enum becomes unnecessary — actions are expressed by which phase an envelope transitions to.

**AudioPipeline.run() disappears.** Replaced by `AudioConsumer` with its own internal queue.

**Hub class shrinks significantly.** Hub becomes a wiring container (registers consumers with the bus) rather than a processing loop.

### 3.3 What breaks or becomes harder

**1. Ordering guarantees.**
The current architecture gives strict FIFO per user. With `asyncio.gather` fan-out, message B can reach `SecurityConsumer` before message A has finished `PoolConsumer` — because each stage is now concurrent. You lose FIFO without explicit re-sequencing.

This is the hardest problem. Solutions:
- Per-user ordering queue at the bus level (brings back much of the current complexity)
- Accept that ordering is per-phase (consumers within a phase run in parallel)
- Introduce ordering keys and consumer-level sequence numbers

**2. The filter() function is called synchronously on every publish.**
In the command consumer above, `filter()` has a side effect: it parses the command and mutates `env.msg`. This is wrong. Parsing should happen in `handle()`. But then how does the consumer "claim" a ROUTING_RESOLVED event before ContextConsumer also picks it up?

The current pipeline avoids this with `if router.is_command(msg): ... return`. In the bus model, both CommandConsumer and ContextConsumer subscribe to ROUTING_RESOLVED. If command detection is in `filter()`, it has side effects. If it is in `handle()`, both consumers run concurrently on the same envelope.

Solutions:
- Use a shared mutable `phase` field and accept the race (bad)
- Add a `COMMAND_DETECTED` phase between ROUTING_RESOLVED and CONTEXT_READY, with CommandConsumer being the only consumer of ROUTING_RESOLVED and emitting either COMMAND_DETECTED or CONTEXT_READY
- Make the envelope immutable and let consumers return "I handled it" via a result, with the bus doing sequential dispatch for guard phases

**3. _resolve_context atomicity.**
The session resume logic in `_resolve_context` checks `pool.is_idle` and then calls `pool.resume_session()`. This must be atomic relative to any concurrent resume. In the current system it runs inside the single Hub.run() loop — no concurrency. In the event bus, ContextConsumer could run for two messages to the same pool concurrently. This is a correctness hazard.

**4. Error propagation is weaker.**
The current pipeline short-circuits on the first DROP. With concurrent consumers, you can get duplicate DROPs, duplicate responses, or a message being partially processed by two consumers before one drops it.

**5. Observability regresses.**
The current `MessagePipeline` has a `trace_hook` that produces a clean sequential trace: `inbound.message_received → inbound.platform_invalid → pool.agent_selected → outbound.command_handled`. With concurrent consumers, the trace becomes a DAG of events. Debugging "why was this message dropped?" requires correlating events across consumers by `msg.id`.

---

## 4. Tradeoffs analysis

### 4.1 What you gain

**Extensibility.** Adding a new processing step (e.g., spam classification, content filtering, metadata enrichment from an external API) means writing a new consumer and registering it. No changes to MessagePipeline.

**Parallelism for independent work.** If you add a "log message to audit trail" consumer that runs concurrently with SecurityConsumer, they can run in parallel rather than sequentially.

**Conceptual clarity for new contributors.** "Each component subscribes to what it cares about" is easier to explain than "read a 460-line pipeline method and understand all the implicit control flow."

**Testability of individual consumers.** Each consumer can be unit-tested with a mock `EventBus`. The current pipeline requires a full `Hub` mock.

**Pluggability.** A plugin could subscribe to `ROUTING_RESOLVED` to enrich the envelope (e.g., attach user preferences) before ContextConsumer runs.

### 4.2 What you lose

**Simplicity.** The current Hub.run() + MessagePipeline is simple to understand end-to-end in one sitting. The event bus requires understanding the subscription graph, which phases exist, which consumers subscribe to each phase, and how fan-out ordering works. This is *more* complexity, not less, for the current scale.

**Strict ordering guarantees.** As analyzed above, maintaining FIFO per user requires explicit sequencing that negates much of the fan-out benefit.

**Atomic guard chains.** The current pipeline short-circuits cleanly. The bus model requires careful design to avoid a message being "half-processed" by two competing consumers.

**Debuggability.** A sequential pipeline has a clear call stack. In a bus, a dropped message may require correlating log lines from multiple consumers.

**Error isolation between unrelated paths.** Currently, a command dispatch failure cannot affect a concurrent LLM submission because they are sequential. With concurrent bus fans, shared mutable state (pool, session) can be accessed by multiple consumers simultaneously.

**The AudioPipeline's re-injection pattern is clear.** Audio → STT → re-enqueue is a one-liner to understand. In the bus model, an AudioConsumer publishing `TEXT_RAW` causes the full consumer chain to re-run, which is correct but less obvious.

### 4.3 Honest complexity assessment

The current architecture is 6 modules, each ~100-400 lines, with clear responsibilities:

| Module | Lines | Responsibility |
|--------|-------|----------------|
| `hub.py` | 361 | Wiring + run loop |
| `message_pipeline.py` | 461 | Guard chain |
| `audio_pipeline.py` | 371 | Audio → STT → text |
| `inbound_bus.py` | 170 | Per-platform queues |
| `outbound_dispatcher.py` | 378 | Outbound queue + circuit |
| `pool_processor.py` | 460 | Agent dispatch |

An event bus implementation would require:

| Module | Lines (est.) | Responsibility |
|--------|------|---------------|
| `event_bus.py` | ~150 | Bus core (pub/sub, fan-out) |
| `message_envelope.py` | ~80 | Envelope + phase enum |
| `consumers/security.py` | ~60 | Platform + rate limit guard |
| `consumers/routing.py` | ~70 | Binding + agent + pool resolution |
| `consumers/audio.py` | ~200 | STT (same logic as AudioPipeline) |
| `consumers/command.py` | ~120 | Command dispatch |
| `consumers/context.py` | ~180 | Session resume (same logic) |
| `consumers/pool_submit.py` | ~60 | Pool submission |
| `consumers/outbound.py` | ~60 | Response routing |
| `hub.py` (wiring only) | ~150 | Consumer registration |

That is roughly the same line count but spread across more files with more interfaces. The code is not simpler — it is differently organized.

---

## 5. Risk assessment

### 5.1 Hard problems

**Ordering (HIGH RISK).** The current system provides strict FIFO per user because there is exactly one consumer of the staging queue (Hub.run). The bus fan-out breaks this. Restoring it requires either (a) a per-user ordered sub-queue inside the bus (reproducing InboundBus), or (b) accepting that message ordering within a user's conversation may change under concurrent load. Option (b) is safe for single-message latency but can cause session corruption if message A creates a pool and message B assumes it exists.

Concrete example of the hazard: User sends "hello" then immediately "what time is it?". Under the bus, both TEXT_RAW events fire concurrently. Both reach RoutingConsumer concurrently. Both call `get_or_create_pool()` concurrently. PoolManager.get_or_create_pool is not thread-safe (no lock). Race condition: two Pool objects could be created for the same pool_id.

Mitigation: PoolManager would need a per-pool-id asyncio.Lock. The current system avoids this entirely through single-consumer serialization.

**The "who handles ROUTING_RESOLVED?" problem (MEDIUM RISK).** As analyzed above, CommandConsumer and ContextConsumer both want to subscribe to `ROUTING_RESOLVED`. They cannot both handle the same envelope — one of them must "win". Options:

- Sequential dispatch for guard phases (not concurrent) — this is the current pipeline model under a different name.
- Reserve ROUTING_RESOLVED for CommandConsumer exclusively; CommandConsumer emits either COMMAND_DISPATCHED (terminal) or CONTEXT_READY (for non-commands). This works but requires CommandConsumer to understand the non-command path.
- Phase-specific ordering: the bus dispatches phase-change events sequentially within a message but allows different messages to be at different phases concurrently.

**Session resume atomicity (HIGH RISK).** `_resolve_context` checks `pool.is_idle` and then calls `pool.resume_session()`. This is a check-then-act pattern that is safe only when serialized. In the bus model, two messages to the same pool could both observe `is_idle = True`, both call `resume_session()`, and corrupt session state. The current architecture gets this for free from Hub.run()'s single-consumer loop. Fixing this requires a per-pool lock in the ContextConsumer or moving the check inside Pool itself.

**Backpressure (MEDIUM RISK).** The current system has explicit backpressure at the InboundBus platform queue boundary. The bus model needs to decide: does `publish()` block if a consumer's internal queue is full? If yes, the bus is effectively synchronous and you lose the fan-out benefit. If no, consumers must manage their own queues (as AudioConsumer does above) and drops are implicit.

**Exactly-once processing (LOW-MEDIUM RISK).** With concurrent consumers, the risk of duplicate processing is real. The current pipeline guarantees exactly-once by construction (sequential). The bus model needs explicit design around how a "claimed" envelope is protected from being re-processed by another consumer at the same phase.

### 5.2 Easy problems

**Testing consumer isolation** — straightforward with mock EventBus.

**Adding new consumers** — low risk, doesn't touch existing consumers.

**AudioConsumer's own queue** — straightforward, same as current AudioPipeline but with a different trigger mechanism.

---

## 6. Design recommendation

### 6.1 The core tension

The user's mental model ("each component consumes based on its own criteria") is appealing. But it implicitly assumes *independent* components with no shared state. Lyra's pipeline is *not* independent:

- SecurityConsumer must run before RoutingConsumer (no binding check on unknown platform)
- RoutingConsumer must run before CommandConsumer (command dispatch needs agent router)
- ContextConsumer must run before PoolConsumer (session state must be set before submission)
- All of these must serialize *per user* to preserve ordering

This is a *pipeline*, not a *bus*. The dependencies between stages are total and ordered. A pub/sub model adds mechanism without exploiting the "subscribers are independent" property that makes pub/sub valuable.

### 6.2 What a hybrid could look like

If the goal is extensibility and testability without the full event bus migration risk, a better path is:

**Option A: Middleware chain (like WSGI/ASGI middleware)**
```python
class PipelineMiddleware(Protocol):
    async def __call__(
        self,
        msg: InboundMessage,
        envelope: ProcessingContext,
        next: Callable[..., Awaitable[PipelineResult]],
    ) -> PipelineResult: ...
```
Each middleware can short-circuit or call `next()`. This preserves ordering and sequential guarantees while making stages composable and independently testable. This is essentially what `MessagePipeline` already is — it could be refactored to explicit middleware objects rather than private methods.

**Option B: Event bus for non-pipeline concerns only**
Use the bus for genuinely decoupled concerns: monitoring, analytics, audit logging, plugin hooks. The processing pipeline stays sequential and ordered. The bus (which already exists in embryonic form via `lyra.core.events` and ADR-022) handles observers.

This is what ADR-022 intended: the EventBus is for monitoring telemetry, not for control flow.

**Option C: Full event bus with ordered delivery**
If the bus is desired for control flow, implement ordered delivery per `scope_id`. Each `(platform, bot_id, scope_id)` gets its own sequenced delivery channel. The bus internally maintains one asyncio.Queue per scope and fans out per-scope. This is essentially InboundBus + PoolManager, recreated at the bus layer. The added complexity over the current model is hard to justify.

### 6.3 Recommendation

**For the current scale (2 adapters, 1 agent type, single process):** Keep the current architecture. The sequential pipeline provides correct ordering, atomic guard chains, and simple debugging. The cost is low extensibility for new stages, but new stages have been added cleanly as private methods (`_resolve_context` in ADR-026, `_dispatch_command` as a separate method) without requiring a rewrite.

**If extensibility becomes the driver:** Refactor MessagePipeline from private methods to explicit middleware objects (Option A above). Each middleware is independently testable, has a clear interface, and can be registered by plugins. No ordering risk, no concurrent fan-out, no shared state hazards. The pipeline stays sequential and correct.

**If the bus model is desired long-term:** Implement it for a new, genuinely independent concern first (e.g., a real-time monitoring consumer, a plugin hook system) where the "no ordering guarantees" property is acceptable. Validate the patterns before migrating the core pipeline.

---

## 7. Migration path (if proceeding)

If proceeding with a full event bus migration, the safe incremental path is:

### Phase 1: Introduce the envelope (no behavior change)
- Create `MessageEnvelope` and `MessagePhase`
- Wrap `InboundMessage` in an envelope at the InboundBus feeder output
- Pass the envelope through the existing pipeline (pipeline stages read `env.msg`)
- Output: envelope exists, pipeline unchanged, all tests pass

### Phase 2: Extract SecurityConsumer
- Move `_validate_platform` + `_check_rate_limit` from pipeline to SecurityConsumer
- SecurityConsumer runs in Hub.run() before pipeline.process() (still sequential, not bus)
- Output: guards are extracted, sequential, no behavior change

### Phase 3: Extract RoutingConsumer
- Move binding + agent lookup to RoutingConsumer
- Still sequential in Hub.run(): security → routing → (command | context | pool)
- Output: pipeline is now a chain of consumer objects, no bus yet

### Phase 4: Introduce EventBus for non-sequential path (monitoring only)
- Add EventBus with `SECURITY_PASSED`, `ROUTING_RESOLVED`, `LLM_SUBMITTED` phases
- Existing pipeline emits events to bus as side effects (fire-and-forget)
- New monitoring consumers subscribe to bus
- Output: bus exists, old pipeline still drives control flow, bus is telemetry only

### Phase 5: Migrate control flow to bus (RISKY)
- Requires solving ordering, atomicity, and "who handles ROUTING_RESOLVED" before starting
- Requires per-scope ordered delivery channels in EventBus
- Requires Pool._inbox as the sequencing primitive (this already exists)
- Requires locking in PoolManager.get_or_create_pool

**Estimated scope:** F-full tier minimum. Not a safe S or F-lite task.

---

## 8. Key interfaces if proceeding

These are the interfaces that would need to be stable for the bus model to work:

```python
# event_envelope.py
@dataclass
class MessageEnvelope:
    msg: InboundMessage          # always the current text message
    phase: MessagePhase          # mutable — consumer advances it
    binding: Binding | None      # set by RoutingConsumer
    pool_id: str | None          # set by RoutingConsumer
    agent_name: str | None       # set by RoutingConsumer
    drop_reason: str | None      # set when phase = DROPPED
    metadata: dict[str, Any]     # carry-forward for consumers to communicate

# consumer_protocol.py
class MessageConsumer(Protocol):
    def filter(self, env: MessageEnvelope) -> bool: ...
    async def handle(self, env: MessageEnvelope, bus: EventBus) -> None: ...

# event_bus.py
class EventBus:
    def subscribe(self, consumer: MessageConsumer) -> None: ...
    async def publish(self, env: MessageEnvelope) -> None: ...
    # MUST decide: sequential or concurrent dispatch within publish()
    # For correctness with shared state: sequential (phase-ordered)
    # For throughput on independent work: concurrent (no shared state)
```

The critical design choice in `EventBus.publish()` — sequential vs. concurrent — determines whether you get a pipeline-under-a-different-name or a true bus. For Lyra's current workload (ordered, stateful, sequential by nature), sequential dispatch per message is the correct choice, which means the bus is fundamentally a pipeline with a registration API.

---

## 9. Related ADRs

- **ADR-017** (`017-coupling-hotspots-and-decoupling-strategy.mdx`): Previous decoupling analysis; introduced PoolContext to break Hub→Pool coupling.
- **ADR-022** (`022-event-bus-singleton-vs-dependency-injection.mdx`): EventBus for monitoring telemetry (not control flow); establishes that module-level singleton is acceptable for telemetry.
- **ADR-031** (`031-processor-registry-and-concurrent-outbound-dispatch.mdx`): Processor registry pattern — a limited form of "consumers registered by command name."
- **ADR-032** (`032-llmevent-streamprocessor-renderevent-hexagonal-streaming.mdx`): Hexagonal streaming; RenderEvent as a typed event stream from LLM to adapter.

The monitoring EventBus in ADR-022 is a correct, limited application of pub/sub. The streaming RenderEvent pipeline in ADR-032 is also a correct application of typed event streams. The proposal here extends those patterns to the *control plane*, where ordering and atomicity requirements are stricter.
