# P03 Code Smells Audit — outbound + streaming

**Scope:** `src/lyra/outbound/**/*.py`, `src/lyra/streaming/**/*.py`
**Date:** 2026-05-26
**Partition:** P03
**Context:** Epic #1277 stage-axis refactor (Phase 2 / Phase 5). Prior audit 2026-05-18 covered hexagonal conformance, mutualization, dead-code; findings here are new or distinct.

---

### Summary

- `OutboundEmitter` is a god class (9+ responsibilities) and hosts two functions that breach the 50-line threshold; one is explicitly `noqa: C901`.
- A pair of no-op default callbacks is duplicated verbatim across `outbound/emitter.py` and `outbound/formatter.py` (~15 lines each).
- Nine identical `guard(lambda ...)` + `if isinstance(result, Err): pass` stanzas in `emitter.py` could be collapsed into a small internal helper.

---

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `src/lyra/outbound/emitter.py` | 127 | High | God class `OutboundEmitter` handles placeholder send/edit, trace placeholder, text dispatch, tool dispatch/recap, reasoning dispatch, run-lifecycle events, error fallback, typing lifecycle, final delivery | Split into cohesive collaborators: `PlaceholderManager`, `RecapAccumulator`, `TypingCoordinator`, `DeliveryEngine`. Align with #1279 Phase 7 follow-up spec. |
| `src/lyra/outbound/emitter.py` | 337 | High | `_run_event_loop` is 67 lines with `noqa: C901` — 4-branch `isinstance` dispatch ladder inside `try/except`, plus `assert_never` fallback | Extract per-event-type handler methods and a small `EventRouter` mapping type -> handler; the `try` body then becomes ~10 lines. |
| `src/lyra/outbound/emitter.py` | 427 | Medium | `_deliver_final` is 58 lines, mixing final recap edit, display-text assembly, chunk overflow delivery, and error-fallback editing | Split into `_deliver_recap_final`, `_deliver_text_chunks`, `_deliver_error_fallback`; delegate display-text assembly to `StreamState` or a new `DisplayBuilder`. |
| `src/lyra/outbound/emitter.py` | 241 | Medium | `_on_text_v2` is 45 lines; contains nested debounce logic and a `guard(lambda ...)` stanza | Extract `_should_debounce_edit(now) -> bool` helper; move guard stanza to a shared `_safe_edit(callable, context)` wrapper. |
| `src/lyra/outbound/emitter.py` | 65 / `formatter.py` 24 | Medium | DRY violation: `_default_no_op_edit_reasoning` / `_default_no_op_edit_tool_recap` in `emitter.py` are identical to `default_no_op_edit_reasoning` / `default_no_op_edit_tool_recap` in `formatter.py` (~15 duplicated lines) | Move both no-ops to a shared module (e.g. `outbound/_defaults.py`) and import from there; `PlatformCallbacks` can reference the shared versions. |
| `src/lyra/outbound/emitter.py` | 179-476 | Low | Repetitive pattern: 9 identical `self._handler.guard(lambda ...)` calls followed by `if isinstance(result, Err): pass` | Introduce a private `_guard_ignore_err(call: Awaitable[T], context: str) -> T | None` helper that logs and swallows errors internally, collapsing each call site to 1 line. |
| `src/lyra/outbound/emitter.py` | 534 | Medium | Deferred bottom-of-file imports (`StreamState`, `ToolRecapAccumulator`, `format_recap_lines`, `STREAMING_EDIT_INTERVAL`) to break circular imports | Relocate `StreamState` and recap helpers out of `adapters.shared` into `outbound/` (already planned per #1279 S7 comment) so imports can move to the top. |
| `src/lyra/outbound/emitter.py` | 84 | Low | `PlatformCallbacks` dataclass has 14 fields — mixing send, edit, chunking, typing, reasoning, tool recap, i18n | Coalesce into 3-4 nested dataclasses (`SendCallbacks`, `EditCallbacks`, `TypingCallbacks`) or flatten into the planned stage interfaces per #1279 Phase 7. |
| `src/lyra/streaming/event_emitter.py` | 83 / `state_machine.py` 72 | Low | Structural duplication: `snapshot = list(container); container.clear(); yield from snapshot` appears in both `EventEmitter.flush()` and `StateMachine.drain()` | Extract a small shared helper `def _snapshot_then_clear(container: list[T] | deque[T]) -> Iterable[T]: ...` in a `streaming/_helpers.py` module, or leave as-is given the simplicity and cross-module boundary. |

---

### Metrics

| Metric | Count | Notes |
|--------|-------|-------|
| Files in scope | 9 | 5 outbound + 4 streaming |
| Total lines | ~952 | Excluding blank/docstring lines |
| Exempted >300 lines | 1 | `emitter.py` at 546 lines — tracked in `tools/file_exemptions.txt` (#1279 Phase 7 follow-up) |
| Functions >50 lines | 2 | `_run_event_loop` (67), `_deliver_final` (58) |
| Functions 30-50 lines | 2 | `_on_text_v2` (45), `run` (40) |
| God classes (>5 responsibilities) | 1 | `OutboundEmitter` (9 responsibilities) |
| Cross-file DRY violations (>3 lines) | 1 pair | No-op default callbacks (2 functions, 2 files) |
| `noqa: C901` cognitive-complexity waivers | 1 | `_run_event_loop` |
| Intra-file repetitive guard() stanzas | 9 | All in `emitter.py` |
| Deferred E402 imports (circular-import smell) | 4 | `StreamState`, `ToolRecapAccumulator`, `format_recap_lines`, `STREAMING_EDIT_INTERVAL` at bottom of `emitter.py` |

---

### Recommendations (prioritized)

1. **Refactor `OutboundEmitter` into stage-aligned collaborators** (#1279 Phase 7). The god-class status is the highest-impact smell. A `DeliveryEngine` + `RecapAccumulator` + `TypingCoordinator` split would bring each class to 2-3 responsibilities and make the 67-line dispatch ladder testable in isolation.

2. **Extract an `EventRouter` for `_run_event_loop`**. Replace the 4-branch `isinstance` ladder with a `dict[type[RenderEvent], Callable]` or method-dispatch table. This removes the `noqa: C901`, shrinks the function to ~15 lines, and eliminates the risk of forgetting to handle a new event type (the `assert_never` safety net becomes redundant).

3. **Deduplicate no-op default callbacks**. Move `default_no_op_edit_reasoning` and `default_no_op_edit_tool_recap` to a shared `outbound/_defaults.py` module. `PlatformCallbacks` and `OutboundFormatter` both import from there. Immediate win: ~15 lines deleted, zero behavioural change.

4. **Collapse repetitive `guard(lambda ...)` pattern into `_guard_ignore_err`**. Add a 3-line helper inside `OutboundEmitter`; replace all 9 call sites. Saves ~40 lines, improves readability, and reduces the chance of inconsistent error-handling if the pattern ever needs to change.

5. **Relocate `StreamState` and recap helpers out of `adapters.shared`**. Move `StreamState`, `IntermediateTextState`, `ToolRecapAccumulator`, and `format_recap_lines` into `lyra.outbound` or `lyra.streaming`. This resolves the deferred-import workaround, removes the circular-import smell, and lets `emitter.py` use normal top-of-file imports. Aligns with the existing #1279 S7 plan.
