# Async Patterns Analysis: LLM + Agents + Misc

### Summary
The codebase demonstrates mature async patterns with proper use of async context managers, asyncio.to_thread for blocking operations, and cleanup in finally blocks. A few areas warrant attention: shared mutable state without synchronization in the NATS driver, broad exception handling that may mask issues, and subprocess operations that should use async variants consistently.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| src/lyra/llm/drivers/nats_driver.py | 85-90 | Shared dict `_worker_freshness` modified without lock; `_any_worker_alive()` prunes while `_on_heartbeat()` writes | Medium | Add `asyncio.Lock` or ensure single-threaded access pattern is documented |
| src/lyra/llm/drivers/nats_driver.py | 64, 78, 243, 287 | Broad `except Exception:` catches in async callbacks; may mask async-specific errors | Low | Consider catching specific exceptions or re-raising after logging |
| src/lyra/llm/drivers/sdk.py | 122, 251, 258 | Broad `except Exception:` in tool execution and streaming; errors silently logged but not propagated appropriately in some paths | Low | Review whether all error paths correctly emit `ResultLlmEvent(is_error=True)` |
| src/lyra/llm/smart_routing.py | 234-240 | Broad exception catch in routing classification falls back silently; routing failure not distinguishable in logs | Low | Add structured logging with routing failure indicator |
| src/lyra/agents/simple_agent.py | 130 | Broad `except Exception:` in session tools construction disables processor pipeline silently | Medium | Warn user or surface error; consider retry mechanism |
| src/lyra/agents/anthropic_agent.py | 111 | Broad `except Exception:` in session tools setup | Medium | Same as above; surface or retry |
| src/lyra/agents/anthropic_agent.py | 201-203 | Exception re-raised after logging, but pool CB failure may not be distinguishable from other errors | Low | Consider custom exception type for CB failures |
| src/lyra/agents/simple_agent_prompts.py | 87-88 | Temp file cleanup in finally block; good pattern, but `tmp_path.unlink(missing_ok=True)` could fail on permission errors | Low | Consider wrapping in try/except to avoid exception during cleanup |
| src/lyra/monitoring/checks.py | 30, 49 | Uses blocking `subprocess.run()` in sync functions, properly offloaded via `asyncio.to_thread()` | None | Pattern is correct |
| src/lyra/monitoring/escalation.py | 75-88 | Uses `asyncio.create_subprocess_exec()` for non-blocking subprocess; correct pattern | None | Good |
| src/lyra/stt/__init__.py | 87 | Uses `asyncio.to_thread()` for blocking sync transcription; correct pattern | None | Good |
| src/lyra/cli_bot.py | 66-67, 95-96, 118-119 | Consistent `finally: await store.close()` pattern for resource cleanup | None | Good |
| src/lyra/cli_setup.py | 49, 154 | Proper `finally` cleanup for bot session and credential store | None | Good |
| src/lyra/llm/decorators.py | 74 | Uses `asyncio.sleep()` for retry backoff; non-blocking | None | Good |
| src/lyra/llm/drivers/nats_driver.py | 209 | Bounded `asyncio.Queue(maxsize=512)` prevents unbounded memory growth | None | Good |

### Metrics
- Async functions analyzed: 52 (26 in llm, 6 in agents, 9 in monitoring, 2 in stt, 9 in misc CLI)
- Blocking calls in async: 0 (all properly offloaded via `asyncio.to_thread()` or async subprocess)
- Potential race conditions: 1 (`_worker_freshness` dict in nats_driver.py)
- Broad exception handlers: 8 instances (mostly intentional with logging, but review recommended)
- Resource leaks: 0 detected (consistent finally block cleanup patterns)

### Recommendations
1. **High Priority**: Add synchronization (asyncio.Lock or document thread-safety) for `_worker_freshness` dict in `nats_driver.py` line 85-90
2. **Medium Priority**: Surface or retry session tools construction failures in `simple_agent.py` and `anthropic_agent.py` rather than silently disabling processor pipeline
3. **Low Priority**: Review broad exception handlers in LLM drivers to ensure error context is preserved for debugging
4. **Low Priority**: Add structured logging for smart routing failures to distinguish from successful fallbacks
5. **Documentation**: Consider documenting the async safety patterns used (asyncio.to_thread for blocking I/O, async context managers for resources)
