# src/lyra/bootstrap/ — Startup Wiring

## Invariant

Bootstrap = orchestration only. No business logic — all domain behaviour lives in `core/`.

## Public entry points (called from CLI)

- `_bootstrap_hub_standalone` — hub process
- `_bootstrap_adapter_standalone` — Telegram / Discord adapter process
- `_bootstrap_clipool_standalone` — CLI pool process
- `_bootstrap_turn_writer_standalone` — turn-writer JetStream subscriber process (`lyra turn-writer`, `standalone/worker_standalone.py`)
- `_bootstrap_unified` — all-in-one single process

## Flat files at root

`auth_seeding.py`, `bootstrap_stores.py`, `types.py` stay flat — they bridge multiple
subdirs and are imported by both `standalone/` and `factory/`.

## NATS driver composition (post-#1278)

`factory/llm_overlay.py` and `factory/voice_overlay.py` compose NATS drivers in 3 layers:

```
NatsTransport(nc) → WorkerPoolClient(transport, hb_subject, validate_worker_id)
                 → DomainClient(pool, codec)
```

Each `init_nats_*` helper builds and returns the domain client. Callers (wiring_helpers)
treat the returned object as opaque — they only call domain methods (`complete()`,
`synthesize()`, etc.) plus `stop()` for shutdown.

## Rules

- All intra-bootstrap imports: full absolute paths (`lyra.bootstrap.<subdir>.<module>`).
