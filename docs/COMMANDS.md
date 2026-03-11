# Lyra — Command Router

> How slash commands work in Lyra. From message to response.

---

## Overview

Lyra intercepts messages starting with `/` before they reach the LLM agent. Commands are routed to CLI tools on the host machine — fast, deterministic, zero token cost.

```
User → /agenda → Telegram → Hub → CommandRouter → gws CLI → Response → Telegram → User
User → hello   → Telegram → Hub → (not a command) → Agent (LLM) → Response → Telegram → User
```

---

## Available Commands

| Command | Description | CLI dependency |
|---------|------------|----------------|
| `/help` | List available commands | — (builtin) |
| `/stop` | Cancel the current processing turn | — (builtin) |
| `/circuit` | Show circuit breaker status (admin-only) | — (builtin) |
| `/echo <text>` | Echo back the message (test) | `echo` (system) |
| `/agenda` | Show today's calendar | `gws` |
| `/tasks` | List pending tasks | `gws` |
| `/save <url>` | Save a URL to the vault | `vault` |
| `/search <query>` | Search the vault | `vault` |
| `/voice <text>` | Generate speech | `voicecli` |
| `/image <prompt>` | Generate image prompt | — (prompt-only) |

---

## UX Examples

### `/help` — Discover commands

```
You:  /help
Lyra: Available commands:
      /help    — List available commands
      /echo    — Echo back the message (test command)
      /agenda  — Show today's calendar ⚠ requires gws
      /voice   — Generate speech ⚠ requires voicecli

      Type any command or just chat normally.
```

### `/agenda` — Calendar

```
You:  /agenda
Lyra: 📅 Today — March 5, 2026

      09:00  Standup (Google Meet)
      11:30  Dentist
      14:00  Review PR #70

      3 events today.
```

### `/save <url>` — Save to vault

```
You:  /save https://example.com/article
Lyra: ✓ Saved: https://example.com/article
```

### `/search <query>` — Search vault

```
You:  /search lyra architecture
Lyra: 🔍 3 results for "lyra architecture":

      1. ARCHITECTURE.md — Hub-and-spoke design, asyncio bus...
      2. ADR-010 — External tool integration pattern...
      3. ROADMAP.md — Phase 1 scope, Phase 2 SLMs...
```

### Normal chat (unchanged)

```
You:  What's the weather in Paris?
Lyra: Currently in Paris it's 12°C and partly cloudy...
```

No interception — goes through the normal LLM agent flow.

---

## Error Handling

### CLI not installed

```
You:  /agenda
Lyra: ⚠ Command unavailable: `gws` is not installed.
      Install: see setup.sh or run `uv tool install gws`
```

### Unknown command

```
You:  /pizza
Lyra: Unknown command: /pizza
      Type /help for available commands.
```

### Timeout

```
You:  /voice Generate a long podcast intro
Lyra: ⚠ Command timed out after 30s.
      Try a shorter request or run manually.
```

---

## How It Works

### End-to-End Flow: `/agenda`

```
1. USER types "/agenda" in Telegram

2. TELEGRAM ADAPTER
   ├── Parses update → text="/agenda", chat_id, user_id
   ├── Creates Message(content=TextContent(text="/agenda"))
   └── Pushes to hub.inbound_bus (per-platform Queue)

3. HUB RUN LOOP
   ├── Pulls message from bus
   ├── Rate limit check → OK
   ├── Resolves binding → agent="lyra_default"
   │
   ├── agent.command_router.is_command(msg) → True
   │
   ├── agent.command_router.dispatch(msg)
   │   ├── Parses: command="/agenda", args=[]
   │   ├── Registry: "/agenda" → skill="google-workspace", cli="gws"
   │   ├── shutil.which("gws") → found ✓
   │   ├── Runs: gws calendar list --today --json
   │   ├── Formats JSON → human-readable
   │   └── Returns Response("📅 Today — March 5...")
   │
   └── dispatch_response → Telegram adapter → user sees reply

4. LLM agent is NEVER called — zero tokens, fast response
```

### End-to-End Flow: `/save https://example.com/article`

```
1. USER types "/save https://example.com/article"

2. HUB → command_router.dispatch(msg)
   ├── Parses: command="/save", args=["https://example.com/article"]
   ├── Registry: "/save" → skill="vault", cli="vault"
   ├── Runs: vault add https://example.com/article
   └── Returns Response("✓ Saved: ...")

3. No LLM involved — direct CLI execution
```

---

## Configuration

Commands are declared in the agent TOML config:

```toml
# src/lyra/agents/lyra_default.toml

[commands."/help"]
builtin = true
description = "List available commands"

[commands."/agenda"]
skill = "google-workspace"
action = "calendar-today"
cli = "gws"
description = "Show today's calendar"

[commands."/save"]
skill = "vault"
action = "add"
cli = "vault"
description = "Save a URL to the vault"
```

Commands hot-reload when the TOML file changes — no restart needed.

### Adding a new command

A command is defined across 3 layers:

**Layer 1 — CLI tool on the machine** (already installed via `setup.sh`)
```bash
# The tool already works from the terminal
gws calendar list --today
voicecli generate "Hello world"
vault add https://example.com
```

**Layer 2 — TOML declaration** (tells Lyra the command exists)
```toml
# Add to src/lyra/agents/lyra_default.toml
[commands."/mycmd"]
skill = "my-skill"          # skill domain
action = "my-action"        # action within that domain
cli = "mycli"               # CLI binary to check for
description = "My command"  # shown in /help
```

**Layer 3 — Skill registry** (maps skill+action → actual CLI command)
```python
# In src/lyra/core/command_router.py — add one line
SKILL_REGISTRY = {
    ("my-skill", "my-action"): ["mycli", "subcommand", "--flag"],
    # user args are appended: mycli subcommand --flag arg1 arg2
}
```

**That's it.** No new files, no new classes. Just:
1. CLI installed on PATH
2. One TOML block
3. One dict entry

The command appears in `/help` immediately (hot-reload).

### Full chain

```
1. lyra_default.toml declares [commands."/mycmd"]
2. Lyra hot-reloads → /mycmd appears in /help
3. User types "/mycmd foo bar" in Telegram (or Discord)
4. CommandRouter:
   ├── Parses: command="/mycmd", args=["foo", "bar"]
   ├── TOML lookup → skill="X", action="Y", cli="Z"
   ├── shutil.which("Z") → is CLI installed?
   ├── SKILL_REGISTRY[("X","Y")] → ["Z", "sub", "cmd"]
   ├── Runs: Z sub cmd foo bar  (async subprocess, 30s timeout)
   └── Returns stdout as response
5. User sees result — no LLM involved
```

---

## Architecture

See also: [ADR-010 — External tool integration](architecture/adr/010-external-tool-integration-pattern.mdx)

```
[commands] TOML (Layer 3)     roxabi-plugins SKILL.md (Layer 2)     CLI on PATH (Layer 1)
─────────────────────────     ─────────────────────────────────     ─────────────────────
/agenda → gws                 google-workspace/SKILL.md             gws binary
/voice  → voicecli            voice-cli/SKILL.md                    voicecli binary
/save   → vault               vault/SKILL.md                        vault binary
```

Two access paths for the same tools:
1. **Command router** (user types `/agenda`) → direct CLI subprocess, no LLM
2. **Agent with Bash tool** (user asks "what's on my calendar?") → LLM decides to call gws
