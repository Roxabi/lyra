# Tech Debt Analysis: Infrastructure + NATS

**Area:** `src/lyra/infrastructure/**/*.py`, `src/lyra/nats/**/*.py`
**Date:** 2026-04-22

## Summary

The infrastructure and NATS areas show good overall hygiene with no TODO/FIXME markers, no unused imports, and no commented-out code. Constants are generally well-extracted with clear naming. The primary tech debt is code duplication across the three NATS voice/image clients (`NatsSttClient`, `NatsTtsClient`, `NatsImageClient`) which share nearly identical heartbeat handling, circuit breaker patterns, and error translation logic.

## Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `nats/nats_stt_client.py` | 106-133 | Duplicated `_on_heartbeat` pattern across 3 clients | Medium | Extract to `VoiceClientBase` mixin or helper function |
| `nats/nats_tts_client.py` | 55-83 | Duplicated `_on_heartbeat` pattern (identical to STT) | Medium | Same as above |
| `nats/nats_image_client.py` | 164-192 | Duplicated `_on_heartbeat` pattern (identical to STT/TTS) | Medium | Same as above |
| `nats/nats_stt_client.py` | 228-247 | `_raise_nats_failure` pattern duplicated across 3 clients | Medium | Extract to shared `raise_nats_failure(exc, payload_kb, domain_error, cb)` helper |
| `nats/nats_tts_client.py` | 129-148 | `_raise_nats_failure` pattern (identical structure) | Medium | Same as above |
| `nats/nats_image_client.py` | 222-237 | `_raise_nats_failure` pattern (identical structure) | Medium | Same as above |
| `nats/nats_bus.py` | 64 | `noqa: PLR0913` (too many args) on `__init__` | Low | Acceptable for constructor; consider config object if args grow |
| `nats/nats_stt_client.py` | 79 | `noqa: PLR0913` on `__init__` | Low | Same as above |
| `infrastructure/stores/turn_store.py` | 125 | `noqa: PLR0913` on `log_turn` | Low | Consider kwargs-only signature for future |
| `nats/nats_bus.py` | 71 | Magic number `500` for `staging_maxsize` | Low | Already parameterized; add named constant for default |
| `nats/voice_health.py` | 28 | Magic number `64` for `MAX_WORKERS` | Low | Properly named; document rationale (cap prevents unbounded growth) |
| `infrastructure/stores/sqlite_base.py` | 66 | Magic number `1800` for WAL checkpoint interval | Low | Properly named; consider making configurable via constructor |

## Metrics

| Metric | Count |
|--------|-------|
| TODOs | 0 |
| FIXMEs | 0 |
| XXX/HACK comments | 0 |
| Unused imports (F401) | 0 |
| Unused variables (F841) | 0 |
| Commented-out code blocks | 0 |
| Bare `except:` clauses | 0 |
| `noqa` suppressions | 3 |
| Duplicated patterns | 3 patterns across 3 files |

## Recommendations

### Priority 1: Extract Voice Client Base Pattern

The three NATS clients (`NatsSttClient`, `NatsTtsClient`, `NatsImageClient`) share:

1. **Heartbeat subscription logic** - identical JSON parse, worker_id validation, registry update
2. **Circuit breaker lifecycle** - `record_failure()`/`record_success()` calls on every path
3. **NATS failure translation** - `_raise_nats_failure` with identical structure

**Approach:** Create a `NatsVoiceClientBase` abstract class or mixin in `lyra.nats`:

```python
class NatsVoiceClientBase(ABC):
    _nc: NATS
    _cb: NatsCircuitBreaker
    _registry: VoiceWorkerRegistry
    _hb_sub: Subscription | None

    async def _start_heartbeat_listener(
        self, subject: str, domain: str
    ) -> None: ...
    def _raise_nats_failure(
        self, exc: Exception, payload_kb: float, domain_error: type[Exception]
    ) -> NoReturn: ...
```

Estimated reduction: ~90 lines of duplicated code.

### Priority 2: Named Constants for Defaults

Extract default values to named constants at module level:

```python
# nats_bus.py
DEFAULT_STAGING_MAXSIZE = 500

# nats_tts_client.py
DEFAULT_TTS_TIMEOUT = 30.0

# nats_image_client.py  
DEFAULT_IMAGE_TIMEOUT = 120.0
```

### Priority 3: Constructor Parameter Objects (Optional)

If any client `__init__` exceeds 7 parameters, consider introducing a config dataclass:

```python
@dataclass
class NatsSttConfig:
    timeout: float = 15.0
    model: str = "large-v3-turbo"
    language_detection_threshold: float | None = None
    # ...
```

Currently all clients are at or below the threshold, so this is preventive only.

## Notes

- No security issues found (input validation via `validate_nats_token` and `validate_worker_id`)
- No deprecated API usage detected
- Exception handling is specific (no bare `except:`)
- The `# noqa: PLR0913` suppressions are legitimate for constructor signatures with optional parameters
