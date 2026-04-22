# Architecture Analysis: llm, agents, misc

### Summary

The `llm` and `agents` modules demonstrate good architectural hygiene with correct dependency direction (llm/agents → core). However, there are **critical layer violations** in `core/processors/` which import from `integrations/`, and `config.py`/`cli_setup.py` which import from `adapters/`. The `agents/` module also has layer violations importing from `integrations/` and `commands/`. The `monitoring` and `stt` modules are properly isolated and standalone.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| src/lyra/core/processors/_scraping.py | 19 | Layer violation: imports `ScrapeFailed` from `integrations.base` (core → integrations) | High | Move exception classes to `lyra/core/exceptions.py` or `lyra/errors.py` |
| src/lyra/core/processors/vault_add.py | 23 | Layer violation: imports `VaultWriteFailed` from `integrations.base` (core → integrations) | High | Same as above |
| src/lyra/config.py | 26-27 | Layer violation: imports from `adapters.discord` and `adapters.telegram` (root → adapters) | Medium | Extract config classes to `lyra/core/config/` and have adapters import from there |
| src/lyra/cli_setup.py | 122 | Layer violation: imports `VOICE_COMMANDS` from `adapters.discord.voice` | Medium | Move voice command definitions to `lyra/core/commands/` or a shared constants module |
| src/lyra/agents/simple_agent.py | 122-125 | Layer violation: imports from `integrations.base`, `integrations.vault_cli`, `integrations.web_intel` | Medium | Inject SessionTools via constructor (already done) but import inside method; move to TYPE_CHECKING |
| src/lyra/agents/simple_agent.py | 143 | Layer violation: imports `cmd_add_vault` from `commands.add_vault.handlers` | Medium | Register via processor registry instead of direct import |
| src/lyra/agents/anthropic_agent.py | 103-105, 126 | Same layer violations as simple_agent.py (integrations, commands imports) | Medium | Same recommendations as simple_agent.py |
| src/lyra/agents/simple_agent.py | 186-293 | SRP violation: 100+ line `process()` method handles voice routing, STT, streaming, error handling, response building | Medium | Extract voice handling, STT, and streaming into separate methods or mixins |
| src/lyra/agents/simple_agent.py | 118-150 | SRP violation: `_register_session_commands` mixes processor registration, vault command wiring, and session tools construction | Low | Split into focused methods |
| src/lyra/llm/drivers/sdk.py | 54-185 | SRP violation: `complete()` method has 130+ lines with tool-use loop, error handling, streaming | Medium | Extract tool execution and error mapping to helper methods |
| src/lyra/llm/smart_routing.py | 47-84 | Good: ComplexityClassifier is well-isolated with single responsibility | N/A | Good pattern - keep as reference |
| src/lyra/monitoring/* | All | Good: No imports from `lyra.*` - fully standalone | N/A | Good pattern - keep isolation |
| src/lyra/stt/__init__.py | All | Good: No imports from `lyra.*` - standalone with clear protocol | N/A | Good pattern - keep isolation |

### Metrics

- **Module coupling**: 6/10 (llm/agents have proper direction but core→integrations and root→adapters violations degrade score)
- **Circular deps**: 0 actual circular imports detected (TYPE_CHECKING guards prevent runtime cycles)
- **Layer violations**: 7 instances (2 critical in core/processors, 3 in agents/, 2 in root config/cli)

### Recommendations

1. **High Priority - Fix core→integrations layer violations**:
   - Move `ScrapeFailed`, `VaultWriteFailed`, `AudioConversionFailed`, `ServiceControlFailed` from `integrations/base.py` to `lyra/errors.py` or `lyra/core/exceptions.py`
   - This allows `core/processors/` to import exceptions without crossing layer boundaries

2. **Medium Priority - Fix config imports from adapters**:
   - Extract `TelegramConfig`, `DiscordConfig` to `lyra/core/config/telegram.py` and `lyra/core/config/discord.py`
   - Have `config.py` re-export from core and adapters import from core

3. **Medium Priority - Fix agents→integrations/commands imports**:
   - Move `SessionTools` construction to bootstrap layer, inject via constructor
   - Use lazy imports inside methods with TYPE_CHECKING guards for type hints

4. **Low Priority - Refactor large methods for SRP**:
   - Split `simple_agent.py:process()` into `_handle_voice()`, `_handle_stt()`, `_handle_streaming()` helpers
   - Split `sdk.py:complete()` tool execution into `_execute_tool_loop()` helper

5. **Maintain - Preserve good patterns**:
   - Keep `monitoring/` and `stt/` as standalone modules with no internal imports
   - Keep `llm/` module's strict `llm → core` dependency direction
   - Continue using `TYPE_CHECKING` guards for forward references
