# Type Safety Analysis: Bootstrap

### Summary
The bootstrap package demonstrates reasonable type safety with consistent use of modern Python typing (`from __future__ import annotations`, union types with `| None`), but has notable gaps in NATS client typing (ubiquitous `Any`), loose generic type annotations on several functions, and untyped parameters in the shutdown helper. The codebase shows intentional type safety practices but needs tightening on infrastructure boundary types.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| src/lyra/bootstrap/infra/health.py | 19 | `_probe_nats(nc: Any | None)` - NATS client typed as `Any` | Medium | Import `NATS` from `nats.aio.client` and use `NATS | None` |
| src/lyra/bootstrap/infra/health.py | 57 | `create_health_app(hub, nc: Any | None)` - Same issue | Medium | Type as `NATS | None` |
| src/lyra/bootstrap/factory/config.py | 150 | `_load_raw_config(...) -> dict[str, Any]` | Low | Acceptable for config parsing, document as intentional |
| src/lyra/bootstrap/factory/config.py | 176-268 | Multiple functions with `dict[str, Any]` parameters | Low | Acceptable for raw TOML data; Pydantic models provide downstream safety |
| src/lyra/bootstrap/wiring/bootstrap_wiring.py | 44 | `nats_client: Any = None` | Medium | Type as `NATS | None = None` |
| src/lyra/bootstrap/wiring/bootstrap_wiring.py | 119 | `nats_client: Any = None` (duplicate pattern) | Medium | Type as `NATS | None = None` |
| src/lyra/bootstrap/lifecycle/lifecycle_helpers.py | 15 | Protocol `stop() -> Any` should return `None` | Medium | Change to `async def stop(self) -> None: ...` |
| src/lyra/bootstrap/lifecycle/bootstrap_lifecycle.py | 36 | `nc: Any | None = None` | Medium | Type as `NATS | None` |
| src/lyra/bootstrap/factory/agent_factory.py | 37-39 | `_resolve_bot_agent_map(agent_store, tg_bots: list, dc_bots: list) -> dict` - Missing type args | Medium | Add `list[TelegramBotConfig]`, `list[DiscordBotConfig]`, `dict[tuple[str, str], str]` |
| src/lyra/bootstrap/standalone/adapter_standalone.py | 96 | `wired: list[tuple]` without tuple element types | Low | Use `list[tuple[TelegramAdapter, Bus[InboundMessage]]]` |
| src/lyra/bootstrap/standalone/adapter_standalone.py | 231 | `wired_dc: list[tuple]` without tuple element types | Low | Add tuple element types |
| src/lyra/bootstrap/standalone/stt_adapter_standalone.py | 21 | `import pynvml  # type: ignore[import-untyped]` | Low | Add `py.typed` marker or create type stubs |
| src/lyra/bootstrap/standalone/tts_adapter_standalone.py | 20 | `import pynvml  # type: ignore[import-untyped]` | Low | Same as above |
| src/lyra/bootstrap/standalone/adapter_standalone.py | 104, 239 | `# type: ignore[type-arg]` on `NatsBus` construction | Low | Investigate if generic type can be specified correctly |
| src/lyra/bootstrap/factory/voice_overlay.py | 65-66 | `stt: object | None, tts: object | None` | Medium | Use `STTProtocol | None`, `TtsProtocol | None` |
| src/lyra/bootstrap/standalone/hub_standalone_helpers.py | 76-81 | `shutdown_hub_runtime` has untyped params: `readiness_sub`, `dispatchers`, `proxies`, `nats_llm_driver` | High | Add proper type annotations |
| src/lyra/bootstrap/auth_seeding.py | 24 | `raw_config: dict` without type args | Low | Change to `dict[str, Any]` for consistency |
| src/lyra/bootstrap/factory/unified.py | 49 | `raw_config: dict` without type args | Low | Change to `dict[str, Any]` |
| src/lyra/bootstrap/standalone/adapter_standalone.py | 25 | `raw_config: dict` without type args | Low | Change to `dict[str, Any]` |

### Metrics
- **Type coverage**: ~78% (most functions have return types; some param types incomplete)
- **`Any` usage**: 26 instances (mostly NATS client and config dict patterns)
- **`type: ignore`**: 6 instances (4 for `pynvml` import, 2 for `NatsBus` generic)

### Recommendations
1. **High Priority**: Add type annotations to `shutdown_hub_runtime` parameters in `hub_standalone_helpers.py`
2. **Medium Priority**: Replace `Any` with `NATS` type for NATS client parameters across:
   - `health.py`
   - `bootstrap_wiring.py`
   - `bootstrap_lifecycle.py`
3. **Medium Priority**: Fix `_Stoppable` protocol in `lifecycle_helpers.py` to return `None` instead of `Any`
4. **Medium Priority**: Use specific types instead of `object` in `voice_overlay.py` (`STTProtocol`, `TtsProtocol`)
5. **Medium Priority**: Add type arguments to generic `list` and `dict` returns in `agent_factory.py`
6. **Low Priority**: Standardize `dict` parameters to `dict[str, Any]` throughout
7. **Low Priority**: Investigate `# type: ignore[type-arg]` on `NatsBus` to see if proper generic type can be specified
