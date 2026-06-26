# P05 — Adapters Architecture Audit (2026-05-26)

Partition: `src/lyra/adapters/**/*.py`
Focus: stage-axis decomposition, cross-stage imports, layer violations, ADR-048 migration, new/changed code since 2026-05-18.

---

## Summary

- Stage-axis decomposition is **largely correct** for platform adapters: inbound delegates to `lyra.inbound.*` (#1277 Phase 3), outbound composes `lyra.outbound.*` (#1279 Phase 2). Two stage-alignment anomalies remain: `nats_stream_decoder.py` (streaming-stage file in `adapters/nats/`) and `_shared.py` (mixes inbound + outbound primitives in one module).
- New code since 2026-05-18 (typing plane #1376/#1396, outbound stage composition #1279 T15-T19) follows the correct **downward** layer direction and introduces **zero importlinter violations**. The transitional `_make_emitter` → `_make_streaming_callbacks` → `PlatformCallbacks` patching is a known hybrid path pending S7.
- ADR-048 in adapters: both platform adapters use the **correct** transitional pattern (`TYPE_CHECKING` import of concrete `TurnStore`, runtime uses `ThreadStoreProtocol` from `core/stores`). No runtime infrastructure leaks.
- **Positive delta**: `PlatformCallbacks` moved from `adapters/shared/_shared_streaming_emitter` to `lyra.outbound.emitter` — the port is now in the correct outbound stage module, resolving the prior audit's Finding 10 (A1).

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `adapters/nats/nats_stream_decoder.py` | 1 | Medium | Streaming-stage file (chunk decode, seq ordering, terminal detection, `RenderEvent` yield) lives in `adapters/nats/` instead of `lyra.streaming/` or `lyra.nats/` per ADR-073. | Relocate to `src/lyra/streaming/nats_decoder.py` or `src/lyra/nats/stream_decoder.py` in Phase 7 (#1284). |
| `adapters/shared/_shared.py` | 74 / 219 | Low | `push_to_hub_guarded` (inbound primitive) and `send_with_retry` (outbound primitive) share one file, violating single-stage-per-file. | Split into `_shared_inbound.py` / `_shared_outbound.py` during S7 cleanup. |
| `adapters/discord/adapter.py` | 60 | Low | `_discord_scope_resolver` duplicated per-adapter; not yet using `lyra.typing.make_typing_factory` helper per #1409. | Migrate to `make_typing_factory` when #1409 lands (same for Telegram). |
| `adapters/telegram/telegram.py` | 57 | Low | `_telegram_scope_resolver` — same as above. | Migrate to `make_typing_factory` when #1409 lands. |
| `adapters/nats/nats_stream_decoder.py` | 25 | Low | `RenderEvent` imported via `lyra.core.hub.hub_protocol` indirection instead of canonical `lyra.core.messaging.render_events`. | Import directly from `lyra.core.messaging.render_events`. |
| `adapters/discord/discord_inbound.py` | 112 | Low | Unannotated `except Exception` in `_try_auto_create_thread` (create_thread failure + recovery). Inbound CLAUDE.md claims BLE001 drained in adapters, but this path was not covered. | Narrow to `discord.DiscordException` or add `# noqa: BLE001` with justification. |
| `adapters/telegram/telegram_inbound.py` | 139 | Low | Unannotated `except Exception` in `handle_voice_message` around `_download_audio`. Same gap as Discord inbound. | Narrow to `TelegramAPIError`/`ValueError` or add `# noqa: BLE001` with justification. |
| `adapters/discord/discord_outbound.py` | 275 | Low | Annotated `DEBT:boundary-broad-catch` in `_send_message` final chunk — expected S7 drain. | Drain in S7 when formatter Protocol fully owns send-mechanics. |
| `adapters/telegram/telegram_outbound.py` | 266 | Low | Same annotated broad-catch pattern in `_send_message` final chunk. | Drain in S7. |

---

## Metrics

| Metric | Value |
|--------|-------|
| Total `.py` files | 39 |
| Total LOC | 6 236 |
| Importlinter contracts | 8 / 8 kept (0 broken) |
| Files analyzed (up from 364 baseline) | 410 |
| Stage-pure files (inbound / outbound / transport) | ~30 / 39 (77 %) |
| Stage-mixed or shared files | ~9 / 39 (23 %) — `_shared.py`, `nats_stream_decoder.py`, adapter facades, worker |
| Unannotated broad catches found | 2 (Discord inbound create_thread, Telegram inbound voice download) |
| Annotated `DEBT:boundary-broad-catch` in outbound paths | 4 (known, S7 pending) |
| ADR-048 TYPE_CHECKING infra imports in adapters | 2 files (`discord/adapter.py`, `telegram/telegram.py`), correct pattern |
| Bootstrap imports in adapters | 0 |
| Circular deps (known) | 1 deferred-import cycle: `outbound.emitter` ↔ `adapters.shared._shared_streaming_state` ↔ `outbound.error_handler` — tracked as `DEBT:importlinter-outbound-shared-state-transition` |

---

## Recommendations (prioritized)

1. **Relocate `nats_stream_decoder.py` in Phase 7** — It is a streaming-stage module sitting in the adapter transport directory. Move it to `src/lyra/streaming/` (or `src/lyra/nats/` if it is codec-coupled) so the stage-axis file tree matches the import graph.
2. **Split `adapters/shared/_shared.py` into stage halves during S7** — `push_to_hub_guarded` (inbound) and `send_with_retry` (outbound) are the two remaining cross-stage primitives in the shared module. Separating them makes the stage boundary grep-discoverable.
3. **Close typing-plane N×M gap via #1409** — Both platform adapters still carry per-adapter `_scope_resolver` functions and `_start_typing` lambdas that should be consumed through `lyra.typing.make_typing_factory`. When #1409 lands, delete the duplicated per-adapter resolvers.
4. **Narrow or annotate the 2 unannotated broad catches** — `discord_inbound.py:112` (`_try_auto_create_thread`) and `telegram_inbound.py:139` (`handle_voice_message` audio download) are unannotated `except Exception` sites in paths the inbound CLAUDE.md claims are BLE001-free. Narrow the exception type or add a documented `# noqa`.
5. **Add a shared `OutboundEmitter` factory to `lyra.outbound` (optional, S7)** — Both `_make_emitter` implementations share an identical fallback path (`if meta is None: callbacks = _build_streaming_callbacks(...)`) and identical composition order (`formatter → typing → error_handler → callbacks patching`). A thin `OutboundEmitterFactory.build(formatter, typing, callbacks)` would reduce per-adapter boilerplate to <15 lines, keeping new-target cost under the 100-line ADR-073 target.
