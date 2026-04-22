# Architecture Analysis: Bootstrap

### Summary
The bootstrap module correctly serves as the composition root with proper dependency direction (imports from all layers, nothing imports from it). The V4 decomposition into subdirectories (factory, lifecycle, infra, standalone, wiring) demonstrates good architectural awareness. However, several functions exhibit excessive complexity and parameter counts that violate single responsibility, and scattered `sys.exit()` calls reduce testability.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| bootstrap/factory/unified.py | 49 | `_bootstrap_unified` flagged `# noqa: C901, PLR0915` - 297 lines, high cyclomatic complexity | Medium | Extract sub-functions for config loading, store setup, agent creation phases |
| bootstrap/standalone/adapter_standalone.py | 24 | `_bootstrap_adapter_standalone` handles both Telegram AND Discord with platform branching | Medium | Split into separate `_bootstrap_telegram_adapter_standalone` and `_bootstrap_discord_adapter_standalone` |
| bootstrap/standalone/hub_standalone.py | 43 | `_bootstrap_hub_standalone` flagged `# noqa: C901, PLR0915` - complex startup wiring | Medium | Consider extracting phases similar to unified.py decomposition |
| bootstrap/wiring/bootstrap_wiring.py | 110 | `wire_discord_adapters` flagged `# noqa: PLR0913, C901` - 12 parameters | Low | Acceptable for wiring functions, but document dependency rationale |
| bootstrap/lifecycle/bootstrap_lifecycle.py | 26 | `run_lifecycle` flagged `# noqa: PLR0913, C901` - 13 parameters | Low | Acceptable for lifecycle orchestration entry point |
| Multiple files | Various | 21 `sys.exit()` calls scattered across 7 files | Medium | Raise typed exceptions, let CLI layer handle exit; improves testability |
| bootstrap/factory/hub_builder.py | 60,127 | `build_hub` and `register_agents` flagged `# noqa: PLR0913` - many params | Low | Acceptable for construction/factory functions |
| bootstrap/bootstrap_stores.py | 65 | `_atomic_table_copy` flagged `# noqa: C901` - 90 lines migration logic | Low | Complex but cohesive migration logic, acceptable |

### Metrics

- **Module coupling**: 6/10 (High internal coupling in unified.py and hub_standalone.py with 8+ internal imports; expected for composition root)
- **Circular deps**: 0 (Excellent - bootstrap is never imported by core/infrastructure/adapters)
- **Layer violations**: 0 within bootstrap (Bootstrap correctly imports from all layers as composition root)

### Recommendations

1. **High Priority**: Replace `sys.exit()` calls with typed exceptions (e.g., `BootstrapError`, `ConfigurationError`). Let `cli.py` or `__main__.py` handle the exit. This improves testability and error handling consistency.

2. **Medium Priority**: Split `adapter_standalone.py` into platform-specific files. The current `if platform == "telegram": ... elif platform == "discord":` pattern adds unnecessary branching complexity.

3. **Medium Priority**: Extract helper functions from `_bootstrap_unified` and `_bootstrap_hub_standalone` to reduce cyclomatic complexity. Consider following the pattern established in `hub_standalone_helpers.py`.

4. **Low Priority**: Document the 12+ parameter functions with dependency rationale comments. Many parameters are unavoidable for wiring/construction, but explicit documentation helps maintainers understand the trade-offs.

5. **Architectural Note**: The bootstrap module correctly follows the composition root pattern. The existing decomposition into subdirectories is well-structured. The high coupling is intrinsic to bootstrap's responsibility of wiring all components together.
