# P03 Architecture Audit — Outbound + Streaming

**Date:** 2026-05-26
**Scope:** `src/lyra/outbound/**/*.py`, `src/lyra/streaming/**/*.py`
**Context:** Post-#1279/#1282 new packages; prior audit 2026-05-18 did not cover these files (they did not yet exist).

---

## Summary

- `lyra.outbound` and `lyra.streaming` are correctly stage-axis decomposed: one file per stage/primitive, no mixing of concerns.
- One upward layer violation persists — `outbound.emitter` imports `adapters.shared._shared_streaming_state` and `_tool_recap` at module bottom to break a circular load path. Importlinter explicitly exempts this (`DEBT:importlinter-outbound-shared-state-transition`); deferred to Phase 7 (#1284).
- No ADR-048 store migration gaps, no new layer violations, no infrastructure imports, and both packages have dedicated unit-test suites.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/outbound/emitter.py` | 538-541 | High | Deferred runtime import of `StreamState` and `ToolRecapAccumulator` from `lyra.adapters.shared` — upward layer violation (outbound, a lower layer, imports adapters, a higher layer). Breaks the clean-architecture dependency rule. | Relocate `StreamState` and `ToolRecapAccumulator` into `lyra.outbound` in Phase 7 (#1284); remove the importlinter exemption. |
| `src/lyra/outbound/emitter.py` | 545 | Medium | File exceeds quality-gate 300-line limit (545 LOC, exempted). Hosts `OutboundEmitter`, `PlatformCallbacks`, and multiple helpers in a single file. | Split in Phase 7: extract `PlatformCallbacks` into a dedicated module or absorb it into `OutboundFormatter` Protocol. |
| `src/lyra/adapters/shared/_shared_streaming_state.py` | 127-129 | Medium | `StreamState.build_display_text` performs a deferred import of `OutboundErrorHandler` to break the circular load path. | Same fix as row 1: once `StreamState` lives in `lyra.outbound`, the import becomes intra-package and can move to the top of the file. |
| `src/lyra/outbound/emitter.py` | 402, 506 | Low | Two `# DEBT:boundary-broad-catch` annotations remain in `run()` and `_run_event_loop()`. OutboundErrorHandler was meant to be the single broad-catch site. | Unify terminal stream-error capture into `OutboundErrorHandler.guard` in the S7 follow-up. |
| `src/lyra/streaming/parser.py` | 22-28 | Low | `@runtime_checkable` `Parser` Protocol is not satisfied at runtime by `CliStreamingParser` or `StreamProcessor` (they expose `parse_line`/`process`, not `feed`/`finalize`). | Add `feed = parse_line` / `finalize` / `is_done` aliases on consumers when the Protocol needs to be enforced; add conformance tests. |

---

## Metrics

| Metric | Value |
|---|---|
| Files in scope | 9 (outbound 5, streaming 4) |
| Total LOC | 944 (outbound 741, streaming 203) |
| Importlinter contracts | 8 kept, 0 broken |
| Exemptions referencing this partition | 1 (`DEBT:importlinter-outbound-shared-state-transition`) |
| Unit-test files | 5 (`tests/outbound/` 3, `tests/streaming/` 3) |
| Cross-stage imports (internal) | 4 (emitter → error_handler/throttle; __init__ → all) — all downward |
| Cross-layer imports (external) | 4 (outbound → core.messaging, transport; streaming → transport) — all downward |
| Upward layer imports | 1 (outbound → adapters.shared) — exempted, deferred |
| ADR-048 store migration touches | 0 |

---

## Recommendations (prioritized)

1. **Relocate `StreamState` + `ToolRecapAccumulator` into `lyra.outbound`** (#1284, Phase 7) — kills the only upward import, eliminates the deferred-import hacks in both `emitter.py` and `_shared_streaming_state.py`, and allows the importlinter exemption to be retired.
2. **Split `emitter.py` below 300 lines** (#1284, Phase 7) — extract `PlatformCallbacks` into its own module or fold it into `OutboundFormatter` Protocol; this was the original intent per the file-exemption comment.
3. **Unify the two remaining broad-catch sites** into `OutboundErrorHandler.guard` — completes the T6/T7 promise of a single catch site for outbound boundaries.
4. **Add runtime `Parser` conformance** on `CliStreamingParser` and `StreamProcessor` once downstream code needs `isinstance` checks; keep the Protocol structural for now but plan aliases.
5. **Retire `_make_streaming_callbacks` legacy path** (S7 cleanup) — `PlatformCallbacks` dataclass and the legacy callback builders in `telegram_outbound.py`/`discord_outbound.py` should be absorbed into the formatter stage, completing the stage-axis transition.
