### Summary

The adapters area (36 Python files across `discord/`, `telegram/`, `nats/`, `shared/`) is well-maintained with no TODO/FIXME comments. However, there are notable patterns requiring attention: duplicate module-level constants between NATS files, over-broad `except Exception` handlers across 35+ sites, local variable naming inconsistency (`_is_dm` vs `is_dm`), and magic numbers embedded in code rather than named constants.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `nats/nats_envelope_handlers.py` | 32-33 | Duplicate constants `_MAX_STREAMS=100`, `_MAX_QUEUE_SIZE=256` | Medium | Extract to shared constants module or import from nats_outbound_listener |
| `nats/nats_outbound_listener.py` | 33-34 | Duplicate constants `_MAX_STREAMS=100`, `_MAX_QUEUE_SIZE=256` | Medium | Same as above - consolidate |
| `discord/discord_audio.py` | 32, 41, 53 | Magic numbers `4`, `12`, `8` for byte length checks with `# noqa: PLR2004` | Low | Define named constants (e.g., `_MIN_MAGIC_BYTES = 4`) |
| `discord/discord_audio.py` | 186-213 | Prefixed local vars `_audio_is_dm`, `_audio_is_thread` etc. | Low | Remove underscore prefix; not module-private |
| `discord/discord_inbound.py` | 59-94 | Prefixed local vars `_is_dm`, `_is_thread`, `_is_mention`, `_is_watch_channel`, `_should_process` | Low | Remove underscore prefix; standard practice is unprefixed locals |
| `discord/discord_outbound.py` | 84 | Magic number `9` (typing interval seconds) | Low | Already documented inline; extract to named constant `_DISCORD_TYPING_INTERVAL = 9` |
| `discord/discord_formatting.py` | 28, 59 | Magic numbers `3` and `100` with `# noqa: PLR2004` | Low | Define named constants |
| `discord/voice/discord_voice.py` | 19 | Magic number `3840` (PCM frame size) | Low | Define named constant with doc: `_FRAME_SIZE = 3840  # 20ms x 48kHz stereo 16-bit PCM` |
| `shared/_shared_audio.py` | 39 | Magic number `5 * 1024 * 1024` (5MB audio limit) | Low | Extract to named constant (but already configurable via env) |
| `shared/_shared_streaming_state.py` | 23, 28 | Magic numbers `1.0` (edit interval), `8000` (max intermediate chars) | Low | Extract to named constants |
| `shared/_shared_streaming_state.py` | 96-101 | Hardcoded error messages in fallback strings | Low | Consider moving to i18n keys |
| Multiple files | Various | 35+ `except Exception:` handlers without specific exception types | Medium | Catch specific exceptions where possible; see details below |
| `discord/discord_inbound.py` | 29 | `# noqa: C901, PLR0915` for complex function | Low | Consider further decomposition if feasible |
| `discord/discord_outbound.py` | 34, 95, 152 | `# noqa: C901` for complex functions | Low | Acceptable given platform API constraints |

### Metrics

- TODOs: 0
- FIXMEs: 0
- Dead code lines: 0
- Deprecated patterns: 0
- `except Exception` handlers: 35+
- Duplicate constants: 2 pairs (NATS limits, audio max bytes appears 3x)
- Magic numbers: 12 instances
- Prefixed local variables: 11 instances

### Over-Broad Exception Handling

The adapters area contains 35+ `except Exception` catch-all handlers. While many are justified (network/API operations where any exception should be caught and logged), some could benefit from specificity:

**Legitimate catch-alls (network/API boundaries):**
- `nats_envelope_handlers.py`: Deserialization, JSON decode
- `discord_audio.py`: API sends, file downloads
- `discord_inbound.py`: Thread creation, normalization
- `shared/_shared_streaming_emitter.py`: Placeholder edits, fallback sends

**Candidates for specificity:**
- `discord/discord_audio.py:32` — Magic byte validation could catch only `IndexError`
- `shared/_shared.py:239` — Retry logic could catch specific retryable exceptions
- `shared/_shared_streaming_emitter.py:173` — Stream error could narrow to known stream errors

### Recommendations

1. **High Priority — Consolidate NATS constants**
   - Move `_MAX_STREAMS` and `_MAX_QUEUE_SIZE` to a single location
   - Either: create `nats/_constants.py` or have `nats_outbound_listener.py` define and export them

2. **Medium Priority — Exception handler specificity**
   - Audit `except Exception` handlers in `discord_audio.py` magic byte checks
   - Add specific exception types for known failure modes in streaming emitter

3. **Low Priority — Naming convention cleanup**
   - Remove underscore prefixes from local variables in `discord_inbound.py` and `discord_audio.py`
   - Standard pattern: underscores for module-private names, not local scope

4. **Low Priority — Named constants**
   - Extract magic numbers to named constants at module level
   - Document units and rationale in comments
   - Already done well for `_FRAME_SIZE` and `DISCORD_MAX_LENGTH`

5. **Acceptable — Complexity suppressions**
   - `# noqa: C901` suppressions for gateway dispatch and streaming callbacks are acceptable
   - Platform API constraints make further decomposition impractical
