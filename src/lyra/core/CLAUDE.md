# src/lyra/core/ — Hub, Pool, and Pipeline

## Purpose

The `core/` package is the brain of Lyra. It owns the message routing pipeline,
conversation pool lifecycle, agent dispatch, command routing, memory, and all
shared protocols. Everything else in the project depends on `core/`.

## Key architecture: hub-and-spoke

```
Inbound (platform) → InboundBus → MessagePipeline → Pool → Agent → LlmProvider
                                                        ↓
Outbound (platform) ←──────────────── OutboundDispatcher ←──────────────────
```

- `Hub` (`hub.py`) is the singleton coordinator. It owns one `PoolManager`, one
  `InboundBus`, one `OutboundDispatcher` per registered adapter, and the agent registry.
- `Pool` (`pool.py`) is one-per-conversation-scope. It serialises turns, debounces
  rapid messages, and holds the SDK history deque. A Pool never knows which platform
  it came from — routing is done by `PoolManager` before the Pool is touched.
- `MessagePipeline` (`message_pipeline.py`) is the fail-fast guard chain called
  inside `Hub.run()`. It produces a `PipelineResult` with an `Action` enum.

## Key protocols

### ChannelAdapter (`hub_protocol.py`)
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

### PoolContext (`pool.py`)
Narrow interface that `Pool` requires from its owner (Hub). Decouples Pool from
the full Hub for testing. Implements `get_agent()`, `dispatch_response()`,
`dispatch_streaming()`, and circuit breaker hooks.

### RoutingKey (`hub_protocol.py`)
`NamedTuple` of `(platform, bot_id, scope_id)`. Always call `.to_pool_id()` to
get the canonical string — never construct the pool ID inline (ADR-001 §4).

## Store pattern

Stateful resources follow the async store pattern:
- `__init__` initialises data structures only (no I/O)
- `connect()` opens the DB, runs migrations, warms caches
- `close()` / `stop()` tears down cleanly

Stores provide **sync reads from cache** and **async writes** to SQLite.
The cache is updated atomically with the write so the event loop never blocks on
a read. See `agent_store.py`, `auth_store.py`, `thread_store.py`.

## File map

| File | Responsibility |
|------|---------------|
| `hub.py` | Central hub; owns InboundBus, adapters, PoolManager, OutboundDispatcher |
| `hub_protocol.py` | `ChannelAdapter`, `RoutingKey`, `Binding` protocols/types |
| `hub_outbound.py` | `HubOutboundMixin` — outbound dispatch helpers mixed into Hub |
| `message_pipeline.py` | Fail-fast routing pipeline; `Action`, `PipelineResult` |
| `pool.py` | Per-conversation pool; `PoolContext` protocol |
| `pool_manager.py` | Pool lifecycle: create, evict stale, flush |
| `pool_processor.py` | Turn execution: submit message → call agent → dispatch reply |
| `guard.py` | `Guard`, `GuardChain`, `BlockedGuard`, `Rejection` |
| `agent.py` | `AgentBase` ABC; `load_agent_config` re-export |
| `agent_config.py` | `ModelConfig`, `Agent`, `SmartRoutingConfig` dataclasses |
| `agent_store.py` | SQLite-backed agent config store (write-through cache) |
| `agent_seeder.py` | TOML → `AgentRow` parse + DB import |
| `command_router.py` | Routes `/cmd` to builtin, session, or plugin handlers |
| `command_loader.py` | TOML plugin manifest discovery and handler loading |
| `builtin_commands.py` | Stateless functions for built-in slash commands |
| `workspace_commands.py` | `/folder`, `/workspace` handlers |
| `inbound_bus.py` | Platform queue → staging queue fanout |
| `outbound_dispatcher.py` | Per-adapter outbound queue and dispatch |
| `circuit_breaker.py` | Per-pool circuit breaker; `CircuitRegistry` |
| `message.py` | `InboundMessage`, `OutboundMessage`, `Response`, `Platform` |
| `trust.py` | `TrustLevel` enum (TRUSTED, UNTRUSTED, BLOCKED, ADMIN) |
| `identity.py` | `Identity` dataclass (user_id, trust_level, is_admin) |
| `memory.py` | `MemoryManager`; session snapshot and compaction |
| `session_lifecycle.py` | `SessionManager` mixin; context compaction logic |

## Conventions

- Every public module has a module-level docstring explaining its single
  responsibility.
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
