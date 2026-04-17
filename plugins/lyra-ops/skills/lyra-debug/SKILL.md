---
name: lyra-debug
description: 'Debug Lyra on production — check status, pull logs, diagnose root cause, suggest fix. Triggers: "debug lyra" | "lyra debug" | "check lyra" | "lyra status" | "lyra down" | "why is lyra not responding".'
version: 0.1.0
allowed-tools: Bash, Read, Glob, Grep, ToolSearch, AskUserQuestion
---

# Lyra Debug

Diagnose Lyra issues on production (roxabituwer). Runs from the **local** machine
(`~/projects/lyra`) — all production access is via `make remote` and SSH.

Let:
  H := DEPLOY_HOST (from `~/projects/lyra/.env`)
  L := `~/.local/state/lyra/logs/` (remote log dir)
  procs := {lyra_hub, lyra_telegram, lyra_discord, voicecli_tts, voicecli_stt}
  Σ := severity (🔴 down | 🟡 degraded | 🟢 healthy)
  pat := known error patterns (see §Known Patterns)

## Known Patterns

| Pattern | Root Cause | Fix |
|---------|-----------|-----|
| `OperationalError: database is locked` | Hub + adapter race on same SQLite DB at startup | Stagger restarts or add busy_timeout |
| `suspiciously fast.*dead backend` | Claude CLI pool not responding | Restart lyra_hub |
| `backend is dead — skipping guard` | Stale session with dead CLI process | Restart lyra_hub |
| `dead_backend_hits > 0` in /health/detail | Backend silently failing (fast empty returns) | Restart lyra_hub (counter resets on restart) |
| `FATAL.*Exited too quickly` | Crash loop — process dies before startsecs | Check stderr for import/config errors |
| `CancelledError` in starlette | Normal shutdown noise — not a root cause | Ignore unless paired with other errors |
| `Rate limit` / `429` | Anthropic API rate limit | Wait or check API key quota |
| `NATS.*connection` | Hub ↔ adapter NATS transport broken | Restart hub first, then adapters |

## Phase 1 — Status

```bash
cd ~/projects/lyra && make remote status
```

∀ proc ∈ procs: record state (RUNNING + uptime | FATAL | STOPPED).
If all RUNNING → Σ_status := 🟢; ∃ FATAL → Σ_status := 🔴; else 🟡.

## Phase 2 — Health Endpoint

```bash
cd ~/projects/lyra && ssh $H "curl -s -H 'Authorization: Bearer $(cat ~/.lyra/secrets/health_secret)' http://localhost:8443/health/detail"
```

Parse JSON. Key fields:
- `dead_backend_hits` > 0 → 🔴 backend silently failing
- `queue_size` > 10 → 🟡 queue backing up
- `circuits` with non-closed state → 🔴 circuit breaker tripped
- `reaper_alive` = false → 🟡 CLI pool reaper dead
- `reaper_last_sweep_age` > 120 → 🟡 reaper stalled

## Phase 3 — Logs

Pull last 200 lines of each log file for Lyra processes (hub, telegram, discord).
Run in parallel:

```bash
ssh H "tail -200 ${L}lyra_hub.log"
ssh H "tail -200 ${L}lyra_hub_error.log"
ssh H "tail -200 ${L}lyra_discord.log"
ssh H "tail -200 ${L}lyra_discord_error.log"
ssh H "tail -200 ${L}lyra_telegram.log"
ssh H "tail -200 ${L}lyra_telegram_error.log"
```

Also check per-session structured logs if present:

```bash
ssh H "ls -t ${L}*.log | head -5"
```

## Phase 4 — Diagnosis

1. Match log content against §Known Patterns table.
2. Build a causal chain: what failed first → what cascaded.
3. Check for **timing correlation** (timestamps across processes).
4. Cross-reference health endpoint data (dead_backend_hits, circuits, reaper).
5. If thread ID provided in user message, grep for it in logs to trace the exact failure path.

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

**Affected:** {which processes/threads/users}
```

## Phase 5 — Remediation

Present fix options via AskUserQuestion. Common fixes:

| Fix | Command | When |
|-----|---------|------|
| Restart hub only | `make remote hub reload` | Dead backend, stale CLI pool |
| Restart all Lyra | `make remote lyra reload` | DB locked, NATS broken |
| Restart specific adapter | `make remote discord reload` | Single adapter FATAL |
| Check DB locks | `ssh H "fuser ~/.lyra/*.db"` | Persistent DB locked errors |
| Full deploy | `make deploy` | Code fix needed on production |

After user picks a fix, execute it and re-run Phase 1 + Phase 2 to confirm recovery.
Verify `dead_backend_hits` is 0 after restart.

## Phase 6 — Post-mortem (if recurring)

If the same pattern was seen before (check conversation context or memory),
flag it as recurring and suggest a code-level fix:

- DB locking → add `busy_timeout` pragma or serialize startup
- Dead backend → add CLI pool health-check + auto-restart
- Crash loops → check recent deploys (`ssh H "cd ~/projects/lyra && git log --oneline -5"`)

$ARGUMENTS
