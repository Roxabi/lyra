# Lyra tenant confs

These three `*.conf` files are lyra's tenant contributions to the machine-level supervisord at `~/projects/supervisord.conf`. Lyra is a tenant on the machine host — it does NOT ship its own supervisor host.

## On each host

Copy the confs into the machine-level conf.d and reload:

    cp deploy/conf.d/*.conf ~/projects/conf.d/
    ~/projects/scripts/supervisorctl.sh reread
    ~/projects/scripts/supervisorctl.sh update

## Files

| Conf | Program | Command |
|------|---------|---------|
| `lyra-hub.conf` | `lyra-hub` | `deploy/scripts/run_hub.sh` |
| `lyra-telegram.conf` | `lyra-telegram` | `deploy/scripts/run_adapter.sh telegram` |
| `lyra-discord.conf` | `lyra-discord` | `deploy/scripts/run_adapter.sh discord` |

## Launcher scripts

The `command=` lines reference `deploy/scripts/run_hub.sh` and `deploy/scripts/run_adapter.sh`. These wrappers source `.env` before exec, with supervisor-set env vars taking precedence (see inline comments in each script).

## References

- ADR-041 — Supervisor pattern (superseded by ADR-047 for host layout)
- ADR-047 — Project layering / machine-level supervisord at `~/projects/supervisord.conf`
- `docs/architecture/target-architecture.md` — Tenant/host separation
