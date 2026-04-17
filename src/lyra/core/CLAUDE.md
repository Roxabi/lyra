# src/lyra/core/ — Hub, Pool, Pipeline, and Persistence

## Architecture: hub-and-spoke

```
Inbound (platform) → Bus[T] (LocalBus) → Middleware pipeline → Pool → Agent → LlmProvider
                                                        ↓
Outbound (platform) ←──────────────── OutboundDispatcher ←──────────────────
```

- `Hub` is the singleton coordinator — owns `PoolManager`, `LocalBus`, one `OutboundDispatcher` per adapter, and the agent registry.
- `Pool` is one-per-conversation-scope — serialises turns, debounces rapid messages, holds SDK history. Never knows which platform it came from.
- The active inbound pipeline is the composable middleware stack (`hub/middleware.py`, #431), which replaced the legacy monolithic `MessagePipeline`.

## Subdirectory layout

| Subdir | Purpose |
|--------|---------|
| `hub/` | Message routing, outbound dispatch, pool lifecycle orchestration |
| `stores/` | Store protocols + factory functions (implementations moved to `lyra.infrastructure.stores`) |
| `pool/` | Pool primitives — lifecycle, per-message processing, session observation |
| `commands/` | Internal command routing infra (NOT plugin commands) |

## Domain event types

`events.py` defines `LlmEvent` (`TextLlmEvent | ToolUseLlmEvent | ResultLlmEvent`) —
the streaming protocol shared between `llm/` drivers and `core/stream_processor`.
Placed here (not in `llm/`) so `llm → core` stays unidirectional. See
`src/lyra/llm/CLAUDE.md` for the event field reference.

`render_events.py` defines the platform-agnostic render events that
`StreamProcessor` emits downstream to adapters.

## CLI subprocess protocol

The `cli_*.py` files handle the Claude CLI subprocess protocol:

- `cli_protocol.py` — Public API re-exports (`StreamingIterator`, `send_and_read_stream`, etc.)
- `cli_streaming.py` — Async I/O layer: `StreamingIterator` handles timeout, EOF, process death
- `cli_streaming_parser.py` — Pure JSON parsing: `CliStreamingParser` converts NDJSON lines to `LlmEvent` objects (no I/O, fully testable in isolation)
- `cli_non_streaming.py` — Non-streaming protocol (`read_until_result`, `send_and_read`)
- `cli_pool.py` — Process pool management (`_ProcessEntry`, `CliPool`); inherits lifecycle, streaming, session, and worker mixins
- `cli_pool_lifecycle.py` — `CliPoolLifecycleMixin`: `start`, `stop`, `drain`, `get_reaper_status` (#760)
- `cli_pool_streaming.py` — `CliPoolStreamingMixin`: `send_streaming`, stale-resume guard (#760)
- `cli_pool_session.py` — `CliPoolSessionMixin`: TurnStore wiring, CLI session persistence for `--resume`

## Non-obvious placement decisions

**`pool_manager.py` and `pipeline_types.py` are in `hub/`** — both import `Hub` at runtime; placing them in `pool/` would create a circular import. `message_pipeline.py` is a backward-compatibility shim that re-exports from `pipeline_types.py`.

**`builtin_commands.py` and `workspace_commands.py` are flat in `core/`** — not in `commands/`. The `commands/` subdir is routing infra only; built-in handlers live at the `core/` level.

**`commands/` vs `src/lyra/commands/`** — `core/commands/` is the router/loader/registry plumbing. User-facing plugin commands (echo, search, pairing…) live in the top-level `src/lyra/commands/`.

**`pairing_config.py`** is a sibling dataclass with no DB logic — it is not a store.

**`json_agent_store.py`** is the DB-free test stub for `AgentStore`. Use via `make_agent_store(use_json=True)`.

## Key protocols

### ChannelAdapter (`hub/hub_protocol.py`)
Structural protocol every platform adapter must implement. The hub trusts `InboundMessage.user_id` as authenticated identity — adapters must verify platform-level auth before constructing the message.

### PoolContext (`pool/pool.py`)
Narrow interface `Pool` requires from its owner. Test seam: inject a mock to unit-test `Pool` without pulling in `Hub`.

### RoutingKey (`hub/hub_protocol.py`)
`NamedTuple(platform, bot_id, scope_id)`. Always call `.to_pool_id()` — never build pool ID strings manually (ADR-001 §4).

### Guard / GuardChain (`guard.py`)
`Guard` protocol has one method: `check(identity) -> Rejection | None`. Compose via `GuardChain`. Never raise from `check()` — return a `Rejection`.

## Store pattern

All stores follow the async store pattern (implementations in `lyra.infrastructure.stores`):
- `__init__` — data structures only, no I/O
- `connect()` — open DB, run migrations, warm cache
- `close()` — teardown

Reads are synchronous (from cache). Writes are async (SQLite). Cache updated atomically with write — event loop never blocks on a read.

Protocols remain in this package for dependency inversion.

## Import patterns

```python
# Top-level re-exports
from lyra.core import Hub, Pool, RoutingKey

# Subpackage re-exports
from lyra.core.hub import Hub, MiddlewarePipeline, OutboundDispatcher
from lyra.core.pool import Pool, PoolProcessor
from lyra.core.stores import AgentStoreProtocol, make_agent_store
from lyra.core.commands import CommandRouter, CommandLoader

# Direct imports (when not re-exported)
from lyra.core.hub.hub_protocol import ChannelAdapter, RoutingKey, Binding
from lyra.core.hub.pool_manager import PoolManager
from lyra.core.stores.agent_store_protocol import make_agent_store

# SQLite implementations (infrastructure layer, per ADR-048)
from lyra.infrastructure.stores.agent_store import AgentRow, AgentStore
from lyra.infrastructure.stores.auth_store import AuthStore
from lyra.infrastructure.stores.identity_alias_store import IdentityAliasStore
from lyra.infrastructure.stores.pairing import PairingStore
from lyra.infrastructure.stores.sqlite_base import SqliteStore
from lyra.infrastructure.stores.turn_store import TurnStore
```

Never import from old flat-core paths — always import from the subpackage directly.

## What NOT to do

- Do NOT add business logic to `Hub` — logic belongs in Middleware, Pool, or Agent.
- Do NOT make `Pool` depend on `Hub` directly — use `PoolContext`.
- Do NOT call store async methods before `connect()` or from synchronous code.
- Do NOT add platform-specific code to `core/` — that belongs in `adapters/`.
- Do NOT construct pool ID strings manually — use `RoutingKey.to_pool_id()`.
- Do NOT create nested CLAUDE.md files inside subdirs — this file covers all of `core/`.
