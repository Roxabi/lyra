# Runbook — Operator & deploy audit logs

Where factory records **who ran what** on the production host, separate from container runtime logs (journald).

## Storage map

| Store | Path | Format | Scope |
|-------|------|--------|-------|
| **Operator audit** | `~/.local/state/factory/logs/operator.log` | JSONL (one object/line) | Shell deploy actions (`install.sh`, `make converge`) |
| **Credential rotations** | `~/.roxabi/factory/rotation-log.md` | Markdown append | Voluntary secret changes (blobstore regen, future nkey rotations) |
| **Container runtime** | journald `--user` | plaintext | Quadlet stdout/stderr — see [DEPLOYMENT.md](../DEPLOYMENT.md) |
| **Deploy timers** | journald `--user` | plaintext | `factory-quadlet-sync`, `factory-post-autoupdate` |
| **Domain audit** | JetStream `FACTORY_AUDIT` on disk | NATS messages | `factory.audit.blobs.*`, `factory.audit.security.*` |
| **Conversation audit** | `~/.roxabi/factory/turns.db` | SQLite | Message content — not an ops log |

`~/.local/state/factory/logs/` holds **operator.log only**. It is not mounted into containers and is not written by application `logging_setup` (apps log to stdout → journald).

## Incident triage — which log?

| Symptom / question | First place to look |
|--------------------|---------------------|
| Hub / adapter crash, 401 blobstore, NATS errors | `journalctl --user -u factory-<unit> -n 200` |
| Timer deploy ran? failed? | `journalctl --user -u factory-quadlet-sync` or `factory-post-autoupdate` |
| Deploy failure notification | `journalctl --user -t factory-deploy-failure` |
| Did converge run / skip? | `grep '"event":"converge_' ~/.local/state/factory/logs/operator.log \| tail` |
| Who regenerated `blobstore.tok`? | `~/.roxabi/factory/rotation-log.md` + `grep blobstore ~/.local/state/factory/logs/operator.log` |
| Audit trail for blob PUT/GET | `nats stream view FACTORY_AUDIT` or hub logs `factory.audit.blobs` |
| Replay a conversation | `turns.db` — not Loki/journald |

## Query recipes

### Operator log (JSONL)

```bash
# Last 20 operator events
tail -20 ~/.local/state/factory/logs/operator.log

# Converge history
grep '"event":"converge_' ~/.local/state/factory/logs/operator.log | tail -20

# install.sh runs
grep '"event":"install_' ~/.local/state/factory/logs/operator.log | tail -20

# With jq (optional)
grep '"event":"blobstore' ~/.local/state/factory/logs/operator.log | jq -r '[.ts,.event,.reason] | @tsv'
```

### journald (containers + timers)

```bash
journalctl --user -u factory-hub -n 100 --no-pager
journalctl --user -u factory-quadlet-sync --since "1 hour ago" --no-pager
make factory logs          # on host: tail factory-hub
make remote hub logs       # from laptop via SSH
```

### Rotation log

```bash
cat ~/.roxabi/factory/rotation-log.md
```

## What gets logged automatically

| Action | operator.log event | rotation-log.md |
|--------|-------------------|-----------------|
| `deploy/install.sh` start/end | `install_start`, `install_done` | — |
| `blobstore.tok` missing on install | `blobstore_regen` | yes (`missing-file`) |
| `--force-regen-blobstore` | `blobstore_regen` | yes (`operator-request`) |
| `blobstore.tok` skipped | `blobstore_skip` | — |
| `make converge` skip (stamp match) | `converge_skip` | — |
| `make converge` run | `converge_start`, `converge_complete` | — |
| Concurrent converge (flock) | `converge_lock_held` | — |
| `factory secrets reset` | `secrets_reset_start`, `secrets_reset_complete` / `secrets_reset_failed` | yes (`nats-nkeys`, `disaster-recovery`) |
| `factory secrets reset --dry-run` | `secrets_reset_dry_run` | — |

**Never logged:** token bytes, seed contents, env values, full argv with secrets.

## Manual operations

Operations outside instrumented scripts (raw `podman secret create`, `printf > blobstore.tok`, SSH one-liners) are **not** captured. Prefer:

- `deploy/install.sh --force-secrets --secrets-only`
- `deploy/install.sh --force-regen-blobstore --secrets-only` (blobstore only)
- `factory secrets reset` (NATS nkeys — does not touch `blobstore.tok`)

After a manual blobstore rotation, append to `rotation-log.md` if the script path was bypassed.

## Retention

- `operator.log`: `factory-operator-logrotate.timer` (weekly Sunday 04:30) runs `deploy/factory-operator-logrotate.sh` — `rotate 12`, `maxsize 10M` (~90 days). Installed by `make quadlet-sync-install`. Manual check: `systemctl --user list-timers factory-operator-logrotate.timer`.
- `rotation-log.md`: keep indefinitely (small, Syncthing-synced).
- journald: host journald retention policy.
- JetStream `FACTORY_AUDIT`: 90 days / 1 GiB (stream config).

Decision record: [ADR-093](../architecture/adr/093-operator-audit-three-channel.mdx).

## Loki (shipped)

Promtail ships `operator.log` + filtered journald to **Loki** (`factory-loki` on `127.0.0.1:3100`). Query recipes: [loki-query.md](loki-query.md). Control-plane dashboard (#1760) will compose Loki — this runbook stays the grep-first field reference.