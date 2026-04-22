# Code Smells Analysis: Bootstrap

### Summary
The bootstrap area contains 30 Python files with significant code smell issues, primarily concentrated in three mega-functions (`_bootstrap_unified`, `_bootstrap_adapter_standalone`, `_bootstrap_hub_standalone`) that exceed 200 lines. Long parameter lists are endemic (10 functions with 6+ params), and code duplication exists between adapter standalone implementations. The codebase shows signs of legitimate complexity due to wiring/orchestration responsibilities, but would benefit from further decomposition.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `factory/unified.py` | 49-296 | Function `_bootstrap_unified` is 248 lines | Critical | Extract into smaller orchestration phases |
| `standalone/adapter_standalone.py` | 24-299 | Function `_bootstrap_adapter_standalone` is 276 lines | Critical | Split by platform (telegram/discord) into separate functions |
| `standalone/hub_standalone.py` | 43-258 | Function `_bootstrap_hub_standalone` is 216 lines | Critical | Use hub_standalone_helpers pattern more extensively |
| `infra/health.py` | 56-164 | Function `create_health_app` is 109 lines | High | Extract endpoint handlers as separate functions |
| `wiring/bootstrap_wiring.py` | 110-227 | Function `wire_discord_adapters` is 118 lines | High | Extract nested function `_parse_channel_ids` to module level |
| `lifecycle/bootstrap_lifecycle.py` | 26-122 | Function `run_lifecycle` is 97 lines | Medium | Consider extracting audit consumer setup |
| `bootstrap_stores.py` | 65-151 | Function `_atomic_table_copy` is 87 lines | Medium | Already has `# noqa: C901`, acceptable for migration code |
| `factory/agent_factory.py` | 230-299 | Function `_resolve_agents` is 70 lines | Medium | Acceptable for factory pattern |
| `factory/agent_factory.py` | 158-227 | Function `_create_agent` has 9 params | High | Introduce AgentFactoryConfig dataclass |
| `factory/hub_builder.py` | 127-153 | Function `register_agents` has 10 params | High | Bundle params into config object |
| `standalone/hub_standalone_helpers.py` | 73-99 | Function `shutdown_hub_runtime` has 7 params | Medium | Acceptable for teardown sequencing |
| `wiring/bootstrap_wiring.py` | 37-107 | Function `wire_telegram_adapters` is 71 lines | Medium | Already reasonable structure |
| `wiring/bootstrap_wiring.py` | 230-282 | Function `_build_bot_auths` has 6 params | Medium | Acceptable for wiring contract |
| `wiring/nats_wiring.py` | 28-82 | Function `wire_nats_telegram_proxies` is 55 lines | Low | Acceptable |
| `wiring/nats_wiring.py` | 85-139 | Function `wire_nats_discord_proxies` is 55 lines | Low | Acceptable |
| `standalone/tts_adapter_standalone.py` | 111-166 | Method `handle` is 56 lines | Medium | Extract response building logic |
| `standalone/stt_adapter_standalone.py` | 84-142 | Method `handle` is 59 lines | Medium | Extract response building logic |
| `standalone/tts_adapter_standalone.py` + `stt_adapter_standalone.py` | Multiple | Duplicate `_get_vram_info` implementations | Medium | Move to shared mixin or utility |
| `standalone/tts_adapter_standalone.py` + `stt_adapter_standalone.py` | Multiple | Similar `handle` error handling pattern | Medium | Extract common error handling to base class |
| `bootstrap_stores.py` | 65-110 | Deep nesting (5 levels) in `_atomic_table_copy` | Medium | Early returns to reduce nesting |
| `standalone/adapter_standalone.py` | 57-296 | Deep nesting (4-5 levels) in platform branches | High | Extract platform-specific setup to dedicated functions |

### Metrics

- **Total files analyzed**: 30 Python files
- **Functions > 50 lines**: 18 functions
- **Max function length**: 276 lines (`_bootstrap_adapter_standalone`)
- **Avg function length**: ~35 lines (excluding the 3 mega-functions)
- **God classes (≥10 methods)**: 0
- **Classes > 300 lines**: 0
- **Functions with >5 params**: 10
- **Max parameter count**: 10 (`register_agents`)
- **Duplication hotspots**: 2 (STT/TTS adapter handle methods, VRAM info)
- **Deep nesting (>4 levels)**: 2 functions

### Recommendations

1. **Critical - Decompose mega-functions** (Priority: High)
   - `_bootstrap_adapter_standalone`: Split into `_bootstrap_telegram_adapter` and `_bootstrap_discord_adapter` functions that share a common NATS connection setup helper
   - `_bootstrap_unified`: Already has helper modules (hub_builder, config) - continue extracting phases
   - `_bootstrap_hub_standalone`: Further leverage hub_standalone_helpers.py

2. **High - Introduce config dataclasses for parameter bundles** (Priority: High)
   - Create `AgentFactoryContext` dataclass to bundle the 9-10 parameters passed to `_create_agent` and `register_agents`
   - Consider `WireContext` for adapter wiring functions

3. **Medium - Extract duplicate code** (Priority: Medium)
   - Move `_get_vram_info` to a shared utility module (e.g., `infra/gpu_utils.py`)
   - Extract common NATS adapter error handling pattern to `NatsAdapterBase` or a mixin

4. **Medium - Reduce nesting depth** (Priority: Medium)
   - Use early returns in `_atomic_table_copy` to exit early on missing tables
   - Flatten platform branching in `_bootstrap_adapter_standalone` using separate functions

5. **Low - Add noqa comments with justification** (Priority: Low)
   - Several functions already have `# noqa: C901, PLR0913, PLR0915` with good reasons (wiring/orchestration)
   - Ensure all complex functions document why decomposition isn't feasible
