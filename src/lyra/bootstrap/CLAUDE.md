# src/lyra/bootstrap/ — Startup Wiring

## Invariant

Bootstrap = orchestration only. No business logic — all domain behaviour lives in `core/`.

## Subdirs

| Dir | Purpose |
|---|---|
| `standalone/` | Process entry points (`_bootstrap_*_standalone`) |
| `wiring/` | Adapter and NATS wiring |
| `lifecycle/` | Signal handling, teardown, run loop |
| `factory/` | Agent/hub construction, config models, unified entry |
| `infra/` | Embedded NATS, health, lockfile, startup notify |

## Public entry points (called from CLI)

- `_bootstrap_hub_standalone` — hub process
- `_bootstrap_adapter_standalone` — Telegram / Discord adapter process
- `_bootstrap_clipool_standalone` — CLI pool process
- `_bootstrap_unified` — all-in-one single process

## Flat files at root

`auth_seeding.py`, `bootstrap_stores.py`, `types.py` stay flat — they bridge multiple
subdirs and are imported by both `standalone/` and `factory/`.

## Rules

- All intra-bootstrap imports: full absolute paths (`lyra.bootstrap.<subdir>.<module>`).
- `cli.py` / `__main__.py` use lazy imports to avoid circular deps at module load time.
