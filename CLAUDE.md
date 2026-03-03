@.claude/stack.yml

# CLAUDE.md — Instructions for Claude Code

## Project

**Lyra by Roxabi** — Personal AI agent engine (hub-and-spoke, asyncio, multi-channel).
See `docs/ARCHITECTURE.md` for full context.

## Key files

| File | Role |
|------|------|
| `docs/ARCHITECTURE.md` | Architecture + technical decisions |
| `docs/ROADMAP.md` | Roadmap and priorities |
| `docs/GETTING-STARTED.md` | Machine 1 setup guide |
| `artifacts/` | Frames, specs, plans, analyses, explorations (dev-core) |
| `setup.sh` | Machine 1 post-install script |

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

## Conventions

- Language: English for all docs, code and commits
- Commits: Conventional Commits (`feat:`, `fix:`, `chore:`, etc.)
- Issues: via `dev-core` workflow (`/dev #N`)
