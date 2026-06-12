# CLAUDE.md Registry

All sub-CLAUDE.md files in this repo — update here on add/rename/delete.

| P | Scope |
|---|---|
| `CLAUDE.md` | project root |
| `src/factory/core/CLAUDE.md` | hub, stores, pool |
| `src/factory/adapters/CLAUDE.md` | Telegram, Discord, CLI, NATS |
| `src/factory/inbound/CLAUDE.md` | stage-axis inbound pipeline (parser, router, session, dispatcher) |
| `src/factory/agents/CLAUDE.md` | agent impls |
| `src/factory/blobstore/CLAUDE.md` | HTTP-fronted BlobStore service (peer-of-adapters, #1330 V8) |
| `src/factory/bootstrap/CLAUDE.md` | process bootstrap (standalone, wiring, lifecycle, factory, infra) |
| `src/factory/commands/CLAUDE.md` | plugin commands |
| `src/factory/infrastructure/CLAUDE.md` | store implementations (ADR-048) |
| `src/factory/integrations/CLAUDE.md` | external boundary layer (supervisor, systemctl, vault-cli, web-intel) |
| `src/factory/agent_cmd/CLAUDE.md` | agent + bot CLI commands — applicative layer above core |
| `src/factory/llm/CLAUDE.md` | LLM drivers |
| `src/factory/monitoring/CLAUDE.md` | standalone health-check subsystem (`python -m factory.monitoring`) |
| `src/factory/obs/CLAUDE.md` | observability scaffolding (OTel/Langfuse) — ¬wired, see #1235 |
| `src/factory/outbound/CLAUDE.md` | outbound stage composition (formatter/throttle/error_handler/emitter, #1279) |
| `src/factory/streaming/CLAUDE.md` | stage-axis streaming primitives (parser Protocol, state_machine, event_emitter) — composed by CliStreamingParser + StreamProcessor (#1282) |
| `src/factory/transport/CLAUDE.md` | NATS transport + WorkerPoolClient (3-layer primitives, #1278) |
| `src/factory/infrastructure/turn_writer/CLAUDE.md` | JetStream subscriber-writer for turns.db (#1331) — sole writer per ADR-075 |
| `src/factory/infrastructure/outbound_audio/CLAUDE.md` | JetStream stream + consumer + KV provisioning for durable outbound-audio path (#1482) |
| `src/factory/infrastructure/jobs/CLAUDE.md` | FACTORY_JOBS WorkQueue stream + DLQ router provisioning (ADR-088, #1203) |
| `src/factory/nats/CLAUDE.md` | in-tree NATS integration (subjects, codec, domain clients) |
| `src/factory/tools/CLAUDE.md` | GitHub token dispenser (gh_token helper) |
| `packages/roxabi-nats/CLAUDE.md` | NATS transport SDK (ADR-045) |
| `packages/roxabi-contracts/CLAUDE.md` | NATS contract schemas (ADR-049) |
| `packages/roxabi-blobs/CLAUDE.md` | BlobStore client SDK (consumed by hub + adapters) |
| `plugins/lyra-ops/CLAUDE.md` | ops plugin (debug, remote inspection) |
| `plugins/lyra-send/CLAUDE.md` | message-send plugin (HTTP → Telegram/Discord) |
| `plugins/refine-agent/CLAUDE.md` | agent-profile refine plugin |
| `tools/CLAUDE.md` | quality gates + analysis scripts |
| `deploy/CLAUDE.md` | Podman + Quadlet prod deploy (reference impl) |
