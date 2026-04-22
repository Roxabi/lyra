# Type Safety Analysis: Core Agent + CLI

### Summary
The core/agent and core/cli areas show moderate type safety with intentional compromises for circular import avoidance in the CLI layer. The agent codebase has 7 files with `Any` imports, 4 instances of bare generic types (`dict`, `set` without parameters), and 1 `type: ignore` comment. The CLI codebase uses `type: ignore[attr-defined]` comments (9 instances) primarily in mixin classes due to Python's limitation with type-checking mixin method access.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| agent/agent.py | 55 | `smart_routing_decorator: Any | None` - untyped decorator parameter | Medium | Define a Protocol for routing decorators or use `Callable[..., Awaitable[...]]` |
| agent/agent.py | 57,60 | `instance_overrides: dict` - bare generic without type parameters | Medium | Use `dict[str, Any]` to make intent explicit |
| agent/agent.py | 84 | `self._task_registry: set | None` - bare generic | Medium | Use `set[asyncio.Task] | None` |
| agent/agent.py | 163 | `_build_router_kwargs(self) -> dict:` - untyped return | Low | Use `dict[str, Any]` for explicit contract |
| agent/agent_config.py | 141 | `_coerce_routing_table_keys(cls, v: Any)` - `Any` in validator | Low | Acceptable for Pydantic validator; document in comment |
| agent/agent_builder.py | 77,94,115,124,139 | Five functions with bare `dict` parameters | Medium | Add type parameters: `dict[str, Any]` or specific types |
| agent/agent_db_loader.py | 36 | `instance_overrides: dict | None` - bare generic | Medium | Use `dict[str, Any] | None` |
| agent/agent_refiner.py | 75 | `patterns: dict` - bare generic in dataclass | Low | Use `dict[str, bool]` per agent_config.py definition |
| agent/agent_refiner.py | 83 | `fields: dict[str, Any]` - `Any` in value type | Low | Acceptable for flexible patch schema; consider TypedDict |
| agent/agent_refiner.py | 144 | `cast("list[MessageParam]", messages)` - runtime cast | Low | Acceptable for SDK interop; prefer Protocol if possible |
| agent/agent_refiner.py | 148 | `# type: ignore[union-attr]` on ContentBlock | Low | Narrow type with `hasattr` check or `isinstance` |
| agent/agent_seeder.py | 70 | `_m(key: str, default: Any = None) -> Any` - `Any` in helper | Medium | Use `Any` for return or narrow with overloads |
| cli/cli_non_streaming.py | 19,59 | `entry: object` to avoid circular import | Low | Documented workaround; acceptable with runtime assert |
| cli/cli_streaming.py | 33,179 | `entry: object` to avoid circular import | Low | Documented workaround; acceptable with runtime assert |
| cli/cli_pool_lifecycle.py | 32 | `# type: ignore[attr-defined]` for mixin method | Low | Inherent to mixin pattern; no clean alternative |
| cli/cli_pool_lifecycle.py | 87 | `# type: ignore[attr-defined]` for mixin method | Low | Inherent to mixin pattern; no clean alternative |
| cli/cli_pool_streaming.py | 54,62,63,71,72,79,108 | 7x `# type: ignore[attr-defined]` | Low | Inherent to mixin pattern; no clean alternative |
| cli/cli_pool.py | 70 | `Callable[[str, str], Coroutine[Any, Any, None]]` - `Any` in Coroutine | Low | Acceptable for generic async callback signature |

### Metrics
- **Type coverage**: ~85% (estimated based on public API analysis)
  - Most public methods have return type hints
  - Most parameters have type hints
  - Exceptions are primarily in internal/helper functions
- **`Any` usage**: 7 instances across both areas
  - 4 in agent (config validator, seeder helper, decorator param, dataclass field)
  - 3 in CLI (Coroutine generics, assert isinstance patterns)
- **`type: ignore`**: 10 instances total
  - 1 in agent (union-attr on SDK ContentBlock)
  - 9 in CLI (attr-defined for mixin pattern)

### Recommendations

1. **High Priority**: Add type parameters to bare generics in `agent_builder.py`
   - Functions like `_build_smart_routing_from_dict`, `_build_tts_from_dict`, `_build_commands_from_dict` should specify `dict[str, Any]` or more specific types

2. **High Priority**: Fix `self._instance_overrides: dict` and `self._task_registry: set` in `agent.py`
   - These are private attributes but type parameters improve IDE support and catch bugs

3. **Medium Priority**: Define a Protocol for `smart_routing_decorator`
   - Current `Any | None` type offers no safety; a Protocol would document the expected interface

4. **Medium Priority**: Align `RefinementContext.patterns` type with `Agent.patterns`
   - Line 75 of `agent_refiner.py` uses bare `dict` while `Agent` uses `dict[str, bool]`

5. **Low Priority**: Add `hasattr` guard instead of `type: ignore[union-attr]` in `agent_refiner.py:148`
   - Example: `if hasattr(block, "text") and block.text: return block.text`

6. **Low Priority**: Document circular import workaround in CLI files
   - The `entry: object` pattern is acceptable but could use a docstring or comment explaining the trade-off

7. **Low Priority**: Consider TypedDict for `RefinementPatch.fields`
   - The `dict[str, Any]` is flexible but a TypedDict could document expected keys

### Architectural Notes

The CLI mixin pattern (`CliPoolLifecycleMixin`, `CliPoolStreamingMixin`, etc.) intentionally uses `# type: ignore[attr-defined]` because Python's type system cannot express "this class will be mixed with another that provides these methods." This is a known limitation and the ignores are justified. Alternative approaches (composition over inheritance, Protocol + runtime check) would require significant refactoring for marginal type safety gains.
