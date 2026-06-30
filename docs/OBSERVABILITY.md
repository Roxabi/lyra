# Observability — Logging, Tracing & Events

## Overview

factory uses **structured console logs** with **per-turn trace IDs** as the primary observability mechanism, complemented by a **raw turn store** (SQLite audit trail) for conversation persistence.
There is no distributed tracing framework (no OpenTelemetry).
Each inbound turn receives a unique `trace_id` (UUID4) that propagates through the full async call chain via `contextvars`. The `pool_id` remains the conversation-scope correlation key.

---

## Log Output

| Where | Format |
|-------|--------|
| stdout | `%(asctime)s %(levelname)s %(name)s: %(message)s` (plaintext) |

**Level:** `INFO` by default; override via `[logging] level` in `config.toml`.

Configured in `src/factory/__main__.py` — `_setup_logging()`.

---

## Trace IDs

Each inbound message turn receives a unique `trace_id` (UUID4) generated in `TraceMiddleware` (Stage 0 of the middleware pipeline). The ID propagates via `contextvars.ContextVar` through the entire async call chain — middleware stages, pool submission, agent dispatch, LLM call, and response dispatch — without any explicit parameter threading.

A `TraceIdFilter` (attached to all logging handlers at startup) reads `trace_id` and `pool_id` from context vars and injects them into every `LogRecord`. No existing log call sites need modification.

To isolate a single turn's log lines:

```bash
journalctl --user -u factory-hub | grep 'abc-123-...'
```

**Scope boundary:** Log lines emitted in `Hub.run()` outside of pipeline processing (e.g., the main loop itself) do not carry a `trace_id`. Only per-turn processing is traced.

Implemented in `src/factory/core/trace.py`. See #270.

---

## Correlation: the `pool_id`

The `pool_id` is a stable string that identifies a conversation scope and appears in console logs:

```
{platform}:{bot_id}:{scope_type}:{scope_id}
# e.g. telegram:main:chat:123456
```

To reconstruct a full conversation scope:

```bash
journalctl --user -u factory-hub | grep "telegram:main:chat:123456"
```

---

## What Gets Logged Per Request

The following events are emitted (at INFO unless noted) for each inbound message:

| Stage | Logger | Example line |
|-------|--------|-------------|
| Hub routing | `factory.core.hub` | Pool resolved, workspace/cwd overrides |
| Agent dispatch | `factory.agents.anthropic_agent` | `[agent:lyra][pool:telegram:main:chat:123] response: 156 chars` |
| LLM call (SDK) | `factory.llm.drivers.sdk` | `SDK stream [pool:...]: in=45 out=87 tokens` |
| Retry/backoff | `factory.llm.decorators` | Retry attempt N, backoff delay |
| Timeout / cancel | `factory.core.cli_pool` (WARNING/ERROR) | `pool ...: no output for Ns — alive, waiting (1/3)` or `Timeout: no output for Ns` |
| Cancel-in-flight | `factory.core.pool` (DEBUG) | New message while LLM processing |
| Circuit breaker | `factory.core.circuit_breaker` (WARNING) | State transition old→new |

**What is NOT logged:** message content, full prompts/responses (only char/token counts). For full content capture, see the Turn Store below.

---

## Turn Store (L1 — Raw Turn Logging)

> Shipped in #67 (L1 memory layer).

The `TurnStore` (`src/factory/core/turn_store.py`) persists every user and assistant turn to a dedicated **`~/.roxabi/factory/turns.db`** SQLite database (separate from roxabi-vault to avoid write contention). This provides a complete audit trail with message content, platform IDs, and session context.

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

Typed telemetry events are emitted at middleware seam boundaries for observability (#432). These are defined in `src/factory/core/hub/pipeline_events.py` and fanned out via `PipelineEventBus` (`src/factory/core/hub/event_bus.py`).

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

> **Removed — see [#1035](https://github.com/Roxabi/roxabi-factory/issues/1035).** The host-timer units (`lyra-monitor.{service,timer}`) have been removed from `deploy/`. The Python module `src/factory/monitoring/` is preserved for Monitoring v2 spec reference.

> For ad-hoc hub health probes, see `docs/CONFIGURATION.md` § Monitoring — removed.
> For the planned successor, see [#1035](https://github.com/Roxabi/roxabi-factory/issues/1035) (Monitoring v2 — NATS event stream + Tauri dashboard).

---

## Operator & deploy audit

Application logs (above) go to **stdout → journald**. **Shell deploy actions** are audited separately:

| Store | Path | Role |
|-------|------|------|
| Operator log | `~/.local/state/factory/logs/operator.log` | JSONL: `install.sh`, `make converge` |
| Rotation log | `~/.roxabi/factory/rotation-log.md` | Human record of credential rotations |
| Deploy timers | journald `--user` | `factory-quadlet-sync`, `factory-post-autoupdate` |

`~/.local/state/factory/logs/` is **not** written by `setup_logging()` — containers do not use file handlers. The directory exists for `operator.log` only (provisioned by `deploy/setup.py`).

Runbook: [runbooks/operator-log.md](runbooks/operator-log.md). Decision record: [ADR-093](architecture/adr/093-operator-audit-three-channel.mdx) (three-channel audit: operator.log + journald + rotation-log.md).

---

## Loki + Promtail (log engine — ADR-092 Phase 1)

| Unit | Image | Storage |
|------|-------|---------|
| `factory-loki` | `grafana/loki:3.4.2` | `~/.local/state/factory/loki/` |
| `factory-promtail` | `grafana/promtail:3.4.2` | positions in `~/.local/state/factory/promtail/` |

Promtail ingests:

- `~/.local/state/factory/logs/operator.log` (JSONL → labels `event`, `user`, `host`)
- User journald via host `/var/log/journal` (owner UID filter + `factory-*` / `voicecli-*` units — `deploy/observability/promtail-config.yml`)

Loki API: `http://127.0.0.1:3100` (localhost only). Query via `logcli` until control-plane dashboard (#1760).

Runbook: [runbooks/loki-query.md](runbooks/loki-query.md).

---

## OTel Collector + otel-raw store (trace engine v1 — ADR-097)

| Unit | Image | Storage / notes |
|------|-------|-----------------|
| `factory-otel-collector` | `otel/opentelemetry-collector-contrib:0.120.0` | JSONL → `~/.local/state/factory/otel/` |
| SQLite index | — | `~/.roxabi/factory/otel-raw.db` (dashboard BFF queries) |

Flow:

- **Workers:** NATS adapters (`NatsAdapterBase` hooks) → OTLP gRPC → collector → JSONL archive
- **Primary agent:** Claude Code (clipool subprocess) → OTLP gRPC → same collector
- **Secondary:** LiteLLM proxy (`llmcli` OTel v2) → same collector — OMP + cloud relay
- **Dashboard:** `GET /api/bff/spans` reads SQLite index — no per-engine UI required

Bootstrap: `deploy/scripts/bootstrap-otel-raw.sh` → dirs + permissions. Collector env optional (`otel-collector.env`).

**Langfuse (optional / deferred):** six-container stack remains in quadlet manifest for future drill-down but is **not** on the v1 critical path. Collector v1 does not export to Langfuse.

Runbook: [runbooks/otel-traces.md](runbooks/otel-traces.md).

---

## Gaps & Future Work

| Gap | Tracking |
|-----|---------|
| No end-to-end trace IDs | ✅ Resolved in #270 |
| No structured/JSON logs | ✅ Resolved in #270 |
| No message content capture in logs | Captured in Turn Store (L1, #67 ✅) |
| No OpenTelemetry integration | Phase 1 ✅ clipool → Langfuse; LiteLLM (#671) + hub wiring open |
| No central log search UI | Loki ✅ — dashboard composition (#1760) still open |
| Manual ops outside instrumented scripts | Partial — use `install.sh` / `make converge`; see operator-log runbook |
