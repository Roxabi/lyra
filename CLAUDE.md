@.claude/stack.yml
@~/.claude/shared/global-patterns.md

# CLAUDE.md — Instructions for Claude Code

Let:
  A := ~/.roxabi/factory/auth.db (grants, identity only) | C := ~/.roxabi/factory/config.db (agents, bots, prefs) | T := TOML seed | P := CLAUDE.md path

## Project

**Lyra** — AI agent engine (hub-spoke, asyncio, multi-channel)
→ `docs/ARCHITECTURE.md`

## TL;DR

- Entry: `/dev #N` → tier (S/F-lite/F-full) → lifecycle
- Close checklist (pre-cleanup, from worktree): `docs/process/dev-cycle.md` — run **before** worktree removal; `/dev` skill integration pending (roxabi-plugins)
- Decisions → global-patterns.md
- ¬`--force` | ¬`--hard` | ¬`--amend`

## Axial Review (mandatory)

PRs that cross architectural layer boundaries MUST carry `dev-core:axial-adr-review` before merge.
`.github/workflows/axial-review.yml` applies labels automatically; do NOT strip them manually.

| Trigger | Labels applied |
|---------|---------------|
| PR touches top-level `inbound/` **and** top-level `adapters/` | `dev-core:axial-adr-review` |
| PR touches top-level `core/` **and** top-level `infrastructure/` | `dev-core:axial-adr-review` |
| PR adds `except Exception` (any binding form: `as e`, `as exc`, bare) | `dev-core:axial-adr-review` + `dev-core:security-auditor` |

Note: `core/ports/` and `core/hub/` are both sub-packages of `core/` — intra-core changes touching both do NOT trigger the label. Only changes that cross the top-level `factory.*` module boundary trigger `dev-core:axial-adr-review`.

Review checklist (applies when `dev-core:axial-adr-review` is present):
- Confirm no inbound-layer logic leaks into adapters (ADR boundary)
- Confirm core domain objects are not polluted with infrastructure concerns
- `except Exception:` must be justified inline with a comment; open a follow-up issue if swallowing

## Key files

| File | Role |
|---|---|
| `docs/ARCHITECTURE.md` | Architecture + decisions |
| `docs/process/dev-cycle.md` | `/dev #N` close checklist — debt retrospective |
| `docs/architecture/CURRENT.generated.md` | Generated inventory SSoT (layers/subjects/topology) — ¬edit, gated by `architecture_snapshot` |
| `docs/CONFIGURATION.md` | Config files, load order |
| `docs/agent-management.md` | Agent seed flow + CLI |
| `docs/bot-management.md` | Bot seed flow + CLI |
| `docs/ops/container-publishing.md` | CI → GHCR → Quadlet pattern |
| `deploy/quadlet/factory-nats.container` | NATS Quadlet unit — `type=mount` secret anchor (restart-not-HUP for ACL changes) |
| `packages/roxabi-nats/` | NATS transport SDK (ADR-045) |
| `packages/roxabi-contracts/` | NATS contract schemas (ADR-049) |
| `src/factory/transport/` | NATS transport + WorkerPoolClient (3-layer composition, #1278) |

## Agent management

Agents ∈ C (SQLite) | T files = seed only → `factory agent init` before use
Search: `~/.roxabi/factory/agents/` (override) → `src/factory/agents/` (default)
`cwd` → `config.toml [defaults]` (¬T)

→ `docs/agent-management.md` — CLI verbs: `init | list | show | edit | patch | validate | create | delete | assign | unassign | refine`

## CLAUDE.md hygiene

File/rename → update P immediately

→ `docs/claude-md-registry.md` — full P→scope table (29 : root + 28 sub). Update there on add/rename/delete.

Rules: add/delete/move → update P | new subdir with non-obvious invariants → add CLAUDE.md + register here | "invariants, not inventory" (¬file counts, ¬method dumps — let `ls`/`grep` answer that)

## Production entry points (NATS 5-process)

| Subcommand | CLI | Bootstrap |
|---|---|---|
| `hub` | `factory hub` | `_bootstrap_hub_standalone()` |
| `adapter telegram` | `factory adapter telegram` | `_bootstrap_adapter_standalone()` |
| `adapter discord` | `factory adapter discord` | `_bootstrap_adapter_standalone()` |
| `adapter clipool` | `factory adapter clipool` | `_bootstrap_clipool_standalone()` |
| `turn-writer` | `factory turn-writer` | `_bootstrap_turn_writer_standalone()` |

Topics: `factory.inbound.<platform>.<bot_id>` | `factory.outbound.<platform>.<bot_id>`

Unified: `factory start` → hub + adapters in 1 process + embedded NATS

## Container deployment

Prod: Podman Quadlet (systemd `--user`) on M₁ (`factory-hub` role). Eight containers: `factory-nats`, `factory-hub`, `factory-telegram`, `factory-discord`, `factory-clipool`, `factory-gh-helper`, `factory-turn-writer`, `factory-blobstore`. Install: `deploy/install.sh` (idempotent). Manifest: `deploy/quadlet.toml`.

→ `docs/QUADLET-DEPLOYMENT.md` — install runbook, secret rotation, diagnostic
→ `~/projects/docs/container-deployment-standard.md` — 18 standards (S7 secret target, S8 naming, S12 RestartSec=10)
