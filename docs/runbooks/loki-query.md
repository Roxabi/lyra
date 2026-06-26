# Runbook — Loki log search (factory + voiceCLI)

Headless log engine per **ADR-092**. Promtail ships `operator.log` + filtered user journald; Loki stores 31 days on disk. The control-plane dashboard (#1760) will replace raw `logcli` later.

## Topology

| Unit | Role | Reachable |
|------|------|-----------|
| `factory-loki` | Log store | `http://127.0.0.1:3100` (host only) |
| `factory-promtail` | Scrape → push | internal (`roxabi.network`) |

Data: `~/.local/state/factory/loki/` (not Syncthing-synced).

## Health

```bash
systemctl --user is-active factory-loki factory-promtail
curl -sf http://127.0.0.1:3100/ready && echo ok
curl -sf http://127.0.0.1:3100/metrics | head
```

## Install logcli (one-time, host)

```bash
# Ubuntu — grab release binary or use distro package when available
curl -fsSL "https://github.com/grafana/loki/releases/download/v3.4.2/logcli-linux-amd64.zip" -o /tmp/logcli.zip
unzip -o /tmp/logcli.zip -d ~/.local/bin/
chmod +x ~/.local/bin/logcli
export LOKI_ADDR=http://127.0.0.1:3100
```

## Query recipes (LogQL)

### Operator deploy audit (JSONL)

```bash
export LOKI_ADDR=http://127.0.0.1:3100

# Last converge events
logcli query '{job="factory-operator"} | json | event=~"converge_.*"'

# Blobstore rotations
logcli query '{job="factory-operator"} | json | event=~"blobstore.*|rotation_log"'

# Secrets reset
logcli query '{job="factory-operator"} | json | event=~"secrets_reset.*"'
```

### Container runtime (journald)

```bash
# Hub errors last hour
logcli query '{job="factory-journal", systemd_unit="factory-hub.service"} |= "ERROR"' --since=1h

# Blobstore 401
logcli query '{job="factory-journal", systemd_unit="factory-blobstore.service"} |~ "(?i)401"'

# Deploy timer failures
logcli query '{job="factory-journal", syslog_id="factory-deploy-failure"}' --since=24h

# voiceCLI
logcli query '{job="factory-journal", systemd_unit=~"voicecli-.*"}' --since=1h
```

### Labels

| Label | Source |
|-------|--------|
| `job` | `factory-operator` / `factory-journal` |
| `channel` | `operator` / `journald` |
| `event`, `user`, `host` | operator.log JSON |
| `systemd_unit`, `syslog_id` | journald |

## Incident triage (with operator-log runbook)

| Question | Loki query |
|----------|------------|
| Did converge run? | `{job="factory-operator"} \| json \| event=~"converge_"` |
| Hub crash? | `{systemd_unit="factory-hub.service"} \|~ "Traceback\|ERROR"` |
| Timer deploy failed? | `{syslog_id="factory-deploy-failure"}` |
| Stale blobstore token era | `{systemd_unit=~"factory-blobstore\|voicecli-.*"} \|~ "(?i)401"` |

Field reference for grep-first triage: [operator-log.md](operator-log.md).

## Retention

Loki: **31 days** (`deploy/observability/loki-config.yml`). Operator.log on disk still rotates weekly (12 × 10M) independently.

## Future (#1760)

Control-plane dashboard composes Loki queries — operators stop using `logcli` directly. This runbook remains the bootstrap reference.