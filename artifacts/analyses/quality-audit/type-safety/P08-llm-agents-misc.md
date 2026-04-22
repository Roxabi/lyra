# Type Safety Analysis: LLM + Agents + Misc

### Summary
The analyzed codebase demonstrates good type safety practices with Protocol-based interfaces and modern type hint syntax (`X | None`). However, there are opportunities for improvement: `Any` type usage is prevalent in driver implementations (especially for message callbacks and capability dicts), and the STT module uses `# type: ignore` comments for external untyped imports. The monitoring module has an untyped dict return that should be narrowed.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| src/lyra/llm/base.py | 34 | `capabilities: dict[str, Any]` in Protocol | Low | Document that Any is intentional for extensible capability metadata |
| src/lyra/llm/drivers/sdk.py | 33 | `TOOLS: list[dict[str, Any]]` - untyped tool schema | Medium | Define `ToolSchema` TypedDict for tool definitions |
| src/lyra/llm/drivers/sdk.py | 71, 218 | `kwargs: dict[str, Any]` - API request building | Low | Acceptable for dynamic kwargs construction |
| src/lyra/llm/drivers/sdk.py | 87 | `final: Any = None` - accumulator for loop | Low | Could be typed as `Message | None` with proper import |
| src/lyra/llm/drivers/sdk.py | 108 | `tool_results: list[dict[str, Any]]` | Medium | Define `ToolResult` TypedDict |
| src/lyra/llm/drivers/nats_driver.py | 69, 211 | `msg: Any` in callback handlers | Medium | Use `nats.aio.msg.Msg` type with TYPE_CHECKING guard |
| src/lyra/llm/drivers/nats_driver.py | 108, 216 | `payload_dict: dict[str, Any]` | Low | Acceptable for JSON-serializable payloads |
| src/lyra/llm/drivers/nats_driver.py | 209 | `asyncio.Queue[Any]` - untyped queue | Medium | Define typed LlmEventChunk or use `dict` |
| src/lyra/llm/drivers/cli.py | 24 | `capabilities: dict` without type args | High | Change to `dict[str, Any]` or more specific type |
| src/lyra/llm/decorators.py | 34, 113 | `capabilities: dict` without type args | High | Change to `dict[str, Any]` to match Protocol |
| src/lyra/llm/smart_routing.py | 117 | `attachments: list` untyped | Medium | Change to `list[Attachment]` |
| src/lyra/llm/smart_routing.py | 183 | `capabilities: dict[str, Any]` | Low | Delegates to inner, acceptable |
| src/lyra/agents/simple_agent.py | 282 | `meta: dict[str, Any]` | Low | Could use TypedDict for metadata schema |
| src/lyra/config.py | 102, 128, 159 | `dict[str, Any]` for raw config parsing | Low | Acceptable for TOML parsing layer |
| src/lyra/monitoring/checks.py | 64 | Return type `dict | None` is untyped | Medium | Define `HealthJson` TypedDict or use `dict[str, Any]` |
| src/lyra/monitoring/checks.py | 102, 162, 184 | `health_json: dict` parameter untyped | Medium | Define `HealthDetailJson` TypedDict |
| src/lyra/stt/__init__.py | 91-96 | `# type: ignore[import-missing]` for voicecli imports | Low | Consider creating stubs or protocol for voicecli |
| src/lyra/stt/__init__.py | 157 | `# type: ignore[import-untyped]` for unload_model | Low | Same as above |
| src/lyra/stt/__init__.py | 153 | `# noqa: ANN001` - missing param annotation | Medium | Add `socket_path: Path` annotation (already present) |
| src/lyra/cli_bot.py | 9 | Uses `Optional[str]` instead of `str | None` | Low | Modernize to union syntax for consistency |
| src/lyra/cli_agent.py | 20 | Uses `Optional[Path]` instead of `Path | None` | Low | Modernize to union syntax for consistency |

### Metrics
- **Type coverage**: ~85% (estimated based on public API analysis)
- **`Any` usage**: 15+ instances across analyzed files
- **`type: ignore`**: 6 instances (all in stt/__init__.py for external imports)
- **`noqa: ANN`**: 1 instance

### Recommendations

1. **High Priority - Fix missing type args on dict declarations** (Lines: cli.py:24, decorators.py:34,113)
   - Change `capabilities: dict` to `capabilities: dict[str, Any]` to match the Protocol definition
   - This is a clear type safety gap that mypy would flag with `--disallow-any-generics`

2. **Medium Priority - Define TypedDict schemas for JSON payloads**
   - Create `ToolSchema`, `ToolResult`, `HealthDetailJson` TypedDicts for structured dict types
   - Reduces `Any` usage in JSON handling code

3. **Medium Priority - Type NATS message callbacks**
   - Use forward reference `"Msg"` with TYPE_CHECKING guard
   - Pattern: `async def _on_msg(msg: "Msg") -> None:`

4. **Medium Priority - Type the attachments parameter**
   - `smart_routing.py:117` - `attachments: list` should be `list[Attachment]`

5. **Low Priority - Modernize Optional to union syntax**
   - `cli_bot.py` and `cli_agent.py` use legacy `Optional[X]` syntax
   - Convert to `X | None` for consistency with rest of codebase

6. **Low Priority - Consider protocol/stubs for voicecli**
   - STT module has 6 `type: ignore` comments for voicecli imports
   - Creating a stub file or Protocol would eliminate these suppressions
