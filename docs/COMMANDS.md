# Lyra — Command Router

> How slash commands work in Lyra. From message to response.

---

## The `lyra` CLI

The `lyra` CLI is the main entry point for managing Lyra from your shell. It replaces the former `python -m lyra` and `lyra-agent` commands.

### Installation

After `uv sync`, activate the virtual environment to put `lyra` on your PATH:

```bash
source .venv/bin/activate
# Or add .venv/bin to your PATH permanently in ~/.bashrc:
# export PATH="$HOME/projects/lyra/.venv/bin:$PATH"
```

### Subcommands

**Server**

| Command | Description |
|---------|-------------|
| `lyra` / `lyra start` | Start the Lyra server |
| `lyra --version` / `lyra -V` | Print the installed version |
| `lyra --help` | List all available subcommands |

**Agent management**

| Command | Description |
|---------|-------------|
| `lyra agent create <name>` | Scaffold a new agent TOML |
| `lyra agent list` | List all discovered agents |
| `lyra agent validate [<name>]` | Validate agent TOML config(s) |

**Config management**

| Command | Description |
|---------|-------------|
| `lyra config show` | Print the resolved `config.toml` |
| `lyra config validate` | Validate `config.toml` against the schema |

> `lyra-agent` still works but prints a deprecation warning. Migrate to `lyra agent <subcommand>`.

### Agent config directories

Lyra searches for agent TOML files in two locations, in order of precedence:

1. `~/.lyra/agents/` — user-level configs (take precedence)
2. `src/lyra/agents/` — project-level configs (bundled defaults)

If the same agent name exists in both directories, the user-level file wins.

### Machine-wide defaults (`config.toml [defaults]`)

The `[defaults]` section in `config.toml` lets you set machine-wide fallbacks for `cwd`, `persona`, and `workspaces` that apply to all agents unless overridden in the agent's own TOML:

```toml
[defaults]
cwd = "~/projects/lyra"

[defaults.workspaces]
lyra     = "~/projects/lyra"
projects = "~/projects"
```

---

## Overview

Lyra intercepts messages starting with `/` before they reach the LLM agent. Commands are routed to built-in handlers or plugins — fast, deterministic, zero token cost.

```
User → /echo hi → Telegram → Hub → CommandRouter → builtin → Response → Telegram → User
User → hello    → Telegram → Hub → (not a command) → Agent (LLM) → Response → Telegram → User
```

---

## Available Commands

| Command | Description | CLI dependency |
|---------|------------|----------------|
| `/help` | List available commands | — (builtin) |
| `/stop` | Cancel the current processing turn | — (builtin) |
| `/circuit` | Show circuit breaker status (admin-only) | — (builtin) |
| `/routing` | Show smart routing decisions (admin-only) | — (builtin) |
| `/config` | Show/set runtime config (admin-only) | — (builtin) |
| `/svc <action> [service]` | Manage supervisor services (admin-only) | — (plugin) |
| `/clear` | Clear conversation history | — (builtin) |
| `/new` | Start a new session (alias for /clear) | — (builtin) |
| `/echo <text>` | Echo back the message (test) | — (plugin) |
| `/voice <text>` | Generate speech | `voicecli` |
| `/image <prompt>` | Generate image prompt | — (prompt-only) |
| `/<workspace>` | Switch working directory (dynamic) | — (TOML-defined) |

---

## Workspace Commands

Workspaces are named directory shortcuts defined in the agent TOML under `[workspaces]`. Each key becomes a `/keyname` slash command that sets the working directory for the Claude subprocess in that conversation scope.

### Configuration

```toml
# src/lyra/agents/lyra_default.toml

[model]
cwd = "~/projects/lyra"   # optional: fixed default cwd for this agent

[workspaces]
lyra        = "~/projects/lyra"
projects    = "~/projects"
roxabi-vault = "~/.roxabi-vault"
```

### Usage

```
Syntax: /<workspace>
        /<workspace> <question>
```

- `/lyra` — sets the workspace to `~/projects/lyra`, responds with a context confirmation
- `/lyra what's the last commit?` — sets the workspace, then forwards the question to Claude with `cwd=~/projects/lyra`

The cwd override persists for the pool (thread/conversation) lifetime. `/clear` respawns the process but keeps the same workspace. Use another workspace command to switch.

In Discord with `auto_thread=True`: each thread has its own pool, so workspace commands are per-thread. Multiple threads can have different workspaces in parallel.

---

## UX Examples

### `/help` — Discover commands

```
You:  /help
Lyra: Available commands:
      /help    — List available commands
      /stop    — Cancel the current processing turn
      /clear   — Clear conversation history
      /new     — Start a new session (alias for /clear)
      /echo    — Echo back the message (test command)
      /voice   — Generate speech ⚠ requires voicecli

      Type any command or just chat normally.
```

### `/clear` / `/new` — Reset session

```
You:  /clear
Lyra: Conversation history cleared.
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
You:  /voice Hello world
Lyra: ⚠ Command unavailable: `voicecli` is not installed.
      Install: see setup.sh or run `uv tool install voicecli`
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

### End-to-End Flow: `/echo hello`

```
1. USER types "/echo hello" in Telegram

2. TELEGRAM ADAPTER
   ├── Parses update → text="/echo hello", chat_id, user_id
   ├── Creates Message(content=TextContent(text="/echo hello"))
   └── Pushes to hub.inbound_bus (per-platform Queue)

3. HUB RUN LOOP
   ├── Pulls message from bus
   ├── Rate limit check → OK
   ├── Resolves binding → agent="lyra_default"
   │
   ├── agent.command_router.is_command(msg) → True
   │
   ├── agent.command_router.dispatch(msg)
   │   ├── Parses: command="/echo", args=["hello"]
   │   ├── Plugin: "/echo" → cmd_echo handler
   │   └── Returns Response("hello")
   │
   └── dispatch_response → Telegram adapter → user sees reply

4. LLM agent is NEVER called — zero tokens, fast response
```

---

## Configuration

Built-in commands are declared in `CommandRouter._DEFAULT_BUILTINS`. Plugin commands are declared in each plugin's `plugin.toml`:

```toml
# src/lyra/plugins/echo/plugin.toml

[[commands]]
name = "echo"
description = "Echo back the message (test command)"
handler = "cmd_echo"
```

Plugins are enabled per-agent in the agent TOML config:

```toml
# src/lyra/agents/lyra_default.toml

[plugins]
enabled = ["echo"]
```

### Adding a new plugin command

A plugin command is defined across 2 layers:

**Layer 1 — `plugin.toml`** (declares the command)
```toml
[[commands]]
name = "mycmd"
description = "My command"   # shown in /help
handler = "cmd_mycmd"
```

**Layer 2 — `handlers.py`** (implements the handler)
```python
async def cmd_mycmd(args: list[str], msg: InboundMessage) -> Response:
    return Response(content=f"You said: {' '.join(args)}")
```

**That's it.** Enable the plugin in the agent TOML and the command appears in `/help` immediately (hot-reload).

### Full chain

```
1. plugin.toml declares [[commands]] name = "mycmd"
2. Agent hot-reloads → /mycmd appears in /help
3. User types "/mycmd foo bar" in Telegram (or Discord)
4. CommandRouter:
   ├── Parses: command="/mycmd", args=["foo", "bar"]
   ├── Plugin registry: "/mycmd" → cmd_mycmd handler
   ├── Calls: await cmd_mycmd(["foo", "bar"], msg)
   └── Returns response to user
5. User sees result — no LLM involved
```

---

## Architecture

See also: [ADR-010 — External tool integration](architecture/adr/010-external-tool-integration-pattern.mdx)

```
Built-in commands          Plugin commands             CLI-backed commands
───────────────────        ────────────────────        ─────────────────────
/help   → _help()          /echo  → cmd_echo()         /voice → voicecli
/stop   → pool.cancel()    /invite → cmd_invite()
/clear  → _cmd_clear()     /join   → cmd_join()
/config → _cmd_config()    /svc    → cmd_svc()
```

Two access paths for the same tools:
1. **Command router** (user types `/voice`) → direct CLI subprocess, no LLM
2. **Agent with Bash tool** (user asks "say hello") → LLM decides to call voicecli
