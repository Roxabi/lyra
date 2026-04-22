# Type Safety Analysis: Core Commands, Stores, Pool, Messaging

### Summary
The core/commands, core/stores, core/pool, and core/messaging modules demonstrate generally strong type coverage with modern Python typing practices (from `__future__ import annotations`, dataclasses, Protocols). However, there are 5 `type: ignore[misc]` suppressions in the pool processing code and 4 files using `Any` for dynamic data structures. The most significant issues are loose typing on `object` parameters that should be `object | None` and a few `dict` annotations without key/value specificity.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| pool/pool_processor_streaming.py | 49 | `# type: ignore[misc]` on `await _aclose()` | Medium | Type `stream_done_event` parameter as `asyncio.Event | None` |
| pool/pool_processor_streaming.py | 51 | `# type: ignore[misc]` on `stream_done_event.set()` | Medium | Same as above |
| pool/pool_processor_streaming.py | 109 | `# type: ignore[misc]` on `stream_done_event.wait()` | Medium | Type `stream_done_event` as `asyncio.Event | None` |
| pool/pool_processor_streaming.py | 115 | `# type: ignore[misc]` on processor.post call | Medium | Type `processor` parameter as `Protocol` with `post` method |
| pool/pool_processor_exec.py | 162 | `# type: ignore[misc]` on `await result` | Low | Use TypeGuard or Narrowing for coroutine check |
| command_router.py | 61 | `session_driver: object = None` - incompatible types | High | Change to `object | None = None` |
| command_config.py | 31 | `tools: object` - loses type info for SessionTools | Medium | Use `SessionTools | None` or a Protocol |
| messaging/tool_display_config.py | 64-65 | `**data: Any` and `show_raw: Any` | Medium | Use TypedDict or stricter mapping type |
| messaging/message.py | 111 | `metadata: dict[str, Any]` | Low | Acceptable for dynamic metadata; document schema |
| messaging/events.py | 45 | `input: dict[str, Any]` | Low | Acceptable for LLM tool inputs (dynamic by design) |
| command_loader.py | 61 | `_parse_manifest(data: dict)` missing key/value types | Medium | Use `dict[str, Any]` or TypedDict |
| command_patterns.py | 30 | `load_pattern_configs` returns `dict[str, dict]` | Low | Use `dict[str, dict[str, Any]]` for inner dict |
| messaging/messages.py | 44 | `self._templates: dict[str, Any]` | Low | Consider TypedDict for template structure |

### Metrics
- Type coverage: ~92% (most public APIs have complete type hints)
- `Any` usage: 4 files, ~7 instances (mostly appropriate for dynamic metadata/tool input)
- `type: ignore`: 5 instances (all in pool processing, all `[misc]` category)

### Recommendations

1. **High Priority** - Fix `session_driver: object = None` in command_router.py (line 61). This is a type safety violation where `None` is assigned to a non-Optional type.

2. **Medium Priority** - Replace `object` with proper types in command_config.py (line 31). The `tools: object` should be typed as `SessionTools | None` or a Protocol to enable IDE support and static checking.

3. **Medium Priority** - Eliminate `# type: ignore[misc]` suppressions in pool_processor_streaming.py by adding proper type annotations:
   - Type `stream_done_event` as `asyncio.Event | None`
   - Type `processor` parameter with a Protocol that has `post` method

4. **Low Priority** - Consider TypedDict for configuration dictionaries where schemas are known (e.g., `_parse_manifest` input, `load_pattern_configs` output).

5. **Low Priority** - Document the expected schema for `dict[str, Any]` metadata fields in message.py rather than trying to type them statically (dynamic by design).

### Strengths Observed
- Consistent use of `from __future__ import annotations` enabling modern union syntax
- Strong use of dataclasses with frozen=True for immutability
- Proper use of `TYPE_CHECKING` blocks for import-time type-only dependencies
- Protocol definitions with `@runtime_checkable` for duck typing
- Generic types used correctly (e.g., `Bus[T]`, `LocalBus[T]`)
- Most public APIs have complete type hints
- Type aliases used for complex callable signatures (e.g., `AsyncHandler`, `BuiltinHandler`)
