---
title: LLM Streaming & Agents Runtime — Lyra
description: Current truth for LLM/streaming pipeline decisions covering the LlmEvent→StreamProcessor→RenderEvent path.
---

# LLM Streaming & Agents Runtime — Lyra

> Status: LIVING — current truth for LLM/streaming pipeline decisions.
> Last updated: 2026-05-09.
> Source ADRs: 028 (amended), 032 (amended), 070.

## Scope

The streaming pipeline converts raw LLM output into typed render events for channel adapters.
It spans `LlmEvent` (LLM boundary) → `StreamProcessor` (domain) → `RenderEvent` (adapter-facing).
Excludes NATS wire format (see `messaging.md`), audio-specific streaming (see `adapters.md`),
and the `ProcessorRegistry` worker plumbing (see `workers-tooling.md`).

## Current state

### Streaming path shape

→ ADR-028 (amended)

Lyra uses **parallel streaming methods** (Option A): dedicated `send_and_read_stream()`,
`CliPool.send_streaming()`, `ClaudeCliDriver.stream()`, and `SimpleAgent.process()` alongside
their non-streaming counterparts. The non-streaming path is untouched. Streaming is opt-in
per agent via `ModelConfig.streaming: bool = False`.

The `AnthropicSdkDriver` and the `anthropic-sdk` backend were removed in #666; Lyra is
CLI-only. The lock lifecycle (acquire → write stdin → release → return generator) and the
downstream routing through `OutboundDispatcher` remain as designed.

Three edge-case contracts from ADR-028 remain in force: cancel-in-flight `aclose`/reset (EC-1),
session ID propagation on exhausted generator (EC-2), and `--include-partial-messages` spawn-time
toggle requiring process respawn on `ModelConfig` change (EC-4). EC-3 (discard `input_json_delta`)
is superseded — see RenderEvent v2 below.

### Hexagonal streaming pipeline

→ ADR-032 (amended)

The pipeline is a strict hexagonal layering enforced by import-linter:

```
LlmEvent          (lyra.core.messaging.events)          — port, provider-agnostic
    ↓
StreamProcessor   (lyra.core.stream_processor) — domain, config-driven, no network deps
    ↓
RenderEvent       (lyra.core.messaging.render_events) — adapter-facing, no platform types
```

`LlmEvent` owns per-token/per-chunk semantics from the LLM source. `StreamProcessor` handles
aggregation, throttle, and per-tool `show` flags. `RenderEvent` is what adapters (Telegram,
Discord) consume — no platform types cross the boundary.

`stream()` is duck-typed via `hasattr()` (not a required protocol member). The streaming path
bypasses `RetryDecorator` and `CircuitBreakerDecorator` — those wrappers cover `complete()` only.
`SimpleAgent.process()` returns `Response | AsyncIterator[RenderEvent]` (not `str`).

### RenderEvent v2 (AG-UI)

→ ADR-070

ADR-070 extends the v1 two-event model (`TextDeltaRenderEvent`, `ToolCallResultRenderEvent`) by
back-porting four AG-UI event families into `core/messaging/render_events.py`. AG-UI is **not**
adopted as a wire format.

| Family | New events | Replaces (Slice 5) |
|---|---|---|
| Run lifecycle | `RunStarted/Finished/ErrorRenderEvent` | — (additive) |
| Text triplet | `TextStart/Delta/End/ChunkRenderEvent` | `TextDeltaRenderEvent` |
| ToolCall split | `ToolCallStart/Args/End/ResultRenderEvent` | `ToolCallResultRenderEvent` |
| Reasoning typed | `ReasoningStart/Delta/EndRenderEvent` | split from `TextDeltaRenderEvent` |

`ToolCallArgsRenderEvent` (Slice 3 / #1100) supersedes ADR-028 EC-3 by surfacing
`input_json_delta` fragments instead of silently discarding them — with content-sanitization
(NUL-byte stripping, path normalization, length caps) applied in `StreamProcessor` before emit.

Deferred (gated on a concrete consumer): `StateSnapshot/Delta`, `ActivitySnapshot/Delta`,
AG-UI HTTP/SSE adapter.

During Slices 2–4, `StreamingSession.dispatch()` dual-emits v1 and v2 events via an
`isinstance` ladder. Slice 5 (#1102) deletes v1 types and raises the schema floor.

Every new event carries a `SCHEMA_VERSION_*` constant (ADR-049 discipline). `runId == trace_id`
(reuses `TraceMiddleware`'s existing identifier). All new events are `frozen=True`.

## Key invariants

- Streaming is opt-in per agent (`ModelConfig.streaming`); the non-streaming path is never
  modified by streaming changes.
- `LlmEvent` must import nothing outside `lyra.llm`. `StreamProcessor` imports only
  `lyra.core.messaging.events` and `lyra.core.messaging.render_events`. Adapters import `RenderEvent` from
  `lyra.core.messaging.render_events` only — enforced by import-linter.
- `stream()` is always duck-typed (`hasattr`), never a required protocol member.
- The streaming path bypasses circuit-breaker protection — document this explicitly in any
  new streaming driver.
- Cancel-in-flight must trigger `aclose()` + `CliPool.reset(pool_id)` to prevent subprocess
  pipe buffer stall.
- The async generator returned by `send_and_read_stream()` must expose a `session_id`
  attribute (set on `result` event; `None` if cancelled before `result`).
- `--include-partial-messages` is a spawn-time flag; `ModelConfig` change (including toggling
  `streaming`) triggers an automatic process respawn via the existing mismatch check.
- Every `RenderEvent` subtype carries its own `SCHEMA_VERSION_*` constant. → See `messaging.md` (Schema versioning) and `ARCHITECTURE.md` (Schema versioning section) for the bump procedure and receiver policy.
- **Error envelope on `ResultLlmEvent`** — `error_text` and `worker_error` are populated together by drivers/parsers on terminal failure events: `worker_error` carries the structured taxonomy (`domain`, `code`, `message`, `retryable`); `error_text` is an in-process presentation cache of `worker_error.message` consumed directly by adapter renderers. `error_text` is **not a wire-contract field** — only `worker_error` exists on NATS contracts. Neither field is a shim for the other (ADR-066 archive Status, issue #1029).
- `lyra-clipool` is excluded from the RenderEvent co-deploy gate (it is an `LlmEvent`
  producer only, no `render_events` import). Slices that change `LlmEvent` shape include it.
- Slices that introduce new `RenderEvent` types require co-deploying `lyra-hub` +
  `lyra-telegram` + `lyra-discord`. Receivers drop unknown schema versions with a rate-limited
  ERROR log (one per envelope name per 60s); they do not raise.

## Open questions / known gaps

- ADR-028 EC-3 is superseded by ADR-070 Slice 3 (#1100) — verify no callers still filter
  `input_json_delta` at the CLI protocol layer after #1100 merges.
- Slice 3 (#1100) requires a sub-analysis artifact (`artifacts/analyses/1100-toolcall-cli-instrumentation.mdx`)
  to verify the Claude CLI `--include-partial-messages` `InputJsonDelta` JSON shape before
  the spec runs.
- v1/v2 coexistence in `StreamingSession.dispatch()` (Slices 2–4) doubles wire traffic
  temporarily; Slice 5 (#1102) is the cleanup gate.
- `runId == trace_id` assumption: revisit only if interrupt+resume becomes a concrete
  requirement.

## See also

- NATS wire format → `messaging.md` (ADR-036 NatsChunkEnvelope)
- Audio streaming → `adapters.md` (ADR-015, ADR-023)
- Processor registry → `workers-tooling.md` (ADR-031)

## ADR archive

| ADR | Title | Status |
|-----|-------|--------|
| 028 | Token-level streaming path shape | Amended — SDK path removed #666; EC-3 superseded by ADR-070 Slice 3 (#1100); EC-1/2/4 remain in force |
| 032 | LlmEvent → StreamProcessor → RenderEvent hexagonal pipeline | Amended — SDK path removed #666; hexagonal contract normative; extended by ADR-070 |
| 070 | RenderEvent v2 — selective AG-UI modeling | Accepted |
