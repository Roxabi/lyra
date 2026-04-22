# Code Smells Analysis: LLM, Agents, and Misc

### Summary
The analyzed codebase contains several significant code smells concentrated in LLM driver implementations and agent classes. The most critical issues are long functions in `sdk.py` and `simple_agent.py`, extensive code duplication between agent implementations, and consistent violation of parameter count limits across the LLM provider protocol.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| src/lyra/llm/drivers/sdk.py | 54-184 | `complete()` function is 130 lines (threshold: 50) | Critical | Extract tool-use loop, error handling, and response assembly into separate methods |
| src/lyra/llm/drivers/sdk.py | 205-264 | `_stream_gen()` function is 59 lines | Moderate | Extract event processing into separate method |
| src/lyra/llm/drivers/nats_driver.py | 97-170 | `complete()` function is 73 lines | Moderate | Extract JSON parsing and error handling |
| src/lyra/llm/drivers/nats_driver.py | 195-293 | `_stream_gen()` function is 98 lines | Moderate | Extract chunk processing logic |
| src/lyra/llm/smart_routing.py | 190-263 | `complete()` function is 73 lines | Moderate | Extract routing decision logic |
| src/lyra/agents/simple_agent.py | 186-294 | `process()` function is 108 lines | Critical | Extract streaming path, error handling, and voice processing |
| src/lyra/agents/anthropic_agent.py | 135-231 | `_process_llm()` function is 96 lines | Moderate | Extract STT handling and history management |
| src/lyra/agents/simple_agent.py | 118-151 | DRY violation: `_register_session_commands()` duplicated in AnthropicAgent | High | Extract to AgentBase mixin or shared module |
| src/lyra/agents/anthropic_agent.py | 92-134 | DRY violation: `_register_session_commands()` duplicated from SimpleAgent | High | Same as above |
| src/lyra/cli_agent_create.py | 67-81 | `_build_toml()` has 12 parameters | High | Use config dataclass or dict |
| src/lyra/cli_agent_create.py | 121-179 | `create()` has high cyclomatic complexity with many branches | Moderate | Extract sub-config prompting into separate functions |
| src/lyra/agents/simple_agent.py | 63-96 | `__init__()` has 10 parameters | Moderate | Consider builder pattern or config object |
| src/lyra/agents/anthropic_agent.py | 43-74 | `__init__()` has 10 parameters | Moderate | Same as above |
| src/lyra/cli_setup.py | 53-117 | `_register_bot()` has 64 lines with deep nesting | Moderate | Extract plugin loading and command collection |
| src/lyra/monitoring/checks.py | 227-279 | `run_checks()` has 52 lines | Moderate | Already well-structured with early returns |
| src/lyra/llm/drivers/sdk.py | 54-184 | Deep nesting (5+ levels) in `complete()` | High | Flatten via early returns and guard clauses |
| src/lyra/llm/drivers/nats_driver.py | 195-293 | Deep nesting in `_stream_gen()` | Moderate | Extract inner while loop to generator |
| src/lyra/llm/base.py | 36-44 | Protocol `complete()` has 6+ params (acknowledged via `# noqa`) | Low | Protocol design - document as intentional |
| src/lyra/llm/decorators.py | 36-81, 115-144 | `complete()` implementations have 6+ params each | Low | Inherited from protocol - unavoidable |
| src/lyra/cli_bot.py | 24-29, 56-67, 76-96, 109-119 | DRY violation: `_make_store()` pattern repeated | Moderate | Create shared store context manager |

### Metrics

- **Avg function length**: ~35 lines (weighted by occurrence)
- **Max function length**: 130 lines (`sdk.py::complete()`)
- **God classes**: 0 (largest class has ~8 methods)
- **Duplication hotspots**: 2 major (session commands, store setup)
- **Functions > 50 lines**: 7
- **Functions > 100 lines**: 2
- **Deep nesting violations**: 3
- **Long parameter lists**: 8+ (most from protocol design)

### Recommendations

1. **Critical - Extract `sdk.py::complete()` into smaller methods**
   - Create `_handle_tool_use_loop()`, `_assemble_response()`, `_handle_api_errors()`
   - Reduces cognitive load and improves testability

2. **High Priority - Eliminate session command duplication**
   - Move `_register_session_commands()` to `AgentBase` as a mixin method
   - Both `SimpleAgent` and `AnthropicAgent` call identical code

3. **High Priority - Refactor `simple_agent.py::process()`**
   - Extract streaming path into `_process_streaming()`
   - Extract error handling into `_handle_provider_error()`
   - Extract voice handling into `_handle_voice_modality()`

4. **Moderate - Introduce config objects for agent initialization**
   - Create `AgentDeps` dataclass to bundle the 10 parameters
   - Simplifies `__init__` signatures and improves documentation

5. **Moderate - Create shared store context manager**
   - Extract the repeated `_make_store()` + `connect()` / `close()` pattern
   - Apply to `cli_bot.py` and `cli_setup.py`

6. **Low - Document protocol parameter counts as intentional**
   - The `LlmProvider.complete()` protocol requires many parameters
   - Accept as design constraint, use `# noqa` comments consistently
