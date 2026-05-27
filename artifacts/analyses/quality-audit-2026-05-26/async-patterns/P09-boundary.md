### Summary
- One High-severity async bug: a timed-out subprocess in `monitoring/escalation.py` is not killed, becoming a zombie process.
- Four Medium-severity issues: a race in blobstore lazy-provision, and three un-offloaded sync filesystem I/O calls inside async boundary functions.
- Six Low-severity items: httpx client churn (3 locations), an `os.chmod` block, and aiosqlite cursor leaks (2 locations).

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `monitoring/escalation.py` | 88 | High | `asyncio.wait_for(proc.communicate(), timeout=30)` on `claude` CLI subprocess has no `TimeoutError` handler; the process becomes a zombie on expiry. | Wrap in `try/except asyncio.TimeoutError: proc.kill(); await proc.wait(); raise`. |
| `blobstore/serve.py` | 95–101 | Medium | `_maybe_provision` has no concurrency guard; concurrent `healthz`/`metrics` requests can race through `_provision_nats`, causing duplicate KV `create_key_value` attempts and `audit_sink` overwrite. | Add an `asyncio.Lock` in `_maybe_provision` or set `nats_provisioned = True` before yielding to async work. |
| `tools/gh_token/daemon.py` | 127–129 | Medium | Sync blocking filesystem calls (`path.mkdir`, `path.unlink`) inside async `run_daemon` without offloading. | Use `await asyncio.to_thread(...)` for filesystem mutations. |
| `tools/gh_token/helper.py` | 146, 168–178 | Medium | `TokenCache.read`/`write` perform sync `read_text`/`write_text`/`chmod`/`os.replace` inside methods called from async `_resolve_token` (under `asyncio.Lock`), blocking the event loop. | Convert to async methods and offload I/O via `asyncio.to_thread`. |
| `monitoring/checks_varz.py` | 46–48, 82–83 | Medium | Sync `open()` read and write of delta-tracking state file inside async `check_nats_varz` without offloading. | Use `await asyncio.to_thread(...)` for `json.load`/`json.dump` operations. |
| `tools/gh_token/dispenser.py` | 94 | Low | `os.chmod(sock_path, 0o660)` is a sync blocking call inside async `serve()`. | Use `await asyncio.to_thread(os.chmod, ...)`. |
| `monitoring/checks.py` | 67 | Low | `httpx.AsyncClient` instantiated per health-check run, causing repeated TCP connect/teardown churn. | Inject or share a persistent client across monitoring runs. |
| `monitoring/escalation.py` | 176 | Low | `httpx.AsyncClient` instantiated per Telegram alert. | Share a persistent client or accept it as a dependency. |
| `monitoring/checks_varz.py` | 53 | Low | `httpx.AsyncClient` instantiated per NATS `/varz` poll. | Share a persistent client. |
| `blobstore/_handlers.py` | 173–188 | Low | aiosqlite cursors opened without `async with` context manager; exceptions before `await cursor.close()` leave cursors unclosed until connection teardown. | Use `async with await conn.execute(...) as cursor:` for all cursors. |
| `blobstore/_handlers.py` | 233–265 | Low | Same cursor leak pattern in `handle_delete` across multiple `conn.execute` blocks. | Use `async with` cursor context managers consistently. |

### Metrics
- **Files analyzed:** 28 across `integrations/`, `tools/`, `monitoring/`, `obs/`, `blobstore/`
- **Files with findings:** 8
- **Total findings:** 11
  - High: 1 (9%)
  - Medium: 4 (36%)
  - Low: 6 (55%)
- **Blocking calls in async context:** 4 locations (filesystem I/O)
- **Connection churn (httpx):** 3 locations
- **Subprocess timeout leaks:** 1
- **Race conditions:** 1
- **Resource leaks (cursors):** 2

### Recommendations

1. **Kill zombie subprocess on timeout** — `monitoring/escalation.py` `_escalate_via_cli` must mirror the `proc.kill(); await proc.wait()` pattern used in `daemon.py` and `integrations/*.py`.
2. **Serialize lazy NATS provisioning** — Add an `asyncio.Lock` to `_maybe_provision` in `blobstore/serve.py` to eliminate the duplicate KV-setup race.
3. **Offload boundary sync I/O** — Audit all `async def` in boundary modules (`daemon.py`, `helper.py`, `checks_varz.py`, `dispenser.py`) for un-offloaded `open`, `mkdir`, `chmod`, `unlink`, `read_text`, `write_text`. Batch-fix with `asyncio.to_thread`.
4. **Use `async with` for aiosqlite cursors** — Replace manual `await cursor.close()` with `async with await conn.execute(...) as cursor:` in `blobstore/_handlers.py` to guarantee cleanup on exception.
5. **Pool httpx clients in monitoring** — Create a single `httpx.AsyncClient` in `__main__.py` and pass it into `run_checks`, `check_nats_varz`, and `send_telegram_message` to eliminate per-run TCP churn.
