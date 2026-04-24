# Code Quality Audit Summary

**Project:** Lyra AI Agent Engine
**Date:** 2026-04-22 (refreshed 2026-04-22 — cross-referenced with `~/.roxabi/lyra-nats-truth/`)
**Files Analyzed:** 282 source files, 261 test files
**Lines of Code:** ~15,000 (source), ~8,000 (tests)

> **Refresh note:** findings that land on code scheduled for deletion (per NATS-truth M2 Stream 1 / Lane A decoupling) are marked `[DEFERRED — drops with #N]` and excluded from effort totals. See "Deprecation cross-reference" section below.

---

## Executive Summary

- **Overall health: Good** - Well-architected hub-spoke design with recent refactoring (#760, #773) improving modularity
- **Security posture: Strong** - No hardcoded secrets, proper credential handling, SSRF protection, parameterized SQL
- **Test coverage: Weak** - 52.51% line coverage with critical gaps in agents (0%) and adapters (12-15%)
- **Architectural debt: Moderate** - Layer violations between core/infrastructure and incomplete ADR-048 migration
- **Code quality: Good** - No bare `except:` clauses, consistent logging, intentional `noqa` documentation

---

## Critical Issues (P0)

### Security

1. **Path traversal in STT client** (`nats/nats_stt_client.py:152`)
   - `Path(path).resolve()` on user-supplied path without boundary validation
   - **Risk:** Arbitrary file read if path is attacker-controlled
   - **Effort:** 1 hour

2. **Callback execution from metadata** (`core/hub/middleware_submit.py:85-90`, `_dispatch.py:93-96`)
   - Callbacks extracted from `platform_meta` and executed without source validation
   - **Risk:** Code execution if attacker controls metadata source
   - **Effort:** 2 hours

### Test Coverage

3. **Zero coverage on `agents/simple_agent.py`**
   - CLI-subprocess agent path (lyra_cli stream #628) — untested
   - **Risk:** Silent regressions in agent behavior
   - **Effort:** 6 hours (integration tests)
   - ~~`agents/anthropic_agent.py`~~ `[DEFERRED — drops with #666 (AnthropicSdkDriver removal, epic #663)]`

---

## High Priority (P1)

### Architecture - Layer Violations

1. **Core→Infrastructure imports** (ADR-048 incomplete)
   - `core/stores/message_index.py`, `prefs_store.py`, `thread_store.py`, `pairing.py` import from infrastructure
   - **Effort:** 4 hours (migration)

2. **Infrastructure→Core imports**
   - `agent_store.py` imports from `core/agent/agent_models`, `agent_schema`, `agent_seeder`
   - **Effort:** 4 hours (move models to infrastructure or create protocols)

3. **Core→Integrations imports** (`core/processors/_scraping.py`, `vault_add.py`)
   - Exception classes should move to `core/exceptions.py` or `lyra/errors.py`
   - **Effort:** 1 hour

4. **Adapters→NATS layer violation** (`adapters/shared/_shared_streaming_state.py:14`)
   - `StreamChunkTimeout` should move to core or shared exceptions
   - **Effort:** 30 minutes

### Async Patterns

5. **Blocking DNS lookup in async context** (`core/processors/_scraping.py:39`)
   - `socket.getaddrinfo()` blocks event loop during SSRF DNS check
   - **Effort:** 1 hour (wrap in `asyncio.to_thread`)

6. **Race condition in pool eviction** (`core/hub/pool_manager.py:70-77`)
   - Iterating over `self.pools.items()` while calling `pop()` is not atomic
   - **Effort:** 2 hours (add lock or snapshot)

7. **Race condition in VoiceWorkerRegistry** (`nats/voice_health.py:108-113`)
   - `alive_workers()` iterates while `record_heartbeat()` mutates dict
   - **Effort:** 1 hour (add lock or copy)

### Code Smells - God Classes

8. **Hub.__init__ has 21 parameters** (`core/hub/hub.py:74-96`)
   - Critical maintainability issue
   - **Effort:** 4 hours (extract HubConfig dataclass)

9. **Pool has 13 constructor parameters** (`core/pool/pool.py:32`)
   - Extract PoolConfig dataclass
   - **Effort:** 2 hours

10. **CommandRouter has 14 constructor parameters** (`core/commands/command_router.py:46`)
    - Extract RouterConfig dataclass
    - **Effort:** 2 hours

### Test Quality

11. **Flaky test patterns** - `asyncio.sleep(10)` in `test_embedded_nats.py:133`, `asyncio.sleep(9999)` in message pipeline tests
    - **Effort:** 4 hours (replace with event-based sync)

12. **Low adapter coverage** - `discord_outbound: 12%`, `telegram_outbound: 12.9%`, `discord_audio: 15%`
    - **Effort:** 8 hours (expand test coverage)

### Security

13. **`skip_permissions` bypass lacks audit logging** (`core/cli/cli_protocol.py:78-79`)
    - `--dangerously-skip-permissions` flag bypasses security without accountability trail
    - **Effort:** 2 hours

14. **Path traversal in health endpoint** (`bootstrap/infra/health.py:41-53`)
    - `_read_secret()` lacks validation for `..`, `/`, `\`
    - **Effort:** 30 minutes

---

## Medium Priority (P2)

### Code Smells - Long Functions

1. **`_bootstrap_adapter_standalone`** (276 lines) - `bootstrap/standalone/adapter_standalone.py:24`
   - Split by platform (Telegram/Discord)
   - **Effort:** 4 hours

2. **`_bootstrap_unified`** (248 lines) - `bootstrap/factory/unified.py:49`
   - Extract orchestration phases
   - **Effort:** 4 hours

3. ~~**`sdk.py::complete()`** (130 lines) - `llm/drivers/sdk.py:54`~~ `[DEFERRED — file deleted by #666]`
   - AnthropicSdkDriver replaced by LiteLLMDriver (#665 in flight)
   - **Effort saved:** 3 hours

4. **`simple_agent.py::process()`** (108 lines) - `agents/simple_agent.py:186`
   - Extract streaming, error handling, voice handling
   - **Effort:** 3 hours

5. **`handle_message()`** (227 lines) - `adapters/discord/discord_inbound.py:29`
   - Extract auto-thread, session retrieval, DM wiring
   - **Effort:** 4 hours

### Code Smells - DRY Violations

6. ~~**Session command registration** duplicated in `SimpleAgent` and `AnthropicAgent`~~ `[DEFERRED — AnthropicAgent drops with #666]`
   - Revisit after #666: if duplication persists in SimpleAgent + future agents, extract then
   - **Effort saved:** 2 hours

7. **NATS voice client patterns** - STT/TTS/Image clients share ~90 lines of duplicated code
   - Create `NatsVoiceClientBase` class
   - **Effort:** 4 hours
   - ⚠️ Scope review: lyra-side keeps only publishers (`nats_<domain>_client.py`). Satellites migrate to `roxabi-contracts` per ADR-049 Phase 1. Verify dedup target is still 3 clients in lyra after #658/#690/#691 land.

8. **Stale-resume retry logic** duplicated in `cli_pool.py` and `cli_pool_streaming.py`
   - Extract shared helper
   - **Effort:** 1 hour

### Type Safety

9. **64 `Any` usages in adapters** - Primarily for discord.py/aiogram types
   - Add TYPE_CHECKING imports with library type stubs
   - **Effort:** 4 hours

10. **Missing return type** on `_db_or_raise()` - `infrastructure/stores/turn_store.py:119`
    - Add `-> aiosqlite.Connection`
    - **Effort:** 15 minutes

### Error Handling

11. **Schema migration safety** - `memory_schema.py:19-87`
    - Missing `finally` block to restore `PRAGMA foreign_keys = ON`
    - **Effort:** 1 hour

12. **Broad exception handling** - 170+ generic `except Exception:` across codebase
    - Narrow to specific types where feasible
    - **Effort:** 8 hours (incremental)

### Tech Debt

13. **Magic numbers** - 15+ hardcoded timeout/threshold values
    - Extract to constants or config
    - **Effort:** 2 hours

14. **Deprecated API** - `register_session_command()` in command_router.py
    - Add deprecation timeline and migration guide
    - **Effort:** 1 hour

---

## Low Priority (P3)

### Code Smells

1. **God classes (acceptable facades)** - `DiscordAdapter` (22 methods), `TelegramAdapter` (24 methods), `AgentStore` (17 methods)
   - Document as intentional delegation pattern
   - **Effort:** Documentation only

2. **Assertion usage** - `discord_outbound.py:61`, `_shared_streaming_emitter.py:285`
   - Replace with explicit validation
   - **Effort:** 30 minutes

### Type Safety

3. **`object` type hints** - 6 instances in pool/command modules
   - Use TYPE_CHECKING protocols
   - **Effort:** 2 hours

4. **Legacy `Optional[X]` syntax** - `cli_bot.py`, `cli_agent.py`
   - Modernize to `X | None`
   - **Effort:** 30 minutes

### Test Quality

5. **Hardcoded NATS URLs** - 23 occurrences of `localhost:4222`
   - Use configurable test fixtures
   - **Effort:** 2 hours

6. **Missing assertion messages** - ~50+ assertions without explanatory text
   - Add messages for debugging
   - **Effort:** 2 hours

### Error Handling

7. **Pass statements in exception handlers** - 6 silent catches
   - Add debug logging
   - **Effort:** 1 hour

---

## Metrics Dashboard

| Domain | Issues | P0 | P1 | P2 | P3 | Deferred |
|--------|--------|----|----|----|----|----------|
| Architecture | 14 | 0 | 4 | 6 | 4 | 0 |
| Security | 12 | 2 | 2 | 0 | 8 | 0 |
| Code Smells | 35 | 3 | 7 | 12 | 11 | 2 |
| Type Safety | 28 | 0 | 0 | 11 | 17 | 0 |
| Async Patterns | 16 | 0 | 3 | 8 | 5 | 0 |
| Error Handling | 18 | 0 | 0 | 12 | 6 | 0 |
| Test Quality | 22 | 1 | 3 | 8 | 10 | 0 |
| Tech Debt | 15 | 0 | 0 | 7 | 8 | 0 |
| **Active Total** | **158** | **6** | **19** | **64** | **69** | **—** |
| **Deferred** | **2** | — | — | 2 | — | #666 |

---

## Recommended Actions

### Immediate (This Sprint)

| # | Action | Effort | Impact |
|---|--------|--------|--------|
| 1 | Fix STT path traversal vulnerability | 1h | High |
| 2 | Add callback source validation | 2h | High |
| 3 | Add audit logging for `skip_permissions` | 2h | High |
| 4 | Fix health endpoint path traversal | 30m | High |
| 5 | Wrap blocking DNS in `asyncio.to_thread` | 1h | Medium |

### Next Sprint

| # | Action | Effort | Impact |
|---|--------|--------|--------|
| 6 | Complete ADR-048 migration (core stores → infrastructure) | 4h | Medium |
| 7 | Add integration tests for SimpleAgent | 6h | High |
| 8 | Extract HubConfig dataclass | 4h | Medium |
| 9 | Fix flaky test patterns (sleep → events) | 4h | Medium |
| 10 | Add pool eviction synchronization | 2h | Medium |

### Backlog

| # | Action | Effort | Impact |
|---|--------|--------|--------|
| 11 | Create NATS voice client base class | 4h | Low |
| 12 | Extract config dataclasses (Pool, Router) | 4h | Low |
| 13 | Add adapter test coverage | 8h | Medium |
| 14 | Narrow broad exception handlers | 8h | Low |
| 15 | Add TypedDict schemas for JSON payloads | 4h | Low |

---

## Technical Debt Score

**Score: 72/100** (where 100 = pristine)

| Category | Weight | Score | Weighted |
|----------|--------|-------|----------|
| Architecture | 20% | 70 | 14.0 |
| Security | 25% | 85 | 21.25 |
| Code Quality | 15% | 75 | 11.25 |
| Test Coverage | 25% | 55 | 13.75 |
| Maintainability | 15% | 80 | 12.0 |
| **Total** | **100%** | | **72.25** |

**Interpretation:** The codebase is in good health with strong security practices and maintainable architecture. The primary weakness is test coverage (52.51% line, 37.99% branch) with critical gaps in agent and adapter layers. Addressing the 6 P0 issues and improving test coverage would raise the score to ~80.

---

## Top 10 Quick Wins

| # | Action | Effort | Impact |
|---|--------|--------|--------|
| 1 | Add path validation to `_read_secret()` | 15m | High security fix |
| 2 | Add return type to `_db_or_raise()` | 15m | Type safety |
| 3 | Move `StreamChunkTimeout` to core exceptions | 30m | Fix layer violation |
| 4 | Add `finally` to schema migration | 30m | Data integrity |
| 5 | Add debug logging to swallowed exceptions | 30m | Debuggability |
| 6 | Replace `assert` with explicit validation | 30m | Production safety |
| 7 | Add `exc_info=True` to adapter exception logs | 30m | Debuggability |
| 8 | Fix health endpoint path traversal | 30m | Security |
| 9 | Add exception context capture in middleware | 30m | Debuggability |
| 10 | Document God class facades as intentional | 30m | Maintainability |

---

## Deprecation Cross-Reference

Validated against `~/.roxabi/lyra-nats-truth/` on 2026-04-22. Findings that target code scheduled for deletion are deferred — fixing them is wasted work.

| Audit finding | Target file | Drops with | Effort saved |
|---|---|---|---|
| P0 #3 (part) — AnthropicAgent 0% coverage | `agents/anthropic_agent.py` | #666 (epic #663) | 2h |
| P2 #3 — `sdk.py::complete()` 130-line refactor | `llm/drivers/sdk.py` | #666 (file deleted) | 3h |
| P2 #6 — Session cmd dedup (SimpleAgent/AnthropicAgent) | `agents/anthropic_agent.py` | #666 (dup disappears) | 2h |

**Total effort saved by deferring:** 7h.

### Findings verified as still valid post-deprecation

- `agents/simple_agent.py` (all findings) — CLI-subprocess path, part of lyra_cli stream #628
- `bootstrap/standalone/adapter_standalone.py` — telegram/discord bootstrap; distinct from `{stt,tts}_adapter_standalone.py` deleted by lyra#690
- NATS voice client dedup (P2 #7) — scope narrows after #658/#690/#691 but lyra-side publishers remain

### Adjacent deprecations (not in audit, flagged for awareness)

- `scripts/deploy.sh`, `lyra.service`, `deploy/supervisor/` — Lane H (#693, #701). Note: `deploy/supervisor/` was removed in #886 (tenant confs now live in `deploy/conf.d/`); retained here as a historical reference.
- `src/lyra/bootstrap/{stt,tts}_adapter_standalone.py`, `lyra_{stt,tts}.conf` — lyra#690
- `roxabi_nats.adapter_base.CONTRACT_VERSION` → migrates to `roxabi_contracts.envelope` (ADR-049, removed at `roxabi-nats/v0.3.0`)

---

## Appendix: Audit Scope

### Directories Analyzed

- `src/lyra/core/` - 92 files
- `src/lyra/adapters/` - 39 files
- `src/lyra/bootstrap/` - 30 files
- `src/lyra/infrastructure/` - 14 files
- `src/lyra/nats/` - 12 files
- `src/lyra/llm/` - 10 files
- `src/lyra/agents/` - 4 files
- `src/lyra/` root - 9 files
- `tests/` - 261 files

### Analysis Dimensions

1. **Architecture** - Layer boundaries, dependency direction, circular imports
2. **Security** - OWASP coverage, credential handling, input validation
3. **Code Smells** - God classes, long functions, DRY violations, magic numbers
4. **Type Safety** - Any usage, missing hints, type: ignore comments
5. **Async Patterns** - Race conditions, resource leaks, blocking calls
6. **Error Handling** - Exception specificity, logging, cleanup
7. **Test Quality** - Coverage, flaky patterns, mock usage
8. **Tech Debt** - TODOs, deprecated APIs, legacy patterns
