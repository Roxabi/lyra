# src/factory/outbound/ — Outbound Stage Composition

## Purpose

Per-platform-agnostic stage composition for outbound message rendering. Consumed by
`OutboundAdapterBase.send_streaming` via `_make_emitter` on platform adapters (Telegram,
Discord). The per-target outbound files (`telegram_outbound.py`, `discord_outbound.py`)
delegate to `OutboundEmitter` (composition); state helpers live in `_streaming_state.py`.

## Layer contract

```
OutboundEmitter (generic; composes stages)
     ↓ composes
[OutboundFormatter Protocol, ThrottleCapability Protocol, OutboundErrorHandler]
     ↓ used by
Per-platform impls (telegram_formatter.py, discord_formatter.py, *TypingIndicator)
```

`OutboundEmitter` is the only assembly point — it owns the placeholder→edits→delivery
lifecycle. Platform adapters construct it in `_make_emitter`; they never call
formatter/throttle/error_handler methods directly.

## Key invariants

- **No second circuit-breaker layer here.** CB lives in `OutboundDispatcher` (hub-side). Outbound stages MUST NOT add a second CB.
- **SanitizedError only on bus-bound paths.** All exceptions caught by
  `OutboundErrorHandler.guard` are converted to
  `SanitizedError(message=type(exc).__name__, …)` — never `str(exc)`. The discipline
  kills the `str(exc)` leak class (#1212 etc.).
- **OutboundAdapterBase has NO `__init__`.** Discord MRO constraint:
  `DiscordAdapter.__init__` flows to `discord.Client(intents=intents)` via
  `super().__init__(intents=intents)`. Do NOT add `__init__` to any class in the
  `OutboundAdapterBase` inheritance chain.
- **Single broad-catch site in the emitter.** `OutboundErrorHandler.guard` is the single semantic broad-catch site in `lyra.outbound/`. Two additional terminal sites in `OutboundEmitter.run` and `_run_event_loop` capture stream errors with broad-catch — these are intentional (terminal stream-error path).

## State + recap helpers

`IntermediateTextState`, `StreamState` → `factory.outbound._streaming_state`.
`ToolRecapAccumulator`, `format_recap_lines` → `factory.outbound._tool_recap`.
No deferred-import block exists.

## ToolDisplayConfig wiring

`ToolDisplayConfig` (from `factory.core.messaging`) is injected via
`OutboundAdapterBase.send_streaming` — the **single WRITE site** for
`emitter.tool_display_config` (ADR-073). Concrete `_make_emitter` overrides MUST NOT
assign this attribute; doing so re-introduces the target-axis-trap Phase B (#1336) removed.
Thresholds (`bash_max_len`, `group_threshold`, `names_threshold`) are config-driven inside
`ToolRecapAccumulator`; `_route()` gates via `config.show` for visibility control.

## Audio delivery path (durable — #1482)

Outbound audio uses a separate durable JetStream path, NOT the text-chunk Core path:

- Subject: `lyra.outbound.audio.<platform>.<bot_id>` (5 tokens — distinct from 4-token text path)
- Stream: `LYRA_OUTBOUND_AUDIO` (Limits retention, `MaxAge=24h`)
- Consumer: durable pull `outbound-audio-{platform}-{bot_id}` (one per bot, e.g. `outbound-audio-telegram-main`, `outbound-audio-discord-main`)
- Dedup: KV bucket `lyra_outbound_audio_sent` keyed on `stream_id`

Hub publishes and returns immediately (stateless, Model A). Adapter owns the ACK after
platform API confirms. Text path (`lyra.outbound.<platform>.<bot_id>`, Core) is unchanged.

ACL and stream provisioning: T7 (ACL grants) and T14 (stream/consumer/KV bootstrap) — both provisioned, see ADR-079.

→ ADR-077 — full decision record.

## ADR pending — Phase 7

The architectural-decision record for the stage-axis pivot is deferred to Phase 7
(#1284, epic #1277 final cleanup phase). Until then, the SSoT is
`artifacts/analyses/1277-stage-axis-refactor-strategy.mdx`.

## DEBT: enforcement-deferred

A pre-commit grep check for bus-bound `str(exc)` patterns on outbound paths was scoped
out of #1279 — the merge-time AC grep in the spec is the sole gate for this PR.
Follow-up issue should add a `tools/check_outbound_str_exc.sh` pre-commit hook.
