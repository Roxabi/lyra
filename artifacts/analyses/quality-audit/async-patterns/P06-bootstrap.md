# Async Patterns Analysis: Bootstrap

### Summary

The bootstrap area contains 30 Python files with 31 async functions. While most async patterns are well-implemented with proper `await` usage and cleanup in `finally` blocks, there are several blocking synchronous operations called from async contexts that could cause event loop stalls. The primary concerns are synchronous file I/O and database operations within async functions, plus `sys.exit()` calls that bypass async cleanup.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/bootstrap/factory/unified.py` | 56 | `acquire_lockfile()` - synchronous file I/O in async `_bootstrap_unified()` | Medium | Run in thread pool via `asyncio.to_thread()` |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/factory/unified.py` | 68 | `vault_dir.mkdir()` - synchronous filesystem operation in async context | Low | Use `aiofiles.os.makedirs` or `asyncio.to_thread()` |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/factory/unified.py` | 125-134 | `stores.agent.get(n)` - synchronous SQLite query in async function | Medium | Make `AgentStore.get()` async or use thread pool |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/factory/unified.py` | 142 | `_load_messages()` - synchronous file read in async function | Low | Consider async file loading for large configs |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/factory/unified.py` | 95, 111, 135 | `sys.exit()` in async function bypasses cleanup | Medium | Raise custom exception, handle at top level |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/factory/bot_agent_map.py` | 43, 48, 60 | `agent_store.get_bot_agent()` and `agent_store.get()` - synchronous DB calls in async `resolve_bot_agent_map()` | Medium | Make store methods async |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/infra/lockfile.py` | 31-65 | Lockfile functions use sync file I/O, called from async bootstrap | Medium | Create async versions or use thread pool |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/standalone/hub_standalone.py` | 64 | `acquire_lockfile()` - sync file I/O in async `_bootstrap_hub_standalone()` | Medium | Same as above |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/standalone/hub_standalone.py` | 77 | `vault_dir.mkdir()` - sync filesystem op | Low | Use async alternative |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/standalone/hub_standalone.py` | 107-108 | `load_agent_configs()` - sync function with DB access, called from async | Medium | Make async or wrap in thread |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/standalone/hub_standalone.py` | 59, 70, 98, 111 | `sys.exit()` in async context | Medium | Use exception-based control flow |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/standalone/adapter_standalone.py` | 55 | `vault_dir.mkdir()` - sync filesystem op | Low | Minor concern |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/standalone/adapter_standalone.py` | 204-219 | `agent_store.get_bot_settings()` - synchronous DB call in async function | Medium | Make async or use thread pool |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/standalone/stt_adapter_standalone.py` | 87, 142 | `_active_count += 1` / `-=` without synchronization | Low | GIL protects single ops; consider atomic counter for clarity |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/standalone/tts_adapter_standalone.py` | 114, 166 | `_active_count += 1` / `-=` without synchronization | Low | Same as above |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/wiring/bootstrap_wiring.py` | 158 | `agent_store.get_bot_settings()` - sync DB in async `wire_discord_adapters()` | Medium | Make store method async |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/wiring/bootstrap_wiring.py` | 280 | `sys.exit()` in async `_build_bot_auths()` | Medium | Raise exception instead |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/standalone/stt_adapter_standalone.py` | 68, 126 | `except Exception:` - overly broad exception handling | Low | Catch specific exceptions |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/standalone/tts_adapter_standalone.py` | 95, 150 | `except Exception:` - overly broad exception handling | Low | Catch specific exceptions |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/wiring/bootstrap_wiring.py` | 221 | `except Exception:` - catches all including KeyboardInterrupt | Low | Catch specific exceptions |

### Metrics

- **Async functions**: 31
- **Blocking calls in async**: 15+ (file I/O, SQLite operations)
- **Potential race conditions**: 2 (counter increments without locks - low risk due to GIL)
- **Bare `except Exception:` blocks**: 5
- **`sys.exit()` in async contexts**: 15

### Positive Patterns Observed

1. **Proper async context manager usage**: `open_stores()` correctly uses `async with` and `finally` cleanup
2. **Correct `await` usage**: All `.connect()`, `.start()`, `.close()` calls are properly awaited
3. **`asyncio.gather` with `return_exceptions=True`**: Shutdown handlers properly collect all task exceptions
4. **Watchdog pattern**: `watchdog()` function correctly monitors multiple tasks with proper cancellation handling
5. **`asyncio.wait_for` usage**: Timeout handling properly implemented for NATS connection readiness

### Recommendations

1. **High Priority**: Replace `sys.exit()` calls in async functions with raising a custom `BootstrapError` exception that can be caught at the top-level entry point. This allows proper async cleanup via `finally` blocks.

2. **High Priority**: Wrap blocking SQLite operations (`agent_store.get()`, `agent_store.get_bot_agent()`, `agent_store.get_bot_settings()`) with `asyncio.to_thread()` or make the store methods natively async.

3. **Medium Priority**: Create async versions of lockfile functions or use `asyncio.to_thread()` when calling from async bootstrap functions.

4. **Medium Priority**: Replace bare `except Exception:` handlers with specific exception types to avoid masking `KeyboardInterrupt`, `SystemExit`, or other critical exceptions.

5. **Low Priority**: Consider using `aiofiles` for filesystem operations in async bootstrap functions, though the impact is minimal for small config files.

6. **Low Priority**: Add explicit `asyncio.Lock` protection for `_active_count` in STT/TTS adapters if strict consistency is required, though the current implementation is safe under Python's GIL.
