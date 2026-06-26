### Summary

- **15 DEBT annotations** (5 `migration-sequence`, 11 `wiring-deps`, 9 `boundary-broad-catch`, 1 `re-export`) with **zero active tracking issues** for the `migration-sequence` bucket — abstract debt with no concrete milestone.
- **4 file-exemption entries** for bootstrap reference closed/merged issues (#957 MERGED, #1016 CLOSED, #1376 CLOSED, #1396 CLOSED) — stale justifications that need refresh or removal.
- **Zero TODO/FIXME/HACK/XXX** in 3 704 lines across 32 files — the partition is clean of inline task debt.

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `standalone/hub_standalone.py` | 47 | Medium | `DEBT:migration-sequence-bootstrap` on core orchestration function `_bootstrap_hub_standalone` with no tracking issue | Create follow-up issue or bind to active #1277 slice |
| `standalone/adapter_standalone.py` | 27 | Medium | `DEBT:migration-sequence-bootstrap` on `_bootstrap_adapter_standalone` with no tracking issue | Same — bind to concrete milestone |
| `bootstrap_stores.py` | 66 | Medium | `DEBT:migration-sequence-bootstrap` on `_atomic_table_copy` + file exemption cites closed #957 | Re-audit exemption; #417 auth.db split is long-complete |
| `lifecycle/bootstrap_lifecycle.py` | 24 | Medium | `DEBT:migration-sequence-bootstrap` on `run_lifecycle` | No active issue — create one |
| `infra/health.py` | 66 | Low | `DEBT:migration-sequence-bootstrap` on `create_health_app` | Same |
| `factory/wiring_helpers.py` | 1 | Medium | File exemption cites completed ADR-059/V10; 410 lines with no active issue | Extract sub-modules (per-phase bundles) or tie to open issue |
| `standalone/hub_standalone.py` | 1 | Medium | File exemption cites closed #1376 T9 TypingPublisher instantiation | Re-evaluate whether 303 lines still need exemption |
| `standalone/adapter_standalone.py` | 1 | Medium | File exemption cites closed #1016/#1376/#1396 | Re-evaluate; all referenced work is delivered |
| `factory/agent_factory.py` | 10 | Low | `DEBT:re-export-init` — backward-compat re-export of `resolve_bot_agent_map` | Remove after confirming tests no longer import via `agent_factory` |
| `factory/voice_overlay.py` | 64 | Low | Magic string `"large-v3-turbo"` as default STT model | Extract `DEFAULT_STT_MODEL` constant |
| `factory/voice_overlay.py` | 20-28 | Low | `_deprecated_env("STT_MODEL_SIZE", "LYRA_STT_MODEL")` — deprecated env var still supported | Set removal deadline or drop if migration period elapsed |
| `lifecycle/bootstrap_lifecycle.py` | 109 | Low | `drain(timeout=60.0)` hardcoded | Use `cli_pool_cfg` value or named constant |
| `standalone/hub_standalone.py` | 237 | Low | Health port default `8443` duplicated across 3 files (also `lifecycle/bootstrap_lifecycle.py`, `infra/health.py`) | Centralize to `lyra.bootstrap.constants` or env-only |
| `standalone/turn_writer_standalone.py` | 83 | Low | Health port default `8083` | Centralize or document alongside 8443 |
| `infra/embedded_nats.py` | 75 | Low | `interval = 0.1` magic number in poll loop | Extract `EMBEDDED_NATS_POLL_INTERVAL` |
| `infra/embedded_nats.py` | 92 | Low | `timeout=0.5` in TCP probe | Extract `EMBEDDED_NATS_PROBE_TIMEOUT` |
| `infra/embedded_nats.py` | 125 | Low | `timeout=3.0` in graceful-stop wait | Extract `EMBEDDED_NATS_STOP_TIMEOUT` |
| `factory/hub_builder.py` | 50 | Low | `timeout: float = 120.0` in `build_llm_client` | Named parameter (acceptable), but consider config-driven |
| `wiring/bootstrap_wiring.py` | 30 | Low | `_DEFAULT_VAULT_DIR = os.path.expanduser("~/.lyra")` duplicates pattern in 4+ files | Already a named constant locally; consider shared module |
| Multiple | various | Medium | 11 functions carry `DEBT:wiring-bootstrap-deps` (7+ parameters) | Track under active issue; reduce arity after #1277 composition root stabilises |
| Multiple | various | Low | 9 `DEBT:boundary-broad-catch` annotations (`except Exception`) | Narrow 5+ to specific exception types where safe |

### Metrics

| Metric | Count | Note |
|---|---|---|
| Files analysed | 32 | `src/lyra/bootstrap/**/*.py` |
| Total lines | 3 704 | Excluding blank/comments |
| TODO / FIXME / HACK / XXX | 0 | Clean partition |
| DEBT annotations | 26 | 5 migration-sequence + 11 wiring-deps + 9 boundary-broad-catch + 1 re-export |
| Deprecated API usage | 1 | `_deprecated_env` in `voice_overlay.py` |
| Magic numbers / strings | 8 | Ports, timeouts, intervals, model name |
| File exemptions (bootstrap) | 4 | `wiring_helpers.py`, `bootstrap_stores.py`, `hub_standalone.py`, `adapter_standalone.py` |
| Exemptions referencing closed issues | 4/4 | #957, #1016, #1376, #1396 all closed/merged |
| Exemptions referencing no issue | 1/4 | `wiring_helpers.py` cites ADR-059/V10 (delivered) |
| ADR-048 store protocols | 0 | `core/ports/` has 5 protocols; none cover the 8 store classes used by bootstrap |

### Recommendations (prioritised)

1. **Refresh or remove stale file exemptions** — All 4 bootstrap exemptions reference closed/merged work (#957, #1016, #1376, #1396, ADR-059/V10). Audit each file’s current line count drivers; if the justification no longer exists, remove the exemption and trim the file.
2. **Bind `DEBT:migration-sequence-bootstrap` to an active issue** — The 5 annotations on core orchestration functions (`_bootstrap_hub_standalone`, `_bootstrap_adapter_standalone`, `run_lifecycle`, `create_health_app`, `_atomic_table_copy`) reference an abstract bucket with no concrete milestone. Create a single cleanup issue or attach them to the next #1277 slice that owns bootstrap simplification.
3. **Narrow `boundary-broad-catch` handlers** — 9 `except Exception` blocks carry `DEBT:boundary-broad-catch`. At least 5 (`embedded_nats`, `health`, `notify`, `turn_writer_standalone`, `agent_factory` SessionTools init) can be narrowed to specific exception families without losing resilience.
4. **Extract shared constants module** — Health ports (`8443`, `8083`), embedded NATS intervals (`0.1`, `0.5`, `3.0`), vault dir (`~/.lyra`), and default STT model (`large-v3-turbo`) are duplicated or hardcoded across bootstrap. A small `lyra.bootstrap.constants` module reduces drift.
5. **Add store protocols for ADR-048 alignment** — `core/ports/` covers LLM, STT, TTS, and audit, but `bootstrap_stores.py` imports 8 concrete store classes directly from `infrastructure/stores/`. Introduce `StoreBundleProtocol` (or per-store protocols) so bootstrap depends on ports, not concrete implementations.
