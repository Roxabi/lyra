# Architecture Audit — P06 Bootstrap (`src/lyra/bootstrap/**/*.py`)

Date: 2026-05-26
Base audit: 2026-05-18 (prior audit left `bootstrap/wiring/`, `bootstrap/lifecycle/`, `bootstrap/standalone/` unaudited)
Scope: NEW or CHANGED code since 2026-05-18 + gaps explicitly left unaudited

---

## Summary

- **Cross-stage import violation in new code**: `adapter_standalone.py` (post-#1376) imports 4 private underscore-prefixed symbols directly from adapter internals (`lyra.adapters.telegram.telegram`, `lyra.adapters.discord.discord_outbound`), bypassing `core/ports/` and contracts.
- **Bootstrap-internal circular dependency**: `factory/unified.py` imports `run_lifecycle` from `lifecycle/`, while `lifecycle/bootstrap_lifecycle.py` imports `watchdog` from `factory/utils.py`, creating a `factory ↔ lifecycle` cycle not caught by importlinter (package-level only).
- **ADR-048 env-var parity gap**: `turn_writer_standalone.py` respects `LYRA_TURNS_DB` (#1331), but `bootstrap_stores.py` `open_stores()` does not, risking path divergence between the sole writer and the read-only consumers.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|--------------|
| `src/lyra/bootstrap/standalone/adapter_standalone.py` | 120–130 | **Medium** | Imports private adapter symbol `_telegram_scope_resolver` from `lyra.adapters.telegram.telegram` and `_typing_worker` from `lyra.adapters.telegram.telegram_outbound`. Bootstrap (composition root) reaches into adapter private modules. | Extract public scope-resolvers and typing workers into `lyra.typing` or `lyra.core.ports/typing` so bootstrap imports only public APIs. |
| `src/lyra/bootstrap/standalone/adapter_standalone.py` | 260–270 | **Medium** | Imports private adapter symbols `_discord_scope_resolver` from `lyra.adapters.discord.adapter` and `_discord_typing_worker` from `lyra.adapters.discord.discord_outbound`. Same cross-stage boundary violation. | Same as above — consolidate public typing construction surface so adapter internals remain opaque to bootstrap. |
| `src/lyra/bootstrap/factory/unified.py` + `src/lyra/bootstrap/lifecycle/bootstrap_lifecycle.py` | 29 / 53 | **Medium** | Circular dependency within bootstrap subpackages: `factory/unified.py` imports `run_lifecycle` from `lifecycle/`, and `lifecycle/bootstrap_lifecycle.py` imports `watchdog` from `factory/utils.py`. | Move `watchdog` into `lifecycle/lifecycle_helpers.py` (or a new `bootstrap/utils.py`) to break the `factory ↔ lifecycle` cycle. |
| `src/lyra/bootstrap/bootstrap_stores.py` | 267 | **Medium** | `open_stores()` hardcodes `vault_dir / "turns.db"` and ignores `LYRA_TURNS_DB`, which `turn_writer_standalone.py` added in #1331. When the env var is set, hub/adapters open a different read-only path than the writer. | Mirror `LYRA_TURNS_DB` resolution from `turn_writer_standalone.py` into `open_stores()`, or share a `resolve_turns_db_path()` helper. |
| `src/lyra/bootstrap/factory/wiring_helpers.py` | 66–74 | **Low** | `BotAuthBundle` dataclass uses `object` for 5 fields (`tg_bot_auths`, `dc_bot_auths`, `msg_manager`, `circuit_registry`, `admin_user_ids`), erasing type safety. | Replace `object` with proper types (or `Any` if cycle avoidance is needed) under `from __future__ import annotations` to keep runtime clean. |
| `src/lyra/bootstrap/standalone/hub_standalone.py` | 166, 249, 257 | **Low** | Inline imports of `TurnPublisher`, `TypingPublisher`, `watchdog`, and `AuditConsumer` where top-level imports are safe (transport/core are below bootstrap per importlinter). | Promote to top-level imports for consistency and readability; no cycle risk exists. |
| `src/lyra/bootstrap/standalone/turn_writer_standalone.py` | 39 | **Low** | Deferred import of `setup_shutdown_event` inside function body despite `signal_handlers.py` only importing stdlib (zero cycle risk). | Promote to top-level import for consistency with other bootstrap modules. |
| `src/lyra/bootstrap/standalone/turn_writer_standalone.py` | 30 | **Low** | `_bootstrap_turn_writer_standalone` accepts `raw_config: dict` but never references it, creating a misleading API surface. | Either consume a config key (e.g., `turn_writer.health_port`) or remove the parameter and document the standalone signature divergence. |

---

## Metrics

| Metric | Value |
|--------|-------|
| Bootstrap `.py` files total | 27 |
| Files changed since 2026-05-18 | 14 (~52%) |
| New files since 2026-05-18 | 2 (`credentials.py`, `turn_writer_standalone.py`) |
| Importlinter contracts (package-level) | 8 / 8 kept (100%) |
| Circular deps **within** bootstrap subpackages | 1 (`factory ↔ lifecycle`) |
| Cross-stage private-symbol imports | 4 symbols across 2 files |
| ADR-048 store-migration env-var parity gaps | 1 (`LYRA_TURNS_DB` missing in `open_stores`) |
| Inline imports with no cycle justification | 2 files (`hub_standalone.py`, `turn_writer_standalone.py`) |

---

## Recommendations (prioritized)

1. **Extract public typing surface from adapter internals** — Move `_telegram_scope_resolver`, `_discord_scope_resolver`, and the per-platform typing workers into `lyra.typing` (or a `core/ports/typing` module) so `adapter_standalone.py` stops importing private adapter symbols. This closes the only NEW cross-stage boundary violation introduced since 2026-05-18.

2. **Break the `factory ↔ lifecycle` cycle** — Relocate `watchdog` from `factory/utils.py` to `lifecycle/lifecycle_helpers.py` (it monitors long-running tasks during lifecycle). Update `hub_standalone.py` to import it from `lifecycle/` instead of `factory/`, eliminating the bidirectional subpackage dependency.

3. **Sync `LYRA_TURNS_DB` into `open_stores`** — Add `LYRA_TURNS_DB` env-var resolution to `bootstrap_stores.py` so the hub and adapters open the same read-only `turns.db` that the standalone writer writes to. Extract a shared `resolve_turns_db_path()` helper if the logic is duplicated in >1 module.

4. **Centralize typing-listener construction in `factory/`** — Create `factory/typing_wiring.py` helpers that build per-platform `TypingListener` bundles, keeping the adapter-private imports isolated to the factory subpackage. `standalone/adapter_standalone.py` should call these helpers rather than constructing listeners inline.

5. **Add bootstrap-internal boundary rule to `CLAUDE.md`** — Document that `lifecycle/` must not import from `factory/` (and vice versa) in `src/lyra/bootstrap/CLAUDE.md`. This prevents regressions because importlinter only enforces package-level contracts, not subpackage cycles.
