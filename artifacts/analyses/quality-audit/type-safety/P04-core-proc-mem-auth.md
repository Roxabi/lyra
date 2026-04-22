# Type Safety Analysis: Core Processors, Memory, Auth

### Summary
The core/processors, core/memory, and core/auth modules demonstrate strong type safety practices overall, with consistent use of `from __future__ import annotations`, modern union syntax (`|`), and no `Any` imports. However, there are several areas for improvement, primarily around untyped dictionary parameters and missing type hints on a few function parameters.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| memory_schema.py | 14 | `db` parameter missing type hint (has `noqa: ANN001`) | Medium | Type as `aiosqlite.Connection` or create Protocol |
| memory_freshness.py | 14 | `entry: dict` uses bare dict | Medium | Define `VaultEntry` TypedDict |
| memory_freshness.py | 36 | `entry: dict` uses bare dict | Medium | Same TypedDict as above |
| memory_upserts.py | 113 | `data: dict` uses bare dict | Medium | Define `ConceptData` TypedDict |
| memory_upserts.py | 182 | `data: dict` uses bare dict | Medium | Define `PreferenceData` TypedDict |
| memory.py | 137 | Return type `list[dict]` lacks inner type | Medium | Use `list[VaultEntry]` |
| memory.py | 141 | `results: list[dict]` lacks inner type | Medium | Use `list[VaultEntry]` |
| authenticator.py | 181 | `section_cfg: dict` uses bare dict | Low | Define `AuthSectionConfig` TypedDict |
| authenticator.py | 212 | `raw: dict` uses bare dict | Low | Define `RawConfig` TypedDict |
| authenticator.py | 246 | `raw: dict` uses bare dict | Low | Same TypedDict as above |
| authenticator.py | 255 | `bots_list: list[dict]` lacks inner type | Low | Use `list[BotConfig]` |
| _scraping.py | 93 | `scraper` parameter missing type hint | Medium | Type as `ScrapeProvider` |

### Metrics
- Type coverage: ~95% (all public methods have return types, most parameters typed)
- `Any` usage: 0 instances
- `type: ignore`: 0 instances
- `noqa: ANN`: 1 instance (intentional suppression for aiosqlite)
- Bare `dict` parameters: 7 instances
- Bare `list[dict]` usage: 3 instances
- Missing parameter type hints: 2 instances

### Recommendations

1. **High Priority: Define TypedDict for vault entries** - Create `VaultEntry`, `ConceptData`, and `PreferenceData` TypedDicts to replace bare `dict` types in memory_freshness.py and memory_upserts.py. This would catch key errors at type-check time.

2. **Medium Priority: Type config dictionaries** - Create `AuthSectionConfig`, `BotConfig`, and `RawConfig` TypedDicts for authenticator.py configuration handling. This improves IDE autocomplete and catches typos in config keys.

3. **Medium Priority: Add scraper type hint** - The `_scrape_with_fallback` function in _scraping.py should type the `scraper` parameter as `ScrapeProvider` (the Protocol already exists in integrations/base.py).

4. **Low Priority: Resolve aiosqlite typing** - For `apply_schema_compat`, either vendor a minimal Protocol for aiosqlite Connection or accept the `noqa` as documented. The current suppression is intentional and documented.

5. **Best Practice Maintained**: Continue using `from __future__ import annotations` and modern union syntax (`| None` instead of `Optional[]`).

### Positive Observations
- Zero `Any` type usage across all analyzed files
- Zero `# type: ignore` comments
- Consistent use of forward references via `TYPE_CHECKING` blocks
- Protocol definitions in guard.py are well-typed with `@runtime_checkable`
- All dataclasses use frozen=True and have explicit type annotations
- Modern Python 3.10+ union syntax used throughout
