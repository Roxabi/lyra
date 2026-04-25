# Error Handling Analysis: P7 Infrastructure

### Summary
The P7 infrastructure layer (stores, NATS clients, serialization) shows disciplined error handling with no bare `except:` clauses. However, there are significant patterns of generic `Exception` catches that mask root causes, several cases where exceptions are logged and silently swallowed, and inconsistent error propagation. The NATS clients demonstrate good domain-error translation patterns, but SQLite stores frequently return `None` on errors without distinguishing "not found" from "error".

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/sqlite_base.py` | 35 | Generic `except Exception:` in `close_all_sqlite_stores()` cleanup - logs with debug level, acceptable for teardown | Low | Acceptable for cleanup; keep current pattern |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/sqlite_base.py` | 131, 140 | `except asyncio.CancelledError: pass` - silent swallow without logging | Medium | Add debug log for cancellation or document intentional silence |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/auth_store.py` | 110 | `except RuntimeError: pass` - no running loop case silently skipped | Low | Acceptable; fallback behavior is intentional |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/turn_store.py` | 190 | Generic `except Exception:` catches all errors during log_turn, logs and swallows without re-raising | Medium | Consider re-raising or return success/failure indicator |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/turn_store_session.py` | 76 | `except Exception:` in set_cli_session - logs and returns silently | Medium | Document intentional best-effort update or re-raise |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/turn_store_session.py` | 103 | `except Exception:` in increment_resume_count - logs and returns silently | Medium | Document intentional best-effort update or re-raise |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/turn_store_session.py` | 120 | `except Exception:` in end_session - logs and returns silently | Medium | Document intentional best-effort update or re-raise |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/turn_store_queries.py` | 63 | `except sqlite3.Error:` returns None - conflates "not found" with "error" | Medium | Return Result type or raise domain-specific exception |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/turn_store_queries.py` | 80, 95, 126 | Same pattern - sqlite3.Error returns None without error context | Medium | Return Result type or raise domain-specific exception |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/turn_store_queries.py` | 178 | `except Exception:` in backfill_sessions - logs and swallows | Low | Acceptable for one-time migration; keep current |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/agent_store.py` | 61 | `except Exception:` in connect - logs, closes connection, re-raises | Info | Good pattern - cleanup then re-raise |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/bot_agent_map.py` | 44 | `except Exception:` in connect - logs, closes connection, re-raises | Info | Good pattern - cleanup then re-raise |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/identity_alias_store.py` | 276 | `except Exception:` in validate_challenge - rolls back and re-raises | Info | Good pattern - rollback then re-raise |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_bus.py` | 136 | `except Exception:` in stop() unsubscribe - logs and continues | Low | Acceptable for cleanup; keeps shutdown clean |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_bus.py` | 237, 271 | `except Exception:` for JSON parse and deserialize - logs with context | Low | Acceptable for message bus resilience |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_channel_proxy.py` | 171 | `except Exception:` in send_streaming - logs and attempts error recovery | Medium | Good recovery pattern; structured error type would help |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_channel_proxy.py` | 186 | Nested `except Exception:` for stream_error publish failure - logs warning | Medium | Acceptable fallback for error-recovery path |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_channel_proxy.py` | 220 | `except Exception:` in publish_stream_errors - logs warning | Low | Acceptable for shutdown cleanup |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_channel_proxy.py` | 193, 257, 270 | `pass` after iterator drain in audio stream methods | Info | Intentional drain for not-implemented features |
| `/home/mickael/projects/lyra/packages/roxabi-nats/src/roxabi_nats/_serialize.py` | 115 | `except NameError: pass` in _get_hints - intentional fallback to extended resolution | Info | Good pattern - try simple then fallback |
| `/home/mickael/projects/lyra/packages/roxabi-nats/src/roxabi_nats/_serialize.py` | 135 | `except Exception:` in _get_hints - returns empty dict, no caching | Medium | Log at debug level for visibility; currently silent |
| `/home/mickael/projects/lyra/packages/roxabi-nats/src/roxabi_nats/adapter_base.py` | 143 | `except Exception:` in _dispatch JSON parse - logs error and returns | Low | Acceptable for message handler resilience |
| `/home/mickael/projects/lyra/packages/roxabi-nats/src/roxabi_nats/adapter_base.py` | 197 | `except Exception:` in heartbeat loop - logs warning with exc_info | Info | Good pattern - logs context and continues loop |
| `/home/mickael/projects/lyra/packages/roxabi-nats/src/roxabi_nats/readiness.py` | 93, 95 | `except nats.errors.TimeoutError: pass` and `NoRespondersError: pass` | Info | Intentional probe retry loop |
| `/home/mickael/projects/lyra/packages/roxabi-nats/src/roxabi_nats/readiness.py` | 97 | `except Exception:` in wait_for_hub - logs with full traceback | Info | Good pattern - catches unexpected, logs fully |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_image_client.py` | 155 | `except Exception as exc:` - translates to ImageUnavailableError via _raise_nats_failure | Info | Good pattern - domain error translation |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_stt_client.py` | 176 | `except Exception as exc:` - translates to STTUnavailableError | Info | Good pattern - domain error translation |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_tts_client.py` | 168 | `except Exception as exc:` - translates to TtsUnavailableError | Info | Good pattern - domain error translation |

### Metrics
- Files analyzed: 22
- Bare excepts: 0
- Generic Exception catches: 20
- Swallowed errors (pass/return without re-raise): 11
- Missing context (silent failures): 4
- Good patterns (log + re-raise or domain translation): 8
- Specific exception catches: 15 (sqlite3.Error, json.JSONDecodeError, asyncio.CancelledError, TimeoutError, ValidationError, asyncio.QueueFull, nats.errors.*)

### Recommendations

1. **High Priority - TurnStore Session Methods**: The three session methods in `turn_store_session.py` (set_cli_session, increment_resume_count, end_session) catch `Exception`, log it, and return silently. Add docstrings documenting whether this is intentional best-effort behavior: "These updates are non-critical; failures are logged but do not interrupt the session lifecycle."

2. **Medium Priority - Query Error Context**: In `turn_store_queries.py`, the pattern of returning `None` on sqlite3.Error conflates "no matching row" with "database error". Consider returning a Result type `(value, error)` or raising a domain-specific `StoreError` exception that callers can distinguish from None.

3. **Medium Priority - Type Hint Resolution Logging**: In `_serialize.py` line 135, the fallback `except Exception:` returns an empty dict without any logging. Add a debug-level log statement for visibility when type hint resolution fails, to aid debugging serialization issues.

4. **Good Patterns to Preserve**:
   - `agent_store.py` and `bot_agent_map.py` connect methods: catch, log, cleanup, re-raise
   - NATS clients (STT/TTS/Image): translate generic NATS exceptions to domain-specific errors (STTUnavailableError, TtsUnavailableError, ImageUnavailableError)
   - `nats_channel_proxy.py`: proper use of finally block for stream tracking cleanup
   - `readiness.py`: probe loop with specific exception handling for expected cases and full traceback for unexpected

5. **Documentation**: Add error handling strategy documentation to store classes, explaining which methods use best-effort semantics (silent failure) vs which require explicit error handling by callers.
