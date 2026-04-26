# Code Quality Audit Summary

**Project:** Lyra AI Agent Engine
**Date:** 2026-04-26 (refreshed: P0 fixes #852, #879, #923)
**Files Analyzed:** 282 source files, 261 test files
**Lines of Code:** ~15,000 (source), ~27,000 (tests)

---

## Executive Summary

- **Overall health: Good** - Well-architected hub-spoke design with recent refactoring improving modularity
- **Security posture: Strong** - No hardcoded secrets, proper credential handling, SSRF protection, parameterized SQL throughout
- **Test coverage: Weak** - 52.51% line coverage with critical gaps in agents (0%) and adapters (12-15%)
- **Architectural debt: Moderate** - Layer violations between core/infrastructure, incomplete ADR-048 migration
- **Code quality: Good** - No bare `except:` clauses, consistent logging patterns, intentional `noqa` documentation

---

## Critical Issues (P0)

### Security

1. ~~**Path traversal in STT client**~~ — **FIXED** by #852 (API now takes `bytes` not `Path`)

2. **Callback execution from metadata** (`core/hub/middleware_submit.py:85-90`, `_dispatch.py:93-96`)
   - Callbacks extracted from `platform_meta` and executed without source validation
   - **Risk:** Code execution if attacker controls metadata source
   - **Effort:** 2 hours

### Data Integrity

3. ~~**Schema migration database corruption**~~ — **FIXED** by #923 (`finally` block ensures `PRAGMA foreign_keys = ON`)

### Test Coverage

4. **Zero coverage on `agents/simple_agent.py`**
   - CLI-subprocess agent path untested
   - **Risk:** Silent regressions in agent behavior
   - **Effort:** 6 hours (integration tests)

5. **Zero coverage on `agents/anthropic_agent.py`**
   - Anthropic SDK integration untested
   - **Risk:** Silent regressions in LLM behavior
   - **Effort:** 4 hours

6. ~~**Flaky test with extreme sleep**~~ — **FIXED** by #879 (event-based sync replaced `asyncio.sleep`)

---

## High Priority (P1)

### Architecture - Layer Violations

1. **Core->Infrastructure imports** (ADR-048 incomplete)
   - `core/stores/message_index.py:13`, `prefs_store.py:10`, `thread_store.py:23`, `pairing.py:21` import from infrastructure
   - **Effort:** 4 hours (migration)

2. **Infrastructure->Core imports**
   - `agent_store.py:8-22` imports from `core/agent/agent_models`, `agent_schema`, `agent_seeder`
   - **Effort:** 4 hours (move models to infrastructure or create protocols)

3. **Core->Integrations imports** (`core/processors/_scraping.py:19`, `vault_add.py:23`)
   - Exception classes (`ScrapeFailed`, `VaultWriteFailed`) should move to `core/exceptions.py`
   - **Effort:** 1 hour

4. **Adapters->NATS layer violation** (`adapters/shared/_shared_streaming_state.py:14`)
   - `StreamChunkTimeout` imported from nats layer
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
   - **Risk:** `RuntimeError: dictionary changed size during iteration`
   - **Effort:** 1 hour (add lock or copy)

8. **Missing lifecycle methods** - NATS clients (`nats_stt_client.py`, `nats_tts_client.py`, `nats_image_client.py`)
   - Heartbeat subscriptions never closed; no `stop()` method
   - **Effort:** 2 hours

### Code Smells - God Classes / Long Parameters

9. **Hub.__init__ has 21 parameters** (`core/hub/hub.py:74-96`)
   - Critical maintainability issue
   - **Effort:** 4 hours (extract HubConfig dataclass)

10. **Pool has 13 constructor parameters** (`core/pool/pool.py:32`)
    - Extract PoolConfig dataclass
    - **Effort:** 2 hours

11. **CommandRouter has 14 constructor parameters** (`core/commands/command_router.py:46`)
    - Extract RouterConfig dataclass
    - **Effort:** 2 hours

### Security

12. **`skip_permissions` bypass lacks audit logging** (`core/cli/cli_protocol.py:78-79`)
    - `--dangerously-skip-permissions` flag bypasses security without accountability trail
    - **Effort:** 2 hours

13. **Path traversal in health endpoint** (`bootstrap/infra/health.py:41-53`)
    - `_read_secret()` lacks validation for `..`, `/`, `\`
    - **Effort:** 30 minutes

### Test Quality

14. **Flaky test patterns** - `asyncio.sleep(10)` in `test_embedded_nats.py:133`, `asyncio.sleep(3600)` in conftest
    - **Effort:** 4 hours (replace with event-based sync)

15. **Low adapter coverage** - `discord_outbound: 12%`, `telegram_outbound: 12.9%`, `discord_audio: 15%`
    - **Effort:** 8 hours (expand test coverage)

---

## Medium Priority (P2)

### Code Smells - Long Functions

1. **`_bootstrap_adapter_standalone`** (276 lines) - `bootstrap/standalone/adapter_standalone.py:24`
   - Split by platform (Telegram/Discord)
   - **Effort:** 4 hours

2. **`_bootstrap_unified`** (248 lines) - `bootstrap/factory/unified.py:49`
   - Extract orchestration phases
   - **Effort:** 4 hours

3. **`sdk.py::complete()`** (130 lines) - `llm/drivers/sdk.py:54`
   - Extract tool-use loop, error handling, response assembly
   - **Effort:** 3 hours

4. **`simple_agent.py::process()`** (108 lines) - `agents/simple_agent.py:186`
   - Extract streaming, error handling, voice handling
   - **Effort:** 3 hours

5. **`handle_message()`** (227 lines) - `adapters/discord/discord_inbound.py:29`
   - Extract auto-thread, session retrieval, DM wiring
   - **Effort:** 4 hours

6. **`dispatch_outbound_item()`** (170 lines) - `core/hub/_dispatch.py:27`
   - Extract routing validation, circuit check, retry loop
   - **Effort:** 3 hours

### Code Smells - DRY Violations

7. **NATS voice client patterns** - STT/TTS/Image clients share ~90 lines of duplicated code
   - Create `NatsVoiceClientBase` class
   - **Effort:** 4 hours

8. **Stale-resume retry logic** duplicated in `cli_pool.py` and `cli_pool_streaming.py`
   - Extract shared helper
   - **Effort:** 1 hour

9. **Platform validation pattern** duplicated across hub modules
   - Create shared helper
   - **Effort:** 30 minutes

10. **Session command registration** duplicated in `SimpleAgent` and `AnthropicAgent`
    - Extract to AgentBase mixin
    - **Effort:** 2 hours

### Type Safety

11. **64 `Any` usages in adapters** - Primarily for discord.py/aiogram types
    - Add TYPE_CHECKING imports with library type stubs
    - **Effort:** 4 hours

12. **Missing return type** on `_db_or_raise()` - `infrastructure/stores/turn_store.py:119`
    - Add `-> aiosqlite.Connection`
    - **Effort:** 15 minutes

13. **`session_driver: object = None` type violation** - `command_router.py:61`
    - Change to `object | None = None`
    - **Effort:** 5 minutes

### Error Handling

14. **Broad exception handling** - 170+ generic `except Exception:` across codebase
    - Narrow to specific types where feasible
    - **Effort:** 8 hours (incremental)

15. **Silent error swallowing** - `turn_store.py:190`, `turn_store_session.py:76,103,120`
    - Methods catch Exception, log, return None without re-raising
    - **Effort:** 2 hours

### Async Patterns

16. **Event bus no unsubscribe** (`core/hub/event_bus.py:38-56`)
    - Subscribers added but never removed; memory leak for long-running processes
    - **Effort:** 1 hour

17. **Temp file resource leak** (`core/hub/middleware_stt.py:126-161`)
    - File created before try block; may not be cleaned on cancellation
    - **Effort:** 1 hour

### Tech Debt

18. **Magic numbers** - 15+ hardcoded timeout/threshold values across modules
    - Extract to constants or config
    - **Effort:** 2 hours

19. **Deprecated API** - `register_session_command()` in command_router.py:149
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

3. **Prefixed local variables** - `discord_inbound.py:59-94`, `discord_audio.py:186-213`
   - Remove underscore prefix from local variables
   - **Effort:** 30 minutes

### Type Safety

4. **`object` type hints** - 6 instances in pool/command modules
   - Use TYPE_CHECKING protocols
   - **Effort:** 2 hours

5. **Legacy `Optional[X]` syntax** - `cli_bot.py`, `cli_agent.py`
   - Modernize to `X | None`
   - **Effort:** 30 minutes

6. **Bare `dict` without type params** - 9 instances across infrastructure/stores
   - Add `dict[str, Any]` annotations
   - **Effort:** 1 hour

### Test Quality

7. **Hardcoded NATS URLs** - 23 occurrences of `localhost:4222`
   - Use configurable test fixtures
   - **Effort:** 2 hours

8. **Missing assertion messages** - ~50+ assertions without explanatory text
   - Add messages for debugging
   - **Effort:** 2 hours

9. **Over-mocking** - 1619 mock instances across 142 files
   - Reduce mocking; use fakes/stubs where possible
   - **Effort:** 8 hours

### Error Handling

10. **Pass statements in exception handlers** - 6 silent catches
    - Add debug logging
    - **Effort:** 1 hour

11. **Missing exception context** - Multiple `except Exception:` without capturing variable
    - Use `except Exception as exc` for logging
    - **Effort:** 1 hour

### Tech Debt

12. **Deprecated env var** - `STT_MODEL_SIZE` -> `LYRA_STT_MODEL` migration pending
    - Schedule removal after migration window
    - **Effort:** 30 minutes

---

## Metrics Dashboard

| Domain | Issues | P0 | P1 | P2 | P3 |
|--------|--------|----|----|----|----|
| Architecture | 14 | 0 | 4 | 6 | 4 |
| Security | 14 | 1✓ | 2 | 0 | 10 |
| Code Smells | 42 | 0 | 3 | 18 | 18 |
| Type Safety | 28 | 0 | 0 | 3 | 25 |
| Async Patterns | 16 | 0 | 4 | 4 | 8 |
| Error Handling | 22 | 1✓ | 0 | 4 | 17 |
| Test Quality | 24 | 1✓ | 2 | 2 | 19 |
| Tech Debt | 19 | 0 | 0 | 2 | 17 |
| **Total** | **179** | **4** (3✓) | **15** | **39** | **118** |

✓ = fixed by #852, #879, #923

---

## Recommended Actions

### Immediate (This Sprint)

| # | Action | Effort | Impact | Status |
|---|--------|--------|--------|--------|
| 1 | Add callback source validation | 2h | High | — |
| 2 | Fix health endpoint path traversal | 30m | High | — |
| 3 | Add audit logging for `skip_permissions` | 2h | High | — |
| ~~4~~ | ~~Fix STT path traversal vulnerability~~ | ~~1h~~ | ~~High~~ | ✅ #852 |
| ~~5~~ | ~~Add schema migration `finally` block~~ | ~~1h~~ | ~~High~~ | ✅ #923 |
| ~~6~~ | ~~Fix extreme test sleep values~~ | ~~1h~~ | ~~High~~ | ✅ #879 |

### Next Sprint

| # | Action | Effort | Impact |
|---|--------|--------|--------|
| 7 | Complete ADR-048 migration (core stores -> infrastructure) | 4h | Medium |
| 8 | Add integration tests for SimpleAgent | 6h | High |
| 9 | Extract HubConfig dataclass | 4h | Medium |
| 10 | Fix flaky test patterns (sleep -> events) | 4h | Medium |
| 11 | Add pool eviction synchronization | 2h | Medium |
| 12 | Wrap blocking DNS in `asyncio.to_thread` | 1h | Medium |

### Backlog

| # | Action | Effort | Impact |
|---|--------|--------|--------|
| 13 | Create NATS voice client base class | 4h | Low |
| 14 | Extract config dataclasses (Pool, Router) | 4h | Low |
| 15 | Add adapter test coverage | 8h | Medium |
| 16 | Narrow broad exception handlers | 8h | Low |
| 17 | Add TypedDict schemas for JSON payloads | 4h | Low |
| 18 | Add NATS client lifecycle methods | 2h | Low |

---

## Technical Debt Score

**Score: 75/100** (where 100 = pristine)

| Category | Weight | Score | Weighted | Rationale |
|----------|--------|-------|----------|-----------|
| Architecture | 20% | 70 | 14.0 | Layer violations moderate, ADR-048 incomplete |
| Security | 25% | 88 | 22.0 | Strong posture, 3/4 P0 fixed, 1 remaining (callback validation) |
| Code Quality | 15% | 72 | 10.8 | God classes, long functions, DRY violations |
| Test Coverage | 25% | 55 | 13.75 | 52.51% line coverage, critical gaps in agents |
| Maintainability | 15% | 78 | 11.7 | Good docs, some magic numbers, deprecated APIs |
| **Total** | **100%** | | **72.25** | |

**Interpretation:** Codebase in good health. P0 reduction from 7→4 via #852, #879, #923. Remaining focus: callback validation (2h), test coverage gaps (agents 0%, adapters 12-15%).

---

## Top 10 Quick Wins

| # | Action | Effort | Impact |
|---|--------|--------|--------|
| 1 | Add path validation to `_read_secret()` | 15m | High security fix |
| 2 | Add return type to `_db_or_raise()` | 15m | Type safety |
| 3 | Move `StreamChunkTimeout` to core exceptions | 30m | Fix layer violation |
| ~~4~~ | ~~Add `finally` to schema migration~~ | ~~30m~~ | ~~Data integrity~~ ✅ |
| 5 | Fix `session_driver: object = None` type | 5m | Type safety |
| 6 | Replace `assert` with explicit validation | 30m | Production safety |
| 7 | Add `exc_info=True` to adapter exception logs | 30m | Debuggability |
| 8 | Fix health endpoint path traversal | 30m | Security |
| 9 | Add exception context capture in middleware | 30m | Debuggability |
| 10 | Document God class facades as intentional | 30m | Maintainability |

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

1. **Architecture** - Layer boundaries, dependency direction, circular imports, SRP violations
2. **Security** - OWASP Top 10 coverage, credential handling, input validation, SSRF protection
3. **Code Smells** - God classes, long functions, long parameters, DRY violations, magic numbers
4. **Type Safety** - Any usage, missing hints, type: ignore comments, bare generics
5. **Async Patterns** - Race conditions, resource leaks, blocking calls, fire-and-forget tasks
6. **Error Handling** - Exception specificity, logging patterns, cleanup (finally blocks)
7. **Test Quality** - Coverage metrics, flaky patterns, mock usage, assertion quality
8. **Tech Debt** - TODOs, deprecated APIs, legacy patterns, magic numbers

### Key Findings by Domain

**Architecture (14 issues):**
- 4 layer violations (core->infrastructure, infrastructure->core)
- 6 SRP violations in large classes/methods
- 4 circular dependency workarounds via dynamic imports

**Security (14 issues):**
- Strong overall posture with parameterized SQL throughout
- No hardcoded secrets; credentials from env vars
- SSRF protection via private IP detection
- Token/credential redaction in logs
- 1 P0 remaining: callback validation (path traversal fixed by #852)

**Code Smells (42 issues):**
- 3 God classes with 17+ methods
- 18 long functions (>50 lines)
- 12 long parameter lists (>5 params)
- 9 DRY violation hotspots

**Type Safety (28 issues):**
- 64 `Any` usages in adapters (discord.py/aiogram types)
- 9 bare `dict` without type parameters
- 6 `object` type hints that should be specific

**Async Patterns (16 issues):**
- 3 race conditions identified
- 3 resource leak patterns (NATS subscriptions, event bus)
- 1 blocking call in async context (DNS lookup)

**Error Handling (22 issues):**
- 0 bare `except:` clauses (excellent)
- 170+ generic `except Exception:` handlers
- 6 silent exception catches (pass statements)

**Test Quality (24 issues):**
- 52.51% line coverage, 37.99% branch coverage
- 45 source classes <40% coverage
- 13 flaky test patterns with long sleeps
- 1619 mock instances across 142 files

**Tech Debt (19 issues):**
- 0 TODO/FIXME comments (clean codebase)
- 3 deprecated patterns (env vars, APIs)
- 15+ magic numbers embedded in code
