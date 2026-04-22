# Tech Debt Analysis: core/commands + stores + pool + messaging

**Date:** 2026-04-22
**Scope:** `src/lyra/core/commands/**/*.py`, `src/lyra/core/stores/**/*.py`, `src/lyra/core/pool/**/*.py`, `src/lyra/core/messaging/**/*.py`
**Files analyzed:** 31

## Summary

The analyzed modules are well-maintained with minimal TODO/FIXME debt. Primary concerns are:
1. A deprecated API (`register_session_command`) that should be scheduled for removal
2. Broad exception catching patterns that obscure error types
3. Several `object` type hints that could be more specific
4. Duplicated default timeout values (30.0) across multiple files

## Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| command_router.py | 149 | Deprecated API: `register_session_command` marked as deprecated | Medium | Schedule removal date or create migration guide |
| command_config.py | 22, 32 | Magic number: `timeout: float = 30.0` duplicated | Low | Extract to shared constant |
| command_loader.py | 47 | Magic number: `timeout: float = 30.0` duplicated | Low | Extract to shared constant |
| command_loader.py | 114 | Broad exception: `except Exception` catches all | Medium | Narrow to specific exceptions (OSError, tomllib.TOMLDecodeError) |
| command_router.py | 116 | Broad exception: catches all when loading processor registry | Low | Document acceptable exceptions or narrow |
| command_router.py | 46 | Large constructor: PLR0913 (11 params) — documented as DI pattern | Info | Acceptable per architecture |
| command_router.py | 61, 81, 143 | Type hint: `object` for session_driver/tools | Low | Consider TYPE_CHECKING protocol or generic |
| command_config.py | 30 | Type hint: `object` for tools with comment `# SessionTools` | Low | Use TYPE_CHECKING import |
| builtin_commands.py | 223 | Bare `pass` in except OSError block | Low | Document intent or add logging |
| pairing_config.py | 25 | Magic constant: `_MAX_CODE_ATTEMPTS = 10` | Low | Document rationale in comment |
| pairing_config.py | 50-55 | Magic numbers in PairingConfig defaults | Info | Already well-named in model, acceptable |
| pairing.py | 194, 208 | Broad exception: `except BaseException` and `except Exception` | Medium | Review transaction rollback edge cases |
| json_agent_store.py | 81, 228 | Broad exception catches for JSON/IO errors | Low | Already logged, acceptable resilience |
| agent_store_migrations.py | 29 | Narrow exception: `aiosqlite.OperationalError` | Good | Model for other code |
| pool_processor_streaming.py | 49, 51, 109, 115 | Type ignore comments: `# type: ignore[misc]` | Medium | Investigate if typing can be improved |
| pool_processor_streaming.py | 26, 101-102 | Type hint: `object` for processor/stream_done_event | Medium | Use TYPE_CHECKING Protocol |
| pool_processor.py | 36 | C901 (complexity) documented: "debounce + cancel-in-flight adds inherent branches" | Info | Acceptable per architecture |
| pool_processor.py | 52, 150 | Broad exception catches | Low | Already logged, acceptable resilience |
| pool.py | 32 | Large constructor: PLR0913 documented as "DI constructor" | Info | Acceptable per architecture |
| pool_observer.py | 84, 116, 131, 153, 187 | Broad exception catches with logging | Low | Pattern: resilient async helpers, acceptable |
| pool_processor_exec.py | 98 | C901 + PLR0915 documented: "session-id update adds branches" | Info | Acceptable per architecture |
| pool_processor_exec.py | 162 | Type ignore: `# type: ignore[misc]` | Medium | Union type narrowing issue |
| inbound_bus.py | 80 | `del bot_id` to satisfy protocol | Info | Documented workaround, acceptable |
| messages.py | 45 | Broad exception: catches all on TOML load | Low | Could narrow to OSError, tomllib exceptions |
| tool_display_config.py | 56-59 | Magic numbers: thresholds and limits | Low | Already configurable via TOML, acceptable |
| tool_recap_format.py | 19, 22, 25 | Magic constants: display limits | Low | Move to config or document rationale |

## Metrics

| Category | Count |
|----------|-------|
| TODOs | 0 |
| FIXMEs | 0 |
| XXX/HACK | 0 |
| Deprecated patterns | 1 |
| Broad exception catches | 12 |
| Type ignore comments | 5 |
| Object type hints | 6 |
| Magic numbers (non-config) | 4 |
| Pass statements (potential stubs) | 0 |

## Recommendations

### Priority 1: High
None identified — codebase is clean.

### Priority 2: Medium

1. **Deprecated API Migration** (command_router.py:149)
   - Add removal timeline to docstring (e.g., "Will be removed in v2.0")
   - Create migration guide for users of `register_session_command`
   - Issue deprecation warning at runtime if called

2. **Type Annotation Improvements** (pool_processor_streaming.py)
   - Replace `object` hints with `TYPE_CHECKING` protocols
   - Investigate `type: ignore[misc]` comments — may indicate real typing gaps

3. **Exception Narrowing** (command_loader.py:114, pairing.py:194)
   - Replace `except Exception` with specific exception types
   - For TOML loading: `OSError | tomllib.TOMLDecodeError`
   - For transaction: keep `BaseException` but document why

### Priority 3: Low

1. **Extract Timeout Constant**
   - Create `DEFAULT_COMMAND_TIMEOUT = 30.0` in command_config.py
   - Import in command_loader.py and SessionCommandEntry

2. **Document Magic Numbers**
   - Add inline comments explaining `_MAX_CODE_ATTEMPTS = 10`
   - Add comments for display limits in tool_recap_format.py

3. **Type Hints for DI Constructors**
   - Consider using `TYPE_CHECKING` block with Protocol for session_driver/tools
   - Would improve IDE support without runtime import cost

## Patterns Observed

### Positive Patterns
- Consistent use of `# noqa: ...` with rationale comments
- Exception handlers log before suppressing
- Dataclasses used for immutable config
- Clear separation of concerns (processor vs pool vs observer)

### Areas for Improvement
- Several `object` type hints could use Protocol pattern
- Broad exceptions in resilient paths are acceptable but should be documented
- Timeout values scattered across multiple files

## Related Issues

- ADR-045: NATS transport architecture (may affect Bus protocol)
- Issue #300: Pool processing extraction (completed)
- Issue #753: Pool processor streaming extraction (completed)
