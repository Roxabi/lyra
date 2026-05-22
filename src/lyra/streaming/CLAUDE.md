# CLAUDE.md — src/lyra/streaming/

## Role

Stage-axis streaming primitives: per-source parsers (CLI today; NATS-stream, SSE, etc. tomorrow)
compose three building blocks instead of re-implementing state tracking + error translation per
source. Phase 5 of the stage-axis decomposition (#1277/#1282).

## Composition (not inheritance)

Three primitives, all composed (not inherited):

| Primitive | Role | Owner |
|---|---|---|
| `Parser[InT, OutT]` Protocol | Duck-typed interface: `feed(item)` → `finalize()` → `is_done()` | Consumer impl (`CliStreamingParser`, `StreamProcessor`) |
| `StateMachine[K, V]` | Per-consumer state tracking: `open_blocks`, `pending`, `dedup_seen` | Parser instances (1 or more per consumer) |
| `EventEmitter[OutT]` | Result-aware terminal translator: `SanitizedError` → `OutT` | Parser instances (1 per consumer) |

**Composition rule:** `Parser` owns its `StateMachine` instances and its `EventEmitter`. No shared/global instance.

## Invariants

- **Per-consumer StateMachine instances** — no cross-consumer key sharing. `CliStreamingParser`
  has 3 instances (tool dedup, open tool blocks, open thinking blocks); `StreamProcessor` has 3
  (text, reasoning, tool). Mixing keys across instances is a bug.

- **SanitizedError boundary** — `EventEmitter` is the ONLY place in this package where exception
  data is translated to terminal events. Never construct `str(exc)` or `f"...{exc}"` here. Use
  `type(exc).__name__` for class-name surfacing (deterministic, no PII leakage). See
  `src/lyra/transport/CLAUDE.md` and ADR-045/049.

- **`error_translator` is per-consumer** — every `EventEmitter` instance is constructed with a
  translator lambda specific to its `OutT` (e.g.
  `lambda err: RunErrorRenderEvent(message=err.message)` for `StreamProcessor`;
  `lambda err: ResultLlmEvent(is_error=True, worker_error=...)` for `CliStreamingParser`).

- **Ordering rule** — `EventEmitter.flush()` precedes `emit_terminal()` when both are needed
  (preserves the streaming ordering invariant: any open block end is emitted before the terminal
  `RunError`/error envelope). Currently neither consumer uses `flush()` because they yield events
  directly; documented for future consumers.

- **Protocol is structural** — `Parser` is `@runtime_checkable` but consumers MAY satisfy it via
  duck typing. Current `parse_line` / `process` method names do not match `feed` exactly — see
  spec #1282 for the migration path. `isinstance` checks against the Protocol are tolerated only
  for Slice ≥2 RED-GATE placeholders.

## Out of scope

- **Event vocabulary** — `LlmEvent`, `RenderEvent`, `WorkerError`, etc. live in
  `src/lyra/core/messaging/events.py` and `render_events.py`. Do not redefine them here.
- **Ordering invariants on the streaming bus** — the comment anchor lives in
  `src/lyra/adapters/shared/_shared_streaming_state.py` ~lines 150–155 (`is_error_pending`
  field). Stream-bus ordering is enforced by the adapter layer, not this package.
- **Adding new parsers (NATS-stream, SSE, …)** — Phase 5 enables this; impls land in separate
  issues. `src/lyra/streaming/` provides the primitives; consumer files
  (`core/cli/cli_streaming_parser.py`, `core/processors/stream_processor.py`) demonstrate the
  composition pattern.

## File map

```
streaming/
├── __init__.py          # Re-exports: Parser, EventEmitter
├── parser.py            # Parser[InT, OutT] Protocol — duck-typed, @runtime_checkable
├── state_machine.py     # StateMachine[K, V] — open/close/mark_seen/drain
└── event_emitter.py     # EventEmitter[OutT] — SanitizedError → OutT translator
```

All files ≤300 LOC, package ≤12 files (quality gates).

## See also

- `artifacts/specs/1282-phase-5-streaming-spec.mdx` — Phase 5 spec (acceptance criteria, breadboard).
- `artifacts/analyses/1277-stage-axis-refactor-strategy.mdx` — parent epic SSoT.
- `src/lyra/transport/CLAUDE.md` — Phase 1 (`SanitizedError`, `Result[T, E]`) — upstream of this package.
