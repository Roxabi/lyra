# Code Smells Analysis: Infrastructure + NATS

### Summary
The infrastructure and NATS modules contain 2 god classes (`AgentStore` with 17 methods, `NatsBus` with 15 methods), 1 long function (`send_streaming()` at ~86 lines), and significant code duplication across the three NATS voice/image clients (~60 lines of duplicated patterns). Three methods have long parameter lists already acknowledged with `noqa: PLR0913` comments, indicating awareness of the issue but no refactoring.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/nats/nats_channel_proxy.py` | 112 | `send_streaming()` function is ~86 lines | High | Extract streaming chunk handling into separate method |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/agent_store.py` | 32 | `AgentStore` class has 17 methods (god class) | High | Split into AgentConfigStore + AgentRuntimeStore + BotMappingFacade |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_bus.py` | 50 | `NatsBus` class has 15 methods (god class) | Medium | Extract subscription management into NatsSubscriptionManager |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/agent_store.py` | 141 | `upsert()` function is ~67 lines | Medium | Extract AgentRow serialization into dedicated method |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_channel_proxy.py` | 112 | Deep nesting (5+ levels) in `send_streaming()` | High | Flatten with early returns and guard clauses |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_stt_client.py` | 106 | Duplicated heartbeat pattern across 3 clients | Medium | Extract into shared NatsWorkerClientMixin base class |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_tts_client.py` | 55 | Duplicated `_on_heartbeat()`, `_parse_reply()`, `_raise_nats_failure()` | Medium | Create shared VoiceClientBase class |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_image_client.py` | 164 | Same duplicated patterns as STT/TTS clients | Medium | Inherit from shared base class |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/turn_store.py` | 125 | `log_turn()` has 10 parameters (noqa comment present) | Medium | Use TurnParams dataclass for parameter grouping |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_bus.py` | 64 | `__init__` has 8 parameters (noqa comment present) | Low | Group connection params into NatsBusConfig |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_stt_client.py` | 79 | `__init__` has 7 parameters (noqa comment present) | Low | Group TTS/STT config params into dataclass |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/turn_store.py` | 80 | `TurnStore` + mixin = 11 methods total (god class borderline) | Low | Monitor; acceptable if methods remain cohesive |

### Metrics

- **Avg function length:** 18 lines (median: 12 lines)
- **Max function length:** 86 lines (`send_streaming()`)
- **God classes:** 2 (`AgentStore`, `NatsBus`)
- **Duplication hotspots:** 3 (STT/TTS/Image client patterns)
- **Functions > 50 lines:** 3
- **Functions > 5 params:** 4 (3 with existing noqa comments)
- **Deep nesting violations:** 1 (`send_streaming()`)

### Detailed Analysis

#### God Class: AgentStore (17 methods)

The `AgentStore` class violates Single Responsibility Principle by handling:
- Agent configuration CRUD (`get`, `get_all`, `upsert`, `delete`)
- Bot-agent mapping delegation (`get_bot_agent`, `set_bot_agent`, etc.)
- Runtime state management (`get_all_runtime_states`, `set_runtime_state`)
- TOML seeding (`seed_from_toml`)

This creates a class with 17 methods that's difficult to test in isolation and has multiple reasons to change.

#### God Class: NatsBus (15 methods)

The `NatsBus` class handles:
- Registration management
- Lifecycle (`start`, `stop`)
- Message I/O (`put`, `get`)
- Introspection (`qsize`, `staging_qsize`, `version_mismatch_count`)
- Internal handler management

The introspection methods could be extracted into a separate diagnostics class.

#### Duplication: NATS Voice Clients

All three clients (`NatsSttClient`, `NatsTtsClient`, `NatsImageClient`) share:
- Nearly identical `_on_heartbeat()` implementations (~28 lines each = ~84 lines total)
- Similar `_raise_nats_failure()` pattern (~20 lines each = ~60 lines total)
- Identical circuit breaker and registry initialization
- Same `start()` pattern

A shared base class would eliminate ~60-80 lines of duplication.

#### Long Function: send_streaming()

The `send_streaming()` method in `NatsChannelProxy` is the most complex function:
- 86 lines total
- 5 levels of nesting (try/except/finally + async for + nested try/except)
- Multiple responsibilities: stream tracking, chunk serialization, error handling, cleanup

Recommended extraction:
1. `_publish_stream_chunk()` - handles individual chunk publishing
2. `_publish_stream_terminal()` - handles sentinel/error publishing
3. `_drain_and_log()` - cleanup pattern

### Recommendations

1. **High Priority:** Refactor `NatsChannelProxy.send_streaming()` - Extract nested logic into 2-3 focused helper methods to reduce from 86 to ~30 lines and eliminate deep nesting.

2. **High Priority:** Split `AgentStore` into focused stores:
   - `AgentConfigStore` - handles agent row persistence
   - `AgentRuntimeStore` - handles runtime state (already separate table)
   - Keep `AgentStore` as facade composing both for backward compatibility

3. **Medium Priority:** Create `NatsVoiceClientBase` class - Extract common heartbeat, circuit breaker, and failure handling patterns from STT/TTS/Image clients into a shared base class.

4. **Medium Priority:** Introduce parameter objects for long parameter lists:
   - `TurnParams` dataclass for `log_turn()` parameters
   - `NatsBusConfig` for `NatsBus.__init__` options

5. **Low Priority:** Monitor `NatsBus` - If new introspection methods are added, extract into `NatsBusDiagnostics` class.
