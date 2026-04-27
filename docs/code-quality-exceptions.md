# Code Quality Exceptions

Summary of all inline and configured exceptions to quality rules.

## File/Folder Exemptions

### File Length (>300 lines)

**Config:** `tools/file_exemptions.txt`

| Path | Lines | Issue | Reason |
|------|-------|-------|--------|
| `src/lyra/core/pool/pool.py` | 326 | #858 | Backward-compat param overrides |
| `src/lyra/core/commands/command_router.py` | 303 | #858 | Backward-compat param overrides |
| `src/lyra/bootstrap/factory/wiring_helpers.py` | 400 | ADR-059/V10 | Bootstrap helper aggregator |

### Folder Size (>12 files)

**Config:** `tools/folder_exemptions.txt`

| Path | Files | Issue | Reason |
|------|-------|-------|--------|
| `src/lyra/core` | 13 | #858 | Config dataclass extraction |
| `src/lyra/infrastructure/stores` | 14 | #935 | ADR-048 store migration |

---

## Pyright Configuration

**Config:** `pyproject.toml [tool.pyright]`

### Permanent Suppressions

| Rule | Reason |
|------|--------|
| `reportPrivateUsage = "none"` | `_name` is convention, not access control |
| `reportUnusedFunction = "none"` | Decorator-registered handlers not statically reachable |

### Wave 4 (#411) — Config Migration

| Rule | Reason |
|------|--------|
| `reportUnknownVariableType = "none"` | Cascading errors from `dict[str, Any]` (~3K hits) |
| `reportUnknownMemberType = "none"` | Same |
| `reportUnknownArgumentType = "none"` | Same |
| `reportUnknownParameterType = "none"` | Same |
| `reportMissingParameterType = "none"` | Same |
| `reportUnknownLambdaType = "none"` | Same |
| `reportMissingTypeArgument = "none"` | Same |

---

## Inline Exceptions

### Ruff `# noqa:` (151 total)

| Code | Count | Description | Status |
|------|-------|-------------|--------|
| `PLR0913` | 51 | Too many args | ✅ Acceptable — DI constructors |
| `C901` | 29 | Complexity | ✅ Acceptable — protocol dispatch with many branches |
| `BLE001` | 13 | Blind exception catch | 🔴 Fix — catch specific exceptions |
| `F401` | 10 | Unused import | ✅ Acceptable — re-exports, patching |
| `E501` | 10 | Line too long | 🟡 Fix — reformat |
| `B008` | 10 | Function call in arg default | ✅ Acceptable — Typer pattern |
| `PLR2004` | 4 | Magic constant | ✅ Acceptable — domain thresholds |
| `E402` | 4 | Late import | 🟢 Low — CLI registration pattern |
| `S608` | 3 | SQL injection | 🔴 Fix — verify safe or parameterize |
| `PLC0415` | 3 | Import not at top | ✅ Acceptable — intentional lazy imports |
| `I001` | 2 | Import order | 🟢 Low — auto-fix with ruff |
| `D401` | 2 | Docstring style | 🟢 Low — cosmetic |
| `ARG002` | 2 | Unused argument | ✅ Acceptable — protocol impl |
| `PLR0915` | 2 | Too many statements | ✅ Acceptable — wiring helpers |
| `TRY401` | 1 | Redundant exception | 🟢 Low — minor |
| `PLC0414` | 1 | Useless import | 🟢 Low — may be re-export |
| `ANN001` | 1 | Missing type annotation | 🟡 Fix |

### Pyright `# type: ignore` (31 total)

| Code | Count | Description | Status |
|------|-------|-------------|--------|
| `[attr-defined]` | 19 | Dynamic attr access | 🟡 Fix — add typed protocols/dataclasses |
| `[misc]` | 5 | Coroutine/async handling | ✅ Acceptable — protocol edge cases |
| `[union-attr]` | 2 | Hub protocol access | ✅ Acceptable — duck typing |
| `[type-arg]` | 2 | Generic Bus type | 🟢 Low — type variance |
| `[return-value]` | 1 | Callback return coercion | ✅ Acceptable |
| `[import-untyped]` | 1 | `telegramify_markdown` | 🟢 Low — no stubs available |
| `[arg-type]` | 1 | Argument type mismatch | 🟡 Fix |

---

## Action Items

### High Priority 🔴

1. **BLE001 (13)** — Replace `except Exception` with specific exception types
2. **S608 (3)** — Audit SQL queries; add safety comments or parameterize

### Medium Priority 🟡

1. **E501 (10)** — Reformat long lines
2. **[attr-defined] (19)** — Add typed protocols or dataclasses for dynamic access patterns
3. **ANN001 (1)** / **[arg-type] (1)** — Add missing type annotations

### Low Priority 🟢

1. **E402 (4)** — Consider refactoring CLI registration (cosmetic)
2. **I001 (2)** — Auto-fix with `ruff format`
3. **[import-untyped] (1)** — Add stubs for `telegramify_markdown` if maintained

### Not Actionable ✅

- `PLR0913` — DI constructors with required dependencies
- `C901` — Protocol dispatch with inherently many branches
- `B008` — Typer idiomatic pattern
- `F401` — Intentional re-exports
- `reportPrivateUsage` / `reportUnusedFunction` — Permanent policy
