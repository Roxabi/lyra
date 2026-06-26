# P04 — core/ (non-hub) Architecture Audit

**Scope:** `src/lyra/core/**/*.py` excluding `src/lyra/core/hub/`
**Date:** 2026-05-26
**Baseline:** Prior audit 2026-05-18 (hexagonal / mutualisation / simplification). Do NOT re-report findings from that audit unless regressed.
**Focus:** Stage-axis decomposition (#1277), cross-stage imports, layer violations, circular dependencies, ADR-048 completeness.

---

## Summary

- **One transitive chain couples the LLM port + CLI pool to the session stage.** `core/agent/agent_config.py` imports `CommandConfig` from `core/commands/command_router.py`, so every consumer of `ModelConfig` (LLM port, CLI pool mixins, TTS dispatch) transitively imports the router and pool stages.
- **Three runtime cross-stage imports bypass ports/protocols.** `command_router.py` → `pool.py`, `agent.py` → `command_loader.py` + `command_router.py`, and `ports/llm.py` → `agent_config.py` + `messaging/events.py`.
- **ADR-048 gap shrank by 1 (`credential_store` removed in #1057) but 4 stores still lack protocols.** `turn_store` (refactored in #1331) also has no protocol in `core/stores/`; `core/stores/__init__.py` still exports only 2 of 4 existing protocols.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `core/agent/agent_config.py` | 9 | High | Runtime import `from ..commands.command_router import CommandConfig` creates a transitive dependency chain: `ports/llm.py` → `agent_config.py` → `command_router.py` → `pool.py`. The LLM driven port and the entire CLI pool stack therefore transitively depend on the session stage. | Extract `CommandConfig` and `ModelConfig` into a neutral `core/types.py` or `core/config.py` module with zero cross-stage imports. |
| `core/commands/command_router.py` | 15 | Medium | Runtime import `from ..pool.pool import Pool`. The router stage directly depends on the session stage without a port or protocol boundary. | Introduce a narrow `SessionHandle` or `PoolHandle` protocol (co-located with router or in `core/ports/`) and inject it; align with the existing `PoolContext` pattern. |
| `core/agent/agent.py` | 22-23 | Medium | Runtime imports `CommandLoader` and `CommandRouter` into `AgentBase` (LLM execution stage owns command routing infrastructure). | Move command routing out of `AgentBase`; pass a pre-built `CommandDispatch` callback or let the hub/bootstrap own router construction. |
| `core/ports/llm.py` | 13-14 | Medium | Driven port `LlmProvider` imports `ModelConfig` (agent stage) and `LlmEvent` (messaging stage) at runtime. The port is not self-contained and leaks domain types. | Move `ModelConfig` and `LlmEvent` (or neutral DTOs) into a shared types module, or create `core/ports/llm_types.py` adjacent to the port. |
| `core/stores/__init__.py` | 7-13 | Low | Exports only 2 of 4 stable protocols (`AgentStoreProtocol`, `ThreadStoreProtocol`); omits `IdentityAliasStoreProtocol` and `PairingManagerProtocol`. | Update `__all__` to export all stable store protocols. |
| `core/config.py` | 63 | Low | `RouterConfig` defers `from lyra.core.commands.command_patterns import load_pattern_configs` inside `_default_pattern_configs()`. Shared config dataclass depends on router stage at call time. | Move pattern loading into bootstrap or accept a callable factory instead of eagerly loading at `RouterConfig` construction time. |
| `core/pool/pool_observer.py` | 10 | Low | TYPE_CHECKING import of `TurnPublisher` from `lyra.transport` (new in #1331). Session stage gains knowledge of transport-layer event publisher. | Document as intentional α-pattern (ADR-075). If a runtime port is desired, add `TurnPublisherProtocol` in `core/ports/`. |
| `core/messaging/message.py` | 13 | Low | TYPE_CHECKING import of `CommandContext` from `core/commands.command_parser`. Inbound stage knows about router stage types. | Move `CommandContext` into `core/messaging/` or a shared types module since it is part of the inbound message envelope. |

**Not re-reported (prior audit, no regression):**
- `core/stores/pairing_protocol.py` runtime infra import — **fixed** since 2026-05-18 (runtime import removed).
- `core/processors/processor_registry.py` → `lyra.integrations.base` TYPE_CHECKING import — unchanged; tracked as ADR-061 gap.
- `smart_routing_protocol.py` dead code — **removed** since 2026-05-18 (finding resolved).
- 11 `.importlinter` exemptions for `core → infrastructure` TYPE_CHECKING imports — all remain TYPE_CHECKING-only, no runtime regression.

---

## Metrics

| Metric | Count / Value |
|--------|---------------|
| Files in scope | ~85 `.py` files |
| Runtime cross-stage imports within core/non-hub | 3 direct + 1 transitive chain |
| TYPE_CHECKING cross-stage imports within core/non-hub | 4 |
| Circular dependencies at runtime | 0 (`lint-imports` baseline holds: 7/7 contracts green) |
| ADR-048 protocols in `core/stores/` | 4 formal (`AgentStoreProtocol`, `ThreadStoreProtocol`, `IdentityAliasStoreProtocol`, `PairingManagerProtocol`) |
| ADR-048 implementations in `infrastructure/stores/` | ~12 files (including turn_store submodules) |
| Stores without protocols | 4 (`auth_store`, `bot_agent_map`, `message_index`, `prefs_store`) + `turn_store` (post-#1331) |
| Resolved since prior audit | `credential_store` removed (#1057) |
| `core/stores/__init__.py` export coverage | 2 / 4 protocols (50%) |

---

## Recommendations (prioritized)

1. **Neutralize `agent_config.py` dependency on `command_router.py` (High).** The transitive chain `ports/llm.py` → `agent_config.py` → `command_router.py` → `pool.py` is the single largest stage-axis leak in core/non-hub. Extract `CommandConfig` and `ModelConfig` into a zero-dependency shared types module (e.g., `core/types.py` or `core/config_types.py`) so that the LLM port and CLI pool do not transitively import the router and pool stages.

2. **Introduce a session handle protocol for CommandRouter (Medium).** Replace the direct `Pool` import in `command_router.py` with a narrow protocol exposing only the methods the router needs (e.g., `reset()`, `history_clear()`). Inject the protocol at bootstrap time rather than importing the concrete `Pool` class.

3. **Decouple `AgentBase` from command routing (Medium).** `AgentBase` should not instantiate `CommandLoader` or `CommandRouter` inside `__init__`. Pass a pre-built dispatch callback or move router construction to the hub/bootstrap layer, aligning with the stage-axis principle that the LLM execution stage receives pre-routed input.

4. **Complete ADR-048 protocols for the 4 remaining stores + TurnStore (Medium).** Add `AuthStoreProtocol`, `BotAgentMapProtocol`, `MessageIndexProtocol`, `PrefsStoreProtocol` to `core/stores/`. After the #1331 refactor, `turn_store` is also imported directly by core modules under TYPE_CHECKING; add a `TurnStoreProtocol` so that `pool_observer.py` and `cli_pool_session.py` can type-hint against a core boundary instead of `lyra.infrastructure`. Update `core/stores/__init__.py` to export all stable protocols.

5. **Document #1331 transport coupling in `PoolObserver` (Low).** `pool_observer.py` now knows about `TurnPublisher` (transport layer) via TYPE_CHECKING. Add a docstring note or ADR-075 reference explaining that the runtime boundary is the `register_turn_publisher()` setter and that `TurnPublisher` is intentionally an infrastructure concern injected at bootstrap.
