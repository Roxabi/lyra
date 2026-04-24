---
name: lyra-debug
description: 'Debug Lyra on production — check status, pull logs, diagnose root cause, suggest fix. Triggers: "debug lyra" | "lyra debug" | "check lyra" | "lyra status" | "lyra down" | "why is lyra not responding".'
version: 0.2.0
allowed-tools: Bash, Read, Glob, Grep
---

# Lyra Debug

Diagnose Lyra issues on production (roxabituwer). Runs from the **local** machine
(`~/projects/lyra`) — all production access is via `make remote` and SSH.

Production runs **Podman Quadlet** (rootless, systemd --user units). Supervisord
fallback still supported if `LYRA_SUPERVISORCTL_PATH` is set in remote `.env`
(legacy hosts only) — `make remote` auto-branches.

Let:
  H      := DEPLOY_HOST (from `~/projects/lyra/.env`)
  units  := {lyra-hub, lyra-telegram, lyra-discord, nats}
  mon    := lyra-monitor.timer + lyra-monitor.service (systemd --user)
  Σ      := severity (🔴 down | 🟡 degraded | 🟢 healthy)
  pat    := known error patterns (see §Known Patterns)

## Known Patterns

| Pattern | Root Cause | Fix |
|---------|-----------|-----|
| `OperationalError: database is locked` | Hub + adapter race on same SQLite DB at startup | Stagger restarts or add busy_timeout |
| `suspiciously fast.*dead backend` | Claude CLI pool not responding | Restart lyra-hub |
| `backend is dead — skipping guard` | Stale session with dead CLI process | Restart lyra-hub |
| `dead_backend_hits > 0` in /health/detail | Backend silently failing (fast empty returns) | Restart lyra-hub (counter resets on restart) |
| `start-limit-hit` / unit in `failed` | systemd gave up after 5 restarts in 60s | Inspect journal, fix root cause, `systemctl --user reset-failed` |
| `CancelledError` in starlette | Normal shutdown noise — not a root cause | Ignore unless paired with other errors |
| `Rate limit` / `429` | Anthropic API rate limit | Wait or check API key quota |
| `NATS.*connection` / `no servers available` | Hub ↔ adapter NATS transport broken or nats.service down | Restart `nats` first, then `lyra-hub`, then adapters |
| `IsADirectoryError.*config.toml` | Quadlet bind-mount downgrade (see commit c9187fb) | Ensure inline `Volume=%h/.lyra/config.toml:/app/config.toml:ro,z` |
| `permission denied.*\.lyra` | UserNS mapping mismatch (ADR-054) | Verify `UserNS=keep-id:uid=1500,gid=1500` in container unit |

## Phase 1 — Status

```bash
cd ~/projects/lyra && make remote status
```

Also inspect containers + nats directly:

```bash
ssh $H "podman ps -a --format '{{.Names}}\t{{.Status}}\t{{.Image}}' | grep -E 'lyra-|nats'"
ssh $H "systemctl --user status lyra-hub lyra-telegram lyra-discord nats --no-pager"
```

∀ unit ∈ units: record state (active/running + uptime | failed | inactive).
All active → Σ := 🟢; ∃ failed → Σ := 🔴; else 🟡.

## Phase 2 — Health Endpoint

Health is published on host loopback at `127.0.0.1:8443` (see `deploy/quadlet/lyra-hub.container` `PublishPort`):

```bash
ssh $H "curl -s -H 'Authorization: Bearer $(cat ~/.lyra/secrets/health_secret)' http://localhost:8443/health/detail"
```

Parse JSON. Key fields:
- `dead_backend_hits` > 0 → 🔴 backend silently failing
- `queue_size` > 10 → 🟡 queue backing up
- `circuits` with non-closed state → 🔴 circuit breaker tripped
- `reaper_alive` = false → 🟡 CLI pool reaper dead
- `reaper_last_sweep_age` > 120 → 🟡 reaper stalled

If curl fails: hub container is either not running, crash-looping before bind,
or `LYRA_HEALTH_HOST` wasn't set to `0.0.0.0` inside the container (see commits c3e3d03/b2fc3bc).

## Phase 3 — Logs

Quadlet logs go to the user journal (stdout/stderr captured by systemd).
Pull last 200 lines per unit — run in parallel:

```bash
ssh $H "journalctl --user -u lyra-hub -n 200 --no-pager"
ssh $H "journalctl --user -u lyra-hub -n 200 -p err --no-pager"
ssh $H "journalctl --user -u lyra-telegram -n 200 --no-pager"
ssh $H "journalctl --user -u lyra-discord -n 200 --no-pager"
ssh $H "journalctl --user -u nats -n 100 --no-pager"
```

Equivalent via Makefile (foreground tail): `make remote hub logs` / `telegram logs` / `discord logs` / `hub errors`.

Monitor timer (health probe every N minutes):

```bash
ssh $H "systemctl --user list-timers lyra-monitor.timer"
ssh $H "journalctl --user -u lyra-monitor.service -n 50 --no-pager"
```

In-container structured logs (if the hub writes files to the logs volume):

```bash
ssh $H "podman exec lyra-hub ls -t /home/lyra/.local/state/lyra/logs/ | head -10"
ssh $H "podman exec lyra-hub tail -200 /home/lyra/.local/state/lyra/logs/<file>"
```

Legacy hosts (supervisord): logs still in `~/.local/state/lyra/logs/*.log`.

## Phase 4 — Diagnosis

1. Match log content against §Known Patterns table.
2. Build a causal chain: what failed first → what cascaded.
3. Check for **timing correlation** (timestamps across units — journal ts are monotonic per host).
4. Cross-reference health endpoint data (dead_backend_hits, circuits, reaper).
5. If unit is `failed`, check `systemctl --user status <unit>` for exit code + recent invocations.
6. If thread ID provided in user message, grep for it in journalctl output.

Present diagnosis as:

```
## Diagnosis

**Severity:** {Σ}
**Root cause:** {one-line summary}
**Causal chain:**
1. {first event + timestamp}
2. {cascade effect}
3. {current state}

**Evidence:**
- {log line 1}
- {log line 2}

**Health endpoint:**
- dead_backend_hits: {N}
- circuits: {state}

**Affected:** {which units / threads / users}
```

## Phase 5 — Remediation

Present fix options via DP(A) (load `${CLAUDE_PLUGIN_ROOT}/../shared/references/decision-presentation.md`). Common fixes:

| Fix | Command | When |
|-----|---------|------|
| Restart hub only | `make remote hub reload` | Dead backend, stale CLI pool |
| Restart all Lyra | `make remote lyra reload` | DB locked, NATS broken |
| Restart specific adapter | `make remote discord reload` / `make remote telegram reload` | Single adapter failed |
| Restart NATS | `ssh $H "systemctl --user restart nats"` | NATS connection errors |
| Clear failed state | `ssh $H "systemctl --user reset-failed lyra-hub"` | Unit stuck in `failed` after start-limit-hit |
| Check DB locks | `ssh $H "podman exec lyra-hub fuser /home/lyra/.lyra/*.db"` | Persistent DB locked errors |
| Reinstall Quadlet units | `make quadlet-install` then `ssh $H "systemctl --user daemon-reload"` | Unit file drift |
| Full deploy | `make deploy` | Code fix needed on production |
| Rebuild + push image | `make build && make push && make remote lyra reload` | Image-level fix needed |

After user picks a fix, execute it and re-run Phase 1 + Phase 2 to confirm recovery.
Verify `dead_backend_hits` is 0 after restart.

## Phase 6 — Post-mortem (if recurring)

If the same pattern was seen before (check conversation context or memory),
flag it as recurring and suggest a code-level fix:

- DB locking → add `busy_timeout` pragma or serialize startup
- Dead backend → CLI pool health-check + auto-restart
- Crash loops → check recent deploys (`ssh $H "cd $DEPLOY_DIR && git log --oneline -5"`) and last image (`ssh $H "podman images localhost/lyra --format '{{.Created}}\t{{.ID}}'"`)
- start-limit-hit → tune `StartLimitIntervalSec` / `StartLimitBurst` in the .container unit, or fix the underlying crash

$ARGUMENTS
