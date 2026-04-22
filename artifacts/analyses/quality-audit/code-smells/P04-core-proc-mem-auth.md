# Code Smell Analysis: core/processors, core/memory, core/auth

### Summary
The analyzed modules show moderate code smell density. The primary concerns are 4 functions exceeding 50 lines (all in the memory layer), one god class candidate (Authenticator with 10 methods), and multiple long parameter lists already marked with noqa comments. The codebase demonstrates awareness of issues through explicit noqa annotations but hasn't prioritized remediation.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/core/memory/memory.py` | 65 | `recall()` function: 65 lines (>50) | Medium | Extract alias resolution, session query, and concept search into separate methods |
| `/home/mickael/projects/lyra/src/lyra/core/memory/memory_schema.py` | 14 | `apply_schema_compat()` function: 73 lines (>50), deep nesting (5 levels), missing type annotation | High | Split into smaller migration steps; add `db: aiosqlite.Connection` type annotation |
| `/home/mickael/projects/lyra/src/lyra/core/memory/memory_upserts.py` | 113 | `upsert_concept()` function: 67 lines (>50), deep nesting (4+ levels) | Medium | Extract stale-check logic and meta-merge logic into helper methods |
| `/home/mickael/projects/lyra/src/lyra/core/memory/memory_upserts.py` | 182 | `upsert_preference()` function: 56 lines (>50) | Medium | Similar pattern to upsert_concept - extract shared upsert pattern |
| `/home/mickael/projects/lyra/src/lyra/core/auth/authenticator.py` | 33 | God class: 10 methods | Low-Medium | Consider splitting into AuthenticatorCore + AuthenticatorFactory |
| `/home/mickael/projects/lyra/src/lyra/core/auth/authenticator.py` | 40 | `__init__`: 6 parameters (PLR0913) | Medium | Extract config into dataclass or use builder pattern |
| `/home/mickael/projects/lyra/src/lyra/core/auth/authenticator.py` | 244 | `from_bot_config()`: 7 parameters (PLR0913) | Medium | Group related params into config objects |
| `/home/mickael/projects/lyra/src/lyra/core/auth/authenticator.py` | 210 | `from_config()`: 6 parameters | Medium | Same as above |
| `/home/mickael/projects/lyra/src/lyra/core/processors/stream_processor.py` | 87 | `process()`: C901 cyclomatic complexity, noqa comment present | Medium | Extract event handlers into separate methods per event type |
| `/home/mickael/projects/lyra/src/lyra/core/memory/memory_upserts.py` | 113-180 | DRY violation: `upsert_concept` and `upsert_preference` share 70% similar logic | Medium | Create abstract upsert pattern or shared helper |

### Metrics

| Metric | Value |
|--------|-------|
| Total files analyzed | 19 |
| Total lines of code | 1,796 |
| Functions > 50 lines | 4 |
| Max function length | 73 lines (`apply_schema_compat`) |
| Avg function length | ~18 lines (estimated) |
| Classes > 300 lines | 0 |
| God classes (>=10 methods) | 1 (`Authenticator`) |
| Long parameter lists (>5) | 4 |
| Deep nesting hotspots (>4 levels) | 3 |
| Explicit noqa comments | 4 (excluding E501 line-length) |
| Duplication hotspots | 2 |

### Recommendations

1. **High Priority - Refactor `apply_schema_compat()`** (memory_schema.py)
   - Split the 73-line migration function into smaller, focused methods
   - Add proper type annotation (currently has `# noqa: ANN001`)
   - Reduce nesting by early-return pattern or extracting SQL execution blocks

2. **Medium Priority - Extract upsert pattern** (memory_upserts.py)
   - `upsert_concept()` and `upsert_preference()` share identical structure: query existing, check staleness, merge metadata, update or insert
   - Create a `_base_upsert()` helper or use template method pattern
   - Each method should drop to ~30 lines after extraction

3. **Medium Priority - Reduce Authenticator parameter count**
   - Create `AuthenticatorConfig` dataclass holding store, role_map, default, admin_user_ids, alias_store
   - Factory methods (`from_config`, `from_bot_config`) would construct config first
   - `__init__` drops to 2 params: `config`, `public_commands`

4. **Medium Priority - Simplify `StreamProcessor.process()`**
   - Extract `TextLlmEvent` handler, `ToolUseLlmEvent` handler, and `ResultLlmEvent` handler into separate methods
   - Current C901 violation indicates 10+ decision points

5. **Low Priority - Split `recall()` method** (memory.py)
   - Extract alias resolution block (lines 73-77)
   - Extract session query block (lines 79-103)
   - Extract result formatting block (lines 108-130)

### Positive Observations

- All classes stay under 300 lines (max 297)
- Small processors (explain.py, summarize.py, search.py) are exemplary - 27-52 lines each
- Good use of helper functions in `_scraping.py` (`_is_private_ip`, `_extract_and_validate_url`)
- Explicit noqa comments indicate developer awareness of technical debt
- Clean separation of concerns in auth/guard.py (Guard protocol pattern)
