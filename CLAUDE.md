@.claude/stack.yml

# CLAUDE.md — Instructions for Claude Code

## Project

**Lyra by Roxabi** — Personal AI agent engine (hub-and-spoke, asyncio, multi-channel).
See `docs/ARCHITECTURE.md` for full context.

## TL;DR

- **Project:** Lyra
- **Before work:** Use `/dev #N` as the single entry point — it determines tier (S / F-lite / F-full) and drives the full lifecycle
- **Always** `AskUserQuestion` for choices — never plain-text questions
- **Never** commit without asking, push without request, or use `--force`/`--hard`/`--amend`
- **Always** use appropriate skill even without slash command

### AskUserQuestion

Always `AskUserQuestion` for: decisions, choices (≥2 options), approach proposals.
**Never** plain-text "Do you want..." / "Should I..." → use the tool.

### Git

Format: `<type>(<scope>): <desc>` + `Co-Authored-By: Claude <model> <noreply@anthropic.com>`
Types: feat|fix|refactor|docs|style|test|chore|ci|perf
Never push without request. Never force/hard/amend. Hook fail → fix + NEW commit.

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

## Gotchas

<!-- Add project-specific gotchas here -->
