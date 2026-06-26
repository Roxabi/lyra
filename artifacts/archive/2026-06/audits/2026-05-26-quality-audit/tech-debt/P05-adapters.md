# Tech Debt Audit — P05: src/lyra/adapters/**/*.py

**Date:** 2026-05-27
**Partition:** P05 (adapters: Telegram, Discord, NATS, CliPool, shared)
**Scope:** 36 files, ~6 236 lines
**Context:** Epic #1277 stage-axis refactor active; prior audit 2026-05-18 covered hexagonal conformance / duplication / dead-code.

---

## Summary

- **Zero informal debt markers** (TODO/FIXME/HACK/XXX) in the partition, but **26 formal `DEBT:` annotations** concentrate on boundary broad-catch (7) and wiring-bootstrap-deps (9).
- **ADR-048 migration incomplete**: `ThreadStoreProtocol` lives in `core/stores/` (not `core/ports/`); `TurnStore` has no protocol at all.
- **S7 follow-up is a floating phase label** with no concrete issue across 4 exempted adapter files (~1 484 lines) — the single largest debt-drain pressure in P05.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `discord/discord_audio.py` | 35 | Low | Magic constant `4` in `len(data) < 4` with `DEBT:adapter-magic-constants` | Extract `_MIN_MAGIC_BYTES = 4` |
| `discord/discord_audio.py` | 44 | Low | Magic constants `12` and `8` in RIFF/WAVE check with `DEBT:adapter-magic-constants` | Extract `_RIFF_HEADER_LEN = 12` / `_RIFF_TYPE_OFFSET = 8` |
| `discord/discord_audio.py` | 56 | Low | Magic constants `8` and `4` in MP4 `ftyp` check with `DEBT:adapter-magic-constants` | Extract `_MP4_FTYP_OFFSET = 4` / `_MP4_MIN_LEN = 8` |
| `discord/discord_formatting.py` | 33 | Low | Magic constant `3` in `len(lines) < 3` with `DEBT:adapter-magic-constants` | Extract `_MAX_TOOL_NAME_LINES = 3` |
| `discord/discord_threads.py` | 66 | Low | Inline `500` cache cap (no named constant) | Extract `_THREAD_CACHE_MAX = 500` (same file already has identical literal at line 130) |
| `discord/discord_threads.py` | 130 | Low | Inline `500` cache cap repeated | Consolidate with line 66 into single named constant |
| `shared/_tool_recap.py` | 21 | Low | `_BASH_DISPLAY_MAX = 80` and `_AGENT_DISPLAY_MAX = 48` are named but file-level only; 120/117 truncation in `telegram_formatter.py` (lines 109-110, 124-125) are unnamed | Move truncation logic into shared formatter constants |
| `shared/_shared_text.py` | 42 | Low | Inline `255` name truncation | Extract `_MAX_NAME_LEN = 255` |
| `discord/adapter.py` | 21 | Low | `DEBT:module-level-patch-fixtures` — module-level import for test patching | Migrate to factory injection or fixture override |
| `telegram/telegram.py` | 22 | Low | `DEBT:lint-residual` — out-of-order import | Fix import grouping once S7 cleanup shrinks file |
| `telegram/telegram.py` | 35 | Low | `DEBT:re-export-init` — `_typing_loop` re-exported for backward compat | Delete re-export in S7 cleanup; move consumers to canonical import |
| `telegram/telegram.py` | 67 | Low | `load_config = load_telegram_config` backward-compat alias | Schedule removal with deprecation cycle |
| `discord/__init__.py` | 3 | Low | `DiscordAdapter` re-exported for backward compat | Schedule removal with deprecation cycle |
| `shared/_shared.py` | 8 | Low | Re-exports for "existing importers" | Audit consumers and delete shim |
| `shared/_base_outbound.py` | 39 | Medium | `_make_streaming_callbacks()` legacy factory retained until "S7 cleanup" | Open tracking issue for S7 adapter slice; delete legacy factory |
| `discord/discord_outbound.py` | 193 | Medium | `build_streaming_callbacks()` legacy closure with `DEBT:wiring-bootstrap-deps`; exemption says "cleanup pending S7" | Same as above |
| `telegram/telegram_outbound.py` | 187 | Medium | `build_streaming_callbacks()` legacy closure with `DEBT:wiring-bootstrap-deps`; exemption says "cleanup pending S7" | Same as above |
| `discord/adapter.py` | 258 | Medium | Comment: "send-mechanics until the S7 follow-up absorbs send_* into the rendering surface" | Same as above |
| `telegram/telegram.py` | 273 | Medium | Comment: "rendering surface until the S7 follow-up absorbs send_* into the rendering surface" | Same as above |
| `clipool/clipool_worker.py` | 215 | Low | `DEBT:boundary-broad-catch` — `except Exception` on NATS publish boundary | Narrow to `nats.errors.Error` + `ValidationError` once contract stabilizes |
| `clipool/clipool_worker.py` | 293 | Low | `DEBT:boundary-broad-catch` — `except Exception` on message dispatch | Narrow after ADR-054 exception taxonomy matures |
| `clipool/clipool_worker.py` | 332 | Low | `DEBT:boundary-broad-catch` — `except Exception` on worker loop | Same as above |
| `discord/discord_audio.py` | 232 | Low | `DEBT:boundary-broad-catch` — `except Exception` on audio handling | Narrow to `discord.DiscordException` + `aiohttp.ClientError` |
| `discord/discord_outbound.py` | 275 | Low | `DEBT:boundary-broad-catch` — resilient: caller has no fallback for partial streams | Document why narrowing is unsafe; add metric |
| `discord/discord_outbound.py` | 318 | Low | `DEBT:boundary-broad-catch` — `except Exception as exc` on outbound send | Narrow once platform error taxonomy matures |
| `nats/mint_failure_subscriber.py` | 129 | Low | `DEBT:boundary-broad-catch` — NATS publish: exception type varies | Same as above |
| `nats/nats_outbound_listener.py` | 126 | Low | `DEBT:boundary-broad-catch` — deserialization: exception type varies | Use `msgspec.ValidationError` + `json.JSONDecodeError` |
| `nats/nats_outbound_listener.py` | 166 | Low | `DEBT:boundary-broad-croad-catch` — send_streaming: exception type varies | Narrow to adapter-specific exceptions |
| `shared/_inbound_cache.py` | 95 | Low | `DEBT:boundary-broad-catch` — cache op: non-fatal | Acceptable; add metric |
| `shared/_shared_audio.py` | 74 | Low | `DEBT:boundary-broad-catch` — audio op: non-fatal | Narrow to `aiohttp.ClientError` + `OSError` |

### ADR-048 Migration (core/ports/ gap)

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `core/stores/thread_store_protocol.py` | N/A | Medium | `ThreadStoreProtocol` lives in `core/stores/`, not `core/ports/` as ADR-048 requires | Move to `core/ports/thread_store.py`; update all imports |
| `infrastructure/stores/turn_store.py` | N/A | Medium | `TurnStore` has **no protocol**; adapters import concrete class directly | Define `TurnStoreProtocol` in `core/ports/turn_store.py`; inject via constructor |
| `infrastructure/stores/` | N/A | Medium | 15 files in `infrastructure/stores/` but `core/ports/` only has `audit_sink`, `llm`, `stt`, `tts` | Audit all store implementations; create missing port protocols |

---

## Metrics

| Metric | Count |
|--------|-------|
| Files in partition | 36 |
| Total lines | ~6 236 |
| `TODO` / `FIXME` / `HACK` / `XXX` | 0 |
| Formal `DEBT:` annotations | 26 |
| `DEBT:boundary-broad-catch` | 10 |
| `DEBT:wiring-bootstrap-deps` | 9 |
| `DEBT:adapter-magic-constants` | 4 |
| `DEBT:adapter-dispatch-complexity` | 2 |
| `DEBT:re-export-init` | 1 |
| `DEBT:lint-residual` | 1 |
| `DEBT:defensive-narrow-payloads` | 1 |
| `DEBT:module-level-patch-fixtures` | 1 |
| Deprecated asyncio APIs (`asyncio.coroutine`, `ensure_future`, etc.) | 0 |
| Named constants for platform limits | 8 (4096, 1024, 2000, 3840, 8192, 600, 256, 100) |
| Inline magic numbers (≥3 digits, unnamed) | 7 (500×2, 120, 117, 255, 48, 80) + small literals 4, 8, 12, 3 |
| File exemptions (adapters) | 5 (clipool_worker 388, telegram_outbound 388, telegram 327, discord_outbound 382, discord/adapter 337) |
| Folder exemptions touching adapters | 0 (adapters subdirs are ≤12 files) |
| Missing port protocols (ADR-048) | 2 (`ThreadStoreProtocol` misplaced, `TurnStoreProtocol` absent) |
| Legacy callback factories pending S7 | 4 (`build_streaming_callbacks` ×2, `_make_streaming_callbacks` ×2) |
| Backward-compat re-exports / aliases | 4 (`load_config`, `_typing_loop`, `DiscordAdapter` __init__, `_shared.py` shim) |

---

## Recommendations (prioritized, max 5)

1. **Open a concrete issue for "S7 adapters cleanup"** — The S7 label is referenced in 4 file exemptions and 6 source files but has no tracking issue. Without it, the 1 484 exempted lines (telegram_outbound, telegram.py, discord_outbound, discord/adapter, clipool_worker) will not shrink. Scope: delete `build_streaming_callbacks`, `_make_streaming_callbacks`, legacy dataclass, and associated file exemptions.

2. **Close ADR-048 port gap for stores** — Move `ThreadStoreProtocol` from `core/stores/` to `core/ports/thread_store.py`; define `TurnStoreProtocol` in `core/ports/turn_store.py`; inject both into adapters via constructor rather than concrete imports. This removes the hexagonal boundary leak.

3. **Extract remaining magic constants in `discord_audio.py` and `discord_formatting.py`** — The four `PLR2004 noqa` lines in `discord_audio.py` (4, 12, 8, 8) and one in `discord_formatting.py` (3) are the last un-named adapter literals. Extract them to module-level constants to eliminate the `DEBT:adapter-magic-constants` category entirely.

4. **Schedule backward-compat re-export removal** — Audit consumers of `load_config`, `_typing_loop`, `DiscordAdapter` in `discord/__init__.py`, and `_shared.py` re-exports. Set a deprecation deadline (e.g., 2 releases) and delete the shims.

5. **Narrow 3 highest-risk broad-catch boundaries** — Focus on `clipool_worker.py` (lines 215, 293, 332): these are NATS bus boundaries where ADR-054 already defines message contracts. Narrow to `nats.errors.Error` + `msgspec.ValidationError` + `pydantic.ValidationError` and delete the `BLE001 noqa` annotations.
