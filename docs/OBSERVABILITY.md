# Observability — Logging, Tracing & Events

## Overview

Lyra uses **plaintext rotating file logs** as the primary observability mechanism.
There is no distributed tracing framework (no OpenTelemetry), no structured JSON logs, and no database audit trail today.
The `pool_id` is the de-facto correlation key to reconstruct a request's lifecycle across log lines.

---

## Log Storage

| Where | Format |
|-------|--------|
| `~/.lyra/logs/{YYYYMMDD_HHMMSS}_lyra.log` | Rotating file, UTC-stamped at startup |
| stdout | Mirror of file output |

**Rotation policy:** 10 MB per file, 5 backups kept (~50 MB total).
**Level:** `INFO` by default.
**Format:** `%(asctime)s %(levelname)s %(name)s: %(message)s`

Configured in `src/lyra/__main__.py` — `_setup_logging()`.

---

## Correlation: the `pool_id`

There are no trace IDs or correlation IDs.
The `pool_id` is a stable string that identifies a conversation scope and appears consistently across all log lines for a given request:

```
{platform}:{bot_id}:{scope_type}:{scope_id}
# e.g. telegram:main:chat:123456
```

To reconstruct a full request lifecycle, grep the log file for its pool_id:

```bash
grep "pool:telegram:main:chat:123456" ~/.lyra/logs/*.log
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
| Timeout / cancel | `lyra.core.pool` (WARNING) | `pool ...: turn timeout after 60s — killing backend` |
| Cancel-in-flight | `lyra.core.pool` (DEBUG) | New message while LLM processing |
| Circuit breaker | `lyra.core.event_bus` (WARNING) | State transition old→new |

**What is NOT logged:** message content, full prompts/responses (only char/token counts).

---

## Event Bus

An in-memory event bus (`src/lyra/core/event_bus.py`) emits typed events during processing.
These are **not persisted** — they feed real-time subscribers (monitoring, future alerting).

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
| No end-to-end trace IDs | — |
| No structured/JSON logs | — |
| No message content capture | — |
| No persistent audit trail | Persistent JSONL session logs — #67 (Phase 2) |
| No OpenTelemetry integration | — |
