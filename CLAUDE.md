@.claude/stack.yml
@~/.claude/shared/global-patterns.md

# CLAUDE.md — Instructions for Claude Code

Let:
  A := ~/.lyra/auth.db (grants, identity only) | C := ~/.lyra/config.db (agents, bots, prefs) | T := TOML seed | P := CLAUDE.md path

## Project

**Lyra** — AI agent engine (hub-spoke, asyncio, multi-channel)
→ `docs/ARCHITECTURE.md`

## TL;DR

- Entry: `/dev #N` → tier (S/F-lite/F-full) → lifecycle
- Decisions → global-patterns.md
- ¬`--force` | ¬`--hard` | ¬`--amend`

## Key files

| File | Role |
|---|---|
| `docs/ARCHITECTURE.md` | Architecture + decisions |
| `docs/architecture/CURRENT.generated.md` | Generated inventory SSoT (layers/subjects/topology) — ¬edit, gated by `architecture_snapshot` |
| `docs/CONFIGURATION.md` | Config files, load order |
| `docs/agent-management.md` | Agent seed flow + CLI |
| `docs/bot-management.md` | Bot seed flow + CLI |
| `docs/ops/container-publishing.md` | CI → GHCR → Quadlet pattern |
| `deploy/quadlet/lyra-nats.container` | NATS Quadlet unit — `type=mount` secret anchor (restart-not-HUP for ACL changes) |
| `packages/roxabi-nats/` | NATS transport SDK (ADR-045) |
| `packages/roxabi-contracts/` | NATS contract schemas (ADR-049) |
| `src/lyra/transport/` | NATS transport + WorkerPoolClient (3-layer composition, #1278) |

## Agent management

Agents ∈ C (SQLite) | T files = seed only → `lyra agent init` before use
Search: `~/.lyra/agents/` (override) → `src/lyra/agents/` (default)
`cwd` → `config.toml [defaults]` (¬T)

→ `docs/agent-management.md` — CLI verbs: `init | list | show | edit | patch | validate | create | delete | assign | unassign | refine`

## CLAUDE.md hygiene

File/rename → update P immediately

| P | Scope |
|---|---|
| `CLAUDE.md` | project root |
| `src/lyra/core/CLAUDE.md` | hub, stores, pool |
| `src/lyra/adapters/CLAUDE.md` | Telegram, Discord, CLI, NATS |
| `src/lyra/inbound/CLAUDE.md` | stage-axis inbound pipeline (parser, router, session, dispatcher) |
| `src/lyra/agents/CLAUDE.md` | agent impls |
| `src/lyra/blobstore/CLAUDE.md` | HTTP-fronted BlobStore service (peer-of-adapters, #1330 V8) |
| `src/lyra/bootstrap/CLAUDE.md` | process bootstrap (standalone, wiring, lifecycle, factory, infra) |
| `src/lyra/commands/CLAUDE.md` | plugin commands |
| `src/lyra/infrastructure/CLAUDE.md` | store implementations (ADR-048) |
| `src/lyra/integrations/CLAUDE.md` | external boundary layer (supervisor, systemctl, vault-cli, web-intel) |
| `src/lyra/agent_cmd/CLAUDE.md` | agent + bot CLI commands — applicative layer above core |
| `src/lyra/llm/CLAUDE.md` | LLM drivers |
| `src/lyra/monitoring/CLAUDE.md` | standalone health-check subsystem (`python -m lyra.monitoring`) |
| `src/lyra/obs/CLAUDE.md` | observability scaffolding (OTel/Langfuse) — ¬wired, see #1235 |
| `src/lyra/outbound/CLAUDE.md` | outbound stage composition (formatter/throttle/error_handler/emitter, #1279) |
| `src/lyra/streaming/CLAUDE.md` | stage-axis streaming primitives (parser Protocol, state_machine, event_emitter) — composed by CliStreamingParser + StreamProcessor (#1282) |
| `src/lyra/transport/CLAUDE.md` | NATS transport + WorkerPoolClient (3-layer primitives, #1278) |
| `src/lyra/infrastructure/turn_writer/CLAUDE.md` | JetStream subscriber-writer for turns.db (#1331) — sole writer per ADR-075 |
| `src/lyra/infrastructure/outbound_audio/CLAUDE.md` | JetStream stream + consumer + KV provisioning for durable outbound-audio path (#1482) |
| `src/lyra/nats/CLAUDE.md` | in-tree NATS integration (subjects, codec, domain clients) |
| `src/lyra/tools/CLAUDE.md` | GitHub token dispenser (gh_token helper) |
| `packages/roxabi-nats/CLAUDE.md` | NATS transport SDK (ADR-045) |
| `packages/roxabi-contracts/CLAUDE.md` | NATS contract schemas (ADR-049) |
| `packages/roxabi-blobs/CLAUDE.md` | BlobStore client SDK (consumed by hub + adapters) |
| `plugins/lyra-ops/CLAUDE.md` | ops plugin (debug, remote inspection) |
| `plugins/lyra-send/CLAUDE.md` | message-send plugin (HTTP → Telegram/Discord) |
| `plugins/refine-agent/CLAUDE.md` | agent-profile refine plugin |
| `tools/CLAUDE.md` | quality gates + analysis scripts |
| `deploy/CLAUDE.md` | Podman + Quadlet prod deploy (reference impl) |

Rules: add/delete/move → update P | new subdir with non-obvious invariants → add CLAUDE.md + register here | "invariants, not inventory" (¬file counts, ¬method dumps — let `ls`/`grep` answer that)

## Production entry points (NATS 5-process)

| Subcommand | CLI | Bootstrap |
|---|---|---|
| `hub` | `lyra hub` | `_bootstrap_hub_standalone()` |
| `adapter telegram` | `lyra adapter telegram` | `_bootstrap_adapter_standalone()` |
| `adapter discord` | `lyra adapter discord` | `_bootstrap_adapter_standalone()` |
| `adapter clipool` | `lyra adapter clipool` | `_bootstrap_clipool_standalone()` |
| `turn-writer` | `lyra turn-writer` | `_bootstrap_turn_writer_standalone()` |

Topics: `lyra.inbound.<platform>.<bot_id>` | `lyra.outbound.<platform>.<bot_id>`

Unified: `lyra start` → hub + adapters in 1 process + embedded NATS

## Container deployment

Prod: Podman Quadlet (systemd `--user`) on M₁ (`lyra-hub` role). Eight containers: `lyra-nats`, `lyra-hub`, `lyra-telegram`, `lyra-discord`, `lyra-clipool`, `lyra-gh-helper`, `lyra-turn-writer`, `lyra-blobstore`. Install: `deploy/install.sh` (idempotent). Manifest: `deploy/quadlet.toml`.

→ `docs/QUADLET-DEPLOYMENT.md` — install runbook, secret rotation, diagnostic
→ `~/projects/docs/container-deployment-standard.md` — 18 standards (S7 secret target, S8 naming, S12 RestartSec=10)
