---
title: LLM Streaming & Agents Runtime — factory
description: Current truth for the LLM/streaming pipeline — LlmEvent→StreamProcessor→RenderEvent, driver stack, outbound emitter, and the RenderEvent NATS wire crossing.
---

# LLM Streaming & Agents Runtime — factory

> Status: LIVING — current truth for LLM/streaming pipeline decisions, wire crossing included.
> Last updated: 2026-07-02.
> Source ADRs: 099 (typed pipeline — absorbs 028/032/070), 100 (wire protocol — absorbs 036/072).

## Scope

The streaming pipeline converts raw LLM output into typed render events for channel adapters,
and carries those events across the NATS hub↔adapter boundary. It spans `LlmEvent`
(LLM boundary) → `StreamProcessor` (domain) → `RenderEvent` (adapter-facing) →
`NatsRenderEventCodec` (wire). This page is the single SSoT for streaming: canonical types,
driver stack, outbound emission, chunk protocol, codec registry, and schema versioning.
Excludes NATS plane/subject catalogues and transport primitives (see `messaging.md`),
audio-specific streaming (see `adapters.md`), and `ProcessorRegistry` worker plumbing
(see `workers-tooling.md`).

## Current state

### Streaming path shape

→ ADR-028 (amended)

factory uses **parallel streaming methods** (Option A): dedicated `send_and_read_stream()`,
`CliPool.send_streaming()`, `ClaudeCliDriver.stream()`, and `SimpleAgent.process()` alongside
their non-streaming counterparts. The non-streaming path is untouched. Streaming is opt-in
per agent via `ModelConfig.streaming` (default off).

The SDK driver and the `anthropic-sdk` backend were removed in #666; the CLI path is the
sole in-process driver. The lock lifecycle (acquire → write stdin → release → return
iterator) and downstream routing through `OutboundDispatcher` remain as designed.

Three edge-case contracts from ADR-028 remain in force: cancel-in-flight `aclose()` +
pool reset (EC-1), session ID propagation on exhausted iterator (EC-2), and the
`--include-partial-messages` spawn-time toggle requiring process respawn on `ModelConfig`
change (EC-4). EC-3 (discard `input_json_delta`) is superseded — the fragments are surfaced
as `ToolUseDeltaLlmEvent` / `ToolCallArgsRenderEvent` (see RenderEvent v2 below).

### Hexagonal pipeline

→ ADR-032 (amended)

The pipeline is a strict hexagonal layering enforced by import-linter (contract:
`transport ← streaming ← core ← llm/nats ← infrastructure ← adapters ← bootstrap`):

```
LlmEvent          (factory.core.ports.llm_types, re-exported via factory.core.messaging.events)
    ↓
StreamProcessor   (factory.core.processors.stream_processor — domain, no framework/network deps)
    ↓
RenderEvent       (factory.core.messaging.render_events — adapter-facing, no platform types)
```

`LlmEvent` owns per-token/per-chunk semantics from the LLM source. `StreamProcessor` handles
aggregation, throttle, and per-tool `show` flags. `RenderEvent` is what adapters (Telegram,
Discord) consume — no platform types cross the boundary. All event dataclasses are
`frozen=True`; callers must never mutate event objects after construction.

The `LlmEvent` union covers text (`TextLlmEvent`), extended thinking (`ThinkingLlmEvent`),
the tool-use lifecycle (`ToolUseLlmEvent`, `ToolUseDeltaLlmEvent`, `ToolUseEndLlmEvent`,
`ToolResultLlmEvent`), and the terminal `ResultLlmEvent`. The union is defined in
`factory.core.ports.llm_types` (stdlib + pydantic only — the ports isolation contract);
`factory.core.messaging.events` re-exports it so `llm → core` stays unidirectional.

**Error envelope on `ResultLlmEvent`** — `error_text` and `worker_error` are populated
together by drivers/parsers on terminal failure events: `worker_error` carries the structured
taxonomy (`domain`, `code`, `message`, `retryable`); `error_text` is an in-process
presentation cache of `worker_error.message` consumed directly by adapter renderers.
`error_text` is **not a wire-contract field** — only `worker_error` exists on NATS contracts.
Neither field is a shim for the other (ADR-066 archive Status, issue #1029).

### LLM port & driver stack

The domain port lives in `factory.core.ports.llm` (`factory.llm.base` is a
backward-compatibility re-export shim). The protocol is split:

- `LlmProvider` — base protocol: `complete()` → `LlmResult`, `is_alive()`, `capabilities`.
- `StreamingLlmProvider` — extends the base with `stream()` → `AsyncIterator[LlmEvent]`.
  Non-streaming backends satisfy the base only; `SimpleAgent` gates on
  `getattr(provider, "stream", None)` so they are never streamed.
- `SessionAware` / `WorkspaceAware` — capability protocols (session link/reset/resume,
  `switch_cwd`) checked via `isinstance`, so drivers without those surfaces are not polluted.

`ModelConfig.backend` selects the driver; valid backends are `claude-cli`, `nats`, and
`omp-rpc` (pydantic-validated at construction):

| Backend | Driver | Streaming | Decoration (bootstrap) |
|---|---|---|---|
| `claude-cli` | `ClaudeCliDriver` (in-process `CliPool` subprocess) or `ClaudeRpcDriver` (job dispatch over NATS: `factory.jobs.claude` + `factory.job.<job_id>.progress` / `factory.job.<job_id>.result`) | yes | `CircuitBreakerDecorator` |
| `nats` | `LlmClient` (composed from the transport layer's WorkerPoolClient + codec → llmCLI worker) | yes (inbox stream) | `RetryDecorator` |
| `omp-rpc` | `OmpRpcDriver` | no (base protocol only) | bare — the driver owns its own timeout |

The stack is assembled in `bootstrap/` (provider registry builders), never in `llm/`. The
formerly-documented NatsLlmDriver / CliNatsDriver classes were dissolved into `LlmClient`
during the transport-layer refactor (Epic #1277); a SmartRouting decorator layer never
shipped.

**Decorators cover streaming too** (this supersedes the earlier "streaming bypasses
decorators" rule): `RetryDecorator.stream()` retries connect-time failures only — a raise or
a terminal error event *before any non-terminal event* — with exponential backoff; once live
data has been forwarded it never retries (streams cannot be replayed), and cancellation is
never retried. `CircuitBreakerDecorator.stream()` yields exactly one
`ResultLlmEvent(is_error=True)` when the circuit is open, and records success/failure on the
terminal event; cancellation mid-probe releases the probe slot instead of recording failure.

### StreamProcessor internals

`StreamProcessor` composes per-concern handlers (`StreamTextHandler`, `StreamToolHandler`,
`StreamCloseHandler`) with the stage-axis streaming primitives from `factory.streaming`:
a duck-typed `Parser` protocol, per-consumer `StateMachine` instances (open blocks, pending,
dedup), and an `EventEmitter` that is the only place exception data is translated into
terminal events (`SanitizedError` boundary — `type(exc).__name__`, never `str(exc)`).
`CliStreamingParser` (NDJSON → `LlmEvent`) composes the same primitives on the driver side.

Tool display is config-driven via `ToolDisplayConfig` (`[tool_display]` section: name/group
thresholds, bash truncation, `throttle_ms`, per-tool `show` flags — see
`docs/CONFIGURATION.md`). `configure_tool_display()` on the outbound base adapter is the
single permitted write point. Tool-result content is sanitized before emit
(`_sanitize_tool_result_content`); tool-args fragments are forwarded as
`ToolCallArgsRenderEvent` deltas rather than silently discarded.

### RenderEvent v2 (AG-UI)

→ ADR-070

ADR-070 back-ported four AG-UI event families into `core/messaging/render_events.py`.
AG-UI is **not** adopted as a wire format.

| Family | Events |
|---|---|
| Run lifecycle | `RunStartedRenderEvent`, `RunFinishedRenderEvent`, `RunErrorRenderEvent` |
| Text triplet | `TextStartRenderEvent`, `TextDeltaRenderEvent`, `TextEndRenderEvent` (+ `TextChunkRenderEvent`, defined but not yet emitted) |
| ToolCall lifecycle | `ToolCallStartRenderEvent`, `ToolCallArgsRenderEvent`, `ToolCallEndRenderEvent`, `ToolCallResultRenderEvent` |
| Reasoning typed | `ReasoningStartRenderEvent`, `ReasoningDeltaRenderEvent`, `ReasoningEndRenderEvent` |

The v1 two-event model and its dual-emit migration ladder are gone: the legacy wire types
(`text`, `tool_summary`) were removed from the codec and all consumer paths migrated to v2
(issue #1192). `message_id` is per text block (not per turn), supporting interleaved
text→tool→text. `run_id` mirrors the per-turn `trace_id` (reuses `TraceMiddleware`'s
identifier). `RunErrorRenderEvent.code` is drawn from the canonical
`roxabi_contracts.errors.KNOWN_CODES` registry (e.g. `stream.error`, `cli.auth`,
`llm.rate_limit`) — see [error-codes.md](../../packages/roxabi-contracts/docs/error-codes.md);
`None` survives only as the pre-taxonomy sentinel. Every event carries a `SCHEMA_VERSION_*`
constant (ADR-049 discipline) and is `frozen=True`.

Deferred (gated on a concrete consumer): StateSnapshot/Delta, ActivitySnapshot/Delta,
AG-UI HTTP/SSE adapter.

### Outbound delivery

`OutboundAdapterBase.send_streaming()` is concrete on the base class and delegates to a
per-platform `_make_emitter()`, which composes an `OutboundEmitter` (renamed from the legacy
StreamingSession) from three stages: `OutboundFormatter` (pure formatting + platform I/O
mechanics), `ThrottleCapability` (typing indicator + edit interval), and
`OutboundErrorHandler` (single broad-catch site + stream-error classification). The old
PlatformCallbacks bundle was dissolved into these stages. The emitter owns the
placeholder → throttled edits → final delivery lifecycle and the typing-indicator tail;
platform differences live only in formatters and typing wrappers.

### Streaming chunk protocol

→ ADR-036 (amended)

`RenderEvent` iterators cross the NATS boundary as discrete JSON chunks on the persistent
outbound subject `factory.outbound.<platform>.<bot_id>` (not ephemeral reply inboxes).
Each chunk carries:

- `stream_id` — echoes the inbound message id for request-response turns (adapter-minted);
  hub-minted for proactive sends. Copied onto every chunk of the stream.
- `seq` — 0-indexed, monotonically increasing per stream.
- `event_type` — v2 taxonomy string (see codec below) or a synthetic sentinel.
- `payload` — serialized event fields, discriminated by `event_type`.
- `done` — true on terminal chunks (`RunFinishedRenderEvent`, `RunErrorRenderEvent`,
  sentinels).

The outer chunk shape is intentionally unversioned — only inner payloads carry
`schema_version` (see Schema versioning below).

Hub side, `NatsChannelProxy.send_streaming()` encodes each event through the codec, always
publishes a terminal `stream_end` sentinel so the adapter drain exits cleanly even on an
empty iterator, and runs a keepalive loop publishing `stream_keepalive` sentinels during
idle periods (long tool calls) so the adapter's per-chunk timeout does not trip (#687).
A `stream_error` sentinel is emitted by the transport on mid-stream hub crash (#538).

Adapter side, `decode_stream_events` (consumed by `NatsOutboundListener`) enforces the
ordering contract with **no reorder buffer**: an out-of-order `seq` is logged as a warning;
a chunk missing `event_type` aborts the stream; a stalled stream is bounded by a per-chunk
idle timeout raising `StreamChunkTimeout`; a hub-liveness poll between chunks raises
`HubUnavailableError` without waiting for the full backstop. ADR-036's original
fatal-abort-on-gap rule (STREAM_ABORTED) is thus realized as warn-on-gap + bounded-timeout
abort. ADR-036's v1 single-text-chunk-per-turn shape is historical — superseded by the v2
incremental taxonomy below. Terminated streams are tombstoned (FIFO + TTL eviction) so late
chunks are dropped silently.

### NATS render-event codec

→ ADR-072 (amended)

`NatsRenderEventCodec` (`src/factory/nats/render_event_codec.py`) encodes and decodes
`RenderEvent` instances to/from the wire chunk format. Both `NatsChannelProxy` (hub,
encodes) and `NatsOutboundListener` (adapter, decodes) import this single class.

The registry (`render_event_codec_registry.py`) maps every `RenderEvent` subtype to a
`CodecBranch` (encode/decode functions, schema version, terminal flag), with an inverse
string index for O(1) decode lookup. Both maps are built once in `__init__` and are
immutable thereafter. `decode()` checks the sentinel sets (`_SYNTHETIC_TERMINALS` =
`stream_end` / `stream_error`; non-terminal `stream_keepalive`) *before* registry lookup, so
sentinels never surface an "unknown event_type" warning and are never yielded as render
events.

**Registered event families (v2 only):**

| Family | Event types |
|---|---|
| Text triplet | `text_start`, `text_delta`, `text_end` |
| Text chunk (compat, not yet emitted) | `text_chunk` |
| Run lifecycle | `run_started`, `run_finished` (terminal), `run_error` (terminal) |
| ToolCall lifecycle | `tool_call_start`, `tool_call_args`, `tool_call_end`, `tool_call_result` |
| Reasoning lifecycle | `reasoning_start`, `reasoning_delta`, `reasoning_end` |

Adding a new `RenderEvent` subtype requires a single registry insertion; a codec
completeness test fails loudly at CI if the registry is missing a union member.

### Schema versioning

Every `RenderEvent` payload carries `schema_version: int` guarded by a module-level
`SCHEMA_VERSION_*` constant in `src/factory/core/messaging/render_events.py`. The receiver
policy is centralized in `check_schema_version` (roxabi-nats): accept `schema_version <=`
expected; drop strictly-greater with a rate-limited ERROR log (one per envelope name per
interval) plus an always-incremented drop counter; malformed values are dropped. Legacy
payloads without `schema_version` default to version 1.

Bumping a version: bump the constant, update the field default, co-deploy hub + adapters
simultaneously (rolling deploys across a bump produce loud ERRORs on still-old receivers),
then verify with a `SCHEMA_VERSION_` grep over `src/factory/core/messaging/`.
→ `ARCHITECTURE.md` (Schema versioning section) for the full receiver policy.

## Key invariants

- Streaming is opt-in per agent (`ModelConfig.streaming`); the non-streaming path is never
  modified by streaming changes.
- `factory.core.ports.llm_types` imports stdlib + pydantic only; `StreamProcessor` has no
  framework or network imports; adapters import `RenderEvent` from
  `factory.core.messaging.render_events` only — enforced by import-linter.
- `stream()` belongs to `StreamingLlmProvider`, not the base `LlmProvider`; consumers gate
  with `getattr(provider, "stream", None)` — never assume every provider streams.
- Streaming decorators must preserve replay-safety: never retry after the first non-terminal
  event has been forwarded; never retry cancellation.
- Cancel-in-flight must trigger `aclose()` on the streaming iterator, which resets the
  subprocess pool entry to prevent pipe-buffer stall.
- The iterator returned by `send_and_read_stream()` exposes a `session_id` attribute
  (set once parsed from the stream; `None` if cancelled before the terminal event).
- `--include-partial-messages` is a spawn-time flag; a `ModelConfig` change (including
  toggling `streaming`) triggers an automatic process respawn via the existing mismatch check.
- Exception data never crosses the streaming boundary raw: `EventEmitter` and
  `RunErrorRenderEvent` carry `type(exc).__name__` / driver-curated text only — never
  `str(exc)` (bus subscribers can read these events).
- Every `RenderEvent` subtype carries its own `SCHEMA_VERSION_*` constant; new subtypes
  require one codec-registry insertion (completeness is CI-checked).
- `stream_id` is minted by the sender and copied onto every chunk; `seq` gaps are never
  reordered — warn and rely on the bounded per-chunk timeout. Sentinel `event_type`s are
  transport-level and must never be yielded as render events.
- Every stream must end with a terminal chunk (`run_finished`, `run_error`, or a sentinel);
  the hub always publishes `stream_end` after the event iterator completes.
- Slices that introduce new `RenderEvent` types require co-deploying `factory-hub` +
  `factory-telegram` + `factory-discord`. Receivers drop unknown/greater schema versions with
  a rate-limited ERROR log; they do not raise.
- `factory-clipool` is excluded from the RenderEvent co-deploy gate (it is an `LlmEvent`
  producer only, no `render_events` import). Slices that change `LlmEvent` shape include it.

## Open questions / known gaps

- `TextChunkRenderEvent` is defined but not emitted by `StreamProcessor`; its shape is
  revisable until the first concrete consumer (TTS tee, AG-UI bridge, rich-rendering
  adapter) wires up.
- `StreamProcessor` does not yet satisfy the structural `Parser` protocol (it exposes
  `process`, not `feed`/`finalize`/`is_done`); conformance is deferred.
- `run_id` == `trace_id` assumption: revisit only if interrupt+resume becomes a concrete
  requirement (`RunFinishedRenderEvent` reserves `outcome="interrupt"`).
- `OmpRpcDriver` satisfies the base protocol only; omp streaming (and runtime selection
  across backends) is tracked in the omp follow-up issues (#1812, #1813).

## See also

- NATS planes, subjects, transport layer → `messaging.md` (ADR-001/002/035/065/076)
- Cross-project contract schemas → `contracts.md` (ADR-045, ADR-049)
- Audio streaming → `adapters.md` (ADR-015, ADR-023)
- Processor registry → `workers-tooling.md` (ADR-031)
- Error-code taxonomy → [error-codes.md](../../packages/roxabi-contracts/docs/error-codes.md)

## ADR archive

| ADR | Title | Status |
|-----|-------|--------|
| 099 | Typed LLM streaming pipeline — LlmEvent → StreamProcessor → RenderEvent (v1→v2) | Accepted — 2026-07-02; consolidation record, absorbs 028/032/070 |
| 100 | RenderEvent NATS wire protocol — seq-framed envelope + codec registry | Accepted — 2026-07-02; consolidation record, absorbs 036/072 |
| 028 | Token-level streaming path shape | Superseded by ADR-099 — archived (`adr/archive/`) |
| 032 | LlmEvent → StreamProcessor → RenderEvent hexagonal pipeline | Superseded by ADR-099 — archived (`adr/archive/`) |
| 036 | RenderEvent streaming chunk protocol over NATS | Superseded by ADR-100 — archived (`adr/archive/`) |
| 070 | RenderEvent v2 — selective AG-UI modeling | Superseded by ADR-099 — archived (`adr/archive/`) |
| 072 | Codec registry pattern (v2 RenderEvent dispatch) | Superseded by ADR-100 — archived (`adr/archive/`) |
