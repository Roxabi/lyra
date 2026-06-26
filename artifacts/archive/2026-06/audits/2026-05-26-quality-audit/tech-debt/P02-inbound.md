# Tech-Debt Audit — P02 Inbound (`src/lyra/inbound/**/*.py`)

**Date:** 2026-05-27
**Scope:** 9 files | 1,083 total lines
**Prior audit:** 2026-05-18 (hexagonal / duplication / dead-code). Only regressions or new debt categories are reported.

---

## Summary

- **Zero markers:** No TODO / FIXME / HACK / XXX; no deprecated stdlib or `asyncio.coroutine` usage.
- **Magic values:** 3 categories (cache cap, trust/admin defaults, platform fallback) across 5 call sites.
- **ADR-048 gap:** `TurnStore` is imported concretely from `lyra.infrastructure.stores` in `context.py`; no `TurnStoreProtocol` exists in `core/stores/`.
- **Stale milestone:** `pipeline.py` still carries a "Stub: full logic implemented in Wave 5 (Slice 5)" comment even though the pipeline is fully implemented.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `session_builder.py` | 165, 202 | medium | Magic number `500` used as in-memory thread-session cache eviction threshold (two paths). | Extract `THREAD_SESSION_CACHE_LIMIT` constant at module level. |
| `wire_parser_telegram.py` | 38-39 | medium | Hardcoded `trust_level=TrustLevel.PUBLIC` and `is_admin=False`. Adapter-level `normalize` defaults to `TRUSTED` and documents `is_admin` as identity-derived. | Accept `trust_level` / `is_admin` in `TelegramWireParser` constructor or delegate identity resolution fully to the adapter. |
| `wire_parser_discord.py` | 48-49 | medium | Hardcoded `trust_level=TrustLevel.PUBLIC` and `is_admin=False`. Same mismatch with adapter-level defaults/expectations. | Accept `trust_level` / `is_admin` in `DiscordWireParser` constructor or delegate identity resolution fully to the adapter. |
| `session_builder.py` | 237 | low | `_platform_enum` falls back to `Platform.TELEGRAM` for unknown platform strings. | Make default platform configurable, or reject unknowns explicitly to surface misconfiguration. |
| `context.py` | 40 | medium | `TurnStore` imported directly from `lyra.infrastructure.stores.turn_store` and used as a type annotation (`TurnStore \| None`). | Create `TurnStoreProtocol` in `core/stores/`; switch `context.py` and `session_builder.py` to protocol imports per ADR-048 invariant. |
| `pipeline.py` | 31 | low | Stale comment: "Stub: full logic implemented in Wave 5 (Slice 5)." The `run()` method is fully implemented (parse → route → session → dispatch). | Delete comment; add a concise implementation-complete docstring if needed. |
| `session_builder.py` | 86, 142, 145, 146 | low | Four `assert` guards ("guarded by caller") that are stripped under `python -O`. | Replace with explicit `if ...: raise AssertionError(...)` or `TypeError`/`ValueError` so guards survive optimized execution. |

---

## Metrics

| Metric | Count |
|--------|-------|
| Files analyzed | 9 |
| Total lines | ~1,083 |
| TODO / FIXME / HACK / XXX | 0 |
| Deprecated API usages | 0 |
| Magic numbers / strings | 5 occurrences across 3 categories |
| ADR-048 protocol gaps in partition | 1 (`TurnStore` lacks protocol) |
| Stale milestone / wave references | 1 |
| `assert` guards (runtime `-O` risk) | 4 |
| Exemptions (file / folder) referencing P02 | 0 |
| Exemption aging (>90 days without activity) | N/A — no inbound exemptions |

---

## Recommendations (prioritized)

1. **Create `TurnStoreProtocol` in `core/stores/`** — Eliminate the concrete `lyra.infrastructure.stores.turn_store` import from `context.py`. This closes the last open ADR-048 gap visible in the inbound partition and restores caller → protocol → implementation layering.
   *Effort: small. No runtime change; type annotations + new protocol file only.*

2. **Extract named constants** — `THREAD_SESSION_CACHE_LIMIT = 500` (and reuse in both `_build_turnstore_path` and `_build_thread_path`). Also consider a `DEFAULT_PLATFORM_FALLBACK` for `_platform_enum`.
   *Effort: trivial.*

3. **Parameterize `trust_level` and `is_admin` in WireParsers** — Both parsers currently hardcode values that contradict adapter-level defaults. Either pass identity-derived values from the adapter into the parser constructor, or remove the parameters from the parser entirely and let the adapter's `normalize()` handle them internally.
   *Effort: small; requires adapter constructor changes.*

4. **Delete stale "Stub" comment in `pipeline.py`** — Misleading to new readers; the pipeline is live.
   *Effort: trivial.*

5. **Harden `assert` guards in `session_builder.py`** — Replace with explicit exceptions so caller-contract enforcement survives `python -O`.
   *Effort: trivial.*
