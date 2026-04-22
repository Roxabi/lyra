### Summary

The bootstrap package (28 files, ~2,000 lines) is well-maintained with no TODO/FIXME comments. Primary debt is architectural complexity: 13 functions exceed argument limits (PLR0913), 6 exceed cyclomatic complexity (C901), and 4 exceed statement count (PLR0915). Secondary debt includes hardcoded timeout values (8 instances), deprecated env var handling, and 6 type-ignores for untyped dependencies.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| factory/voice_overlay.py | 18-28 | Deprecated env var `STT_MODEL_SIZE` -> `LYRA_STT_MODEL` | Low | Schedule removal after migration window |
| infra/embedded_nats.py | 75 | Magic number `interval=0.1` (poll interval) | Low | Extract to constant |
| infra/embedded_nats.py | 92 | Magic number `timeout=0.5` (TCP connect) | Low | Extract to constant |
| infra/embedded_nats.py | 125 | Magic number `timeout=3.0` (graceful stop) | Low | Extract to constant |
| infra/notify.py | 29 | Magic number `timeout=10` (HTTP request) | Low | Extract to constant |
| factory/voice_overlay.py | 83 | Magic number `timeout=1.0` (NATS probe) | Low | Extract to constant |
| lifecycle/bootstrap_lifecycle.py | 115 | Magic number `timeout=60.0` (CLI drain) | Low | Extract to constant |
| standalone/hub_standalone_helpers.py | 92 | Magic number `timeout=60.0` (CLI drain) | Low | Duplicate of above |
| factory/unified.py | 49 | PLR0913 + PLR0915 + C901 - function too large | Medium | Consider decomposition |
| standalone/adapter_standalone.py | 24 | PLR0915 + C901 - function too large | Medium | Extract platform branches |
| standalone/hub_standalone.py | 43 | PLR0913 + PLR0915 + C901 - function too large | Medium | Consider decomposition |
| wiring/bootstrap_wiring.py | 110 | PLR0913 + C901 - too many args | Medium | Accept (wiring surface) |
| lifecycle/bootstrap_lifecycle.py | 26 | PLR0913 + C901 - too many args | Medium | Accept (lifecycle surface) |
| factory/agent_factory.py | 158 | PLR0913 - too many args | Medium | Accept (factory pattern) |
| factory/agent_factory.py | 230 | PLR0913 - too many args | Medium | Accept (factory pattern) |
| factory/hub_builder.py | 60 | PLR0913 - too many args | Medium | Accept (builder pattern) |
| factory/hub_builder.py | 127 | PLR0913 - too many args | Medium | Accept (builder pattern) |
| wiring/nats_wiring.py | 28 | PLR0913 - too many args | Medium | Accept (wiring surface) |
| wiring/nats_wiring.py | 85 | PLR0913 - too many args | Medium | Accept (wiring surface) |
| wiring/bootstrap_wiring.py | 37 | PLR0913 - too many args | Medium | Accept (wiring surface) |
| wiring/bootstrap_wiring.py | 230 | PLR0913 - too many args | Medium | Accept (wiring surface) |
| standalone/hub_standalone_helpers.py | 73 | PLR0913 - too many args | Medium | Accept (shutdown surface) |
| bootstrap_stores.py | 65 | C901 - cyclomatic complexity | Low | Sequential migration steps (acceptable) |
| standalone/stt_adapter_standalone.py | 21 | type: ignore[import-untyped] for pynvml | Low | Add py.typed or stubs |
| standalone/tts_adapter_standalone.py | 20 | type: ignore[import-untyped] for pynvml | Low | Add py.typed or stubs |
| standalone/adapter_standalone.py | 104 | type: ignore[type-arg] for NatsBus | Low | Investigate generic variance |
| standalone/adapter_standalone.py | 239 | type: ignore[type-arg] for NatsBus | Low | Duplicate of above |
| wiring/nats_wiring.py | 23 | Empty TYPE_CHECKING block with pass | Low | Remove if unused |

### Metrics

- TODOs: 0
- FIXMEs: 0
- Dead code lines: 1 (empty pass statement)
- Deprecated patterns: 1 (env var migration)
- Magic numbers: 8
- Noqa suppressions: 20
- Type ignores: 6

### Recommendations

1. **Extract timeout constants** (Priority: Low)
   - Create `BOOTSTRAP_TIMEOUTS` dataclass or module-level constants
   - Consolidate duplicate `60.0` CLI drain timeout

2. **Remove deprecated env var** (Priority: Low)
   - Schedule removal of `STT_MODEL_SIZE` fallback in Q3 2026
   - Add deprecation timeline to migration docs

3. **Accept complexity suppressions** (Priority: Info)
   - PLR0913/C901/PLR0915 suppressions are justified for bootstrap wiring
   - Wiring surface requires many dependencies; decomposition adds indirection cost

4. **Address pynvml typing** (Priority: Low)
   - Consider `pynvml-stubs` or vendor minimal type hints
   - Alternatively, wrap in typed interface

5. **Remove dead TYPE_CHECKING block** (Priority: Low)
   - `wiring/nats_wiring.py:23` - empty block can be removed
