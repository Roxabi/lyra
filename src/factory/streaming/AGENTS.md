# AGENTS.md — src/factory/streaming/

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

- **Per-consumer StateMachine instances** — no cross-consumer key sharing. Mixing keys across instances is a bug.

- **SanitizedError boundary** — `EventEmitter` is the ONLY place in this package where exception
  data is translated to terminal events. Never construct `str(exc)` or `f"...{exc}"` here. Use
  `type(exc).__name__` for class-name surfacing (deterministic, no PII leakage). See
  `src/factory/transport/AGENTS.md` and `docs/architecture/contracts.md`.

- **`error_translator` is per-consumer** — every `EventEmitter` instance is constructed with a
  translator lambda specific to its `OutT` (e.g.
  `lambda err: RunErrorRenderEvent(message=err.message)` for `StreamProcessor`;
  `lambda err: ResultLlmEvent(is_error=True, worker_error=...)` for `CliStreamingParser`).

- **Ordering rule** — `EventEmitter.flush()` precedes `emit_terminal()` when both are needed
  (preserves the streaming ordering invariant: any open block end is emitted before the terminal
  `RunError`/error envelope). Currently neither consumer uses `flush()` because they yield events
  directly; documented for future consumers.

- **Protocol is structural** — `Parser` is `@runtime_checkable`. `CliStreamingParser`
  satisfies it at the method-name level: it exposes `feed` (alias of `parse_line`),
  `finalize`, and `is_done` — therefore `isinstance(CliStreamingParser(), Parser)` returns
  `True`. `StreamProcessor` still exposes only `process` (legacy public API) and does NOT
  yet satisfy the Protocol — `isinstance(stream_processor_instance, Parser)` returns `False`.
  Conformance for `StreamProcessor` is deferred; when its aliases land, isinstance-conformance
  tests should be added to `tests/streaming/test_parser_protocol.py`.

- **Stage purity** — streaming primitives + public surfaces enforced by generalized
  `stage-purity` + `per-part-stage-helpers-isolation` contracts (kernel-like: only
  transport public + contracts; no I/O/outer/stages per engineering-standards →
  `docs/architecture/job-model.md`; consolidated from low-consensus proposals to avoid
  patchwork).

## Out of scope

- **Event vocabulary** — `LlmEvent`, `RenderEvent`, `WorkerError`, etc. live in
  `src/factory/core/messaging/events.py` and `render_events.py`. Do not redefine them here.
- **Ordering invariants on the streaming bus** — the comment anchor lives in
  `src/factory/outbound/_streaming_state.py` ~lines 109–142 (`is_error_pending`
  field). Stream-bus ordering is enforced by the adapter layer, not this package.
- **Adding new parsers (NATS-stream, SSE, …)** — Phase 5 enables this; impls land in separate
  issues. `src/factory/streaming/` provides the primitives; consumer files
  (`core/cli/cli_streaming_parser.py`, `core/processors/stream_processor.py`) demonstrate the
  composition pattern.

## See also

- `artifacts/specs/1282-phase-5-streaming-spec.mdx` — Phase 5 spec (acceptance criteria, breadboard).
- `artifacts/analyses/1277-stage-axis-refactor-strategy.mdx` — parent epic SSoT.
- `src/factory/transport/AGENTS.md` — Phase 1 (`SanitizedError`, `Result[T, E]`) — upstream of this package.
