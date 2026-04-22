# Error Handling Analysis: Infrastructure + NATS

### Summary
The infrastructure and NATS layers show disciplined error handling with no bare `except:` clauses. However, there are multiple instances of generic `Exception` catches that mask root causes, and several patterns where exceptions are logged and silently swallowed without re-raising. The codebase demonstrates good use of context-specific exceptions in some areas (sqlite3.OperationalError, json.JSONDecodeError) but inconsistent application across files.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/turn_store.py` | 190 | Generic `except Exception:` catches all errors during log_turn, logs and swallows without re-raising | Medium | Catch specific sqlite3 errors, consider re-raising for critical failures |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/turn_store_session.py` | 76, 103, 120 | Three methods silently swallow exceptions after logging - set_cli_session, increment_resume_count, end_session | Medium | Either re-raise or document intentional silent failure; add context to log messages |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/turn_store_queries.py` | 63, 80, 95, 126, 178 | Multiple `except sqlite3.Error:` blocks return None silently - callers cannot distinguish "not found" from "error" | Low | Consider raising or returning a Result type with error info |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/agent_store.py` | 61 | Generic `except Exception:` during connect - good pattern (logs, closes, re-raises) | Info | Good pattern - kept as reference |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/bot_agent_map.py` | 44 | Generic `except Exception:` during connect - same good pattern as agent_store | Info | Good pattern - kept as reference |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/identity_alias_store.py` | 276 | Generic `except Exception:` in validate_challenge - rolls back and re-raises, good pattern | Info | Good pattern - kept as reference |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/sqlite_base.py` | 35, 112 | Exception handling for cleanup (acceptable) and specific sqlite3 errors (good) | Info | Good pattern for best-effort cleanup |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_bus.py` | 136, 237, 271 | Generic `except Exception:` for unsubscribe and message parsing - appropriate for message bus resilience | Low | Acceptable for message bus - keeps service running despite bad messages |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_channel_proxy.py` | 171, 186, 220 | Generic `except Exception:` for streaming errors - logs with context and attempts recovery | Medium | Good error recovery pattern, but consider structured error types |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_stt_client.py` | 213, 225 | Generic `except Exception:` but properly translates to domain-specific STTUnavailableError | Info | Good pattern - translates transport errors to domain errors |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_tts_client.py` | 109, 126 | Generic `except Exception:` but properly translates to TtsUnavailableError | Info | Good pattern - translates transport errors to domain errors |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_image_client.py` | 214 | Generic `except Exception:` but properly translates to ImageUnavailableError | Info | Good pattern - translates transport errors to domain errors |

### Metrics
- Try/except blocks: 47 (21 infrastructure, 26 nats)
- Bare excepts: 0
- Swallowed exceptions: 9 (methods that catch and return/continue without re-raising)
- Generic Exception catches: 20
- Specific exception catches: 12 (sqlite3.Error, json.JSONDecodeError, asyncio.CancelledError, TimeoutError, ValidationError)
- Finally blocks present: 2

### Recommendations

1. **High Priority - TurnStore Session Methods**: In `turn_store_session.py`, the methods `set_cli_session`, `increment_resume_count`, and `end_session` all catch `Exception`, log it, and return silently. Document whether this is intentional (best-effort updates) or add re-raise for unexpected errors.

2. **Medium Priority - Query Error Handling**: In `turn_store_queries.py`, returning `None` on sqlite3.Error conflates "no matching row" with "database error". Consider returning a tuple `(value, error)` or raising a domain-specific exception.

3. **Medium Priority - TurnStore log_turn**: The `log_turn` method at line 190 catches all exceptions and only logs them. While logging turn failures may be non-critical, the exception should likely be re-raised to alert callers that persistence failed.

4. **Good Patterns to Preserve**:
   - `agent_store.py` and `bot_agent_map.py` connect methods: catch, log, cleanup, re-raise
   - NATS clients (STT/TTS/Image): translate generic NATS exceptions to domain-specific errors
   - `nats_channel_proxy.py`: proper use of finally block for cleanup
   - `credential_store.py`: atomic key generation with finally for cleanup

5. **Documentation**: Add docstrings explaining the error handling strategy for methods that intentionally swallow exceptions (e.g., "best-effort session update, failures are logged but do not interrupt flow").
