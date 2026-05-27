# src/lyra/outbound/ — Outbound Stage Composition

## Purpose

Per-platform-agnostic stage composition for outbound message rendering. Consumed by
`OutboundAdapterBase.send_streaming` via `_make_emitter` on platform adapters (Telegram,
Discord). Replaces the per-target axis (`telegram_outbound.py` + `discord_outbound.py` +
`_shared_streaming_emitter.py`) that produced the boundary-broad-catch cascade observed
2026-05-19 (epic #1277).

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

- **No second circuit-breaker layer here.** CB lives in `OutboundDispatcher` (hub-side,
  per #1278/transport/CLAUDE.md). Outbound stages MUST NOT add a second CB.
- **SanitizedError only on bus-bound paths.** All exceptions caught by
  `OutboundErrorHandler.guard` are converted to
  `SanitizedError(message=type(exc).__name__, …)` — never `str(exc)`. The discipline
  kills the `str(exc)` leak class (#1212 etc.).
- **OutboundAdapterBase has NO `__init__`.** Discord MRO constraint:
  `DiscordAdapter.__init__` flows to `discord.Client(intents=intents)` via
  `super().__init__(intents=intents)`. Do NOT add `__init__` to any class in the
  `OutboundAdapterBase` inheritance chain.
- **Single broad-catch site in the emitter.** `OutboundErrorHandler.guard` is the only
  `except Exception` in `lyra.outbound/`. Two terminal sites in `OutboundEmitter.run`
  and `_run_event_loop` are temporarily annotated
  `# DEBT:boundary-broad-catch — terminal, migrated in S7` — these are the
  stream-error capture path and will be unified in a follow-up restructure.

## Composition example

```python
from lyra.outbound.emitter import OutboundEmitter
from lyra.outbound.error_handler import OutboundErrorHandler

class MyAdapter(OutboundAdapterBase):
    def _make_emitter(self, original_msg, outbound):
        formatter = MyFormatter(self, get_msg=self._msg, placeholder_text=self._msg("ph", "…"))
        typing = MyTypingIndicator(self)
        handler = OutboundErrorHandler(get_msg=self._msg)
        # Transitional path: dataclass-based PlatformCallbacks bridges
        # send-mechanics until S7 follow-up restructure absorbs it into formatter.
        callbacks = self._make_streaming_callbacks(original_msg, outbound)
        callbacks.edit_reasoning = formatter.edit_reasoning
        callbacks.edit_tool_recap = formatter.edit_tool_recap
        callbacks.chunk_text = formatter.chunk
        return OutboundEmitter(callbacks, outbound, error_handler=handler)
```

## State + recap helpers

`IntermediateTextState`, `StreamState` → `lyra.outbound._streaming_state` (renamed from
`_shared_streaming_state.py`). `ToolRecapAccumulator`, `format_recap_lines` →
`lyra.outbound._tool_recap`. Both relocated into `outbound/` in #1336; the prior circular
import via `lyra.adapters/__init__.py` is dissolved. No deferred-import block exists.

## ToolDisplayConfig wiring

`ToolDisplayConfig` (from `lyra.core.messaging`) is injected via
`OutboundAdapterBase.send_streaming` — the **single WRITE site** for
`emitter.tool_display_config` (ADR-073). Concrete `_make_emitter` overrides MUST NOT
assign this attribute; doing so re-introduces the target-axis-trap Phase B (#1336) removed.
Thresholds (`bash_max_len`, `group_threshold`, `names_threshold`) are config-driven inside
`ToolRecapAccumulator`; `_route()` gates via `config.show` for visibility control.

## ADR pending — Phase 7

The architectural-decision record for the stage-axis pivot is deferred to Phase 7
(#1284, epic #1277 final cleanup phase). Until then, the SSoT is
`artifacts/analyses/1277-stage-axis-refactor-strategy.mdx`.

## DEBT: enforcement-deferred

A pre-commit grep check for bus-bound `str(exc)` patterns on outbound paths was scoped
out of #1279 — the merge-time AC grep in the spec is the sole gate for this PR.
Follow-up issue should add a `tools/check_outbound_str_exc.sh` pre-commit hook.
