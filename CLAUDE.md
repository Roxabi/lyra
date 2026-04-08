@.claude/stack.yml
@~/.claude/shared/global-patterns.md

# CLAUDE.md — Instructions for Claude Code

## Project

**Lyra by Roxabi** — Personal AI agent engine (hub-and-spoke, asyncio, multi-channel).
See `docs/ARCHITECTURE.md` for full context.

## TL;DR

- **Project:** Lyra
- **Before work:** Use `/dev #N` as the single entry point — it determines tier (S / F-lite / F-full) and drives the full lifecycle
- **Decisions:** → see global patterns (@~/.claude/shared/global-patterns.md)
- **Never** commit without asking, push without request, or use `--force`/`--hard`/`--amend`
- **Always** use appropriate skill even without slash command

## Key files

| File | Role |
|------|------|
| `docs/ARCHITECTURE.md` | Architecture + technical decisions |
| `docs/CONFIGURATION.md` | All config files, their purpose, load order, and system vs instance split |
| `docs/ROADMAP.md` | Roadmap and priorities |
| `docs/GETTING-STARTED.md` | Machine 1 setup guide |
| `artifacts/` | Frames, specs, plans, analyses, explorations (dev-core) |
| `deploy/provision.sh` | Machine 1 post-install provisioning script |
| `deploy/quadlet/` | Podman Quadlet units (`.container`, `.volume`, `.network`) — systemd-integrated containers |
| `deploy/nats/nats-container.conf` | NATS config for container deployment (no TLS, 0.0.0.0 bind) |

## Local infrastructure

Machine data (IPs, partitions, configs) lives in **`local/machines.md`** (gitignored, not versioned).

Check this file for:
- Machine IPs and hostnames
- Disk layouts
- Useful SSH commands
- Active services

```bash
# Connect to Machine 1 (Hub)
ssh mickael@192.168.1.16
```

## Machines

- **Machine 1** (`roxabituwer`, `192.168.1.16`) — Hub, Ubuntu Server 24.04, RTX 3080, 24/7
- **Machine 2** (`ROXABITOWER`) — AI Server, Windows + WSL2, RTX 5070Ti, on-demand

## Agent management

Agents live in **`~/.lyra/auth.db`** (SQLite). TOML files are seed sources only — they must be imported into the DB via `lyra agent init` before startup uses them.

Two TOML search locations (in precedence order):
1. `~/.lyra/agents/` — user-level overrides (machine-specific, gitignored)
2. `src/lyra/agents/` — bundled system defaults

```bash
lyra agent init           # seed DB from TOML files (first-time or after TOML edits)
lyra agent init --force   # overwrite existing DB rows
lyra agent list           # list all agents in DB
lyra agent show <name>    # full config for one agent
lyra agent edit <name>    # edit interactively in DB (no TOML needed)
lyra agent validate <name>
lyra agent create <name>                       # scaffold a new agent TOML
lyra agent assign <name> --platform telegram --bot <bot_id>
lyra agent unassign --platform telegram --bot <bot_id>
lyra agent delete <name>  # refuses if bot still assigned
```

- `cwd` is machine-specific → lives in `config.toml [defaults]`, NOT in agent TOML.
- `workspaces` — each key becomes accessible via `/workspace <key>` (not `/<key>`), overriding cwd for the current pool. See `docs/COMMANDS.md`.
- TOML edits → `lyra agent init --force` + restart to take effect.

## Conventions

- Language: English for all docs, code and commits
- Commits: Conventional Commits (`feat:`, `fix:`, `chore:`, etc.)
- Issues: via `dev-core` workflow (`/dev #N`)

## CLAUDE.md hygiene (hard rule)

**When you add, remove, or rename a file/package, update the relevant CLAUDE.md immediately.**

CLAUDE.md locations (one per package, single level — no nested sub-CLAUDE.md):
- `CLAUDE.md` — project root (this file)
- `src/lyra/core/CLAUDE.md` — hub, stores, pool, commands infra + all flat modules
- `src/lyra/adapters/CLAUDE.md` — Telegram, Discord, CLI, NATS adapters
- `src/lyra/agents/CLAUDE.md` — agent implementations
- `src/lyra/commands/CLAUDE.md` — plugin commands
- `src/lyra/llm/CLAUDE.md` — LLM drivers and providers

Rules:
- New file added → add it to the file table in the appropriate CLAUDE.md
- File deleted → remove its entry
- File moved → update source and destination CLAUDE.md
- New package/subdir under `src/lyra/` → add to the nearest CLAUDE.md (do NOT create a new nested CLAUDE.md)

## Production entry points (NATS three-process mode)

Lyra runs as three separate supervisor processes on Machine 1:

| Supervisor program | CLI command | Bootstrap function |
|-------------------|-------------|-------------------|
| `lyra_hub` | `lyra hub` | `_bootstrap_hub_standalone()` |
| `lyra_telegram` | `lyra adapter telegram` | `_bootstrap_adapter_standalone()` |
| `lyra_discord` | `lyra adapter discord` | `_bootstrap_adapter_standalone()` |

Scripts: `run_hub.sh` and `run_adapter.sh` in `supervisor/scripts/` (lyra's own supervisord, managed via `lyra.service`).
NATS topics: `lyra.inbound.<platform>.<bot_id>` (adapter→hub) · `lyra.outbound.<platform>.<bot_id>` (hub→adapter).

The unified single-process mode (`lyra start` → `_bootstrap_unified`) runs hub + adapters in one process with NATS. Auto-starts embedded nats-server when NATS_URL is not set.

## Gotchas

<!-- Add project-specific gotchas here -->

