# P01 — Architecture audit: `src/lyra/core/hub/**/*.py`

Date: 2026-05-26
Partition: P01 (core/hub)
Prior audit: 2026-05-18 (hexagonal / mutualization / simplification)
Focus: NEW or CHANGED code since 2026-05-18 + gaps left unaudited.

---

## Summary

- **TurnPublisher / TypingPublisher wiring (#1331, #1376) expands the ADR-048 protocol gap** — the hub registration API and inbound pipeline now accept concrete transport/infrastructure types (`TurnPublisher`, `TypingPublisher`, `TurnStore`) with no corresponding protocols in `core/ports/` or `core/stores/`.
- **PoolManager and MessagePrepMiddleware continue to depend on the full `Hub` concrete class** — #1331 added direct access to `hub._turn_publisher` and `hub._turn_store` inside stage 7 of the inbound pipeline, coupling the pipeline to transport and infrastructure concerns.
- **Cross-stage imports within `core/hub/` remain un-ported** — `middleware_submit` reaches into `outbound_errors`, outbound files import `AudioPipeline` from outside the outbound sub-package, and `PoolManager` receives `Hub` as its runtime context instead of the documented `PoolContext` protocol.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `middleware/middleware_pool.py` | 118 | **high** | `MessagePrepMiddleware` (stage 7, inbound pipeline) directly accesses `ctx.hub._turn_publisher` and `ctx.hub._turn_store` to build a `_resume_fn` closure that publishes via JetStream. Added in #1331. This couples the inbound pipeline to transport (`TurnPublisher`) and infrastructure (`TurnStore`) concrete types. | Extract a `ResumePublisherPort` in `core/ports/`; inject it into `PipelineContext`. The middleware should never reach through `Hub` into transport/infrastructure. |
| `hub/hub_registration.py` | 77 | **high** | `set_turn_publisher()` (added #1331) and `set_typing_publisher()` (added #1376) accept concrete transport types with no core protocol. The hub registration API now has 5 store/transport dependencies lacking protocols (`TurnStore`, `MessageIndex`, `PrefsStore`, `TurnPublisher`, `TypingPublisher`). | Create protocols for all five in `core/ports/` or `core/stores/`; migrate setters to accept protocols. This is the continuation of ADR-048. |
| `pipeline/pool_manager.py` | 62 | **medium** | `PoolManager` passes the full `Hub` as `ctx` to `Pool` (violating `core/CLAUDE.md`: "use PoolContext"). #1331 amplified the leak by adding `self._hub._turn_publisher` access at line 70. `PoolManager` also reads `self._hub._message_index`, `_pairing_manager`, `agent_registry`, `cli_pool`, `_memory_tasks` directly. | Narrow `PoolManager` to accept a `PoolFactory` protocol or `PoolContext`; stop passing the full `Hub`. |
| `middleware/middleware_submit.py` | 124 | **medium** | `SubmitToPoolMiddleware` (stage 9, terminal) imports `try_notify_user` from `outbound_errors` — an inbound pipeline stage depends on an outbound sub-package helper, not via `core/ports/` or a contract schema. | Move user-notification into a `NotificationPort` in `core/ports/` or create a pipeline-internal notifier abstraction that the outbound layer implements. |
| `hub/hub_shutdown.py` | 109 | **medium** | `shutdown()` directly calls `await self._turn_store.close()` and `await self._message_index.close()` on infrastructure concrete types. No port indirection. | Accept `ClosableStore` protocol or delegate store lifecycle to a shutdown coordinator in `bootstrap/`. |
| `hub/outbound/*.py` | various | low | 4 outbound files (`outbound_router.py:35`, `outbound_streaming.py:20`, `outbound_tts.py:20`, `hub.py:15`) import `AudioPipeline` from `core/tts_dispatch.py`, which lives outside the `hub/outbound/` sub-package. | Move `tts_dispatch.py` into `hub/outbound/` or re-export `AudioPipeline` through `hub/outbound/__init__.py` so outbound stage imports stay within the stage boundary. |
| `middleware/middleware_stt.py` | 117 | low | Transitional `b""` placeholder in `hub._stt.transcribe(b", msg.audio.mime_type)`. Changed in #1064 (BlobRef contracts). The pipeline stage bypasses the actual audio payload, breaking the STT port contract until #1067 resolves BlobRef → bytes. | Complete BlobRef resolution in #1067; remove the `b""` placeholder. |
| `middleware/middleware_guards.py` | 111 | low | Deferred import `from ..hub import RoutingKey` inside `__call__` to break an import-time cycle between `middleware` and `hub` sub-packages. | Relocate `RoutingKey` and `Binding` to a neutral module (e.g., `core/messaging/routing.py`) so the inbound pipeline does not need to import from the hub package at runtime. |

---

## Metrics

| Metric | Value |
|---|---|
| Total `.py` files in `core/hub/` | 28 |
| Files touched since 2026-05-18 | 5 (`hub.py`, `hub_registration.py`, `middleware_pool.py`, `pool_manager.py`, `middleware_stt.py`) |
| Files with `TYPE_CHECKING` imports to `infrastructure/` or `transport/` | 4 (`hub.py`, `hub_registration.py`, `hub_shutdown.py`, `pool_manager.py`) |
| Files with runtime imports to `infrastructure/` or `transport/` | 0 (all runtime access is via attribute drilling through `Hub`) |
| Hub-facing stores / publishers **with** core protocols | 4 (`AgentStore`, `ThreadStore`, `IdentityAliasStore`, `PairingManager`) |
| Hub-facing stores / publishers **without** core protocols | 5 (`TurnStore`, `MessageIndex`, `PrefsStore`, `TurnPublisher`, `TypingPublisher`) |
| ADR-048 protocol coverage (hub-facing) | 4 / 9 = **44%** |
| Inbound pipeline stages that reach into outbound or infrastructure | 3 / 10 (`MessagePrepMiddleware` → turn_publisher/turn_store; `SttMiddleware` → dispatch_response; `SubmitToPoolMiddleware` → outbound_errors) |
| Import-time circular dependencies (deferred imports to break cycle) | 1 (`middleware_guards.py` → `..hub.RoutingKey`) |
| Contracts broken (`lint-imports`) | 0 / 8 |

---

## Recommendations (prioritized, max 5)

1. **Halt adding new concrete infrastructure/transport setters to `HubRegistrationMixin` until protocols exist.** The #1331 / #1376 pattern (`set_turn_publisher`, `set_typing_publisher`) repeats the ADR-048 deviation. Before any new store or transport dependency is wired into the hub, a protocol must be defined in `core/ports/` or `core/stores/` and accepted by the setter. This is the single most impactful gate to prevent further drift.

2. **Extract `ResumePublisherPort` and inject it into `PipelineContext`.** The `_resume_fn` closure in `MessagePrepMiddleware` (lines 118-139) should be supplied by the hub or bootstrap layer, not constructed inside the pipeline by drilling into `ctx.hub._turn_publisher`. This removes the only runtime infrastructure/transport leak inside the inbound middleware chain.

3. **Narrow `PoolManager` from `Hub` to `PoolContext` (or a new `PoolFactory` protocol).** `PoolManager` currently accesses 8+ private attributes of `Hub`. Introduce a protocol that exposes only the attributes `PoolManager` needs (`_max_pools`, `_pool_ttl`, `_turn_store`, `_turn_publisher`, `_message_index`, `_pairing_manager`, `agent_registry`, `cli_pool`, `_memory_tasks`). This closes the architectural mismatch documented in `core/CLAUDE.md`.

4. **Relocate `RoutingKey` + `Binding` to `core/messaging/routing.py` to eliminate the deferred import cycle.** The `RateLimitMiddleware` deferred import (`DEBT:plc0415-deferred-import`) is a mechanical consequence of `RoutingKey` living in `hub_protocol.py`. Moving the type to a neutral module breaks the cycle between `middleware/` and `hub/` cleanly.

5. **Move `tts_dispatch.py` into `hub/outbound/` and re-export `AudioPipeline` from `hub/outbound/__init__.py`.** All four outbound consumers of `AudioPipeline` are inside `hub/outbound/`. Keeping the module outside the outbound sub-package creates a cross-sub-package dependency that contradicts stage-axis decomposition. The move is non-breaking because `tts_dispatch.py` is only consumed by hub code.
