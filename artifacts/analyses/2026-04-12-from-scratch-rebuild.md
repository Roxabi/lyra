---
title: Lyra — From-Scratch Rebuild Plan
date: 2026-04-12
status: reference
sources:
  - ~/.roxabi/lyra-nats-truth/ (architecture truth, 2026-04-09)
  - ~/.roxabi/2026-04-10-lyra-nats-lego-guide.md
  - ~/.roxabi/forge/diagrams/lyra-chimera/ (Chimera vision + roadmap, 2026-03-27)
  - current codebase audit (2026-04-12)
---

# Lyra — From-Scratch Rebuild Plan

Clean-slate build order that reaches the target topology (M0→M3 + plugin layer + hardening) without the migration debt of the current codebase.

## Target end state

Two nested horizons:

**M0–M3 (infrastructure):** Hub = stateless NATS router. LLM / harness / CLI run as separate NATS workers. Multi-backend per agent (Anthropic API, local GPU, Ollama). Zero-downtime hub restarts via Redis sessions + hub queue groups. Plugin ecosystem via entry-points + `PLUGIN.json`. Hardened: nkey auth, non-root containers, health probes, CB wired to every `LlmProvider`, SQLite/Postgres backup, TLS 365d.

**Chimera (capability):** on top of M0–M3 — lane-based task queue (user / cron / background), proactive SOP engine (time-triggered outbound), persistent cron scheduler, multi-agent delegation (orchestrator spawns sub-agents via Bus), Machine 2 LLM worker (RTX 5070 Ti distributed inference), WebSocket gateway for future web UI, MCP client for external tool servers, encrypted secrets service replacing `.env`. Destination: capability ≈ 7/10 while holding DX ≈ 9/10.

## Architectural invariants (lock on day one)

1. **`core/` has zero framework imports.** No `anthropic`, `aiogram`, `discord`, `nats`, `redis`, `sqlite3`. Enforced in CI via `import-linter` contract, not by discipline.
2. **Every boundary is a `Protocol`, even with one implementation.** `LlmProvider` exists before `AnthropicSdkDriver`; `ChannelAdapter` exists before `TelegramAdapter`. Adding NATS later is "another adapter," not a rewrite.
3. **Dependency rule points inward.** `adapters/` → `core/`, never the reverse. `llm/` is lowest-level and framework-free.
4. **Two envelopes, not one.**
   - *Transport envelope* (wire) — request/reply, streaming inbox, subject registry.
   - *Domain event union* — `LlmEvent` (`Text | ToolUse | Result`, frozen dataclasses) and `RenderEvent`, framework-agnostic.
5. **NATS-first from L1.** Every process-to-process call is a NATS subject, not an import. No flag-day migrations later.
6. **Lanes, not a single queue.** Task dispatch is lane-aware from day one: `user` / `cron` / `background` / `proactive` / `delegation`. Adding lanes later forces a rewrite of the pool.
7. **Hard phase gate: automated deploy is green BEFORE NATS swap.** Rollback safety is a prerequisite, not an afterthought (Chimera Phase 2 gate).
8. **Backups before features.** `auth.db` loss is unrecoverable. M-OPS-4 ships before any Phase 0 work.

## Build order

Architecture-down. Each layer unlocks the next; don't skip.

### L-1 — Architecture contract

Ship before any code.

- Declare layers: domain (`core`) ← application (hub) ← infra (`adapters`, `nats`, `stores`).
- Declare dependency rule (inward only).
- Declare ports as empty `Protocol` classes:
  - `LlmProvider` · `ChannelAdapter` · `Bus[T]` · `AgentStoreProtocol` · `TurnStoreProtocol` · `SttProvider` · `TtsProvider`
- Add `import-linter` contract in CI preventing reverse dependencies.
- Add `ruff` + `pyright` gates.

### L0 — Foundations (Chimera Phase 0: Foundation)

Ship ops hygiene BEFORE any feature code. Order inside L0 matters.

1. **M-OPS-4 backups first** — `sqlite3 .backup` systemd timer (7 daily + 4 weekly) for `auth.db` and turn store. One day of work, unrecoverable if skipped.
2. `config.toml`, `~/.lyra/` layout, `stack.yml`.
3. Encrypted **secrets service** (M-OPS-11) from day one — no `.env` files with API keys. Age-encrypted or sops-backed store, key on Machine 1.
4. `gen-nkeys.sh` generates all 7 seeds upfront: `hub`, `cli-worker`, `llm-worker`, `harness`, `monitor`, `tts-adapter`, `stt-adapter`. `auth.conf` with all public nkeys.
5. `Dockerfile` — multi-stage, `python:3.12-slim`, non-root `lyra` user.
6. Quadlet units from the start: `User=lyra`, `HealthCmd=curl -f http://localhost:8443/health`, `MemoryLimit=1G`.
7. `systemd` user unit with linger + `lyra.service` → `start.sh --all`.
8. **M-OPS-1 health endpoint** + **M-OPS-2 alerting** (Telegram monitoring bot on failure).
9. **M-OPS-5 automated deploy** with rollback — **HARD GATE: must be green before L1 NATS swap begins.**
10. Voice pipeline scaffold (TTSService via `voicecli`) — unblocks #79, #232 downstream.

**Unlocks:** every process authenticated, containerized, non-root, backed-up, deployable with rollback.

### L1 — NATS bus + two envelopes

- `nats-container.conf` with TLS + nkey auth (no `--no_auth` even in dev — use ephemeral nkeys).
- **Transport envelope:** NATS request/reply, streaming inbox pattern (`msg.reply` subscription, not `nc.request()`), payload size enforcement.
- **Domain event union:** `LlmEvent = TextLlmEvent | ToolUseLlmEvent | ResultLlmEvent` (frozen), `RenderEvent = TextRenderEvent | ToolSummaryRenderEvent`.
- `StreamProcessor` pipeline: `LlmEvent → RenderEvent`.
- Subject registry: `lyra.inbound.<plat>.<bot>` · `lyra.outbound.<plat>.<bot>` · `lyra.llm.<provider>.request` · `lyra.harness.<provider>.request` · `lyra.harness.cli.request`.
- Reject `*` and `>` in user-supplied subject components at publish/subscribe time.
- JetStream object store for payloads >1MB (#521 in issue map).

**Unlocks:** every boundary is a subject; domain events are framework-neutral.

### L2 — SDK adapters behind `LlmProvider`

- `AnthropicSdkDriver`, `OllamaDriver` implement `LlmProvider` port.
- Hosted in `lyra_llm` worker (standalone process, NATS subscriber on `lyra.llm.*.request`).
- Stateless: token generation only, no history, no tool loop.
- Streaming via `msg.reply` inbox.
- Hub never imports these drivers.
- **Machine 2 LLM worker** (ROXABITOWER, RTX 5070 Ti) — second `lyra_llm` instance subscribes to the same subject via NATS queue group. Routes Qwen 2.5 14B / Gemma 3 27B for local inference. Horizontal scaling is free once L1 is in place.

**Unlocks:** backend selection per agent; distributed inference across machines; M2 prerequisite.

### L3 — Stateless harness worker (`lyra_harness`)

- Subscribes `lyra.harness.anthropic.request`.
- Multi-turn tool loop, tool executor registry (Read, Grep, Edit, Bash, MCP).
- Calls `lyra_llm` via NATS for token generation.
- Returns delta turns only (not full history).
- `MAX_HISTORY_BYTES` budget + truncation strategy.

**Unlocks:** agentic loop outside hub; hub restart ≠ session death.

### L4 — CLI harness worker (`lyra_cli`)

- Subscribes `lyra.harness.cli.request`.
- Claude CLI subprocess pool, isolated from hub.
- Input validation on model names (`^[a-zA-Z0-9\-\.]+$`) and tool names (`^[A-Za-z][A-Za-z0-9_]*$`) — reject flag-like strings.

**Unlocks:** Claude CLI crash ≠ hub crash.

### L5 — Hub = pure router

- `NatsHarnessClient`, `NatsLlmClient`, `NatsCliClient` — SDK *connectors*.
- Hub talks only through ports: `LlmProvider`, `ChannelAdapter`, `Bus[T]`, `*StoreProtocol`.
- No inline drivers, no Anthropic SDK import, no subprocess management.
- `PoolManager`, `Pool`, `OutboundDispatcher`, `Middleware`, SQLite turn store.
- **Lane-based dispatch queue** (OpenClaw 5-lane pattern) — `user` / `cron` / `background` / `proactive` / `delegation`. Each lane has its own priority + rate budget. Retrofitting lanes onto a single queue is a pool rewrite, so ship them here.
- `auth.db` on Postgres (concurrent-writer safe).
- Circuit breaker wired to every `LlmProvider` call, including `stream()`.
- `hub.py` stays under 400 lines — routing only, no domain logic.

**Unlocks:** hub has no subprocess, no SDK import, no agentic loop; lane-aware scheduling from day one.

### L6 — Redis sessions + hub queue groups

- `Pool` in-memory state → Redis keys (`lyra:session:<pool_id>:*`) with TTL.
- Redis AOF persistence (`appendonly yes`, `appendfsync everysec`).
- `HUB_INBOUND` NATS queue group — two hubs share inbound traffic.

**Unlocks:** two hubs serve same user; zero-downtime deploys.

### L7 — Adapters behind `ChannelAdapter`

Order: CLI (dev) → Telegram → Discord (parallel once CLI proves the contract) → WebSocket gateway.

- `NatsAdapterBase` worker.
- `make_push_to_hub()` factory (no per-adapter duplication).
- `NatsChannelProxy` implements `ChannelAdapter`, publishes outbound to NATS subjects.
- Telegram webhook secret: `raise SystemExit` on empty, never warn-and-run.
- Telegram topic threads: `user_scoped()` to match Discord guild behavior.
- **WebSocket gateway adapter** (Chimera Phase 2) — real-time event stream on `lyra.outbound.web.*`, enables future web UI without touching hub.

**Unlocks:** end-to-end flow for humans across chat + web.

### L8 — Plugin layer (Chimera Phase 3)

- `entry-points` pip discovery (issue #644).
- `PLUGIN.json` manifest schema (#645) — validated, versioned, dependency-declared.
- `agents.yml` generator (#646).
- Plugin **registry + loader** with execution isolation (no `hub.py` changes for new plugins).
- Agent TOML seed files shipped in repo (`lyra_default.toml`).
- First tenant: `roxabi-plugins`.
- **MCP client** — connects to external MCP tool servers; registers their tools behind the same tool executor used by `lyra_harness`.

**Unlocks:** third-party commands/agents/tools without forking.

### L8b — Proactive + scheduling (Chimera Phase 2/3)

- **Persistent cron scheduler** — schedule store (SQLite), triggers NATS messages on `lyra.proactive.*`. Monday briefings, periodic summaries, reminders.
- **SOP engine** — condition-matched automation. Autonomous dispatch subjects (e.g. `briefing.scheduled`, `reminder.fire`) feed the `proactive` lane in L5.
- No human trigger required — hub's proactive lane picks up and routes to the target agent.

**Unlocks:** Lyra initiates conversation; recurring workflows; reactive → proactive shift.

### L8c — Multi-agent delegation (Chimera Phase 3)

- Orchestrator agent spawns sub-agents via `Bus[T]` on the `delegation` lane.
- Results returned asynchronously via reply subject.
- `hub.py` stays under 400 lines — delegation is a pattern on top of existing ports, not a new hub feature.

**Unlocks:** agent-of-agents; parallel sub-task execution.

### L9 — Hardening floor

Ship before any external exposure.

- CB wired to every `LlmProvider` including `stream()`; count stream failures.
- Health endpoint: hub state + bus connectivity + pool count + worker heartbeat.
- Input validation: model/tool names, paths (allowlist base dirs), URLs (HTTPS-only, no SSRF), `platform_meta` (type + 256-char cap).
- nkey seed file mode check (`0o600`) — exit if world-readable.
- TLS certs: 365-day validity + renewal runbook.
- SQLite/Postgres backup systemd timer (7 daily + 4 weekly).
- `pip-audit` + `uv` cache + Docker build validation in CI.
- Pygments ≥2.20.0, cryptography ≥46.0.7 (CVE-2026-4539, CVE-2026-39892).

### L10 — Observability

- Structured logs with `session_id` correlation.
- `/status` command (session_id, workspace, voice_mode, agent_name).
- `/health/detail` endpoint.
- Monitoring bot (existing pattern) for rate-limit, binding-miss, audio-fail notifications.
- **Prometheus + Loki** (Chimera Phase 2) — metrics exporter + log aggregation, once NATS is stable. Not before — single-machine `journalctl` is sufficient until then.

## Rebuild flavor — decision

| Option | Tradeoff |
|---|---|
| 1. Greenfield clone of target | fastest to clean code; discards proven bootstrapping sequence; harder to validate per layer |
| 2. Greenfield hub + keep existing harness/CLI binaries | splits risk; harder cutover |
| **3. Layered rebuild, NATS-first from L1** (recommended) | every layer demonstrable on its own; no flag day; matches §7 rollout-sequence discipline (config toggle, not feature flag) |

## Phase mapping (Chimera ↔ L-layers)

| Chimera phase | Scope | Maps to |
|---|---|---|
| Phase 0 — Foundation (15–20 d) | backups, secrets, health, alerting, auto-deploy, voice scaffold | **L-1, L0** |
| Phase 1 — Intelligence (25–30 d) | lane queue, FallbackChain, RRF search, pipelines, tool registry | **L1, L5 (lanes)**, parts of L8 |
| Phase 2 — Distribution (40–50 d) | NATS JetStream, Machine 2 LLM worker, SOP engine, WebSocket gateway, Prometheus/Loki | **L1–L4, L7 (websocket), L8b, L10** |
| Phase 3 — Extension (30–40 d) | plugins, cron, MCP, multi-agent, secrets hardening, staging env | **L8, L8b, L8c, L9** |
| Phase 4 — Chimera (ongoing) | web dashboard, RAG, WhatsApp/Slack/Signal, edge, A2A protocol | beyond this plan |

**Hard gates:**

- Phase 0 must ship backups (M-OPS-4) before anything else inside L0.
- Phase 1 does not start until automated deploy (M-OPS-5) is green.
- Phase 2 NATS swap does not start until Phase 1 is stable with safe deploy.
- Phase 3 plugins do not land until Phase 2 NATS is stable.

## Coverage vs current codebase

Current code already exhibits clean + hexagonal architecture:

- Ports: `LlmProvider` (`llm/base.py:32`), `ChannelAdapter` (`core/hub/hub_protocol.py:23`), `Bus[T]` (`core/bus.py:26`), `AgentStoreProtocol` (`core/stores/agent_store_protocol.py:34`).
- SDK adapters: `AnthropicSdkDriver` (`llm/drivers/sdk.py:37`), `ClaudeCliDriver` (`llm/drivers/cli.py:17`).
- SDK connectors: `NatsSttClient`, `NatsChannelProxy` (`nats/nats_channel_proxy.py:39`).
- Event envelopes: `LlmEvent` (`llm/events.py:58`), `RenderEvent` (`core/render_events.py:106`), `StreamProcessor`.
- Containers: `Dockerfile` + `deploy/quadlet/*.container`.

Missing vs target (this plan addresses each):

- `lyra_llm` standalone worker → L2.
- `lyra_harness` standalone worker → L3.
- Machine 2 LLM worker (distributed inference) → L2.
- Redis session store → L6.
- Postgres for `auth.db` → L5.
- Lane-based queue (single queue today) → L5.
- CB wired to every LLM client (partial today) → L5 + L9.
- Health probe depth (shallow today) → L9 + L10.
- Container `User=` directive (missing today) → L0.
- Encrypted secrets service (`.env` today) → L0.
- SOP engine + persistent cron → L8b.
- Multi-agent delegation → L8c.
- MCP client → L8.
- WebSocket gateway adapter → L7.
- Automated backups for `auth.db` → L0 (M-OPS-4, first task).
