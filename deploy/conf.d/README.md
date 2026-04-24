# Lyra tenant confs

These three `*.conf` files are lyra's tenant contributions to the machine-level supervisord at `~/projects/supervisord.conf`. Lyra is a tenant on the machine host — it does NOT ship its own supervisor host.

## Registering on a host

Canonical path — run from the lyra repo root:

```bash
cd ~/projects/lyra
make register          # symlinks deploy/conf.d/*.conf → ~/projects/conf.d/
```

`make register` creates symlinks so the machine host always picks up conf edits after a `git pull` without an extra copy step.

Fallback (hosts without the hub-mk setup, e.g. bootstrap) — explicit copy from the repo root:

```bash
cd ~/projects/lyra
cp deploy/conf.d/*.conf ~/projects/conf.d/
~/projects/scripts/supervisorctl.sh reread
~/projects/scripts/supervisorctl.sh update
```

A `cp` copy is a static snapshot; re-copy after each `git pull` to stay in sync.

## Files

| Conf | Program | Command |
|------|---------|---------|
| `lyra-hub.conf` | `lyra-hub` | `deploy/scripts/run_hub.sh` |
| `lyra-telegram.conf` | `lyra-telegram` | `deploy/scripts/run_adapter.sh telegram` |
| `lyra-discord.conf` | `lyra-discord` | `deploy/scripts/run_adapter.sh discord` |

## Launcher scripts

The `command=` lines reference `deploy/scripts/run_hub.sh` and `deploy/scripts/run_adapter.sh`. These wrappers source `.env` before `exec`, with supervisor-set env vars taking precedence (see inline comments in each script).

## References

- ADR-041 — Supervisor pattern (superseded by ADR-047 for host layout)
- ADR-047 — Project layering / machine-level supervisord at `~/projects/supervisord.conf`
- `lyra-nats-truth §14` — Cross-project subscribers / tenant-host separation (design doc at `~/.roxabi/lyra-nats-truth/14-cross-project-subscribers.md`, not checked into this repo)
