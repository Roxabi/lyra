# src/lyra/core/ — Hub, Pool, and Pipeline

## Purpose

The `core/` package is the brain of Lyra. It owns the message routing pipeline,
conversation pool lifecycle, agent dispatch, command routing, memory, and all
shared protocols. Everything else in the project depends on `core/`.

## Key architecture: hub-and-spoke

```
Inbound (platform) → Bus[T] (LocalBus) → MessagePipeline → Pool → Agent → LlmProvider
                                                        ↓
Outbound (platform) ←──────────────── OutboundDispatcher ←──────────────────
```

- `Hub` (`hub/hub.py`) is the singleton coordinator. It owns one `PoolManager`, one
  `LocalBus` (typed as `Bus[T]`), one `OutboundDispatcher` per registered adapter, and the agent registry.
- `Pool` (`pool/pool.py`) is one-per-conversation-scope. It serialises turns, debounces
  rapid messages, and holds the SDK history deque. A Pool never knows which platform
  it came from — routing is done by `PoolManager` before the Pool is touched.
- `MessagePipeline` (`hub/message_pipeline.py`) is the fail-fast guard chain called
  inside `Hub.run()`. It produces a `PipelineResult` with an `Action` enum.

## Layout

`core/` is split into 4 subdirectories plus flat modules:

| Subdir | Modules | Purpose |
|--------|---------|---------|
| `stores/` | 12 | SQLite persistence — all durable stores, shared base, pairing state |
| `hub/` | 8 | Message routing, outbound dispatch, pool lifecycle orchestration |
| `pool/` | 3 | Pool primitives — lifecycle, per-message processing, session observation |
| `commands/` | 4 | Internal command routing infra (NOT plugin commands) |

Each subdir also contains an `__init__.py` with re-exports.

Each subdir has its own `CLAUDE.md`. See them for file-level details and gotchas.

### Flat modules (remain in core/)

These modules did not move into subdirs:

- **Agent**: `agent.py`, `agent_builder.py`, `agent_commands.py`, `agent_config.py`,
  `agent_db_loader.py`, `agent_loader.py`, `agent_models.py`, `agent_refiner.py`,
  `agent_schema.py`, `agent_seeder.py`
- **Memory**: `memory.py`, `memory_freshness.py`, `memory_schema.py`, `memory_types.py`,
  `memory_upserts.py`
- **Message types**: `message.py`, `messages.py`, `render_events.py`
- **Guards / trust**: `circuit_breaker.py`, `guard.py`, `identity.py`, `trust.py`
- **Auth / persona**: `auth.py`, `authenticator.py`, `persona.py`
- **Runtime / infra**: `debouncer.py`, `events.py`, `inbound_bus.py`,
  `tts_dispatch.py`, `processor_registry.py`,
  `runtime_config.py`, `session_lifecycle.py`, `stream_processor.py`,
  `tool_display_config.py`, `workspace_commands.py`, `builtin_commands.py`,
  `cli_pool.py`, `cli_pool_worker.py`, `cli_protocol.py`
## Key protocols

### ChannelAdapter (`hub/hub_protocol.py`)
Every platform adapter (Telegram, Discord) must implement this structural Protocol.
Key methods: `normalize()`, `send()`, `send_streaming()`, `render_audio()`,
`render_attachment()`. The hub trusts `InboundMessage.user_id` as authenticated
identity — adapters are responsible for platform-level verification before constructing
the message.

Never derive `user_id` or `scope_id` from unverified inbound data.

### Guard / GuardChain (`guard.py`)
A `Guard` is a `Protocol` with one method: `check(identity) -> Rejection | None`.
Guards are composable via `GuardChain` (sequential, short-circuit on first rejection).
`BlockedGuard` is the built-in implementation that rejects `TrustLevel.BLOCKED` users.
Add new guards without subclassing — just implement `check()`.

### PoolContext (`pool/pool.py`)
Narrow interface that `Pool` requires from its owner (Hub). Decouples Pool from
the full Hub for testing. Implements `get_agent()`, `dispatch_response()`,
`dispatch_streaming()`, and circuit breaker hooks.

### RoutingKey (`hub/hub_protocol.py`)
`NamedTuple` of `(platform, bot_id, scope_id)`. Always call `.to_pool_id()` to
get the canonical string — never construct the pool ID inline (ADR-001 §4).

## Store pattern

Stateful resources follow the async store pattern:
- `__init__` initialises data structures only (no I/O)
- `connect()` opens the DB, runs migrations, warms caches
- `close()` / `stop()` tears down cleanly

Stores provide **sync reads from cache** and **async writes** to SQLite.
The cache is updated atomically with the write so the event loop never blocks on
a read. See `stores/agent_store.py`, `stores/auth_store.py`, `stores/thread_store.py`.

## Import patterns

```python
# Top-level re-exports (preferred for Hub, Pool, core types)
from lyra.core import Hub, Pool, MessagePipeline, RoutingKey

# Subpackage re-exports
from lyra.core.hub import Hub, MessagePipeline
from lyra.core.pool import Pool, PoolProcessor
from lyra.core.stores import AgentStore, AuthStore, SqliteStore
from lyra.core.commands import CommandRouter, CommandLoader

# Direct module imports (when you need something not re-exported)
from lyra.core.hub.hub_protocol import ChannelAdapter
from lyra.core.stores.agent_store import AgentRow
```

## Conventions

- Every public module has a module-level docstring explaining its single responsibility.
- Async stores: `connect()` before first use, `close()` on shutdown. Never call
  async methods before `connect()`.
- `PoolContext` is the test seam — inject a mock to unit-test Pool without Hub.
- `MessagePipeline` stages return `PipelineResult | None`. Returning `None`
  means "continue to next stage"; returning a `PipelineResult` stops the pipeline.
- Pool IDs are always produced by `RoutingKey.to_pool_id()` — never build them
  with string formatting.

## What NOT to do

- Do NOT add business logic to `Hub`. Hub orchestrates; logic belongs in Pipeline,
  Pool, or Agent.
- Do NOT make `Pool` depend on `Hub` directly — use `PoolContext` instead.
- Do NOT call store async methods from synchronous code or before `connect()`.
- Do NOT add platform-specific code to `core/` — that belongs in `adapters/`.
- Do NOT construct `pool_id` strings manually. Use `RoutingKey.to_pool_id()`.
- Do NOT raise exceptions from `Guard.check()` — return a `Rejection` instead.
- Do NOT import from old flat-core paths — always import from the subpackage directly
  (e.g. `from lyra.core.stores.agent_store import AgentStore`, not `lyra.core.agent_store`).
