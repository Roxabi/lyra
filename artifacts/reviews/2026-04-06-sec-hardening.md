# Security Hardening Review — 2026-04-06

## Verdict: WARN

Two real security findings remain after the fixes (one HIGH, one MEDIUM). The eight
hardening fixes are all directionally correct; three are partial. No fix introduces a
new exploitable bug, though one introduces a minor logical inconsistency in eviction
bookkeeping. The TTL reaper is sound. See details below.

---

## Cache Bounding + TTL Reaper

### Caches that were unbounded (before 4f5c884)

| Cache | Location | Risk |
|-------|----------|------|
| `NatsOutboundListener._cache` | `nats_outbound_listener.py` | Unbounded dict — any attacker who can publish to `lyra.outbound.*` can exhaust process heap |
| `NatsOutboundListener._stream_queues` | same | Unbounded asyncio.Queue per stream — no queue depth limit, no stream count limit |
| `NatsOutboundListener._stream_tasks` | same | Unbounded asyncio.Task set |
| `NatsOutboundListener._stream_outbound` | same | Unbounded per-stream outbound dict |
| `discord_threads.py` — `thread_sessions` cache | `discord_threads.py` | Unbounded `dict[str, tuple]` passed in by caller |
| `NatsBus._staging` | `nats_bus.py` | Already had `maxsize=500` hard-coded, now parameterized |

DoS/memory exhaustion is excluded from the reporting scope, so the above is
context only — not a finding.

### Limit and TTL values

| Constant | Value | Assessment |
|----------|-------|-----------|
| `_MAX_CACHE_SIZE` | 500 entries | Reasonable. 500 InboundMessage objects are at most a few MB. |
| `_MAX_STREAMS` | 100 concurrent streams | Conservative. 100 simultaneous streaming LLM responses is well above realistic load. |
| `_MAX_QUEUE_SIZE` | 256 chunks/stream | Fine. 256 × ~100 B avg chunk = ~25 KB worst case. |
| `_CACHE_TTL_SECONDS` | 120 s | Matches the `asyncio.wait_for` timeout added to `_events()` — consistent. |
| `_REAPER_INTERVAL_SECONDS` | 30 s | Reaper fires 4× per TTL window. Max staleness = TTL + interval = 150 s. Acceptable. |
| Discord thread_sessions | 500 | Consistent with other caches. Hard-coded, not configurable. |
| `staging_maxsize` | 500 (default), configurable via `[inbound_bus]` | Correct; now threaded through from config. |

### TTL reaper correctness

**Sound.** The reaper (`_reap_stale`) uses a snapshot (`list(self._cache_ts.items())`)
before eviction — no concurrent modification of the dict being iterated.
Cancellation is handled: `stop()` calls `task.cancel()` + `contextlib.suppress(CancelledError)`.
The reaper evicts `_cache`, `_cache_ts`, `_stream_outbound`, `_stream_tasks`,
and `_stream_queues` in one pass — no orphan state is left behind.

**One minor inconsistency (not a security issue):** `cache_inbound()` calls
`_cache_ts.pop(oldest, None)` when evicting by size, but does NOT cancel the
stream task or remove `_stream_outbound`/`_stream_queues` for the evicted id.
If the evicted stream_id was mid-stream, its drain task continues with a missing
cache entry, drains silently, and cleans itself up. Functionally correct (the drain
task handles `original_msg is None`), but leaves `_stream_tasks` and `_stream_queues`
momentarily out of sync with `_cache`. No security consequence.

**Race between reaper and drain task:** If the reaper cancels a drain task that is
awaiting `adapter.send_streaming()`, the task's `finally` block runs and pops
`_stream_tasks`/`_stream_queues`. The reaper also pops the same keys — both use
`dict.pop(..., None)`, so the double-pop is safe.

---

## 8 Hardening Fixes

### Fix 1 — Credential scrubbing in NATS URL logs
- **Severity:** HIGH
- **Status:** COMPLETE
- `scrub_nats_url()` strips `user:password@` from `nats://user:pass@host:4222`
  before it reaches `log.info`.
- Applied in both `hub_standalone.py:152` and `adapter_standalone.py:41`.
- Note: the failure-path `sys.exit(f"... {nats_url!r} ...")` in `adapter_standalone.py:46`
  still prints the raw URL to stderr (see Remaining Gaps §1).

### Fix 2 — NATS auth key injection guard
- **Severity:** HIGH
- **Status:** COMPLETE
- `nats_connect()` now rejects any `**extra` kwargs that overlap with
  `_RESERVED_AUTH_KEYS = {"nkeys_seed_str", "token", "user", "password", "tls"}`.
- Prevents callers from accidentally injecting credentials that would bypass the
  centralized auth path.
- `ValueError` raised eagerly at call time — correct behavior.

### Fix 3 — Default NATS error/disconnect/reconnect callbacks
- **Severity:** LOW
- **Status:** COMPLETE
- Previously silent NATS-level errors and disconnects were not logged.
- `_default_error_cb`, `_default_disconnected_cb`, `_default_reconnected_cb` are now
  wired by default and can be overridden via `**extra`.

### Fix 4 — Structured deserialization via `deserialize_dict`
- **Severity:** MEDIUM
- **Status:** COMPLETE
- `OutboundMessage(**outbound_data)` and `OutboundAttachment(**attachment_data)`
  previously passed raw JSON dicts directly to dataclass constructors — no type
  coercion, no enum reconstruction, no bytes field decoding.
- Replaced with `_deserialize_dict(outbound_data, OutboundMessage)` which applies
  the full type-aware decode path.
- `_decode_concrete` for `bytes` now raises `ValueError` instead of silently
  returning a wrong-typed value — correct hardening.

### Fix 5 — Stream chunk queue bounding + `put_nowait` drop
- **Severity:** MEDIUM
- **Status:** COMPLETE
- `asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)` limits each stream's chunk backlog.
- `put_nowait` + catch `QueueFull` ensures the NATS callback never blocks.
  (The previous `await q.put(data)` would block the NATS subscription callback
  under backpressure, stalling all message delivery for that bot.)
- Warning logged on drop — observable.

### Fix 6 — Stream task and outbound dict limits
- **Severity:** MEDIUM
- **Status:** COMPLETE
- `_MAX_STREAMS = 100` enforced on both `_stream_outbound` (in `_handle_stream_start`)
  and `_stream_tasks` (in `_handle_chunk`).
- Existing streams are not blocked when at capacity — only new stream_ids are rejected.
  Correct: `stream_id not in self._stream_tasks and at_limit`.

### Fix 7 — `_get_hints` result caching
- **Severity:** LOW
- **Status:** COMPLETE
- `get_type_hints()` is now cached per type in `_hints_cache`.
- The comment "do NOT cache the empty fallback" is correct: a transient resolution
  failure returning `{}` would permanently disable type coercion for that type if cached.
- Security relevance: prevents CPU-side DoS via repeated type resolution on high-volume
  NATS message paths.

### Fix 8 — NatsBus `staging_maxsize` parameterization
- **Severity:** LOW
- **Status:** COMPLETE
- `staging_maxsize` was previously a hard-coded `500` inside `NatsBus.__init__`.
  Now it is a keyword-only parameter with the same default.
- Wired through `_load_inbound_bus_config` → `hub_standalone.py` and `unified.py`.
- Allows operators to tune the staging queue without code changes.

---

## Remaining Gaps

### Gap 1 — Raw NATS URL reaches stderr on connection failure (HIGH)

`adapter_standalone.py:46`:
```python
sys.exit(f"Failed to connect to NATS at {nats_url!r}: {exc}")
```
`hub_standalone.py` has the same pattern. If `NATS_URL` contains embedded credentials
(`nats://user:secret@host:4222`), the raw URL is printed to stderr and captured by
supervisord logs — defeating Fix 1.

**Fix:** wrap with `scrub_nats_url(nats_url)` in the `sys.exit` string.

### Gap 2 — nkey seed file readable by any process running as the same user (MEDIUM)

`_read_nkey_seed()` calls `path.read_text()` without checking file permissions.
The spec notes seeds live at `/etc/nats/nkeys/*.seed` with mode `0600` root-owned,
but the Python process runs as `mickael`. If an operator sets `NATS_NKEY_SEED_PATH`
to a world-readable file or a file owned by another user with group read enabled,
the seed is silently accepted. No permission validation is performed.

**Fix:** after `path.is_file()`, assert `oct(path.stat().st_mode)[-3:] == '600'`
or at minimum warn if mode is broader than `0600`.

### Gap 3 — `_hints_cache` is a module-level mutable dict with no size bound (LOW)

`_hints_cache: dict[type, dict[str, Any]] = {}` in `_serialize.py` grows for every
distinct dataclass type seen. In normal operation the set of types is finite and
small. However, if a NATS message contains a malformed payload that triggers
`_decode_union` with unknown candidate types via dynamic dataclass instantiation,
the cache could be populated with garbage keys. No current code path creates
dynamic types, so this is theoretical. No bound or eviction is implemented.

### Gap 4 — Discord thread_sessions eviction missing from `retrieve_thread_session` (LOW)

`retrieve_thread_session` in `discord_threads.py:128-131`:
```python
if len(cache) >= 500:
    _oldest = next(iter(cache))
    del cache[_oldest]
```
The eviction log line present in `persist_thread_session` is absent here — a silent
eviction that could mask cache thrashing. Not a direct security issue but reduces
observability of anomalous behavior.

### Gap 5 — `_terminated_streams` set is unbounded (LOW)

`NatsOutboundListener._terminated_streams` (live file, line 67) is a `set[str]`
with no eviction. Stream IDs are `discard()`ed in `_drain_stream`'s `finally` block,
so completed streams are cleaned up. However, if a stream is terminated via
`_handle_stream_error` but never starts a drain task, the stream_id remains in
`_terminated_streams` indefinitely. At high churn this leaks memory proportional
to unique stream_id count. The reaper does not clean `_terminated_streams`.

---

## Issues (OWASP-formatted findings)

### F1 — Credentials in error output on NATS connection failure

```
High: NATS_URL credentials reach supervisord stderr on connection failure
  src/lyra/bootstrap/adapter_standalone.py:46
  src/lyra/bootstrap/hub_standalone.py:154
  Category: A3 — Sensitive Data Exposure
  Confidence: 85%
  Exploit scenario: Operator sets NATS_URL=nats://hub:s3cr3t@host:4222. NATS server
    is temporarily down. adapter_standalone calls sys.exit() with the raw URL.
    supervisord captures stderr to a log file readable by any lyra process user.
    Attacker with read access to /tmp or the supervisor log dir extracts the nkey-
    equivalent credential.
  Root cause: scrub_nats_url() is applied to log.info() (Fix 1) but not to the
    sys.exit() failure message, leaving one exposure path unpatched.
  Remediation:
    1. Replace nats_url!r with scrub_nats_url(nats_url)!r in both sys.exit() calls.
       (recommended)
    2. Never embed credentials in NATS_URL — require NATS_NKEY_SEED_PATH exclusively
       for authentication and reject user:pass@ URLs in scrub_nats_url with a warning.
```

### F2 — No seed file permission validation

```
Medium: nkey seed accepted from world-readable files without permission check
  src/lyra/nats/connect.py:29-33
  Category: A6 — Security Misconfiguration
  Confidence: 70%
  Exploit scenario: Operator mistakenly sets NATS_NKEY_SEED_PATH to a file with
    mode 0644. Any local user on the machine reads the nkey seed and authenticates
    to the NATS server as the hub. Suspected — needs runtime verification of the
    deployment's actual file permission conventions.
  Root cause: _read_nkey_seed() validates that the path is a file and is non-empty,
    but does not assert st_mode. The spec mandates 0600 root-owned seeds but the
    code does not enforce it.
  Remediation:
    1. Add stat() check: warn (or exit) if (path.stat().st_mode & 0o777) != 0o600.
       (recommended)
    2. Document the required permissions explicitly in the sys.exit error message
       to guide operators on misconfiguration recovery.
```

---

## Actions

| Priority | Action | Owner |
|----------|--------|-------|
| P0 | Apply `scrub_nats_url()` to both `sys.exit()` failure strings (F1) | backend-dev |
| P1 | Add file permission check in `_read_nkey_seed()` (F2) | backend-dev |
| P2 | Add eviction log to `retrieve_thread_session` silent eviction path (Gap 4) | backend-dev |
| P3 | Have reaper also clean `_terminated_streams` (Gap 5) | backend-dev |
| Backlog | Evaluate `_hints_cache` size cap if dynamic type loading is ever added (Gap 3) | backend-dev |
