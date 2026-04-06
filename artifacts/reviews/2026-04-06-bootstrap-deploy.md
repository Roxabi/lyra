# Bootstrap + Deploy Review — 2026-04-06

## Verdict: WARN

Two issues found — one false comment (FAIL_FILE never auto-cleared), one minor lock ordering
concern. No blockers. Three-process mode is fully intact. Rollback is safe.

---

## Embedded NATS Auto-Start

**What replaced multibot:**

| Old | New |
|-----|-----|
| `_bootstrap_multibot` (`multibot.py`, deleted) | `_bootstrap_unified` (`unified.py`) |
| `multibot_stores.py` | renamed → `bootstrap_stores.py` |
| `multibot_wiring.py` | renamed → `bootstrap_wiring.py` |
| `multibot_lifecycle.py` | renamed → `bootstrap_lifecycle.py` |
| no embedded NATS | `embedded_nats.py` → `EmbeddedNats` + `ensure_nats()` |

**Entry point routing (`lyra start`):**

```
lyra start
  → cli.py:_run_server()
  → bootstrap/unified.py:_bootstrap_unified()
  → ensure_nats(os.environ.get("NATS_URL"))
      NATS_URL unset → EmbeddedNats.start() + wait_ready() → sets os.environ["NATS_URL"]
      NATS_URL set   → nats_connect() directly (external server)
  → _acquire_lockfile()
  → NatsBus wiring + adapter wiring + run_lifecycle()
```

**Who starts embedded NATS:** `_bootstrap_unified` only, via `ensure_nats()`.
`_bootstrap_hub_standalone` (`lyra hub`) requires `NATS_URL` and exits with a clear error if
absent — it never auto-starts embedded NATS. This is the correct separation.

**Three-process mode (`lyra hub` + `lyra adapter telegram` + `lyra adapter discord`):**
Still works — unchanged. `hub_standalone.py` + `adapter_standalone.py` are untouched in logic.
The renamed imports (`bootstrap_stores`, `bootstrap_wiring`) are updated in `hub_standalone.py`.

---

## NATS Shutdown Sequence

**Sequence in `unified.py` `finally` block:**

```
run_lifecycle() exits (SIGTERM / stop event)
  → nc.close()        (NATS client disconnects — no more publishes)
  → embedded.stop()   (terminate → wait 3s → kill if needed)
  → _release_lockfile()
```

**Assessment: correct ordering.**

- NATS client closes before embedded server stops → no "connection refused" errors on in-flight
  publishes during shutdown.
- `atexit.register(_kill_sync)` in `EmbeddedNats.start()` covers crash paths (SIGKILL, uncaught
  exception before `finally`).
- `_deregister_atexit()` called after clean `stop()` — no double-kill on normal exit.
- 3s `terminate` timeout before `kill` is reasonable for a local NATS server.

**Minor concern (WARN, low severity):** `_acquire_lockfile()` is called *after* `ensure_nats()`
succeeds but *before* the `try/finally`. If `_acquire_lockfile()` itself raises (unlikely — it
catches most errors internally and logs warnings), the lockfile atexit handler would not be
registered but the NATS connection would already be open and the embedded process running.
In practice `_acquire_lockfile()` does not raise (it logs + continues on stale-lock errors),
so this is theoretical. Still worth noting.

---

## Three-Process Mode Compatibility

**Verdict: intact.**

| Path | Status |
|------|--------|
| `lyra hub` → `_bootstrap_hub_standalone` | unchanged logic; renamed imports updated |
| `lyra adapter telegram` → `_bootstrap_adapter_standalone` | unchanged |
| `lyra adapter discord` → `_bootstrap_adapter_standalone` | unchanged |
| `lyra start` → `_bootstrap_unified` | new path, replaces deleted `_bootstrap_multibot` |

Supervisor programs (`lyra_hub`, `lyra_telegram`, `lyra_discord`) all use three-process mode
scripts (`run_hub.sh` → `lyra hub`, `run_adapter.sh` → `lyra adapter`). These scripts source
`.env` before exec, so `NATS_URL` from `.env` is available — embedded NATS is never triggered
in the three-process supervisor path.

---

## Port Conflicts

**Risk: low.**

- Embedded NATS binds `127.0.0.1:4222` (loopback only, `--no_auth`).
- Production uses external NATS (`NATS_URL` in `.env`) → embedded NATS never starts.
- Dev: if a standalone `nats-server` is already running on 4222, `wait_ready()` detects the
  early exit (`returncode != None`) and raises `RuntimeError` with the stderr (which will contain
  "address already in use"). The error message instructs to set `NATS_URL` instead.
- No risk of two embedded instances: `_acquire_lockfile()` prevents a second `lyra start`
  from running in the same directory (PID check on existing lockfile).

---

## Deploy SHA Cache Fix

**What the loop was:**

```
cron trigger deploy.sh
  → git fetch → LYRA_LOCAL != LYRA_REMOTE
  → git pull → uv sync → pytest FAILS
  → git reset --hard LYRA_LOCAL
  → uv sync (restore)
  → exit 1
  (next cron tick: repeat from top — same broken SHA every 5 min)
```

**What the fix does:**

```
on test failure:
  echo "$LYRA_REMOTE" >> $FAIL_FILE
  git reset --hard + exit 1

on subsequent ticks (same SHA):
  grep -Fxq "$LYRA_REMOTE" "$FAIL_FILE" → true → skip silently
```

**Is the fix complete?**

Functionally yes — the loop is broken. One issue found:

### ISSUE: FAIL_FILE grows unbounded / never auto-cleared

**File:** `scripts/deploy.sh:36`  
**Comment says:** "Cleared automatically when staging moves forward to a new SHA."  
**Reality:** No code clears the file. When staging advances, the new SHA is simply not in
`FAIL_FILE`, so the new deploy proceeds normally. Old failed SHAs accumulate forever.

This is not a correctness bug (old SHAs never match `$LYRA_REMOTE` once staging moves forward),
but it is a maintenance problem: the file grows indefinitely, and the comment is misleading.

**Fix (one line, after `echo "$LYRA_REMOTE" >> "$FAIL_FILE"`):**
```bash
# Keep only the last 20 failed SHAs (prevents unbounded growth)
tail -20 "$FAIL_FILE" > "${FAIL_FILE}.tmp" && mv "${FAIL_FILE}.tmp" "$FAIL_FILE"
```
Or, since only the current remote SHA is ever checked: truncate and keep only the latest entry.
Either approach matches what the comment implies.

**voiceCLI path:** voiceCLI failures are not protected by the SHA cache — voiceCLI pulls always
re-attempt. This is acceptable since voiceCLI has no test gate in `deploy.sh` (no `pytest`
call for it).

---

## Supervisor Config

**Configs reviewed:** `lyra_hub.conf`, `lyra_telegram.conf`, `lyra_discord.conf`

| Config | Command | Priority | autorestart | startsecs |
|--------|---------|----------|-------------|-----------|
| `lyra_hub` | `supervisor/scripts/run_hub.sh` | 100 | true | 10 |
| `lyra_telegram` | `supervisor/scripts/run_adapter.sh telegram` | 200 | true | 5 |
| `lyra_discord` | `supervisor/scripts/run_adapter.sh discord` | 200 | true | 5 |

**Status: up to date for three-process mode.** No changes were needed — these configs already
used `lyra hub` / `lyra adapter` via the run scripts, and both entry points are unaffected
by the unified bootstrap addition.

**Script path note:** Configs reference `supervisor/scripts/` (not `deploy/supervisor/scripts/`).
Scripts exist at `supervisor/scripts/` — confirmed correct.

**No embedded NATS supervisor program exists or is needed** — embedded NATS is only used by
`lyra start` (development/single-machine path), which is not managed by supervisor.

---

## Rollback Safety

**Verdict: safe.**

| Rollback scenario | Safety |
|-------------------|--------|
| `git reset --hard` to pre-746b4af | `multibot.py` comes back; `unified.py` + `embedded_nats.py` deleted. `lyra start` falls back to old `_main()` path which calls `_bootstrap_multibot`. Supervisor (three-process) is unaffected. |
| `git reset --hard` to pre-817fd9e | `FAIL_FILE` logic removed; deploy returns to original loop behavior. Acceptable. |
| Rollback mid-deploy (deploy.sh already did `git pull`) | Script does `git reset --hard $LYRA_LOCAL` on test failure — already baked in. |

**Caveat:** Rolling back after rename (`bootstrap_stores.py`, `bootstrap_wiring.py`) restores
`multibot_stores.py` + `multibot_wiring.py` since the commit renames the files. Git handles this
correctly as a rename, not a delete-and-add. `hub_standalone.py` imports the old names after
rollback — consistent.

---

## Issues

| # | File | Line | Severity | Description |
|---|------|------|----------|-------------|
| 1 | `scripts/deploy.sh` | 36 | WARN | Comment "Cleared automatically" is false — `FAIL_FILE` never truncated; grows unbounded |
| 2 | `src/lyra/bootstrap/unified.py` | 56–57 | INFO | `_acquire_lockfile()` called outside `try/finally`; theoretical resource leak if it raises |

---

## Actions

| Priority | Action | Owner |
|----------|--------|-------|
| Should | Fix `FAIL_FILE` comment and add truncation (e.g. `tail -20`) after append | DevOps |
| Low | Move `_acquire_lockfile()` inside the `try` block or add explicit guard | Backend |
