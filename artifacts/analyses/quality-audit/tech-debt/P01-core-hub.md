# Tech Debt Analysis: core/hub

### Summary
The `core/hub` area is well-maintained with minimal critical tech debt. The codebase shows good documentation practices and intentional design decisions. Primary concerns are: (1) legacy backward-compatibility aliases that add maintenance burden, (2) magic numbers that should be centralized or configurable, and (3) several complexity-related noqa suppressions that indicate refactoring opportunities.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| /home/mickael/projects/lyra/src/lyra/core/hub/hub.py | 66 | Magic number `604800.0` (7 days in seconds) | Low | Extract to named constant `POOL_TTL_DEFAULT_SECONDS` |
| /home/mickael/projects/lyra/src/lyra/core/hub/middleware_stt.py | 110 | Magic number `30000` (STT timeout default) | Low | Extract to named constant `STT_TIMEOUT_MS_DEFAULT` |
| /home/mickael/projects/lyra/src/lyra/core/hub/outbound_errors.py | 23-25 | Magic numbers for thresholds (60.0, 256, 512) | Low | Document rationale or move to config |
| /home/mickael/projects/lyra/src/lyra/core/hub/message_pipeline.py | 1-31 | Entire file is backward-compatibility shim | Medium | Schedule deprecation, add warning log |
| /home/mickael/projects/lyra/src/lyra/core/hub/pipeline_types.py | 48,65 | Legacy private-name aliases (`_DROP`, `_SESSION_FALLTHROUGH_MSG`) | Low | Document deprecation timeline |
| /home/mickael/projects/lyra/src/lyra/core/hub/middleware_pool.py | 110 | `type: ignore[attr-defined]` for dynamic attribute | Low | Consider using a protocol or dataclass for Pool config state |
| /home/mickael/projects/lyra/src/lyra/core/hub/middleware_stt.py | 196-197 | `type: ignore[union-attr]` on hub access | Low | Add stub protocol for test mock use or tighten typing |
| /home/mickael/projects/lyra/src/lyra/core/hub/_dispatch.py | 27 | `noqa: C901, PLR0913, PLR0915` (high complexity) | Medium | Extract retry logic to separate helper function |
| /home/mickael/projects/lyra/src/lyra/core/hub/outbound_streaming.py | 47 | `noqa: C901, PLR0915` (high complexity) | Medium | Split streaming dispatch paths into smaller methods |
| /home/mickael/projects/lyra/src/lyra/core/hub/middleware_stt.py | 81 | `noqa: C901, PLR0915` (high complexity) | Medium | Extract error handling branches to separate methods |
| /home/mickael/projects/lyra/src/lyra/core/hub/hub.py | 74 | `noqa: PLR0913` (13 parameters) | Low | Consider options dataclass pattern |
| /home/mickael/projects/lyra/src/lyra/core/hub/outbound_router.py | 55 | `noqa: PLR0913` (too many arguments) | Low | Consider config dataclass |
| /home/mickael/projects/lyra/src/lyra/core/hub/middleware_pool.py | 147 | `noqa: PLR0913` (too many arguments) | Low | Acceptable for dispatch signature, document rationale |
| /home/mickael/projects/lyra/src/lyra/core/hub/hub_registration.py | 46-47 | Placeholder methods in TYPE_CHECKING block with noqa | Low | Type stub pattern, acceptable but unusual |
| /home/mickael/projects/lyra/src/lyra/core/hub/middleware_stt.py | 33-40 | Internal metrics dict `_STT_STAGE_OUTCOMES` not exposed | Low | Consider exposing via health check or metrics endpoint |

### Metrics
- TODOs: 0
- FIXMEs: 0
- Dead code lines: 0
- Deprecated patterns: 2 (legacy aliases in message_pipeline.py)
- Magic numbers: 6
- noqa suppressions: 10
- type: ignore suppressions: 3

### Recommendations
1. **High Priority** - Add deprecation timeline to `message_pipeline.py` backward-compatibility shim (e.g., "Remove in v2.0")
2. **Medium Priority** - Refactor `_dispatch.py` retry logic into a dedicated `retry_with_backoff()` helper to reduce complexity
3. **Medium Priority** - Extract magic numbers to a `constants.py` module or config defaults with documented rationale
4. **Low Priority** - Consider using an `@dataclass` for `Hub.__init__` parameters to reduce argument count
5. **Low Priority** - Add typed protocol for test mock patterns to eliminate `type: ignore` comments
6. **Low Priority** - Expose `_STT_STAGE_OUTCOMES` metrics through health check endpoint for observability
