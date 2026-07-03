# CLAUDE.md / AGENTS.md Registry

Instruction content lives in `AGENTS.md`. Each `CLAUDE.md` is a thin shim (`@AGENTS.md`; root also `@.claude/stack.yml`). Update here on add/rename/delete.

| P (shim) | AGENTS.md | Scope |
|---|---|---|
| `CLAUDE.md` | `AGENTS.md` | project root |
| `src/factory/core/CLAUDE.md` | `src/factory/core/AGENTS.md` | hub, stores, pool |
| `src/factory/adapters/CLAUDE.md` | `src/factory/adapters/AGENTS.md` | Telegram, Discord, CLI, NATS |
| `src/factory/adapters/omp/CLAUDE.md` | `src/factory/adapters/omp/AGENTS.md` | OmpWorker NATS adapter — digest gate, `tool_input` suppression, `_result_sent` guard, ADR-073 discipline |
| `src/factory/inbound/CLAUDE.md` | `src/factory/inbound/AGENTS.md` | stage-axis inbound pipeline (parser, router, session, dispatcher) |
| `src/factory/agents/CLAUDE.md` | `src/factory/agents/AGENTS.md` | agent impls |
| `src/factory/blobstore/CLAUDE.md` | `src/factory/blobstore/AGENTS.md` | HTTP-fronted BlobStore service (peer-of-adapters, #1330 V8) |
| `src/factory/bootstrap/CLAUDE.md` | `src/factory/bootstrap/AGENTS.md` | process bootstrap (standalone, wiring, lifecycle, factory, infra) |
| `src/factory/commands/CLAUDE.md` | `src/factory/commands/AGENTS.md` | plugin commands |
| `src/factory/infrastructure/CLAUDE.md` | `src/factory/infrastructure/AGENTS.md` | store implementations (ADR-048) |
| `src/factory/integrations/CLAUDE.md` | `src/factory/integrations/AGENTS.md` | external boundary layer (supervisor, systemctl, vault-cli, web-intel) |
| `src/factory/agent_cmd/CLAUDE.md` | `src/factory/agent_cmd/AGENTS.md` | agent + bot CLI commands — applicative layer above core |
| `src/factory/llm/CLAUDE.md` | `src/factory/llm/AGENTS.md` | LLM drivers |
| `src/factory/monitoring/CLAUDE.md` | `src/factory/monitoring/AGENTS.md` | standalone health-check subsystem (`python -m factory.monitoring`) |
| `src/factory/obs/CLAUDE.md` | `src/factory/obs/AGENTS.md` | observability scaffolding (OTel/Langfuse) — ¬wired, see #1235 |
| `src/factory/outbound/CLAUDE.md` | `src/factory/outbound/AGENTS.md` | outbound stage composition (formatter/throttle/error_handler/emitter, #1279) |
| `src/factory/streaming/CLAUDE.md` | `src/factory/streaming/AGENTS.md` | stage-axis streaming primitives (parser Protocol, state_machine, event_emitter) — composed by CliStreamingParser + StreamProcessor (#1282) |
| `src/factory/transport/CLAUDE.md` | `src/factory/transport/AGENTS.md` | NATS transport + WorkerPoolClient (3-layer primitives, #1278) |
| `src/factory/infrastructure/turn_writer/CLAUDE.md` | `src/factory/infrastructure/turn_writer/AGENTS.md` | JetStream subscriber-writer for turns.db (#1331) — sole writer per ADR-075 |
| `src/factory/infrastructure/outbound_audio/CLAUDE.md` | `src/factory/infrastructure/outbound_audio/AGENTS.md` | JetStream stream + consumer + KV provisioning for durable outbound-audio path (#1482) |
| `src/factory/infrastructure/jobs/CLAUDE.md` | `src/factory/infrastructure/jobs/AGENTS.md` | FACTORY_JOBS WorkQueue stream + DLQ router provisioning (ADR-088, #1203) |
| `src/factory/nats/CLAUDE.md` | `src/factory/nats/AGENTS.md` | in-tree NATS integration (subjects, codec, domain clients) |
| `src/factory/tools/CLAUDE.md` | `src/factory/tools/AGENTS.md` | GitHub token dispenser (gh_token helper) |
| `src/factory/dashboard/CLAUDE.md` | `src/factory/dashboard/AGENTS.md` | control-plane BFF axis (ADR-094) — session-ID footgun |
| `packages/roxabi-nats/CLAUDE.md` | `packages/roxabi-nats/AGENTS.md` | NATS transport SDK (ADR-045) |
| `packages/roxabi-contracts/CLAUDE.md` | `packages/roxabi-contracts/AGENTS.md` | NATS contract schemas (ADR-049) |
| `packages/roxabi-blobs/CLAUDE.md` | `packages/roxabi-blobs/AGENTS.md` | BlobStore client SDK (consumed by hub + adapters) |
| `packages/roxabi-otel/CLAUDE.md` | `packages/roxabi-otel/AGENTS.md` | OTel impl of MessageLifecycleHooks (keeps OTel out of roxabi-nats) |
| `packages/roxabi-obs/CLAUDE.md` | `packages/roxabi-obs/AGENTS.md` | fleet plane ③ reporter — periodic ContainerReport publish |
| `packages/roxabi-satellite/CLAUDE.md` | `packages/roxabi-satellite/AGENTS.md` | shared NATS satellite plumbing for GPU worker CLIs |
| `plugins/factory-ops/CLAUDE.md` | `plugins/factory-ops/AGENTS.md` | ops plugin (debug, remote inspection) |
| `plugins/refine-agent/CLAUDE.md` | `plugins/refine-agent/AGENTS.md` | agent-profile refine plugin |
| `tools/CLAUDE.md` | `tools/AGENTS.md` | quality gates + analysis scripts |
| `scripts/CLAUDE.md` | `scripts/AGENTS.md` | platform orchestration (bash) + domain operational tooling — scripts/ vs tools/ boundary |
| `deploy/CLAUDE.md` | `deploy/AGENTS.md` | Podman + Quadlet prod deploy (reference impl) |
