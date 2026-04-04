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
| `lyra agent init` | Seed DB from TOML files (first-time setup) |
| `lyra agent init --force` | Overwrite existing DB rows with TOML |
| `lyra agent list` | List all discovered agents |
| `lyra agent show <name>` | Full config for one agent |
| `lyra agent create` | Interactively create a new agent TOML |
| `lyra agent edit <name>` | Edit an agent interactively in DB |
| `lyra agent validate [<name>]` | Validate agent TOML config(s) |
| `lyra agent assign <name> --platform <p> --bot <id>` | Assign agent to a bot |
| `lyra agent unassign <name> --platform <p> --bot <id>` | Remove agent-bot assignment |
| `lyra agent delete <name>` | Delete agent (refuses if bot still assigned) |
| `lyra agent patch <name> --field <f> --value <v>` | Update a single agent field |
| `lyra agent refine <name>` | Interactively refine agent persona/voice |

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

Lyra intercepts messages starting with `/` or `!` before they reach the LLM agent. Commands are routed to built-in handlers or plugins — fast, deterministic, zero token cost.

```
User → /echo hi  → Telegram → Hub → CommandRouter → builtin   → Response → Telegram → User
User → hello     → Telegram → Hub → (not a command) → Agent (LLM) → Response → Telegram → User
User → !unknown  → Telegram → Hub → CommandRouter → no handler → Agent (LLM) → Response → Telegram → User
```

### The two prefixes: `/` vs `!`

| Prefix | Known command | Unknown command |
|--------|---------------|-----------------|
| `/`    | Dispatched to handler | Returns "Unknown command" error |
| `!`    | Dispatched to handler | **Falls through to LLM** |

`!` is a "soft command" prefix. Use it when you want command-like syntax but are OK with the LLM receiving it if no handler matches. It is not a separate command system — it shares the same registry. Only the error behavior differs.

### Platform-native command menus

Lyra registers commands with each platform's native menu for autocomplete and discoverability. All command **routing** remains application-level text parsing — native registration is cosmetic only.

**Telegram** — registered via `lyra setup commands` (one-time CLI, not at every startup):

```bash
lyra setup commands              # register for all configured Telegram bots
lyra setup commands -c prod.toml # use a specific config file
```

Re-run after adding/removing commands to update the menu. Admin-only commands (`/circuit`, `/routing`, `/config`, `/svc`, `/invite`, `/unpair`) are excluded from the public menu.

**Discord** — voice commands (`/join`, `/leave`) are registered as native slash commands via `app_commands`. Synced automatically at bot connect (guild-scoped, instant propagation). Text-prefix fallback (`!join`, `!leave`) continues to work.

If `tree.sync()` fails at connect, the bot logs a warning and continues with text-prefix commands only.

### Full routing order

```
Incoming message
  │
  ├─ Adapter pre-routing (Discord only)
  │    └─ !join / !leave → voice channel join/leave (guild-only, before mention filter)
  │
  ├─ CommandParser: detect / or ! prefix → CommandContext
  │
  ├─ CommandRouter.prepare(): bare URL? → rewrite to /vault-add <url> (patterns.toml)
  │
  ├─ CommandRouter.dispatch():
  │    1. /clear, /new   → workspace_commands.cmd_clear()
  │    2. /folder, /cd   → workspace_commands.cmd_folder()
  │    3. /workspace     → workspace_commands.cmd_workspace() (list or switch)
  │    4. Builtin?       → _dispatch_builtin(): /help, /stop, /config, /circuit, /routing, /voice, /text
  │    5. Session?       → registered session command handlers (deprecated, see processor_registry)
  │    6. Plugin?        → any [[commands]] from enabled plugin.toml files
  │    7. ! prefix + unknown → return None → fall through to LLM
  │    8. / prefix + unknown → return "Unknown command" error
  │
  └─ pool.submit(msg) → LLM agent (if no command matched, or fallthrough)
```

### Where should a new command go?

| Scenario | Layer |
|----------|-------|
| Affects session/bot state (clear history, change workspace) | Hub builtin |
| Isolated LLM task that must not pollute chat history | Session command |
| Feature that can be toggled per-agent | Plugin |
| Needs LLM reasoning but user wants `/`-like entry syntax | Passthrough (register in `agent.py`) |
| Discord voice channel control (guild-only, stateful) | Adapter-level pre-routing |
| Everything else | Plain text → LLM |

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
| `/voice <text>` | Send voice reply — routes through LLM then TTS (OGG/Opus) | `voicecli` |
| `/invite` | Generate a pairing code (admin-only) | — (plugin) |
| `/join` | Redeem a pairing code | — (plugin) |
| `/unpair` | Revoke a user's paired session (admin-only) | — (plugin) |
| `/link` | Link your identity across platforms | — (plugin) |
| `/unlink` | Remove your cross-platform identity link | — (plugin) |
| `/vault-add <url>` | Scrape URL → LLM summary → save to vault | `web-intel:scrape`, `vault` |
| `/add-vault <note>` | Save a direct text note to the vault (no LLM call) | `vault` |
| `/explain <url>` | Scrape URL → plain-language explanation | `web-intel:scrape` |
| `/summarize <url>` | Scrape URL → bullet-point summary | `web-intel:scrape` |
| `/search <query>` | Full-text search over vault | `vault` (plugin) |
| `<url>` (bare) | Auto-rewritten to `/vault-add <url>` (configured in `src/lyra/config/patterns.toml`) | — |
| `/workspace <name> [question]` | Switch working directory to named workspace | — (builtin) |
| `/workspace ls` | List configured workspaces | — (builtin) |

---

## Processor Commands

Processor commands (`/vault-add`, `/explain`, `/summarize`) hook into the normal pool flow as pre/post processors. Unlike the old session-command pattern, their responses land in pool conversation history, enabling follow-up questions.

### `/vault-add <url>` — Save to vault

```
/vault-add https://example.com/article
```

Pipeline: **scrape** (`web-intel:scrape`) → **LLM summary** (title, paragraph summary, 3-5 tags) → **vault write** (`vault add`).

Returns the title + summary. If scraping or vault CLI is unavailable, still returns the summary with a note.

### `/explain <url>` — Plain-language explanation

```
/explain https://example.com/paper
```

Pipeline: **scrape** → **LLM explanation** (plain language, suitable for chat). No vault write.

### `/summarize <url>` — Bullet-point summary

```
/summarize https://example.com/doc
```

Pipeline: **scrape** → **LLM 3-5 bullet points**. No vault write.

### Bare URL auto-rewrite

Sending a bare URL (no slash command prefix) is automatically rewritten to `/vault-add <url>`:

```
https://example.com/article   →   /vault-add https://example.com/article
```

The detection uses `CommandRouter._BARE_URL_RE` (`^https?://\S+$`). The target command is read from `src/lyra/config/patterns.toml` `[bare_url].command` — change it there to reroute bare URLs to a different command without touching Python.

### `/search <query>` — Vault full-text search

```
/search asyncio event loop
```

Runs `vault search <query>` and returns matching results. Stateless — no LLM call.

### CLI dependencies

| Command | Requires | Graceful fallback |
|---------|----------|------------------|
| `/vault-add` | `web-intel:scrape`, `vault` | LLM runs on URL string if scrape fails; vault error noted in response |
| `/explain` | `web-intel:scrape` | Explanation runs on URL string if scrape unavailable |
| `/summarize` | `web-intel:scrape` | Summary runs on URL string if scrape unavailable |
| `/search` | `vault` | Returns `"vault CLI not available."` — not fatal |

### How it works internally

Processor commands use `BaseProcessor` from `processor_registry.py` and are registered with the `@register` decorator. Each processor implements `pre()` (enriches the message before pool submission) and optionally `post()` (side effects after the LLM response). Because they run inside the normal pool flow, responses appear in conversation history and are available for follow-up questions.

```python
from lyra.core.processor_registry import BaseProcessor, register

@register("/my-cmd", description="Do something: /my-cmd <url>")
class MyCmdProcessor(BaseProcessor):
    async def pre(self, msg: InboundMessage) -> InboundMessage: ...
    async def post(self, msg: InboundMessage, response: Response) -> Response: ...
```

---

## Workspace Commands

Workspaces are named directory shortcuts defined in the agent TOML under `[workspaces]`. Each key is accessible via `/workspace <key>`, which sets the working directory for the Claude subprocess in that conversation scope.

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
Syntax: /workspace <name>
        /workspace <name> <question>
        /workspace ls
        /workspace list
        /workspace
```

- `/workspace lyra` — sets the workspace to `~/projects/lyra`, responds with a context confirmation
- `/workspace lyra what's the last commit?` — sets the workspace, then forwards the question to Claude with `cwd=~/projects/lyra`
- `/workspace ls` — lists all configured workspaces with their paths
- `/workspace` — same as `/workspace ls`

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
Lyra: ⚠ Command timed out.
      Try a shorter request or run manually.
```

> Timeout depends on LLM response time plus TTS synthesis duration — there is no fixed limit.

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

Built-in commands are implemented in `src/lyra/core/builtin_commands.py` and workspace commands in `src/lyra/core/workspace_commands.py`. Plugin commands are declared in each plugin's `plugin.toml`:

```toml
# src/lyra/commands/echo/plugin.toml

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
async def cmd_mycmd(msg: InboundMessage, pool: Pool, args: list[str]) -> Response:
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
Built-in commands              Plugin commands             CLI-backed commands        Session commands (LLM)
(builtin_commands.py)          ────────────────────        ─────────────────────      ──────────────────────
/help      → _help()           /echo  → cmd_echo()         /voice → voicecli          /vault-add → cmd_add()
/stop      → pool.cancel()     /invite → cmd_invite()                                 /explain → cmd_explain()
/clear     → _cmd_clear()      /join   → cmd_join()                                   /summarize→cmd_summarize()
/config    → _cmd_config()     /svc    → cmd_svc()                                    /search  → cmd_search()

Workspace commands
(workspace_commands.py)
/workspace → _cmd_workspace()                              /search → cmd_search()
```

Two access paths for voice:
1. **`/voice` command** (user types `/voice <text>`) → registered as passthrough in CommandRouter → rewritten with `modality="voice"` → pool/agent → LLM generates reply → TTS synthesizes to OGG/Opus → sent as native voice message (`IS_VOICE_MESSAGE` on Discord, `send_voice` on Telegram). `Response.speak=True` signals the hub to trigger TTS. It is NOT a direct CLI subprocess.
2. **Agent with Bash tool** (user asks "say hello") → LLM decides to call voicecli directly
