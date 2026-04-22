# Tech Debt Analysis: core/agent + core/cli

**Scope:** `src/lyra/core/agent/**/*.py`, `src/lyra/core/cli/**/*.py`
**Date:** 2026-04-22

## Summary

The core/agent and core/cli areas are well-maintained with clear architecture and recent refactoring (split from monolithic files). Tech debt is primarily structural complexity from mixin-based inheritance in CLI pool, broad exception handling for resilience, and backward compatibility re-exports. No TODO/FIXME comments found in the analyzed code.

## Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `cli/cli_pool_streaming.py` | 54, 62, 63, 71, 72, 79, 108 | 7 `type: ignore[attr-defined]` for mixin method calls | Medium | Consider Protocol-based composition or explicit interface declarations |
| `cli/cli_pool_lifecycle.py` | 32, 87 | 2 `type: ignore[attr-defined]` for mixin method calls | Medium | Same as above |
| `agent/agent_refiner_stages.py` | 43, 48 | Bare `except Exception: pass` for JSON parsing | Low | Acceptable for optional display fields; consider logging at DEBUG level |
| `agent/agent_commands.py` | 65, 115 | Broad `except Exception` with BLE001 suppression | Low | Acceptable for plugin resilience; already has logging |
| `agent/agent.py` | 126 | Bare `except Exception` for DB unavailability | Low | Consider catching specific aiosqlite exceptions |
| `cli/cli_pool_worker.py` | 74, 133, 273, 281 | Broad exception handlers in worker lifecycle | Low | Acceptable for subprocess resilience |
| `cli/cli_streaming.py` | 165 | Broad exception in cleanup path | Low | Acceptable for cleanup resilience |
| `cli/cli_non_streaming.py` | 205 | Broad exception catchall for read errors | Low | Returns error result; acceptable |
| `agent/agent_loader.py` | 10 | Re-export for backward compatibility | Info | Document deprecation timeline if planned removal |
| `cli/cli_protocol.py` | 100, 136-138 | Re-exports for backward compatibility | Info | `_SESSION_ID_RE` private alias and late imports; consider cleanup |
| `agent/agent_seeder.py` | 69, 82 | Legacy TOML section support (`[model]`, `[agent].plugins`) | Info | Acceptable for migration path; document removal timeline |
| `agent/agent.py` | 218 | Magic number `token_budget=700` for memory recall | Low | Extract to constant or config |
| `agent/agent_refiner.py` | 132, 143, 204 | Magic numbers `max_tokens=2048`, `max_turns=20` | Low | Extract to constants or make configurable |
| `cli/cli_pool_worker.py` | 143 | Magic number `timeout=0.1` for liveness check | Low | Extract to named constant |
| `cli/cli_pool_streaming.py` | 35 | Magic number `_STALE_RESUME_CHECK_DELAY = 0.05` | Info | Already extracted as constant; document rationale |
| `agent/agent_config.py` | 14 | Magic number `_MAX_PROMPT_BYTES = 64 * 1024` | Info | Already extracted as constant |
| `cli/cli_pool.py` | 47, 66, 103 | `noqa` for E501, PLR0913, C901 (complexity) | Info | Mixin split already done; remaining complexity is inherent |
| `agent/agent_db_loader.py` | 34 | `noqa: C901, PLR0915` for cyclomatic complexity | Medium | Consider extracting JSON field parsing into helper functions |
| `agent/agent_seeder.py` | 49 | `noqa: PLR0915` for many branches | Medium | Consider extracting field parsing into separate functions |
| `cli/cli_streaming_parser.py` | 33 | `noqa: C901, PLR0912` for event dispatch | Info | Protocol state machine complexity; acceptable |
| `cli/cli_non_streaming.py` | 58, 18 | `noqa: C901, PLR0915, PLR0913` for protocol complexity | Info | Protocol implementations; inherent complexity |
| `cli/cli_streaming.py` | 73, 178 | `noqa: C901, PLR0913` for async protocol | Info | Acceptable for protocol layer |
| `agent/agent.py` | 46 | `noqa: PLR0913` for DI constructor | Info | Acceptable for dependency injection pattern |
| `agent/agent_builder.py` | 179 | `noqa: PLR0913` for Agent assembly | Info | One param per Agent field; acceptable |

## Metrics

| Metric | Count |
|--------|-------|
| TODOs | 0 |
| FIXMEs | 0 |
| Dead code lines | 0 |
| Deprecated patterns | 4 (backward compat re-exports) |
| Broad exception handlers | 12 |
| `type: ignore` comments | 9 |
| `noqa` suppressions | 16 |
| Magic numbers (inline) | 3 |
| Magic numbers (extracted to constants) | 3 |

## Recommendations

### Priority 1: High

None identified. The codebase is well-maintained.

### Priority 2: Medium

1. **Mixin type safety (cli/)**: The 9 `type: ignore[attr-defined]` in mixin classes indicate structural typing gaps. Consider:
   - Defining a `CliPoolProtocol` that declares all methods mixins expect
   - Using `typing.Protocol` for mixin interfaces
   - Or converting to composition with explicit delegation

2. **Cyclomatic complexity (agent_db_loader.py, agent_seeder.py)**: The `agent_row_to_config` and `_parse_toml` functions have many branches. Consider extracting:
   - JSON field parsers into separate helper functions
   - Voice config builder into a dedicated function
   - Pattern/permission parsers into smaller units

### Priority 3: Low

1. **Magic numbers**: Extract inline magic values to named constants:
   - `agent.py:218` — `token_budget=700` -> `MEMORY_RECALL_TOKEN_BUDGET = 700`
   - `agent_refiner.py` — `max_tokens=2048`, `max_turns=20` -> class-level defaults
   - `cli_pool_worker.py:143` — `timeout=0.1` -> `LIVENESS_CHECK_TIMEOUT = 0.1`

2. **Exception specificity**: Where possible, replace bare `except Exception` with specific exception types:
   - `agent.py:126` — catch `aiosqlite.Error` instead of `Exception`
   - Parsing errors — catch `json.JSONDecodeError` explicitly where recovery logic applies

3. **Backward compatibility deprecation**: Add deprecation timeline to re-export modules:
   - `agent_loader.py` — document when TOML loader re-export will be removed
   - `cli_protocol.py` — document when `_SESSION_ID_RE` alias can be dropped

## Architecture Notes

- **Mixin pattern (cli/)**: CliPool uses 4 mixins (Lifecycle, Streaming, Session, Worker). This is a deliberate design choice to manage complexity after the #760 split. The `type: ignore` comments are the price of this pattern.
- **Resilience patterns**: Broad exception handling in plugin loading and subprocess management is intentional — these are fault isolation boundaries.
- **Backward compatibility**: The re-exports in `agent_loader.py` and `cli_protocol.py` exist to avoid breaking imports after refactoring. Track these for eventual removal.

## Files Analyzed

| Directory | Files | Lines (approx) |
|-----------|-------|----------------|
| `core/agent/` | 12 | 850 |
| `core/cli/` | 10 | 950 |
| **Total** | **22** | **~1800** |
