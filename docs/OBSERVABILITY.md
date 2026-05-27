# Observability — Logging, Tracing & Events

## Overview

Lyra uses **structured console logs** with **per-turn trace IDs** as the primary observability mechanism, complemented by a **raw turn store** (SQLite audit trail) for conversation persistence.
There is no distributed tracing framework (no OpenTelemetry).
Each inbound turn receives a unique `trace_id` (UUID4) that propagates through the full async call chain via `contextvars`. The `pool_id` remains the conversation-scope correlation key.

---

## Log Output

| Where | Format |
|-------|--------|
| stdout | `%(asctime)s %(levelname)s %(name)s: %(message)s` (plaintext) |

**Level:** `INFO` by default; override via `[logging] level` in `config.toml`.

Configured in `src/lyra/__main__.py` — `_setup_logging()`.

---

## Trace IDs

Each inbound message turn receives a unique `trace_id` (UUID4) generated in `TraceMiddleware` (Stage 0 of the middleware pipeline). The ID propagates via `contextvars.ContextVar` through the entire async call chain — middleware stages, pool submission, agent dispatch, LLM call, and response dispatch — without any explicit parameter threading.

A `TraceIdFilter` (attached to all logging handlers at startup) reads `trace_id` and `pool_id` from context vars and injects them into every `LogRecord`. No existing log call sites need modification.

To isolate a single turn's log lines:

```bash
journalctl --user -u lyra-hub | grep 'abc-123-...'
```

**Scope boundary:** Log lines emitted in `Hub.run()` outside of pipeline processing (e.g., the main loop itself) do not carry a `trace_id`. Only per-turn processing is traced.

Implemented in `src/lyra/core/trace.py`. See #270.

---

## Correlation: the `pool_id`

The `pool_id` is a stable string that identifies a conversation scope and appears in console logs:

```
{platform}:{bot_id}:{scope_type}:{scope_id}
# e.g. telegram:main:chat:123456
```

To reconstruct a full conversation scope:

```bash
journalctl --user -u lyra-hub | grep "telegram:main:chat:123456"
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

**What is NOT logged:** message content, full prompts/responses (only char/token counts). For full content capture, see the Turn Store below.

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

## Pipeline Telemetry Events

Typed telemetry events are emitted at middleware seam boundaries for observability (#432). These are defined in `src/lyra/core/hub/pipeline_events.py` and fanned out via `PipelineEventBus` (`src/lyra/core/hub/event_bus.py`).

> **Note:** The original `events.py` (AgentStarted, AgentCompleted, etc.) was deleted in `6cd433c` — these types were never wired into the codebase. The current pipeline event system replaced them for middleware telemetry.

| Event | Fields | Purpose |
|-------|--------|---------|
| `MessageReceived` | `msg_id`, `platform`, `user_id`, `scope_id` | Inbound message enters pipeline |
| `StageCompleted` | `msg_id`, `stage`, `duration_ms` | Middleware stage finished |
| `MessageDropped` | `msg_id`, `stage`, `reason` | Pipeline short-circuited with DROP |
| `CommandDispatched` | `msg_id`, `command` | CommandMiddleware dispatched a command |
| `PoolSubmitted` | `msg_id`, `pool_id`, `agent_name`, `resume_status` | Message submitted to pool |

The `PipelineEventBus` is injected via constructor (DI, not singleton) per ADR-025. Subscribers receive events via per-subscriber `asyncio.Queue` instances — the pipeline is never blocked by a slow consumer. `audit_consumer.py` is the first consumer, draining events for audit logging.

---

## Health Monitoring

> **Removed — see [#1035](https://github.com/Roxabi/lyra/issues/1035).** The host-timer units (`lyra-monitor.{service,timer}`) have been removed from `deploy/`. The Python module `src/lyra/monitoring/` is preserved for Monitoring v2 spec reference.

> For ad-hoc hub health probes, see `docs/CONFIGURATION.md` § Monitoring — removed.
> For the planned successor, see [#1035](https://github.com/Roxabi/lyra/issues/1035) (Monitoring v2 — NATS event stream + Tauri dashboard).

---

## Gaps & Future Work

| Gap | Tracking |
|-----|---------|
| No end-to-end trace IDs | ✅ Resolved in #270 |
| No structured/JSON logs | ✅ Resolved in #270 |
| No message content capture in logs | Captured in Turn Store (L1, #67 ✅) |
| No OpenTelemetry integration | — |
