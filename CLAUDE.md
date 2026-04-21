@.claude/stack.yml
@~/.claude/shared/global-patterns.md

# CLAUDE.md — Instructions for Claude Code

Let:
  A := ~/.lyra/auth.db | T := TOML seed | P := CLAUDE.md path

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
| `docs/architecture/*.md` | Standard + target patterns |
| `docs/CONFIGURATION.md` | Config files, load order |
| `docs/agent-management.md` | A seed flow + CLI |
| `artifacts/` | Frames, specs, plans (dev-core) |
| `deploy/quadlet/` | Podman Quadlet units |
| `scripts/dep-graph/` | Dep-graph generator → `~/.roxabi/forge/lyra/visuals/` |
| `scripts/corpus/` | Issue sync (GraphQL → SQLite) → `~/.roxabi/corpus.db` |
| `packages/roxabi-nats/` | NATS transport SDK (ADR-045) |
| `packages/roxabi-contracts/` | NATS contract schemas (ADR-049) |
| `deploy/agents.yml` | Agent registry → supervisord conf |

## Agent management

Agents ∈ A (SQLite) | T files = seed only → `lyra agent init` before use
Search: `~/.lyra/agents/` (override) → `src/lyra/agents/` (default)
`cwd` → `config.toml [defaults]` (¬T)

→ `docs/agent-management.md` — CLI: `init | list | show | edit | validate | create | delete`

## Conventions

- EN for docs/code/commits
- Commits: Conventional (`feat:`, `fix:`, `chore`)
- Issues: `/dev #N`

## CLAUDE.md hygiene

File/rename → update P immediately

| P | Scope |
|---|---|
| `CLAUDE.md` | project root |
| `src/lyra/core/CLAUDE.md` | hub, stores, pool |
| `src/lyra/adapters/CLAUDE.md` | Telegram, Discord, CLI, NATS |
| `src/lyra/agents/CLAUDE.md` | agent impls |
| `src/lyra/commands/CLAUDE.md` | plugin commands |
| `src/lyra/llm/CLAUDE.md` | LLM drivers |

Rules: add/delete/move → update P | new `src/lyra/` subdir → nearest P (¬nested)

## Production entry points (NATS 3-process)

| Program | CLI | Bootstrap |
|---|---|---|
| `lyra_hub` | `lyra hub` | `_bootstrap_hub_standalone()` |
| `lyra_telegram` | `lyra adapter telegram` | `_bootstrap_adapter_standalone()` |
| `lyra_discord` | `lyra adapter discord` | `_bootstrap_adapter_standalone()` |

Topics: `lyra.inbound.<platform>.<bot_id>` | `lyra.outbound.<platform>.<bot_id>`

Unified: `lyra start` → hub + adapters in 1 process + embedded NATS
