# Lyra Codebase Audit — 2026-03-12

**Scope:** `src/lyra/` — 44 Python files
**Agents:** 3 Architect, 3 Backend, 3 Security, 2 DevOps

---

## Executive Summary

| Domain | Critical | High | Medium | Low |
|--------|----------|------|--------|-----|
| Architecture (God Classes) | — | — | 5 | — |
| Architecture (SRP) | — | — | 5 | 2 |
| Architecture (Coupling) | — | 1 | 2 | 2 |
| Backend (Code Quality) | — | 1 | 6 | 4 |
| Backend (Abstractions) | — | — | 3 | 8 |
| Backend (Async) | 2 | 5 | 5 | 3 |
| Security (Input Validation) | — | 2 | 4 | 3 |
| Security (Auth & Trust) | — | 1 | 3 | 2 |
| Security (Dependencies) | — | — | 2 | 2 |
| DevOps (Config & Deploy) | — | 3 | 2 | 5 |
| DevOps (Testing) | — | 2 | — | 5 |
| **Total** | **2** | **15** | **32** | **36** |

---

## 1. God Classes

### 1.1 TelegramAdapter — 782 lines, 14 methods
**File:** `adapters/telegram.py`
Mixes: webhook app, inbound normalization, audio download, MarkdownV2 rendering, non-streaming send, streaming send, audio send, hub enqueue.
**Fix:** Split into `TelegramWebhookApp`, `TelegramNormalizer`, `TelegramSender`. Adapter becomes a thin coordinator.

### 1.2 DiscordAdapter — 690 lines, 14 methods
**File:** `adapters/discord.py`
Mixes: gateway lifecycle, normalization, audio, auto-thread, rendering, send ×3, channel resolve.
**Fix:** Extract `DiscordNormalizer`, `DiscordSender`. Auto-thread becomes `AutoThreadPolicy`.

### 1.3 Hub — 648 lines, 16 methods
**File:** `core/hub.py`
Mixes: 4 registries, pool lifecycle, rate limiting, inbound loop, audio loop, dispatch ×2, health timestamps.
**Fix:** Extract `Registry`, `RoutingTable`, `RateLimiter`, `AudioProcessor`. Hub drops to ~150 lines.

### 1.4 AgentBase — 576 lines, 8 methods
**File:** `core/agent.py`
Mixes: config hot-reload, persona tracking, plugin loading, CommandRouter lifecycle.
**Fix:** Move config dataclasses to `agent_config.py`. Extract `HotReloader`.

### 1.5 CommandRouter — 308 lines, 11 methods
**File:** `core/command_router.py`
Mixes: command detection/dispatch + 5 builtin implementations inline.
**Fix:** Extract each builtin as a standalone handler. Unify with plugin handler contract.

---

## 2. SRP Violations

### V-01 (HIGH) — `Hub.run()` 115-line method
Inline: rate-limiting, platform validation, binding resolution, pairing gate, circuit-breaker pre-check, command routing, pool dispatch.
**Fix:** Extract `MessagePipeline` with discrete stages.

### V-02 (MEDIUM) — `AgentBase._maybe_reload()`
3 orthogonal change triggers fused into one method.
**Fix:** Extract `HotReloadWatcher` + `PluginRegistry`.

### V-03 (MEDIUM) — Both adapters
Each holds: normalization, audio download, rendering, streaming, event handling. Telegram also owns `/status` API route.
**Fix:** Extract `Renderer`, `StreamingSender`.

### V-04 (MEDIUM) — `CommandRouter` builtins
Inline handler logic including file I/O in a routing layer.
**Fix:** Extract each builtin as standalone handler.

### V-05 (LOW-MEDIUM) — `load_agent_config()`
145 lines, 8+ independent parsing sections.
**Fix:** Per-section parse methods.

### V-06 (MEDIUM) — STT duplication in agents
Identical STT logic in `AnthropicAgent.process()` and `SimpleAgent.process()`.
**Fix:** Extract `AudioInputHandler`.

### V-07 (MEDIUM) — `Hub._audio_loop()`
Hub owns full STT pipeline (temp file, transcription, noise check).
**Fix:** Extract `AudioProcessor` service.

---

## 3. Coupling Hotspots

*Documented in ADR-017.*

### Hotspot 1 (WORST) — Pool → Hub back-reference
`Pool` holds entire `Hub`, accesses private `_msg_manager`, `agent_registry`, `circuit_registry`.
**Fix:** Extract `PoolContext` protocol with callbacks.

### Hotspot 2 — `LlmProvider` imports `ModelConfig` from `core/agent`
Wrong dependency direction: LLM layer depends on agent module.
**Fix:** Extract `core/model_config.py`.

### Hotspot 3 — `AnthropicAPIError` in core infrastructure
`pool.py` and `outbound_dispatcher.py` import Anthropic SDK.
**Fix:** Define `ProviderError` base exception in `llm/base.py`.

### Hotspot 4 — `CommandRouter` imports `_AGENTS_DIR` (private symbol)
**Fix:** Pass `runtime_config_path` explicitly at construction.

### Hotspot 5 — `__main__` hardcodes agent name
`hub.agent_registry.get("lyra_default")` + `isinstance(agent, AnthropicAgent)`.
**Fix:** Add `runtime_config` property to `AgentBase`.

---

## 4. Code Quality

### HIGH
- **`Hub.run()`** — cyclomatic complexity ~15, bare `except Exception: pass` at line 563–564

### MEDIUM
- **`Hub._audio_loop()`** — triple nesting, duplicated InboundMessage construction
- **`AnthropicSdkDriver.complete()`** — tool-use loop fused inline
- **`AgentBase._maybe_reload()`** — CommandRouter constructor copy-pasted 3×
- **`load_agent_config()`** — 145-line monolith
- **`TelegramAdapter.send_streaming()`** — MarkdownV2 escape duplicated 3×
- **`DiscordAdapter.on_message()`** — 143 lines, audio and text mixed

### LOW
- `CliPool._read_until_result()` — manual deadline duplicates asyncio.timeout
- `_extract_attachments()` — 6-branch if chain, not data-driven
- Deprecated `trust` field coexists with `trust_level`
- `CommandRouter.dispatch()` — string equality chain, dict unused for dispatch

---

## 5. Abstraction Quality

### MEDIUM
- `send_streaming` not enforced by `ChannelAdapter` protocol — runtime `hasattr` fallback
- `AnthropicAPIError` imported in infrastructure (pool, dispatcher) — LLM SDK leak
- `InboundBus` and `InboundAudioBus` duplicate same generic pattern

### LOW
- `InboundMessage.platform` typed as `str`, repeatedly cast to `Platform` enum
- `platform_meta: dict` untyped — hub inspects adapter-private keys
- `STTService` has no protocol
- `_DENY_ALL`/`_ALLOW_ALL` sentinels duplicated in both adapters
- Synthetic `InboundMessage` in `_audio_loop` just to call `dispatch_response`
- `_rebuild_router()` logic triplicated in `AgentBase`
- Deprecated `trust` string field alongside `trust_level` enum
- `Hub.run()` encodes 7+ orthogonal concerns inline

---

## 6. Async/Concurrency Issues

### CRITICAL
- **C-1:** `os.write()`/`os.close()` blocking I/O on event loop in `_audio_loop` (`hub.py:406–409`)
- **C-2:** `tempfile.mkstemp()` blocking on event loop (`hub.py:406`, `telegram.py:411`)

### HIGH
- **H-1:** `_maybe_reload` does blocking `stat`/`open`/TOML parse on every message
- **H-2:** Pool tasks fire-and-forget, `Hub.pools` grows unbounded (memory leak)
- **H-3:** `asyncio.shield` insufficient — double-cancel drops cancellation ack
- **H-4:** `CliPool._idle_reaper` swallows exceptions without stack trace
- **H-5:** Lazy STT model load under thread contention blocks executor threads

### MEDIUM
- **M-1:** No finally/cleanup in `run()`/`_audio_loop()` on cancellation
- **M-2:** Feeder tasks skip `task_done()` on `CancelledError`
- **M-3:** Streaming iterator not drained on worker task cancel
- **M-4:** Shutdown doesn't cancel/await Pool tasks before closing adapters
- **M-5:** RetryDecorator retries auth errors needlessly

### LOW
- TOCTOU race in `CliPool._entries`
- fd leak on `os.write` failure
- `send_backpressure` exceptions can break webhook 200 contract

---

## 7. Security

### HIGH
- **Plugin `exec_module` without symlink check** — `plugin_loader.py:147,188`. Symlink at `plugins/legit` pointing outside plugins dir bypasses `is_relative_to()` name check. Fix: resolve `handlers_path` with `.resolve()` and verify.
- **Unauthenticated `/health` endpoint** — `__main__.py:230–264`. Leaks circuit breaker states, queue sizes, uptime. Fix: add Bearer-token guard or reduce to `{"ok": true}`.

### MEDIUM
- **Orthogonal admin_user_ids / TrustLevel systems** — `command_router.py:200`. Two auth systems not integrated. Fix: require `TrustLevel.OWNER` for admin commands.
- **`/status` reuses webhook secret** — `telegram.py:232`. Conflates trust domains. Fix: use separate `LYRA_STATUS_SECRET`.
- **Pairing rate limit skips successful attempts** — `pairing/handlers.py:54`. Fix: count all attempts.
- **Timing oracle on webhook secret** — `telegram.py:134`. Uses `!=` instead of `hmac.compare_digest()`.
- **`extra_instructions` injected verbatim** — `runtime_config.py:82`. Admin can override persona. Fix: allowlist/presets.
- **LLM error body forwarded raw to logs** — `escalation.py:68`. Fix: truncate `resp.text[:200]`.
- **System_prompt as CLI argument** — `cli_pool.py:207`. Could be misinterpreted if starts with `--`. Fix: validate or use temp file.

### LOW
- Deprecated `trust` field coexists with `trust_level`
- `/unpair` accepts unvalidated `target_identity` string
- In-memory rate limit resets on process restart
- `_write_flat_toml()` doesn't escape special chars
- `roxabi-memory` pinned to mutable git branch (not commit SHA)
- Overly permissive `>=` bounds on network-facing deps

---

## 8. DevOps

### HIGH
- `.env.example` missing ~10 env vars the app actually reads
- `roxabi-memory` pinned to branch, not commit — non-reproducible lock
- `uv sync` without `--frozen` in `deploy.sh` — prod can drift from CI

### MEDIUM
- Config loading scattered across 5+ files; tokens read in multiple places
- `deploy-preview.yml` runs `npm ci` on a Python project — will error

### LOW
- No `PYTHONUNBUFFERED=1` in supervisor env
- No Python version pin (`.python-version`)
- No uv caching in CI
- Rollback in `deploy.sh` doesn't re-run `uv sync`
- Systemd timer referenced but no unit file in repo

---

## 9. Testing Infrastructure

### HIGH
- **No coverage measurement** — no `pytest-cov`, no `--cov`, no coverage gate in CI
- **One consistently failing test** — `test_delayed_stop_cancels_tasks` binds hardcoded port 8443

### OBSERVATIONS
- 738/739 tests passing, ~7.4s runtime
- No integration test layer — everything is unit-level with mocks
- 5 copies of `InboundMessage` builder helper across test modules
- No dependency caching in CI
- `tests/conftest.py` missing at root level

---

## Top 10 Priority Fixes

| # | Issue | Domain | Severity |
|---|-------|--------|----------|
| 1 | Blocking I/O in `_audio_loop` (os.write) | Async | Critical |
| 2 | Pool → Hub back-reference coupling | Coupling | High |
| 3 | `Hub.pools` unbounded growth (memory leak) | Async | High |
| 4 | Plugin loader symlink bypass | Security | High |
| 5 | Unauthenticated `/health` endpoint | Security | High |
| 6 | `Hub.run()` complexity + silent exception | Quality | High |
| 7 | `.env.example` incomplete + deploy.sh `--frozen` | DevOps | High |
| 8 | `AnthropicAPIError` in core infrastructure | Coupling | Medium |
| 9 | No test coverage measurement | Testing | High |
| 10 | `hmac.compare_digest` for webhook secret | Security | Medium |
