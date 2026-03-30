# src/lyra/core/hub/ — Message Routing and Pool Lifecycle

## Purpose

Message routing, outbound dispatch, and pool lifecycle orchestration — the central bus.
`Hub` is the singleton coordinator that owns the inbound bus, adapters, pool manager,
and outbound dispatcher.

## Files

| File | Responsibility |
|------|---------------|
| `hub.py` | `Hub` class — main entry point; owns Bus[T] (LocalBus), adapters, PoolManager, OutboundDispatcher |
| `hub_outbound.py` | `HubOutboundMixin` — outbound dispatch helpers mixed into Hub |
| `hub_protocol.py` | `ChannelAdapter` protocol, `RoutingKey` NamedTuple, `Binding` type |
| `hub_rate_limit.py` | Per-adapter rate limiting logic |
| `message_pipeline.py` | `MessagePipeline` — fail-fast guard chain; `Action` enum, `PipelineResult` |
| `pool_manager.py` | `PoolManager` — pool lifecycle: create, evict stale, flush |
| `outbound_dispatcher.py` | `OutboundDispatcher` — per-adapter outbound queue and dispatch |
| `outbound_errors.py` | Outbound error types and retry helpers |

## Why pool_manager.py is in hub/ (not pool/)

`pool_manager.py` imports `Hub` at runtime to call back into the hub when dispatching
responses. Placing it in `pool/` would create a circular import between `hub/` and
`pool/`. It is architecturally hub-owned — the manager bridges hub and pool lifecycles,
and the hub is its natural owner.

## Why message_pipeline.py is in hub/

`message_pipeline.py` imports `Hub` at runtime for the same circular-import reason.
The pipeline is logically part of hub orchestration, not a standalone primitive.

## Import pattern

```python
# Subpackage re-exports (preferred)
from lyra.core.hub import Hub, MessagePipeline, RoutingKey, ChannelAdapter
from lyra.core.hub import Action, PipelineResult, OutboundDispatcher

# Direct module imports
from lyra.core.hub.hub import Hub
from lyra.core.hub.hub_protocol import ChannelAdapter, RoutingKey, Binding
from lyra.core.hub.message_pipeline import MessagePipeline, Action, PipelineResult
from lyra.core.hub.pool_manager import PoolManager
from lyra.core.hub.outbound_dispatcher import OutboundDispatcher
```

## Gotchas

- Do NOT add business logic to `Hub`. Hub orchestrates; logic belongs in Pipeline,
  Pool, or Agent.
- `RoutingKey.to_pool_id()` is the only authorised way to produce a pool ID string
  (ADR-001 §4). Never construct it with string formatting.
- `ChannelAdapter` is a structural Protocol — adapters implement it without inheriting.
  The hub trusts `InboundMessage.user_id` as authenticated identity; adapters must
  verify platform-level auth before constructing the message.
- `MessagePipeline` stages return `PipelineResult | None`. `None` = continue;
  a `PipelineResult` = stop the pipeline.
