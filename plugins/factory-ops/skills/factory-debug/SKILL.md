---
name: factory-debug
description: 'Debug factory on production — check status, pull logs, diagnose root cause, suggest fix. Triggers: "debug factory" | "factory debug" | "check factory" | "factory status" | "factory down" | "why is factory not responding".'
version: 0.2.0
allowed-tools: Bash, Read, Glob, Grep
---

# factory Debug

Diagnose factory issues on production (roxabituwer). Runs from the **local** machine
(`~/projects/roxabi-factory`) — all production access is via `make remote` and SSH.

Production runs **Podman Quadlet** (rootless, systemd --user units).

Let:
  H      := DEPLOY_HOST (from `~/projects/roxabi-factory/.env`)
  units  := enabled factory-* service units — SSoT `deploy/quadlet.toml` (¬hardcode; derived in Phase 1)
  Σ      := severity (🔴 down | 🟡 degraded | 🟢 healthy)
  pat    := known error patterns (see §Known Patterns)

## Known Patterns

| Pattern | Root Cause | Fix |
|---------|-----------|-----|
| `OperationalError: database is locked` | Hub + adapter race on same SQLite DB at startup | Stagger restarts or add busy_timeout |
| `suspiciously fast.*dead backend` | Claude CLI pool not responding | Restart factory-hub |
| `backend is dead — skipping guard` | Stale session with dead CLI process | Restart factory-hub |
| `dead_backend_hits > 0` in /health/detail | Backend silently failing (fast empty returns) | Restart factory-hub (counter resets on restart) |
| `start-limit-hit` / unit in `failed` | systemd gave up after 5 restarts in 60s | Inspect journal, fix root cause, `systemctl --user reset-failed` |
| `CancelledError` in starlette | Normal shutdown noise — not a root cause | Ignore unless paired with other errors |
| `Rate limit` / `429` | Anthropic API rate limit | Wait or check API key quota |
| `NATS.*connection` / `no servers available` | Hub ↔ adapter NATS transport broken or factory-nats.service down | Restart `factory-nats` first, then `factory-hub`, then adapters |
| `IsADirectoryError.*config.toml` | Quadlet bind-mount downgrade (see commit c9187fb) | Ensure inline `Volume=%h/.roxabi/factory/config.toml:/app/config.toml:ro,z` |
| `permission denied.*\.lyra` | UserNS mapping mismatch (ADR-054) | Verify `UserNS=keep-id:uid=1500,gid=1500` in container unit |

## Phase 1 — Status

```bash
cd ~/projects/roxabi-factory && make remote status
```

Also inspect containers + nats directly. Derive the live unit set from the deploy
manifest — never hardcode it (prod runs the full `[component.*]` roster, not a fixed 4):

```bash
# Enabled factory-* units from the SSoT (skips disabled=true, e.g. Langfuse/otel-collector).
UNITS=$(python3 -c 'import tomllib,pathlib; d=tomllib.loads(pathlib.Path("deploy/quadlet.toml").read_text()); print(" ".join(sorted(v["container"].removesuffix(".container") for v in d["component"].values() if not v.get("disabled") and v["container"].startswith("factory-"))))')
ssh $H "podman ps -a --format '{{.Names}}\t{{.Status}}\t{{.Image}}' | grep -E 'factory-|nats'"
ssh $H "systemctl --user status $UNITS --no-pager"
```

∀ unit ∈ units: record state (active/running + uptime | failed | inactive).
All active → Σ := 🟢; ∃ failed → Σ := 🔴; else 🟡.

## Phase 2 — Health Endpoint

Health is published on host loopback at `127.0.0.1:8443` (see `deploy/quadlet/factory-hub.container` `PublishPort`):

```bash
ssh $H "curl -s -H 'Authorization: Bearer $(cat ~/.roxabi/factory/secrets/health_secret)' http://localhost:8443/health/detail"
```

Parse JSON. Key fields:
- `dead_backend_hits` > 0 → 🔴 backend silently failing
- `queue_size` > 10 → 🟡 queue backing up
- `circuits` with non-closed state → 🔴 circuit breaker tripped
- `reaper_alive` = false → 🟡 CLI pool reaper dead
- `reaper_last_sweep_age` > 120 → 🟡 reaper stalled

If curl fails: hub container is either not running, crash-looping before bind,
or `FACTORY_HEALTH_HOST` wasn't set to `0.0.0.0` inside the container (see commits c3e3d03/b2fc3bc).

## Phase 3 — Logs

Quadlet logs go to the user journal (stdout/stderr captured by systemd).
Pull last 200 lines per unit — run in parallel:

```bash
ssh $H "journalctl --user -u factory-hub -n 200 --no-pager"
ssh $H "journalctl --user -u factory-hub -n 200 -p err --no-pager"
ssh $H "journalctl --user -u factory-telegram -n 200 --no-pager"
ssh $H "journalctl --user -u factory-discord -n 200 --no-pager"
ssh $H "journalctl --user -u factory-nats -n 100 --no-pager"
```

Equivalent via Makefile (foreground tail): `make remote hub logs` / `telegram logs` / `discord logs` / `hub errors`.

Operator deploy audit (host — not inside containers):

```bash
ssh $H "tail -50 ~/.local/state/factory/logs/operator.log"
ssh $H "cat ~/.roxabi/factory/rotation-log.md 2>/dev/null || true"
```

See `docs/runbooks/operator-log.md` for the full triage matrix.

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

Present fix options from the table below and wait for user reply.

| Fix | Command | When |
|-----|---------|------|
| Restart hub only | `make remote hub reload` | Dead backend, stale CLI pool |
| Restart all factory | `make remote reload` | DB locked, NATS broken |
| Restart specific adapter | `make remote discord reload` / `make remote telegram reload` | Single adapter failed |
| Restart NATS | `ssh $H "systemctl --user restart factory-nats"` | NATS connection errors |
| Clear failed state | `ssh $H "systemctl --user reset-failed factory-hub"` | Unit stuck in `failed` after start-limit-hit |
| Check DB locks | `ssh $H "podman exec factory-hub fuser /home/factory/.roxabi/factory/*.db"` | Persistent DB locked errors |
| Reinstall Quadlet units | `make quadlet-install` then `ssh $H "systemctl --user daemon-reload"` | Unit file drift |
| Converge to declared state | `ssh $H "cd ~/projects/roxabi-factory && make converge"` — or wait ≤5 min for the `factory-quadlet-sync` / `factory-post-autoupdate` timers | Unit / config / ACL drift from `staging`; re-reconcile the running system to the declared state |
| Ship a code/image fix | Land via `/dev` → merge to `staging`. CI (`publish.yml`) builds + pushes `ghcr.io/roxabi/factory:staging`; `podman-auto-update` + `factory-post-autoupdate` pull by **digest** and converge automatically (~5 min) | Code- or image-level fix needed on production |

> `make deploy` / `make build && make push` are **not** remediation paths: `make deploy` is retired (#1930 — prints ERROR, exit 1) and local `build`/`push` bypass the CI → GHCR → digest pipeline. `make converge` runs **on M₁** (the production host), never from the dev machine.

After user picks a fix, execute it and re-run Phase 1 + Phase 2 to confirm recovery.
Verify `dead_backend_hits` is 0 after restart.

## Phase 6 — Post-mortem (if recurring)

If the same pattern was seen before (check conversation context or memory),
flag it as recurring and suggest a code-level fix:

- DB locking → add `busy_timeout` pragma or serialize startup
- Dead backend → CLI pool health-check + auto-restart
- Crash loops → check recent deploys (`ssh $H "cd $DEPLOY_DIR && git log --oneline -5"`) and last image (`ssh $H "podman images localhost/factory --format '{{.Created}}\t{{.ID}}'"`)
- start-limit-hit → tune `StartLimitIntervalSec` / `StartLimitBurst` in the .container unit, or fix the underlying crash

$ARGUMENTS
