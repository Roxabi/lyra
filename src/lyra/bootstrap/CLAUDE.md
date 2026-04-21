# src/lyra/bootstrap/ — Startup Wiring and Bootstrap

## Purpose

The `bootstrap/` package wires the full process at startup: config loading, store
opening, adapter setup, NATS connections, agent registration, and lifecycle management.
No business logic lives here — all domain behaviour is in `core/`.

## Subdir layout (V4 decomposition — #773)

```
bootstrap/
  standalone/         # NATS-connected standalone entry points (5 files + __init__)
    adapter_standalone.py          # _bootstrap_adapter_standalone
    hub_standalone.py              # _bootstrap_hub_standalone
    hub_standalone_helpers.py      # load_agent_configs, build_pairing_manager, shutdown_hub_runtime
    stt_adapter_standalone.py      # SttAdapterStandalone, _bootstrap_stt_adapter_standalone
    tts_adapter_standalone.py      # TtsAdapterStandalone, _bootstrap_tts_adapter_standalone

  wiring/             # Adapter and NATS wiring helpers (2 files + __init__)
    bootstrap_wiring.py            # wire_telegram_adapters, wire_discord_adapters, _build_bot_auths
    nats_wiring.py                 # wire_nats_telegram_proxies, wire_nats_discord_proxies

  lifecycle/          # Lifecycle orchestration and signal handling (3 files + __init__)
    bootstrap_lifecycle.py         # run_lifecycle
    lifecycle_helpers.py           # setup_signal_handlers, teardown_buses, teardown_dispatchers
    signal_handlers.py             # setup_shutdown_event

  factory/            # Agent/hub construction and unified entry point (8 files + __init__)
    agent_factory.py               # _resolve_agents, _resolve_bot_agent_map
    bot_agent_map.py               # resolve_bot_agent_map
    hub_builder.py                 # build_hub, build_inbound_bus, build_cli_pool, register_agents
    llm_overlay.py                 # init_nats_llm
    voice_overlay.py               # init_nats_stt, init_nats_tts
    unified.py                     # _bootstrap_unified (single-process mode)
    config.py                      # all Pydantic config models and _load_* helpers
    utils.py                       # watchdog, _log_task_failure

  infra/              # Infrastructure helpers (4 files + __init__)
    embedded_nats.py               # EmbeddedNats, ensure_nats
    health.py                      # create_health_app
    lockfile.py                    # acquire_lockfile, release_lockfile
    notify.py                      # notify_startup

  # Flat files (remain at bootstrap/ root)
  auth_seeding.py                  # seed_auth_store, build_bot_auths
  bootstrap_stores.py              # open_stores (store lifecycle context manager)
  __init__.py                      # re-exports _bootstrap_unified, _bootstrap_hub_standalone, _bootstrap_adapter_standalone
```

## Key invariants

- `cli.py` and `__main__.py` are the only external callers — they use lazy imports
  to avoid circular dependencies at module load time.
- All intra-bootstrap imports use full absolute paths (`lyra.bootstrap.<subdir>.<module>`).
- `auth_seeding.py` and `bootstrap_stores.py` stay flat — they bridge multiple subdirs
  and are imported by both `standalone/` and `factory/`.
