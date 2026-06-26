# Axial ADR Drift Review

## Baseline
ADR-073: Stage-axis decomposition

## Findings

suggestion(blocking): inbound stage imports shared helpers from adapter layer — concern leak across axis
  src/lyra/inbound/dispatcher.py:8
  src/lyra/inbound/context.py:31
  src/lyra/inbound/context.py:32
  -- axial-adr-review
  Root cause: `push_to_hub_guarded`, `PushGuardDeps`, `TypingTaskManager`, and `OutboundListener` live in `lyra.adapters.shared` (non-primary axis) but are consumed by `lyra.inbound` (stage axis). Per ADR-073, shared primitives used by pipeline stages must reside in `lyra.core` or a floating shared module, not in the adapter layer.
  Class: target-axis-trap
  Raw callsites: [{file: "src/lyra/inbound/dispatcher.py", line: 8}, {file: "src/lyra/inbound/context.py", line: 31}, {file: "src/lyra/inbound/context.py", line: 32}]
  Solutions:
    1. Relocate `push_to_hub_guarded` + `PushGuardDeps` and `OutboundListener` / `TypingTaskManager` to `lyra.core` or `lyra.shared` per #1283 Phase 6
    2. Remove corresponding `.importlinter` exemptions (`DEBT:inbound-adapters-transition`) once relocation is complete
  Confidence: 90%

suggestion(blocking): inbound wire parsers couple to concrete adapter classes (platform-parser coupling)
  src/lyra/inbound/wire_parser_telegram.py:10
  src/lyra/inbound/wire_parser_discord.py:15
  -- axial-adr-review
  Root cause: `TelegramWireParser` and `DiscordWireParser` import `TelegramAdapter` and `DiscordAdapter` under `TYPE_CHECKING`. The inbound stage (stage axis) should not reference concrete adapter classes (non-primary axis). Parser Protocol conformance gap (ADR-073 expected debt) means the Protocol is currently shape-only, not a runtime contract.
  Class: target-axis-trap
  Raw callsites: [{file: "src/lyra/inbound/wire_parser_telegram.py", line: 10}, {file: "src/lyra/inbound/wire_parser_discord.py", line: 15}]
  Solutions:
    1. Add `feed`/`finalize`/`is_done` aliases to the `Parser` Protocol so wire parsers can reference the Protocol instead of concrete adapters
    2. Extract platform-agnostic parser interface into `lyra.core` or `lyra.streaming` so inbound stage never imports adapter concrete types
  Confidence: 85%

praise: outbound stage composition is aligned with ADR-073 — no N×M drift
  src/lyra/adapters/telegram/telegram.py:255
  src/lyra/adapters/discord/adapter.py:242
  src/lyra/adapters/shared/_emitter.py:17
  -- axial-adr-review
  Root cause: `TelegramAdapter._make_emitter` and `DiscordAdapter._make_emitter` both delegate to `lyra.adapters.shared._emitter._make_emitter()`, a shared stage-composition helper. `make_typing_factory()` is used in both adapters from `lyra.typing` (single definition, no per-adapter duplication). `OutboundAdapterBase.send_streaming()` is concrete and shared; adapters do not override it. These compose existing stage primitives (formatter, throttle, error_handler) from `lyra.outbound/` rather than re-implementing them.
  Class: target-axis-trap
  Raw callsites: [{file: "src/lyra/adapters/telegram/telegram.py", line: 255}, {file: "src/lyra/adapters/discord/adapter.py", line: 242}, {file: "src/lyra/adapters/shared/_emitter.py", line: 17}]
  Solutions:
    1. Continue this pattern for any new platform adapter
    2. Ensure `clipool` and `nats` adapters do not introduce parallel emit/send logic
  Confidence: 95%

praise: import-linter contracts enforce stage-axis boundaries — 11 kept, 0 broken
  .importlinter:134
  -- axial-adr-review
  Root cause: `inbound-no-adapters` contract (ADR-073 / #1287) actively tracks the two concern-leak exemptions above. Clean architecture layers (transport ← streaming ← core ← llm/nats ← infrastructure ← adapters ← bootstrap) are maintained. No adapter has re-introduced the legacy streaming-emitter shim.
  Class: target-axis-trap
  Raw callsites: [{file: ".importlinter", line: 134}]
  Solutions:
    1. Keep the `inbound-no-adapters` contract and remove exemptions as debts are resolved
    2. Add a pre-commit grep gate for the anti-pattern signal in `src/lyra/nats/` to catch drift early
  Confidence: 100%

praise: anti-pattern signal is clean — 0 hits in NATS transport layer
  src/lyra/nats/:0
  -- axial-adr-review
  Root cause: No new domain client is subclassing or wrapping another client instead of composing `WorkerPoolClient` directly. The NATS transport layer uses `WorkerPoolClient` composition.
  Class: target-axis-trap
  Raw callsites: []
  Solutions:
    1. Continue using `WorkerPoolClient` composition for new transport consumers
    2. Monitor via periodic grep if new NATS client wrappers appear
  Confidence: 100%

## Summary
Files reviewed: 30+ (ADR-073, import-linter report, all adapter modules, inbound stage, infrastructure layer, wire parsers, CLAUDE.md invariants)
Issues found: 2 (both `suggestion(blocking)` — known transitional debt tracked in `.importlinter`)
Clean: no — two documented concern-leak exemptions remain pending Phase 6/7 resolution
