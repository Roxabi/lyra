# Type Safety Analysis: Core Hub

### Summary
The `core/hub` area demonstrates strong type safety practices with modern Python 3.10+ union syntax (`| None` instead of `Optional[]`) and generic type syntax (`dict[...]` instead of `Dict[...]`). However, there are 12 instances of `Any` usage (some intentional for Protocols), 3 `type: ignore` comments, and a few missing type hints on callback parameters. Overall type coverage is estimated at approximately 92%.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/core/hub/hub_protocol.py` | 34 | `Any` type on `normalize(self, raw: Any)` | Low | Intentional - Protocol accepts arbitrary platform data; document rationale |
| `/home/mickael/projects/lyra/src/lyra/core/hub/hub_protocol.py` | 37 | `Any` type on `normalize_audio(self, raw: Any, ...)` | Low | Same as above - Protocol design decision |
| `/home/mickael/projects/lyra/src/lyra/core/hub/middleware.py` | 54 | `router: Any = None` | Medium | Type as `CommandRouter | None` if importable without cycles |
| `/home/mickael/projects/lyra/src/lyra/core/hub/middleware_pool.py` | 151 | `router: Any` parameter | Medium | Same as above - type as `CommandRouter | None` |
| `/home/mickael/projects/lyra/src/lyra/core/hub/outbound_errors.py` | 97 | `adapter: Any` parameter | Medium | Type as `"ChannelAdapter"` (string forward reference) |
| `/home/mickael/projects/lyra/src/lyra/core/hub/outbound_errors.py` | 101 | `circuit: Any = None` | Medium | Type as `"CircuitBreaker | None"` per inline comment |
| `/home/mickael/projects/lyra/src/lyra/core/hub/middleware_pool.py` | 110 | `# type: ignore[attr-defined]` | Low | Acceptable - dynamic attribute on third-party object; consider typed Protocol |
| `/home/mickael/projects/lyra/src/lyra/core/hub/middleware_stt.py` | 196 | `# type: ignore[union-attr]` | Medium | Type `hub` parameter as `Hub` instead of `object` |
| `/home/mickael/projects/lyra/src/lyra/core/hub/middleware_stt.py` | 197 | `# type: ignore[union-attr]` | Medium | Same as above |
| `/home/mickael/projects/lyra/src/lyra/core/hub/_dispatch.py` | 33 | Missing type hint on `verify_routing_fn` | Medium | Type as `Callable[[RoutingContext | None], bool]` |
| `/home/mickael/projects/lyra/src/lyra/core/hub/_dispatch.py` | 34 | Missing type hint on `try_notify_fn` | Medium | Type as `Callable[[InboundMessage, str], Coroutine[Any, Any, None]]` |
| `/home/mickael/projects/lyra/src/lyra/core/hub/outbound_router.py` | 62 | `tts: "object | None"` too generic | Low | Type as `"TtsProtocol | None"` if importable |
| `/home/mickael/projects/lyra/src/lyra/core/hub/outbound_tts.py` | 33 | `tts: "object | None"` too generic | Low | Same as above |
| `/home/mickael/projects/lyra/src/lyra/core/hub/outbound_streaming.py` | 38 | `get_tts: Callable[[], object | None]` too generic | Low | Type as `Callable[[], "TtsProtocol | None"]` |

### Metrics
- **Type coverage**: ~92% (estimated from analysis)
- **`Any` usage**: 12 instances across 7 files
  - 4 intentional (Protocol definitions accepting raw platform data)
  - 8 fixable (router, adapter, circuit parameters)
- **`type: ignore`**: 3 instances across 2 files
- **Modern syntax adoption**: 100% using `| None` union syntax and lowercase generic types

### Recommendations

1. **High Priority - Fix `Any` in `outbound_errors.py`** (Lines 97, 101)
   - The `adapter` and `circuit` parameters have documented intended types in comments
   - Replace `Any` with forward-referenced type strings: `"ChannelAdapter"` and `"CircuitBreaker | None"`

2. **High Priority - Fix missing type hints in `_dispatch.py`** (Lines 33-34)
   - Add explicit `Callable` type hints for `verify_routing_fn` and `try_notify_fn` callback parameters

3. **Medium Priority - Type `router` fields** (`middleware.py:54`, `middleware_pool.py:151`)
   - If `CommandRouter` can be imported without cycles, type as `CommandRouter | None`
   - If cycles exist, use `TYPE_CHECKING` block with string forward reference

4. **Medium Priority - Fix `middleware_stt.py` type ignores** (Lines 196-197)
   - Change `hub: object` parameter type to `"Hub"` using forward reference
   - This eliminates both `type: ignore[union-attr]` comments

5. **Low Priority - Tighten TTS types** (`outbound_router.py`, `outbound_tts.py`, `outbound_streaming.py`)
   - Replace `object | None` with `"TtsProtocol | None"` for better IDE support

6. **Document intentional `Any` usage** (`hub_protocol.py`)
   - Add docstring note explaining why `raw: Any` is appropriate for Protocol normalization methods
