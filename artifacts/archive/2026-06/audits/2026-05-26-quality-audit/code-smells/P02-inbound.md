# P02 Inbound Code-Smell Audit — `src/lyra/inbound/**/*.py`

**Date:** 2026-05-27
**Partition:** P02 (inbound stage-axis)
**Scope:** 9 files, 707 lines
**Prior audit:** 2026-05-18 (hexagonal/duplication/dead-code) — no regressions observed in those dimensions.

---

### Summary

- `session_builder.py` (238 lines, 34 % of partition) concentrates every high-severity smell: a 93-line method with a 36-line nested async closure, duplicate LRU eviction logic, and a borderline god-class load (~5 responsibilities).
- The two `WireParser` implementations share identical structural scaffolding (`del ctx`, bot guard, `adapter.normalize` delegation) but differ in guard/normalize details — a minor structural duplication, not a copy-paste violation.
- The remaining 7 files are clean: no functions exceed 50 lines, no god classes, no DRY violations, no feature envy.

---

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `session_builder.py` | 131 | **High** | `_build_thread_path` is 93 lines and embeds a 36-line async closure. It mixes cache I/O, ThreadStore I/O, exception handling, LRU move-to-end, eviction, dataclass replacement, and closure capture. Cognitive complexity is the highest in the partition. | Extract `_thread_update_fn` to a module-level async helper or private `@staticmethod`; extract cache read/write helpers so the method drops below 50 lines. |
| `session_builder.py` | 165 | **Medium** | Cache eviction block `if len(_cache) >= 500: _oldest = next(iter(_cache)); del _cache[_oldest]` is duplicated at lines 165–167 (read path) and 202–209 (write path). | Extract `_evict_lru(cache: dict, cap: int = 500) -> None` private helper and call it from both sites. |
| `session_builder.py` | 81 | **Medium** | Both `_build_turnstore_path` (49 lines, 16-line closure) and `_build_thread_path` (93 lines, 36-line closure) define long async closures inline. The capture-by-value + `dataclasses.replace` pattern is repeated. | Extract a `_make_update_fn` factory family (one per path) so methods contain only store logic, not closure construction. |
| `session_builder.py` | 21 | **Medium** | `SessionBuilder` owns ~5 responsibilities: turn-store session resolution, thread-store session resolution, in-memory LRU cache management, platform enum mapping, and update-closure generation. | Split into `TurnStoreResolver` + `ThreadStoreResolver` + `CacheMixin`; keep `SessionBuilder` as a thin dispatch facade. |
| `session_builder.py` | 226 | **Low** | `_platform_enum` is a module-level free function consumed only by `SessionBuilder._build_turnstore_path`. It leaks module namespace. | Move to `@staticmethod` inside `SessionBuilder`. |

---

### Metrics

| Metric | Count | % of partition |
|---|---|---|
| Files analyzed | 9 | — |
| Total lines | 707 | — |
| Files >100 lines | 1 (`session_builder.py` @ 238) | 11 % |
| Functions >50 lines | 1 (`_build_thread_path` @ 93) | — |
| Functions with nested closures | 2 | — |
| God classes / modules (>5 responsibilities) | 1 (`SessionBuilder`, borderline) | — |
| DRY violations (copy-paste >3 lines, ≥2 files) | 1 (cache eviction, same file) | — |
| Cognitive-complexity hotspots | 1 (`_build_thread_path`) | — |

---

### Recommendations (prioritized)

1. **Extract `_evict_lru` helper** — Unify the duplicate cache eviction logic in `SessionBuilder`. One 3-line block is repeated with different variable names; a single helper eliminates the DRY violation and makes the eviction policy (cap=500) a named constant.

2. **Decompose `_build_thread_path`** — Split the 93-line method into: (a) cache/thread-store read helper, (b) standalone async update function (not a closure), (c) thin orchestration. Target: each piece <35 lines.

3. **Split `SessionBuilder` into resolver classes** — Move turn-store logic to `TurnStoreResolver`, thread-store + cache logic to `ThreadStoreResolver`. `SessionBuilder.build` becomes a 15-line dispatcher. This isolates platform-specific persistence rules and makes unit testing cache eviction independent of store mocking.

4. **Extract closure-factory helpers** — Both `_turnstore_update_fn` and `_thread_update_fn` share the same lifecycle (capture values → return async callable). A small `_make_update_fn` factory per path removes the nested-function cognitive tax.

5. **Move `_platform_enum` to `@staticmethod`** — Minor hygiene. Keep module-level namespace free of single-use utilities.
