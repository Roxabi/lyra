@.claude/stack.yml
@.claude/dev-core.md

# CLAUDE.md — Instructions for Claude Code

## Project

**Lyra by Roxabi** — Personal AI agent engine (hub-and-spoke, asyncio, multi-channel).
See `docs/ARCHITECTURE.md` for full context.

## TL;DR

- **Project:** Lyra
- **Before work:** Use `/dev #N` as the single entry point — it determines tier (S / F-lite / F-full) and drives the full lifecycle
- **Decisions:** → see global patterns (@.claude/dev-core.md)
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
| `~/projects/lyra-stack/scripts/provision.sh` | Machine 1 post-install provisioning script (sibling repo) |

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

## Production entry points (NATS three-process mode)

Lyra runs as three separate supervisor processes on Machine 1:

| Supervisor program | CLI command | Bootstrap function |
|-------------------|-------------|-------------------|
| `lyra_hub` | `lyra hub` | `_bootstrap_hub_standalone()` |
| `lyra_telegram` | `lyra adapter telegram` | `_bootstrap_adapter_standalone()` |
| `lyra_discord` | `lyra adapter discord` | `_bootstrap_adapter_standalone()` |

Scripts: `run_hub.sh` and `run_adapter.sh` in `supervisor/scripts/` (lyra's own supervisord, managed via `lyra.service`).
NATS topics: `lyra.inbound.<platform>.<bot_id>` (adapter→hub) · `lyra.outbound.<platform>.<bot_id>` (hub→adapter).

The old single-process mode (`python -m lyra --adapter telegram` → `_bootstrap_multibot`) still exists in the codebase but is no longer the production deployment mode.

## Gotchas

<!-- Add project-specific gotchas here -->

## Verification Summary

_Fact-checked 2026-03-17 against `src/lyra/`, `docs/`, and git history._

| # | Claim | Result |
|---|-------|--------|
| 1 | Key docs exist (`ARCHITECTURE.md`, `CONFIGURATION.md`, `ROADMAP.md`, `GETTING-STARTED.md`) | ✅ Confirmed |
| 2 | `artifacts/` (analyses, explorations, frames, plans, specs) | ✅ Confirmed |
| 3 | `lyra-stack/scripts/provision.sh` (relative path) | ❌ Fixed → `~/projects/lyra-stack/scripts/provision.sh` — `lyra-stack` is a sibling repo, not inside this project |
| 4 | "TOML files in `src/lyra/agents/`" as the only location | ❌ Fixed → Two locations: `~/.lyra/agents/` (user-level, higher precedence) and `src/lyra/agents/` (system defaults) |
| 5 | `workspaces` "each key becomes a `/<key>` slash command" | ❌ Fixed → command is `/workspace <key>`, not `/<key>` (verified in `workspace_commands.py`) |
| 6 | `~/.lyra/auth.db` as agent store | ✅ Confirmed (`cli_agent.py:31`) |
| 7 | `lyra agent init/list/show/edit/validate/assign/delete` commands | ✅ All confirmed in `cli_agent_crud.py` |
| 8 | `lyra agent delete` refuses if bot assigned | ✅ Confirmed (`agent_store.py:315`) |
| 9 | `cwd` in `config.toml [defaults]` | ✅ Confirmed (`config.toml:3-4`) |
| 10 | `lyra agent unassign` command (missing) | ➕ Added — exists in `cli_agent_crud.py` |
| 11 | `lyra agent create <name>` command (missing) | ➕ Added — documented in `docs/COMMANDS.md` |
