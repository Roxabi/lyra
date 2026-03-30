# Observability — Logging, Tracing & Events

## Overview

Lyra uses **structured JSON file logs** with **per-turn trace IDs** as the primary observability mechanism, complemented by a **raw turn store** (SQLite audit trail) for conversation persistence.
There is no distributed tracing framework (no OpenTelemetry).
Each inbound turn receives a unique `trace_id` (UUID4) that propagates through the full async call chain via `contextvars`. The `pool_id` remains the conversation-scope correlation key.

---

## Log Storage

| Where | Format |
|-------|--------|
| `~/.local/state/lyra/logs/{YYYYMMDD_HHMMSS}_lyra.log` | Rotating file, UTC-stamped at startup |
| stdout | Mirror of file output (plaintext) |

**Rotation policy:** 10 MB per file, 5 backups kept (~50 MB total).
**Level:** `INFO` by default.
**File format:** JSONL (one JSON object per line) when `json_file = true` (default). Fields: `timestamp`, `level`, `logger`, `message`, `trace_id` (when set), `pool_id` (when set).
**Console format:** `%(asctime)s %(levelname)s %(name)s: %(message)s` (plaintext, unchanged).

Configured in `src/lyra/__main__.py` — `_setup_logging()`. Toggle JSON with `[logging] json_file = true` in `config.toml`.

---

## Trace IDs

Each inbound message turn receives a unique `trace_id` (UUID4) generated in `TraceMiddleware` (Stage 0 of the middleware pipeline). The ID propagates via `contextvars.ContextVar` through the entire async call chain — middleware stages, pool submission, agent dispatch, LLM call, and response dispatch — without any explicit parameter threading.

A `TraceIdFilter` (attached to all logging handlers at startup) reads `trace_id` and `pool_id` from context vars and injects them into every `LogRecord`. No existing log call sites need modification.

To isolate a single turn's log lines:

```bash
# JSON file logs (default)
jq 'select(.trace_id == "abc-123-...")' ~/.local/state/lyra/logs/*.log

# Or grep for the trace_id
grep '"trace_id":"abc-123-..."' ~/.local/state/lyra/logs/*.log
```

**Scope boundary:** Log lines emitted in `Hub.run()` outside of pipeline processing (e.g., the main loop itself) do not carry a `trace_id`. Only per-turn processing is traced.

Implemented in `src/lyra/core/trace.py`. See #270.

---

## Correlation: the `pool_id`

The `pool_id` is a stable string that identifies a conversation scope and appears in both file and console logs:

```
{platform}:{bot_id}:{scope_type}:{scope_id}
# e.g. telegram:main:chat:123456
```

To reconstruct a full conversation scope:

```bash
grep "telegram:main:chat:123456" ~/.local/state/lyra/logs/*.log
```

---

## What Gets Logged Per Request

The following events are emitted (at INFO unless noted) for each inbound message:

| Stage | Logger | Example line |
|-------|--------|-------------|
| Hub routing | `lyra.core.hub` | Pool resolved, workspace/cwd overrides |
| Agent dispatch | `lyra.agents.anthropic_agent` | `[agent:lyra][pool:telegram:main:chat:123] response: 156 chars` |
| LLM call (SDK) | `lyra.llm.drivers.sdk` | `SDK stream [pool:...]: in=45 out=87 tokens` |
| Retry/backoff | `lyra.llm.decorators` | Retry attempt N, backoff delay |
| Timeout / cancel | `lyra.core.cli_pool` (WARNING/ERROR) | `pool ...: no output for Ns — alive, waiting (1/3)` or `Timeout: no output for Ns` |
| Cancel-in-flight | `lyra.core.pool` (DEBUG) | New message while LLM processing |
| Circuit breaker | `lyra.core.circuit_breaker` (WARNING) | State transition old→new |

**What is NOT logged in file logs:** message content, full prompts/responses (only char/token counts). For full content capture, see the Turn Store below.

---

## Turn Store (L1 — Raw Turn Logging)

> Shipped in #67 (L1 memory layer).

The `TurnStore` (`src/lyra/core/turn_store.py`) persists every user and assistant turn to a dedicated **`~/.lyra/turns.db`** SQLite database (separate from roxabi-vault to avoid write contention). This provides a complete audit trail with message content, platform IDs, and session context.

| Column | Purpose |
|--------|---------|
| `pool_id` | Links to the pool (conversation scope) |
| `session_id` | Groups turns within a session |
| `role` | `"user"` or `"assistant"` |
| `platform` | `"telegram"`, `"discord"`, etc. |
| `user_id` | Canonical sender ID |
| `content` | Full message text |
| `message_id` | Platform-specific message ID |
| `reply_message_id` | Platform-specific replied-to message ID |
| `timestamp` | ISO 8601 UTC |
| `metadata` | JSON blob for extensibility |

**Write path:** `Pool.process()` calls `TurnStore.log_turn()` for each inbound and outbound message. Writes are fire-and-forget (`asyncio.create_task`) — a failed write logs a warning but never blocks message processing.

**Query interface:**
- `get_session_turns(session_id)` — all turns for a session, ordered by timestamp
- `get_pool_turns(pool_id, limit)` — recent turns for a pool
- `get_user_turns(user_id, limit)` — recent turns across all pools for a user

---

## Monitoring Events

Typed event dataclasses are defined in `src/lyra/core/events.py`. These are used by the monitoring module for health diagnostics.

> **Note:** The `EventBus` pub/sub mechanism (formerly in `event_bus.py`) was removed during the architecture refactoring. The event dataclasses remain as structured types for the monitoring system.

| Event | Fields |
|-------|--------|
| `AgentStarted` | `agent_id`, `pool_id`, `scope_id` |
| `AgentCompleted` | `agent_id`, `pool_id`, `duration_ms` |
| `AgentFailed` | `agent_id`, `pool_id`, `error` |
| `AgentIdle` | `agent_id`, `pool_id`, `finished_at` |
| `CircuitStateChanged` | `platform`, `old_state`, `new_state` |
| `QueueDepthExceeded` | `queue_name`, `depth`, `threshold` |
| `QueueDepthNormal` | `queue_name`, `depth` |

---

## Health Monitoring

A separate two-layer monitoring system runs on a configurable interval (default: 5 min):

- **Layer 1 — Health checks:** hits `http://localhost:8443/health`, checks queue depth, idle thresholds.
- **Layer 2 — LLM diagnosis:** aggregates Layer 1 results into a natural-language `DiagnosisReport`.

Config keys (in `config.toml` under `[monitoring]`):

| Key | Default | Purpose |
|-----|---------|---------|
| `check_interval_minutes` | 5 | How often checks run |
| `health_endpoint_timeout_s` | 5 | HTTP timeout for `/health` |
| `queue_depth_threshold` | 80 | Alert threshold |
| `idle_threshold_hours` | 6 | Flag pools idle longer than this |
| `quiet_start` / `quiet_end` | `00:00` / `08:00` | Suppress alerts during quiet hours |

---

## Gaps & Future Work

| Gap | Tracking |
|-----|---------|
| No end-to-end trace IDs | ✅ Resolved in #270 |
| No structured/JSON logs | ✅ Resolved in #270 |
| No message content capture in file logs | Captured in Turn Store (L1, #67 ✅) |
| No OpenTelemetry integration | — |
