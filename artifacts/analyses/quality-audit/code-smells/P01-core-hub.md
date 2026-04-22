# Code Smells Analysis: core/hub

### Summary
The core/hub area shows evidence of recent refactoring (issue #760) with extraction of mixins and helper modules, but still contains significant code smells. The most critical issues are extremely long parameter lists in constructors (21 parameters in Hub.__init__) and large functions that bypass complexity checks with noqa comments. The architecture has been improved through mixin decomposition, but several functions remain too complex.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| hub.py | 74-96 | Hub.__init__ has 21 parameters | Critical | Use config object pattern; extract into HubConfig dataclass |
| _dispatch.py | 27-199 | dispatch_outbound_item is 170+ lines with C901/PLR0913/PLR0915 noqa | High | Split into smaller functions: routing validation, circuit check, retry loop, callback handling |
| middleware_stt.py | 81-191 | SttMiddleware.__call__ is 110+ lines with C901/PLR0915 noqa | High | Extract STT transcription logic into separate service class |
| outbound_streaming.py | 47-137 | StreamingDispatch.dispatch is 90+ lines with C901/PLR0915 noqa | Medium | Extract voice-tee setup and fallback path into separate methods |
| outbound_router.py | 55-64 | OutboundRouter.__init__ has 6 parameters with PLR0913 noqa | Medium | Consider config object for adapter/dispatcher/tts/audio_pipeline references |
| _dispatch.py | 27-34 | dispatch_outbound_item has 6 parameters with PLR0913 noqa | Medium | Create DispatchContext dataclass to bundle parameters |
| middleware_pool.py | 147-154 | _dispatch_command has 5 parameters with PLR0913 noqa | Low | Acceptable given context passing requirement |
| hub.py | 74-145 | Hub.__init__ body is 71 lines (initialization bloat) | Medium | Move initialization to separate factory or builder pattern |
| identity_resolver.py + hub_dispatch.py + outbound_router.py + outbound_streaming.py | Multiple | Platform validation pattern `try: Platform(msg.platform) except ValueError` duplicated | Low | Create shared validation helper in hub_protocol.py |
| _dispatch.py + hub_circuit_breaker.py | Multiple | Circuit breaker check pattern duplicated | Low | Already delegated but both have similar notification logic |

### Metrics

- **Avg function length**: ~25 lines (most functions are well-sized)
- **Max function length**: ~170 lines (dispatch_outbound_item in _dispatch.py)
- **God classes**: 0 (Hub is composed of mixins, keeping individual classes small)
- **Duplication hotspots**: 2 (Platform validation pattern, circuit breaker notification logic)
- **Functions > 50 lines**: 4
- **Classes > 300 lines**: 0
- **Long parameter lists (>5 params)**: 4

### Recommendations

1. **Critical - Hub.__init__ refactoring**: Create a `HubConfig` dataclass to bundle the 21 initialization parameters. This would dramatically improve testability and configuration management.
   ```python
   @dataclass
   class HubConfig:
       rate_limit: int = Hub.RATE_LIMIT
       rate_window: int = Hub.RATE_WINDOW
       pool_ttl: float = Hub.POOL_TTL
       # ... all other config values

   class Hub:
       def __init__(self, config: HubConfig, ...) -> None:
   ```

2. **High - dispatch_outbound_item decomposition**: This 170+ line function should be split into:
   - `_validate_routing()` - routing context verification
   - `_check_circuit_breaker()` - circuit breaker status and notification
   - `_dispatch_with_retry()` - retry loop with exponential backoff
   - `_invoke_dispatched_callback()` - post-dispatch callback

3. **High - SttMiddleware extraction**: The STT transcription logic (110+ lines) should be extracted into a dedicated `SttTranscriptionService` class that handles:
   - File writing and temp management
   - Timeout handling
   - Error classification
   - Result validation

4. **Medium - Platform validation helper**: Create a shared helper:
   ```python
   def validate_platform(platform_str: str) -> Platform | None:
       try:
           return Platform(platform_str)
       except ValueError:
           return None
   ```

5. **Low - Consider DispatchContext**: Bundle the 6 parameters of dispatch_outbound_item into a context object for cleaner signatures.
