### Summary

- 71 inline `DEBT:` annotations across 99 files / 12,186 LOC, dominated by boundary-broad-catch (23) and wiring-bootstrap-deps (18). Zero `TODO`/`FIXME`/`HACK`/`XXX` comments — debt is fully tracked via structured annotations.
- 6 infrastructure stores lack matching core protocols per ADR-048; core non-hub directly imports concrete `AuthStore` and `TurnStore` from `lyra.infrastructure.stores` in 4 files.
- 2 deprecated `asyncio.get_event_loop()` calls and 2 hardcoded `range(2)` stale-resume retry loops are the only deprecated-API / magic-number surface in the partition.

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/core/cli/cli_pool_lifecycle.py` | 60, 65 | medium | `asyncio.get_event_loop()` — deprecated in Python 3.10+ | Replace with `asyncio.get_running_loop()` or accept `loop` injection |
| `src/lyra/core/auth/authenticator.py` | 17, 42, 166, 183, 214, 249 | medium | `AuthStore` imported directly from `lyra.infrastructure.stores` with no protocol | Add `AuthStoreProtocol` to `core/stores/` and migrate type hints |
| `src/lyra/core/pool/pool_observer.py` | 9, 33, 48 | medium | `TurnStore` imported directly from `lyra.infrastructure.stores` with no protocol | Add `TurnStoreProtocol` to `core/stores/` and migrate type hints |
| `src/lyra/core/pool/pool.py` | 13, 129 | medium | `TurnStore` imported directly from `lyra.infrastructure.stores` with no protocol | Migrate to `TurnStoreProtocol` type hint; remove concrete import |
| `src/lyra/core/cli/cli_pool.py` | 133 | low | Hardcoded `range(2)` stale-resume retry count | Extract `_STALE_RESUME_MAX_RETRIES` named constant in `PoolConfig` |
| `src/lyra/core/cli/cli_pool_streaming.py` | 54 | low | Hardcoded `range(2)` stale-resume retry count | Reuse same named constant from `PoolConfig` |
| `src/lyra/core/pool/pool.py` | 39 | low | Backward-compat individual param overrides (deprecated comment) | Remove deprecated params, migrate callers to `PoolConfig` |
| `src/lyra/core/commands/command_router.py` | 55 | low | Backward-compat individual param overrides (deprecated comment) | Remove deprecated params, migrate callers to `RouterConfig` |
| `src/lyra/core/commands/command_router.py` | 152 | low | `add_session_command()` marked Deprecated in docstring | Remove method, migrate callers to `@register` from `processor_registry` |
| `src/lyra/core/agent/agent.py` | 223 | low | Hardcoded `token_budget=700` in memory search | Add named constant `MEMORY_SEARCH_TOKEN_BUDGET` |
| `src/lyra/core/session_lifecycle.py` | 99 | low | Inline `0.8` compaction coefficient duplicates `COMPACT_THRESHOLD` logic | Reference `COMPACT_RATIO = 0.8` constant, reuse in both sites |
| `src/lyra/core/session_lifecycle.py` | 124 | low | Inline `0.7` confidence threshold in prompt string | Add named constant `CONCEPT_EXTRACT_CONFIDENCE` |
| `src/lyra/core/cli/cli_pool_worker.py` | 184 | low | Hardcoded `timeout=0.1` in `asyncio.wait_for(proc.wait(), …)` | Add `_PROC_GRACE_TIMEOUT` constant or expose in `CliPoolConfig` |
| `src/lyra/infrastructure/stores` | N/A | medium | 6 stores lack core protocols: `auth_store`, `bot_agent_map`, `message_index`, `prefs_store`, `turn_store`, `turn_store_queries` | Create missing protocols per ADR-048; `sqlite_base` and `agent_store_migrations` are auxiliary and exempt |
| `tools/file_exemptions.txt` | N/A | low | 20/23 entries tagged DEBT; several reference long-closed issues (#773, #848, #858) | Review and remove stale entries |
| `tools/folder_exemptions.txt` | N/A | low | 5/13 entries tagged DEBT; `src/lyra/nats` references #1192 S4 deferred | Reconcile with active epic status; remove if slices are complete |

### Metrics

| Metric | Count | % of partition |
|---|---|---|
| Files analyzed | 99 | — |
| LOC analyzed | 12,186 | — |
| Inline `DEBT:` annotations | 71 | 0.58% of LOC |
| `boundary-broad-catch` (BLE001 noqa) | 23 | 32% of annotations |
| `wiring-bootstrap-deps` (PLR0913 noqa) | 18 | 25% of annotations |
| `complexity-residual` (C901/PLR0915 noqa) | 10 | 14% of annotations |
| `defensive-narrow-payloads` (type: ignore) | 10 | 14% of annotations |
| `protocol-private-ducktyping` (pyright ignore) | 2 | 3% of annotations |
| Deprecated `asyncio.get_event_loop()` | 2 | — |
| Hardcoded `range(2)` retry loops | 2 | — |
| Deprecated backward-compat param surfaces | 2 | — |
| TODO / FIXME / HACK / XXX comments | 0 | — |
| Infrastructure stores without core protocols | 6 | — |
| Exemption entries tagged DEBT (file) | 20 / 23 | — |
| Exemption entries tagged DEBT (folder) | 5 / 13 | — |

### Recommendations (prioritized)

1. **Close ADR-048 protocol gap** — Create protocols for `auth_store`, `turn_store`, and `prefs_store` (the three with the most core non-hub touchpoints), then migrate direct `lyra.infrastructure.stores` imports in `authenticator.py`, `pool.py`, and `pool_observer.py` to protocol types.
2. **Eliminate `asyncio.get_event_loop()`** — The 2 calls in `cli_pool_lifecycle.py` are the only deprecated stdlib API surface in the partition; replace with `asyncio.get_running_loop()` or inject `loop` from the caller.
3. **Parameterize stale-resume retry** — Extract `range(2)` in `cli_pool.py` and `cli_pool_streaming.py` to a named constant (`_STALE_RESUME_MAX_RETRIES`) and wire it through `PoolConfig`.
4. **Retire backward-compat param overrides** — Remove deprecated individual param overrides from `Pool.__init__` and `CommandRouter.__init__`; grep for callers still using positional param form and migrate them to config objects.
5. **Audit exemptions for aging** — Review `tools/file_exemptions.txt` and `tools/folder_exemptions.txt` for entries whose tracking issues (#773, #848, #858, #935) are long closed; remove or refresh stale entries so the exemption list reflects actual active debt.
