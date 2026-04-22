# Type Safety Analysis: Infrastructure + NATS

### Summary

The infrastructure and nats modules demonstrate generally strong type coverage with modern Python typing conventions (`from __future__ import annotations`, union syntax `|`, generics). However, there are several areas for improvement: missing return type annotation on `_db_or_raise()`, untyped `msg` parameters in NATS heartbeat handlers, raw `dict` usage without type parameters in settings/metadata, and a few `Any` type escapes in NATS serialization code.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/turn_store.py` | 119 | Missing return type on `_db_or_raise()` | Medium | Add `-> aiosqlite.Connection` return type |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_stt_client.py` | 106 | Untyped `msg` parameter in `_on_heartbeat` | Medium | Add `msg: Msg` type hint (Msg from nats.aio.msg) |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_tts_client.py` | 55 | Untyped `msg` parameter in `_on_heartbeat` | Medium | Add `msg: Msg` type hint |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_image_client.py` | 164 | Untyped `msg` parameter in `_on_heartbeat` | Medium | Add `msg: Msg` type hint |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_bus.py` | 258 | `Any` type escape for `item` variable | Low | Cast to proper type or use overload |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_channel_proxy.py` | 75 | `Any` type for `raw` parameter in `normalize()` | Low | Protocol requires `Any`; document intentional escape |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_channel_proxy.py` | 82 | `Any` type for `raw` parameter in `normalize_audio()` | Low | Protocol requires `Any`; document intentional escape |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/bot_agent_map.py` | 93 | Untyped `dict` return on `get_bot_settings()` | Medium | Return `dict[str, Any]` or create typed settings model |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/agent_store.py` | 110 | Untyped `dict` return on `get_bot_settings()` | Medium | Return `dict[str, Any]` or create typed settings model |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/bot_agent_map.py` | 108 | Untyped `dict` for `settings` parameter | Medium | Annotate as `dict[str, Any]` |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/bot_agent_map.py` | 136 | Untyped `dict` for `settings` parameter | Medium | Annotate as `dict[str, Any]` |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/agent_store.py` | 120 | Untyped `dict` for `settings` parameter | Medium | Annotate as `dict[str, Any]` |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/agent_store.py` | 128 | Untyped `dict` for `settings` parameter | Medium | Annotate as `dict[str, Any]` |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/turn_store.py` | 136 | Untyped `dict` for `metadata` parameter | Low | Annotate as `dict[str, Any]` |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/bot_agent_map.py` | 31 | Nested untyped `dict` in `_bot_settings` attribute | Low | Change to `dict[tuple[str, str], dict[str, Any]]` |
| `/home/mickael/projects/lyra/src/lyra/nats/voice_health.py` | 64 | Untyped `dict` for `payload` parameter | Low | Annotate as `dict[str, Any]` |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/auth_store.py` | 158 | Untyped `dict` for `raw` parameter | Low | Annotate as `dict[str, Any]` |
| `/home/mickael/projects/lyra/src/lyra/nats/render_event_codec.py` | 63 | Untyped `dict` for `payload` parameter | Low | Annotate as `dict[str, Any]` |

### Metrics

- **Type coverage**: ~95% (strong use of modern typing conventions)
- **`Any` usage**: 3 instances (2 intentional protocol escapes, 1 type escape in deserialize)
- **`type: ignore`**: 0 instances (excellent)
- **Untyped `dict`**: 9 instances
- **Missing return types**: 1 instance
- **Untyped parameters**: 4 instances (3 `msg` handlers, 1 `_db_or_raise`)

### Recommendations

1. **High Priority**: Add return type annotation to `_db_or_raise()` method in `turn_store.py` - this is a public-ish method that should have explicit typing.

2. **Medium Priority**: Type the `msg` parameter in all NATS `_on_heartbeat` handlers across `nats_stt_client.py`, `nats_tts_client.py`, and `nats_image_client.py`. Import `Msg` from `nats.aio.msg` and annotate as `msg: Msg`.

3. **Medium Priority**: Replace raw `dict` returns with `dict[str, Any]` for settings/metadata parameters. Consider creating a `BotSettings` TypedDict or Pydantic model for stronger type safety.

4. **Low Priority**: Document the intentional `Any` escapes in `NatsChannelProxy.normalize()` and `normalize_audio()` methods with inline comments explaining they implement a protocol that must accept platform-specific raw payloads.

5. **Low Priority**: Consider typing the `payload: dict` parameters in voice health and render codec as `dict[str, Any]` for clarity, though these are internal functions with clear context.
