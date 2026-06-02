# src/factory/core/ — Hub, Pool, Pipeline, and Persistence

## Architecture: hub-and-spoke

```
Inbound (platform) → Bus[T] (LocalBus) → Middleware pipeline → Pool → Agent → LlmProvider
                                                        ↓
Outbound (platform) ←──────────────── OutboundDispatcher ←──────────────────
```

Four responsibilities:
- **Hub** — singleton coordinator; owns `PoolManager`, `LocalBus`, one `OutboundDispatcher` per adapter, agent registry
- **Pool** — one per conversation scope; serialises turns, debounces rapid messages, holds SDK history; ¬knows platform
- **Bus** (`LocalBus`) — pub/sub backbone; typed channels; adapters publish, hub subscribes
- **OutboundDispatcher** — routes processed responses back to the originating platform adapter

## Import layers

`core/` may import: stdlib, third-party, `lyra.core.ports` (own ports subdir).
`core/` must NOT import: `lyra.adapters`, `lyra.infrastructure`, `lyra.llm` (drivers), `lyra.commands` (plugin cmds).
Adapters and infrastructure may import `core/`; never the reverse.

## Ports & Protocols taxonomy

Two flavours of `Protocol` live in `core/`:

| Kind | Where | Role | Examples |
|---|---|---|---|
| **Driven port** (Cockburn, secondary) | `core/ports/` | Domain consumes an external capability | `LlmProvider`, `TtsProtocol`, `STTProtocol`, `AuditSink` |
| **Role interface** (Fowler) | Co-located with sub-domain | Internal collaboration between two pieces of `lyra.core` | `ChannelAdapter` (hub), `PipelineMiddleware` (hub/middleware), `PoolContext` (pool), narrow `AgentSeederTarget` (agent) |

Rule of thumb: if the Protocol abstracts something *outside* lyra (LLM, TTS, audit log, future Langfuse, …) → **driven port** → `core/ports/`. If it abstracts an *internal* collaboration (a role another file inside `lyra.core` fills) → **role interface**, co-located with its sub-domain. Driven ports are pure Protocol with no infrastructure import (TYPE_CHECKING-only allowed). Role interfaces follow the same constraint.

`ports/llm.py`, `ports/stt.py`, `ports/tts.py` follow the same shape: protocol + value objects + errors only. `ports/` is the single owner of domain types. Adapter-adjacent helpers (`is_whisper_noise`, `mime_from_suffix`) live in `lyra/nats/stt/stt_helpers.py`, not in `ports/`.

## Store pattern (ADR-048 (absorbed into ADR-059))

Store protocols stay in `core/stores/`; SQLite implementations live in `lyra.infrastructure.stores`.
Pattern: `__init__` = data structures only · `connect()` = open DB + migrate + warm cache · `close()` = teardown.
Reads are synchronous (cache). Writes are async (SQLite). Cache updated atomically — event loop never blocks on a read.

## Non-obvious placements

**`hub/pipeline/` owns `PoolManager`** — `PoolManager` and `pipeline_types` both import `Hub` at runtime; placing them in `pool/` would create a circular import. Re-exported from `lyra.core.hub.pipeline` for consumers.

**`cli/cli_pool_entry.py` (`_ProcessEntry`)** — extracted from `cli_pool.py` solely to break a circular import between pool mixins. Not a separate concern.

**`hub/outbound/`** — `OutboundDispatcher` and its streaming/TTS/audio/router/error helpers. Re-exported via `hub/__init__.py`.

**`core/commands/` vs `src/factory/commands/`** — `core/commands/` is router/loader/registry plumbing + built-in handlers. User-facing plugin commands live in the top-level `src/factory/commands/`.

## Key protocols

**`ChannelAdapter`** (`hub/hub_protocol.py`) — structural protocol every adapter must implement. Hub trusts `InboundMessage.user_id` as authenticated; adapters must verify platform auth before constructing the message.

**`PoolContext`** (`pool/pool_context.py`, re-exported from `pool/pool.py`) — narrow interface `Pool` requires from its owner. Test seam: inject a mock to unit-test `Pool` without pulling in `Hub`.

**`RoutingKey`** (`hub/hub_protocol.py`) — `NamedTuple(platform, bot_id, scope_id)`. Always call `.to_pool_id()` — never build pool ID strings manually (ADR-001 §4).

**`Guard` / `GuardChain`** (`auth/guard.py`) — `Guard.check(identity) -> Rejection | None`. Compose via `GuardChain`. Never raise from `check()`.

## Non-obvious subdirectory placement

`messaging/events.py` defines `LlmEvent` (placed in `core/`, not `llm/`, so `llm → core` stays unidirectional).

## What NOT to do

- ¬add business logic to `Hub` — belongs in Middleware, Pool, or Agent
- ¬make `Pool` depend on `Hub` directly — use `PoolContext`
- ¬call store async methods before `connect()` or from synchronous code
- ¬add platform-specific code to `core/` — belongs in `adapters/`
- ¬construct pool ID strings manually — use `RoutingKey.to_pool_id()`
- ¬create nested CLAUDE.md inside subdirs — this file covers all of `core/`
- ¬add new concrete setters to Hub without a `core/ports/` protocol
