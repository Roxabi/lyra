### Summary
- `wire_nats_telegram_proxies` / `wire_nats_discord_proxies` are 97% identical — worst DRY violation in the partition.
- One non-exempted function exceeds 100 lines (`wire_discord_adapters`, 114 lines); four files hold exempted-size functions.
- `factory/wiring_helpers.py` (407 lines, exempted) mixes auth, voice, clipool, hub, and adapter wiring — god-module smell.

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `wiring/nats_wiring.py` | 28, 85 | High | `wire_nats_telegram_proxies` and `wire_nats_discord_proxies` are 97% identical (only `Platform.TELEGRAM`/`DISCORD` and local prefixes differ). | Extract `wire_nats_platform_proxy(platform, bot_auths, ...)` generic helper; inject platform-specific bits as arguments. |
| `wiring/bootstrap_wiring.py` | 107 | Medium | `wire_discord_adapters` = 114 lines (>100-line threshold). Contains nested `_parse_channel_ids` and a try/except thread-store guard, raising cognitive complexity. | Extract `_parse_channel_ids` to a shared discord utility; split ThreadStore setup into a separate helper. |
| `factory/hub_builder.py` / `factory/wiring_helpers.py` | 92, 246 | Medium | `build_hub` and `_build_hub` duplicate identical HubConfig construction (~25 lines: 6 config loaders + HubConfig dataclass population). Only the final `hub.set_turn_store` / `hub.set_alias_store` wiring differs. | Consolidate to single `build_hub` that returns the bare Hub; let callers layer store wiring in one place. |
| `standalone/adapter_standalone.py` | 63-166, 169-305 | Medium | Telegram and Discord branches each repeat: NatsBus creation, `NatsOutboundListener` wiring, `TypingListener` setup, polling loop, and `close_safely` shutdown sequence. Structurally ~80 lines of near-duplication per branch. | Extract `_wire_platform_standalone(platform, bot_cfg, nc, ...)` helper; platform differences (token, auto_thread, etc.) become parameters. |
| `factory/wiring_helpers.py` | 1-407 | Medium | God module: 10 functions + 3 dataclasses, 407 lines (exempted). Responsibilities span auth seeding, message pruning, bot auth resolution, pairing, voice services, hub construction, clipool init, agent registration, and adapter wiring. | Split by domain: `auth_helpers.py`, `voice_helpers.py`, `hub_helpers.py`, `adapter_wiring_helpers.py`. |
| `standalone/hub_standalone.py` | 47 | Low | `_bootstrap_hub_standalone` = 256 lines (exempted #1376). Inline `_on_nats_reconnect` callback, sequential 15-step wiring. Cognitive complexity is very high. | Already tracked by #1376; re-evaluate after stage-axis refactor when steps may collapse into pipeline stages. |
| `standalone/adapter_standalone.py` | 27 | Low | `_bootstrap_adapter_standalone` = 284 lines (exempted #1376/#1396). Nested platform branches, multiple try/finally scopes, inline imports. | Already tracked by #1376/#1396; monitor during stage-axis refactor. |
| `lifecycle/bootstrap_lifecycle.py` | 24 | Low | `run_lifecycle` = 93 lines. Orchestrates hub, health server, audit consumer, Telegram/Discord polling, teardown buses/dispatchers, clipool drain, and hub shutdown in one flat sequence. | Acceptable for a composition-root orchestrator; defer split until S7 when clipool drain path may be removed. |
| `infra/health.py` | 87 | Low | `health_detail` (nested in `create_health_app`) directly accesses 7 Hub internals (`_start_time`, `_last_processed_at`, `circuit_registry`, `inbound_bus`, `outbound_dispatchers`, `adapter_registry`, `cli_pool`). | Consider `Hub.health_snapshot()` method to reduce feature envy and make health data testable without bootstrap path. |
| `bootstrap_stores.py` | 66 | Low | `_atomic_table_copy` = 97 lines (exempted #957). C901 noqa for sequential migration steps. | Acceptable; DDL migration is inherently procedural and this is a one-time bootstrap path. |
| `factory/agent_factory.py` | 128 | Low | `_create_agent` = 64 lines. Inline `SessionTools` construction with broad `except Exception` fallback. | Extract `SessionTools` construction to a dedicated factory/helper; narrow exception type if possible. |

### Metrics

| Metric | Count |
|---|---|
| Files analyzed | 29 |
| Total lines | 3,704 |
| Exempted files >300 lines | 4 |
| Non-exempted functions >100 lines | 1 |
| Functions >60 lines | 13 |
| DRY violation pairs (≥3 lines, ≥2 files) | 3 |
| God modules (>5 responsibilities) | 1 |

### Recommendations (prioritized)

1. **Consolidate NATS proxy wiring** — `wire_nats_telegram_proxies` + `wire_nats_discord_proxies` → single `wire_nats_platform_proxy(platform, ...)` helper. 97% duplication, trivial refactor, highest payoff.
2. **Merge `build_hub` / `_build_hub` HubConfig construction** — single source of truth for the 6-loader + HubConfig block. Store-specific wiring (`set_turn_store`, `set_alias_store`) should be caller-side layer, not a separate function.
3. **Extract platform-generic standalone adapter wiring** — `adapter_standalone.py` Telegram and Discord branches share bus, listener, typing, polling, and shutdown sequences. A `_wire_platform_standalone(platform, ...)` helper would cut ~120 lines.
4. **Split `factory/wiring_helpers.py` by domain** — 407-line god module with auth, voice, clipool, hub, and adapter wiring. Split into `auth_helpers.py`, `voice_helpers.py`, `hub_helpers.py`, `adapter_wiring_helpers.py`.
5. **Add `Hub.health_snapshot()` to reduce feature envy** — `health_detail` scrapes 7 private Hub attributes. A snapshot dataclass returned by Hub would decouple health reporting from bootstrap internals.
