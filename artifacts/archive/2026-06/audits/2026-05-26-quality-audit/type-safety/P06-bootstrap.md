# P06 Type-Safety Audit — `src/lyra/bootstrap/**/*.py`

Date: 2026-05-26
Scope: 31 files, ~3 704 lines
Prior audit (2026-05-18): hexagonal / duplication findings are NOT re-reported unless regressed.

---

## Summary

- **Central hotspot:** `factory/config.py` carries 18 `typing.Any` usages (66 % of partition total) for raw TOML config parsing.
- **Severe suppression:** `factory/wiring_helpers.py` disables `reportAttributeAccessIssue` and `reportArgumentType` at file level to compensate for `stores: object` and loosely-typed bundle dataclasses.
- **Low surface otherwise:** zero missing return types, zero untyped `*args/**kwargs`, only three `# type: ignore` comments, one runtime `assert`.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `factory/wiring_helpers.py` | 3 | **High** | File-level `# pyright: reportAttributeAccessIssue=false, reportArgumentType=false` suppresses two major check categories for the entire module. | Remove suppression; type `stores` as `StoreBundle`, narrow `BotAuthBundle` / `VoiceBundle` / `CliPoolBundle` fields, then add per-line ignores only where truly unavoidable. |
| `factory/config.py` | 10, 148, 174, 181, 182, 192, 196, 219, 221, 225, 227, 231, 236, 241, 246, 251, 256, 261, 266 | **Medium** | 18 `typing.Any` usages — all `dict[str, Any]` for raw config dict parameters and locals (`_load_*` helpers + `_build_agent_overrides`). | Introduce `RawConfig = dict[str, Any]` alias (or lightweight Pydantic model) at the config boundary so downstream helpers do not re-declare `Any`. |
| `factory/wiring_helpers.py` | 59–84 | **Medium** | `BotAuthBundle` fields use `object` / `dict` / `list` without parameters: `tg_bot_auths: object`, `dc_bot_auths: object`, `bot_agent_map: dict`, `msg_manager: object`, `circuit_registry: object`, `admin_user_ids: list`. | Narrow to concrete types: e.g. `list[tuple[TelegramBotConfig, Authenticator]]`, `dict[tuple[str, str], str]`, `MessageManager`, `CircuitRegistry`, `list[str]`. |
| `factory/wiring_helpers.py` | 61–82 | **Medium** | `VoiceBundle` uses `stt_service: object`, `tts_service: object`; `CliPoolBundle` uses `worker: object`. | Use `NatsSttClient \| None`, `NatsTtsClient \| None`, `CliPoolNatsWorker`. |
| `wiring/bootstrap_wiring.py` | 42, 115 | **Medium** | `nats_client: Any = None` in `wire_telegram_adapters` and `wire_discord_adapters`. | Replace with `nats.aio.client.Client \| None`. |
| `infra/health.py` | 44, 67, 124 | **Medium** | `_probe_nats(nc: Any \| None)`, `create_health_app(nc: Any \| None)`, `result: dict[str, Any]`. | Use `NATS \| None` for `nc`; define a `HealthDetail` TypedDict for `result`. |
| `standalone/hub_standalone_helpers.py` | 30 | **Medium** | `start_mint_failure_subscriber(nc: Any)` — `nc` is only used to pass into `MintFailureSubscriber`, which expects a NATS client. | Use `nats.aio.client.Client`. |
| `standalone/hub_standalone.py` | 73 | **Low** | `_freshness_drivers: list[Any]` holds `LlmClient` instances. | Use `list[LlmClient]`. |
| `standalone/adapter_standalone.py` | 92, 231 | **Low** | Two `# type: ignore[type-arg]` on `NatsBus(...)` because generic param is missing. | Supply the generic argument or fix upstream `NatsBus` default so the ignore is unnecessary. |
| `factory/hub_builder.py` | 191 | **Low** | `# type: ignore[arg-type]` for `nats_llm_client=voice.nats_llm_client`. | Resolve T24 TODO: update `_resolve_agents` signature to accept `LlmClient \| None` natively. |
| `standalone/hub_standalone.py` | 171 | **Low** | `assert hub._turn_publisher is not None` with `# noqa: S101` — used as runtime guard + type narrowing. | Replace with explicit `if hub._turn_publisher is None: raise RuntimeError(...)`; `assert` is stripped in optimized builds. |
| `factory/agent_factory.py` | 36–37 | **Low** | `_resolve_bot_agent_map(agent_store, tg_bots: list, dc_bots: list)` — missing list parameters. | Add `list[TelegramBotConfig]` and `list[DiscordBotConfig]`. |

---

## Metrics

| Metric | Count | Notes |
|---|---|---|
| `typing.Any` usages | **27** | 18 in `factory/config.py`, 3 in `infra/health.py`, 2 in `wiring/bootstrap_wiring.py`, 2 in `lifecycle/lifecycle_helpers.py`, 1 in `standalone/hub_standalone.py`, 1 in `standalone/hub_standalone_helpers.py` |
| `# type: ignore` comments | **3** | 2 `[type-arg]` in `standalone/adapter_standalone.py` (NatsBus generic), 1 `[arg-type]` in `factory/hub_builder.py` (LlmClient param) |
| File-level pyright suppressions | **1** | `factory/wiring_helpers.py` disables `reportAttributeAccessIssue` and `reportArgumentType` |
| Missing return type hints (public) | **0** | All public functions have return annotations |
| Untyped `*args` / `**kwargs` | **0** | None found |
| `cast()` calls | **0** | None found |
| `assert isinstance()` / `assert is not None` | **1** | `standalone/hub_standalone.py:171` (`assert hub._turn_publisher is not None`) |
| `object`-typed params / fields | **~14** | Concentrated in `factory/wiring_helpers.py` bundles and `probe_voice_services` |
| Unparameterized `dict` / `list` | **~12** | Mostly `raw_config: dict`, plus a few `list[tuple]` and bundle fields |
| Files with type safety debt | **9 / 31** | ~29 % of files |

---

## Recommendations (prioritized, max 5)

1. **Remove file-level pyright suppression in `factory/wiring_helpers.py`** — type `stores` as `StoreBundle`, narrow the three bundle dataclasses to concrete types, and delete the line-3 suppression. This single change restores attribute-access and argument-type checking for ~260 lines of bootstrap wiring.

2. **Introduce a `RawConfig` alias in `factory/config.py`** — define `RawConfig = dict[str, Any]` (or a thin Pydantic model) once and reuse it across all `_load_*` helpers. This collapses 18 `Any` declarations into 1 and makes future config migrations type-safe by default.

3. **Audit `nats_client` / `nc` boundary types** — replace `Any` with `nats.aio.client.Client \| None` in `wiring/bootstrap_wiring.py`, `infra/health.py`, and `standalone/hub_standalone_helpers.py`. These are 6 low-risk replacements that eliminate NATS-related `Any` entirely.

4. **Resolve T24 TODO in `factory/hub_builder.py`** — update `_resolve_agents` to accept `LlmClient \| None` natively and remove the `# type: ignore[arg-type]`.

5. **Replace `assert` with explicit guard in `standalone/hub_standalone.py:171`** — `assert` is not a production-safe runtime check. Use an explicit `if ... is None: raise` to guarantee the guard survives `-O` execution.
