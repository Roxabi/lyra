# Error Handling Analysis: LLM + Agents + Misc

### Summary

The LLM, agents, and misc areas show generally sound error handling practices with **zero bare except clauses**. However, there is extensive use of generic `Exception` catches (170+ occurrences in the specified areas), with varying degrees of appropriateness. The codebase uses `# noqa: BLE001` comments to document intentional broad catches, and finally blocks are well-utilized for cleanup. The main concerns are around missing error context in some generic catches and inconsistent propagation patterns.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| /home/mickael/projects/lyra/src/lyra/llm/drivers/nats_driver.py | 64, 78, 243, 287 | Generic Exception catches without capturing exception object | Medium | Use `except Exception as exc` for logging context |
| /home/mickael/projects/lyra/src/lyra/llm/drivers/sdk.py | 122, 251, 258 | Generic Exception catches with `pass` after logging - incomplete context | Medium | Include exc_info=True in log calls or capture exception |
| /home/mickael/projects/lyra/src/lyra/llm/smart_routing.py | 234 | Generic Exception catch swallows classifier errors silently | Low | Acceptable fallback but add structured logging |
| /home/mickael/projects/lyra/src/lyra/agents/anthropic_agent.py | 111, 201 | Generic Exception catches during session tools init and provider call | Medium | Log with exc_info=True for debugging |
| /home/mickael/projects/lyra/src/lyra/agents/simple_agent.py | 130 | Generic Exception catch during session tools init | Medium | Log with exc_info=True for debugging |
| /home/mickael/projects/lyra/src/lyra/monitoring/__main__.py | 74, 80, 91 | Generic Exception catches for LLM escalation and Telegram delivery | Low | Acceptable for monitoring fallback chain |
| /home/mickael/projects/lyra/src/lyra/monitoring/checks.py | 93 | Generic Exception catch for HTTP health check | Low | Acceptable for health check with proper detail in result |
| /home/mickael/projects/lyra/src/lyra/monitoring/config.py | 90-91 | FileNotFoundError with pass - acceptable pattern | Info | This is intentional default behavior |
| /home/mickael/projects/lyra/src/lyra/stt/__init__.py | 149 | Generic Exception catch with log.exception - acceptable pattern | Info | Proper logging pattern |
| /home/mickael/projects/lyra/src/lyra/core/trace.py | 74, 78, 109 | Generic Exception catches in logging filters - defensive pattern | Low | Acceptable; filters must never raise |
| /home/mickael/projects/lyra/src/lyra/core/agent/agent_refiner_stages.py | 43, 48 | Generic Exception catches with `pass` for JSON parsing | Low | Acceptable - parsing errors produce None gracefully |

### Metrics
- Try/except blocks: 363 (across 128 files in scope)
- Bare excepts: 0
- Generic Exception catches: ~170 (in specified areas)
- Swallowed exceptions (pass): ~15 (most in defensive contexts)
- Intentional broad catches documented with noqa: BLE001: 11
- Finally blocks: ~50

### Recommendations

1. **High Priority**: Add `exc_info=True` or capture exception objects in generic Exception catches in LLM drivers (`nats_driver.py`, `sdk.py`) for better debugging context.

2. **Medium Priority**: Standardize the error handling pattern for agent initialization failures - currently `anthropic_agent.py` and `simple_agent.py` use different messaging for the same failure mode.

3. **Low Priority**: Consider creating a helper utility for the common "log and continue" pattern with generic Exception catches to ensure consistent logging with `exc_info=True`.

4. **Low Priority**: Document the rationale for broad catches in monitoring module (`__main__.py`) with comments explaining the fallback chain design.

5. **Info**: The `# noqa: BLE001` pattern with explanatory comments is good practice - continue using this for justified broad exception handling.
