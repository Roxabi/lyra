# P02 — Architecture Audit: `src/lyra/inbound/`

> Scope: `src/lyra/inbound/**/*.py` (9 files, ~620 LOC)
> Date: 2026-05-26
> Prior audit: 2026-05-18 (did not cover inbound — module created during #1280 Phase 3, post-audit)
> Context: Epic #1277 stage-axis refactor, inbound pipeline live since 2026-05-20

---

## Summary

- **Stage-axis decomposition is clean** — each file maps to exactly one stage (parse/route/session/dispatch/compose/data). No intra-inbound circular deps.
- **Quality gate gap** — `lyra.inbound` is absent from all 7 `.importlinter` contracts. Bidirectional `adapters ↔ inbound` coupling is invisible to CI.
- **One layer violation** — `dispatcher.py` imports `push_to_hub_guarded` from `lyra.adapters.shared._shared` at runtime; the function should live in `core/` or `transport/` (or be a port), not in adapters.
- **Minor leaks** — `context.py` TYPE_CHECKING imports adapter types (`TypingTaskManager`, `OutboundListener`); `session_builder.py` catches `sqlite3.Error` because store protocols lack declared exceptions.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|--------------|
| `.importlinter` | — | **High** | `lyra.inbound` is not listed in any contract (layers, forbidden, independence). Bidirectional `adapters → inbound` (telegram/discord inbound) + `inbound → adapters` (`dispatcher.py:8`) is unenforced. | Add `lyra.inbound` to the `clean-architecture-layers` contract. Position it between `lyra.adapters` and `lyra.core` (or adjacent to `lyra.outbound`). |
| `dispatcher.py` | 8 | **High** | Runtime import `from lyra.adapters.shared._shared import push_to_hub_guarded`. This is an `inbound → adapters` upward layer violation. | Move `push_to_hub_guarded` to `lyra.core.messaging.bus` or `lyra.transport` (it bridges inbound bus + circuit breaker, not adapter-specific). Or define a port in `core/ports/inbound.py`. |
| `context.py` | 31-32 | Medium | `DispatchCtx` TYPE_CHECKING imports `TypingTaskManager` and `OutboundListener` from `lyra.adapters.shared.*`. Even under `TYPE_CHECKING`, this structurally couples the inbound dispatch context to adapter internals. | Extract a `TypingManagerProtocol` (or use `Any` / `object`) in `DispatchCtx` so the inbound layer does not depend on adapter types. Move `OutboundListener` to `core/ports/` if it is consumed by inbound. |
| `session_builder.py` | 95, 170, 212 | Medium | Catches `sqlite3.Error` (and `RuntimeError`) from TurnStore/ThreadStore calls. The store protocols do not declare exception types, so the session stage must know the underlying SQLite driver exception. | Add exception declarations to `ThreadStoreProtocol` and the TurnStore interface (e.g., `StoreError` hierarchy in `core/stores/`). Narrow the `except` blocks. |
| `router.py` | 51 | Low | Local import inside `decide()`: `from lyra.core.messaging.message import DiscordMeta, TelegramMeta`. The comment claims this avoids importing discord/aiogram, but `core.messaging.message` has no platform-library imports. | Move the import to module level (cleaner, functionally identical) and update the comment. |

---

## Metrics

| Metric | Value |
|--------|-------|
| Files analyzed | 9 |
| Total LOC (inbound/*.py) | ~620 |
| Files per stage | parse: 3, route: 1, session: 1, dispatch: 1, compose: 1, data: 1, init: 1 |
| Intra-inbound imports | 5 (pipeline→router/session_builder/dispatcher; stages→context) — all clean |
| Runtime imports from `lyra.adapters` into `inbound` | 1 (`dispatcher.py:push_to_hub_guarded`) |
| Runtime imports from `lyra.infrastructure` into `inbound` | 0 |
| Runtime imports from `lyra.inbound` into `lyra.adapters` | 2 files (`telegram_inbound.py`, `discord_inbound.py`) — expected (adapters compose the pipeline) |
| `TYPE_CHECKING` imports from `lyra.adapters` into `inbound` | 3 (`context.py` ×2, `wire_parser_*.py` adapter refs) |
| `TYPE_CHECKING` imports from `lyra.infrastructure` into `inbound` | 1 (`context.py:TurnStore`) |
| Importlinter contracts covering `inbound` | 0 / 7 |
| Tests | 4 files, all pass (`test_pipeline`, `test_router`, `test_session_builder`, `test_dispatcher`) |

---

## Recommendations (prioritized, max 5)

1. **Add `lyra.inbound` to `.importlinter` layers contract** (High). Position it between `lyra.adapters` and `lyra.core` (or co-locate with `lyra.outbound`). This closes the only ungated layer in the inbound pipeline and will immediately flag the `dispatcher.py → adapters` violation.

2. **Relocate `push_to_hub_guarded` out of `adapters/shared/_shared.py`** (High). The function is not adapter-specific — it handles bus enqueue, circuit breaker, and backpressure. Move it to `lyra.core.messaging.bus_helpers` or `lyra.transport.inbound_gateway`. This removes the upward dependency from `inbound/dispatcher` to `adapters`.

3. **Introduce a `TypingManagerProtocol` in `core/ports/` or `core/messaging/`** (Medium). Replace the `TypingTaskManager` adapter type in `DispatchCtx` with a protocol. This decouples the inbound dispatch context from adapter internals while preserving type safety.

4. **Add exception contracts to `ThreadStoreProtocol` and TurnStore** (Medium). Define a `StoreError` base in `core/stores/` so `session_builder.py` can catch a domain exception instead of `sqlite3.Error`. This closes the ADR-048 leak where infrastructure implementation details reach the session stage.

5. **Move `router.py:51` local import to module level** (Low). The deferred import is unnecessary — `core.messaging.message` does not import platform libraries. Clean up the misleading comment.

---

## Context: Relation to 2026-05-18 Audit

The 2026-05-18 audit did **not** cover `src/lyra/inbound/` because the module did not exist in its current form at that time (it was created during #1280 Phase 3, 2026-05-18 through 2026-05-24). Therefore:

- None of the 2026-05-18 findings have regressed in inbound (there were no inbound files to regress).
- The one architectural pattern carried over from legacy adapter code is `push_to_hub_guarded` living in `adapters/shared/_shared.py` — this predates the stage-axis refactor and was not identified in the prior audit because the focus was on `adapters/`, not inbound layering.
- ADR-048 store migration is correctly handled in inbound: `context.py` uses `TYPE_CHECKING` for `TurnStore`, and `session_builder.py` references `ThreadStoreProtocol` from `core/stores/`. The only ADR-048 gap is the undeclared exception types (finding #4 above).
