# Agent Management

Agents and bots are stored in **`~/.roxabi/factory/config.db`** (SQLite). TOML files are seed sources only — run `factory agent init` (agents) and `factory bot init` (bots) to import them into the DB before use.

## Database Tables

| Table | Purpose |
|-------|---------|
| `agents` | Agent configurations (27 columns — soul blobstore + `effort`) |
| `bot_agent_map` | Maps `(platform, bot_id)` → `agent_name` |
| `agent_runtime_state` | Runtime status (idle/active/error, pool_count) |

## Bot Configuration

Bots are configured in `~/.roxabi/factory/config.toml` (`[[telegram.bots]]`, `[[discord.bots]]`, `[[auth.telegram_bots]]`, `[[auth.discord_bots]]`). The hub reads bot metadata from `BotStore` (table `bots`), not from `config.toml` directly.

```bash
# Seeding (required before first hub boot since #1416)
factory bot init                     # import config.toml → BotStore (skip existing)
factory bot init --force             # overwrite existing rows

# Secret management
factory bot secret install <platform> <bot_id>   # create Podman secret for bot token
factory bot secret install <platform> <bot_id>-webhook
```

**Rule:** `factory bot init` is idempotent. Run it after every `config.toml` edit that changes bot definitions.

### Bot CLI management (per platform)

Bot management commands are grouped under `factory agent <platform>` (telegram or discord). Each platform exposes the same 9 verbs.

```bash
# Listing & inspection
factory agent telegram list                     # all Telegram bots in DB
factory agent discord list                      # all Discord bots in DB
factory agent telegram show <bot_id>          # full bot record

# Creation & editing
factory agent telegram add <bot_id> --agent foo --webhook-enabled
factory agent telegram edit <bot_id>            # interactive field editor
factory agent telegram patch <bot_id> --webhook-enabled true
factory agent telegram patch <bot_id> --agent foo
factory agent telegram patch <bot_id> --owner-users "123,456"
factory agent telegram remove <bot_id>          # delete + cascade bot_agent_map cleanup
factory agent telegram remove <bot_id> --yes    # skip confirmation

# Agent assignment
factory agent telegram assign <bot_id> --agent foo
factory agent telegram unassign <bot_id>        # revert to empty agent

# Validation
factory agent telegram validate <bot_id>        # check agent exists, owners non-empty, secret present
```

**Valid trust levels:** `owner`, `trusted`, `public`, `blocked` (default: `blocked`).

**Patchable fields:** `agent`, `webhook_enabled`, `default_trust`, `owner_users`, `trusted_users`, `trusted_roles`, `auto_thread`, `thread_hot_hours`. Typer rejects unknown flags (`--webhook-enabel` → shell error).

## TOML Search Locations

Precedence (later overrides earlier):

1. `src/factory/agents/` — bundled system defaults
2. `~/.roxabi/factory/agents/` — user-level overrides (machine-specific, gitignored)

Override via `ROXABI_FACTORY_DIR` env var: `$ROXABI_FACTORY_DIR/agents/`.

## Agent TOML structure

A seed TOML is one file per agent; the file name must equal the `name` field (e.g.
`lyra_default.toml`). Agent names must match `^[a-zA-Z0-9_-]+$` (validated by
`agent_seeder.py`).

```toml
[agent]
name = "lyra_default"          # unique; ^[a-zA-Z0-9_-]+$
memory_namespace = "lyra"      # memory isolation key
persona = "lyra_default"       # persona file name (without .md)
show_intermediate = true       # show ⏳ intermediate tool-use turns

[model]
backend = "claude-cli"         # one of: claude-cli | nats | omp-rpc
model = "claude-sonnet-4-6"    # model identifier passed to the backend
tools = ["Read", "Grep"]       # allowed tools (empty = backend defaults)
skip_permissions = true        # skip Claude Code permission prompts

# max_turns = 10               # cap agentic turns (omit / 0 = unlimited)

[agent.smart_routing]
enabled = false                # MUST be false — smart_routing is deprecated

[plugins]
enabled = ["echo", "search"]   # plugin names to enable for this agent

[tts]
voice = "Sohee"

[workspaces]
lyra = "~/projects/roxabi-factory"       # /workspace lyra → switches cwd
```

- **`cwd` does NOT go in agent TOML** — it is machine-specific and belongs in
  `config.toml [defaults]` (see [Workspaces & cwd](#workspaces--cwd)).
- **`smart_routing.enabled` must be `false`** — the validator rejects `true` on all
  backends.
- **`workspaces` keys must not conflict** with built-in command names — see
  `_WORKSPACE_BUILTIN_CONFLICTS` in `core/agent/agent_config.py`.

TOML files are seed sources only — the runtime reads from `config.db`, not TOML. After
editing any TOML file, run `factory agent init --force` and restart the daemon; the DB is
not updated automatically (there is no file watcher).

## Agent lifecycle

```
1. Startup:  AgentStore.connect() → factory agent init → DB seeded
2. Register: hub.register_agent(agent)
3. Message:  PoolManager.get_or_create_pool() → pool.submit(msg)
             → agent.handle(msg, pool)
             → LlmProvider.complete() or .stream()
4. Hot-reload: TOML/persona edits detected on next handle() call
```

## CLI Commands

```bash
# Seeding & sync
factory agent init                     # import TOMLs → DB (skip existing)
factory agent init --force             # overwrite existing rows

# Listing & inspection
factory agent list                     # DB agents with status + bot assignments
factory agent list --agents-dir PATH   # list TOML files instead
factory agent show <name>              # full config dump from DB

# Editing (DB-only, no TOML sync)
factory agent edit <name>              # interactive field editor
factory agent patch <name> --json '{"model": "claude-opus-4-6"}'
factory agent patch <name> --effort high   # set extended-thinking effort
factory agent patch <name> --effort none   # disable extended thinking (store NULL)

# Creation & deletion
factory agent create                   # interactive wizard → TOML file
factory agent create <name> --backend claude-cli --model <model> [--effort medium]
                                    # non-interactive: creates directly in DB
factory agent delete <name>            # remove from DB (refuses if bots assigned)
factory agent delete <name> --yes      # skip confirmation

# Bot assignment
factory agent assign <agent> --platform telegram --bot <bot_id>
factory agent unassign --platform telegram --bot <bot_id>

# Validation & refinement
factory agent validate <name>          # check backend, model, JSON fields
factory agent refine <name>            # LLM-guided profile refinement
```

## Agent soul (AgentSoul v1)

Long-form persona lives in **blobstore** as `soul.md` (markdown, five `##` sections). SQLite holds pointers and envelope metadata only:

| Column | Role |
|--------|------|
| `soul_document_blob_ref` | `sha256:…` → blob bytes |
| `soul_document_bytes` | Size metadata (cap 48 KiB at save) |
| `soul_meta_json` | Envelope: `display_name`, `tagline`, memory provision — **not** section text |
| `persona_json` | Legacy inline JSON — fallback until migration validated |

Composition is hub-only (`core/persona.py` → `compose_soul_document()`). Harnesses receive opaque `system_prompt: str`. Soul edits do not update active sessions until `/reset` or a new pool — see [persona-soul-operator.md](runbooks/persona-soul-operator.md).

**Dashboard:** `/agents` → edit harness, model, voice, soul (BFF → hub NATS RPC). **CLI:** `scripts/backfill_soul_documents.py` for one-shot migration from `persona_json`.

### Agent refiner debt (post-migration)

`factory agent refine` / `agent_refiner` still patches legacy `persona_json` inline. After soul blobstore migration this path is **invalid for soul edits** — composed prompt comes from `soul.md` via hub loader. Use dashboard `/agents` or hub `soul.put` instead. Follow-up: wire refiner to soul document flow or deprecate persona_json patches.

## Validation Rules

- **Name**: `[a-zA-Z0-9_-]+`
- **Backend**: `claude-cli` | `nats` | `omp-rpc` (`_VALID_BACKENDS`)
- **Model**: non-empty string
- **JSON fields**: `tools_json`, `plugins_json`, `permissions_json` must be valid JSON arrays; `workspaces_json`, `commands_json` must be valid objects
- **Smart routing**: `enabled=true` is deprecated (no backend supports it)
- **Effort**: `low` | `medium` | `high` | `xhigh` | `max` | `none` (NULL). Controls Anthropic extended-thinking token budget. `none` (or absent) = thinking disabled. New agents default to `medium` via `factory agent create`; existing agents keep `NULL` after migration. Changing `effort` on a live agent requires pool restart to take effect.

## Workflows

### Create a new agent

```bash
factory agent create           # wizard prompts → ~/.roxabi/factory/agents/<name>.toml
factory agent init             # import to DB
factory agent validate <name>  # verify config
```

### Edit existing agent

```bash
factory agent edit <name>      # interactive DB edit
# OR
factory agent patch <name> --json '{"model": "claude-sonnet-4-5"}'
```

TOML edits require `factory agent init --force` + restart to take effect.

### Assign bot to agent

```bash
factory agent assign researcher --platform telegram --bot 123456
factory agent list              # shows assignment
```

## Deprecated alias

`factory-agent` is a deprecated entry point. Invoking it prints:

```
Warning: factory-agent is deprecated, use 'factory agent ...' instead.
```

Use `factory agent <subcommand>` instead.

## Workspaces & cwd

| Field | Scope | Location |
|-------|-------|----------|
| `cwd` | Process spawn | `config.toml [defaults]` (NOT in agent TOML) |
| `workspaces` | Per-pool override | Agent DB row (`workspaces_json`) |

Workspaces: `/workspace <key>` switches pool's cwd for the session.

## What NOT to do (agents)

- Do NOT add store or DB logic to agent implementation files — that belongs in `core/`.
- Do NOT read TOML files at runtime from within agent classes — use `AgentStore`.
- Do NOT hardcode model names in agent classes — read from `Agent.llm_config`.
- Do NOT set `cwd` in agent TOML — it belongs in `config.toml [defaults]`.
- Do NOT enable `smart_routing` — the validator rejects it.

Plugin/command authoring patterns (structure, handler signatures, routing order,
forbidden names) live in [`standards/backend-patterns.md`](standards/backend-patterns.md)
§ Commands / Plugins.

## DB Schema Reference

**agents table** (27 columns):

| Column | Type | Default |
|--------|------|---------|
| `name` | TEXT PK | — |
| `backend` | TEXT | — |
| `model` | TEXT | — |
| `max_turns` | INTEGER | 0 (0 = unlimited) |
| `tools_json` | TEXT | `'[]'` |
| `show_intermediate` | INTEGER | 0 |
| `smart_routing_json` | TEXT | NULL |
| `plugins_json` | TEXT | `'[]'` |
| `memory_namespace` | TEXT | NULL |
| `cwd` | TEXT | NULL |
| `source` | TEXT | `'db'` |
| `skip_permissions` | INTEGER | 0 |
| `permissions_json` | TEXT | `'[]'` |
| `workspaces_json` | TEXT | NULL |
| `commands_json` | TEXT | NULL |
| `streaming` | INTEGER | 0 |
| `persona_json` | TEXT | NULL (legacy — fallback) |
| `soul_meta_json` | TEXT | NULL |
| `soul_document_blob_ref` | TEXT | NULL |
| `soul_document_bytes` | INTEGER | NULL |
| `voice_json` | TEXT | NULL |
| `fallback_language` | TEXT | `'en'` |
| `patterns_json` | TEXT | NULL |
| `passthroughs_json` | TEXT | NULL |
| `effort` | TEXT | NULL |
| `created_at` | TEXT | `datetime('now')` |
| `updated_at` | TEXT | `datetime('now')` |

**bot_agent_map table**:

| Column | Type |
|--------|------|
| `platform` | TEXT (PK part) |
| `bot_id` | TEXT (PK part) |
| `agent_name` | TEXT |
| `settings_json` | TEXT |
| `updated_at` | TEXT |

## Per-session git committer attribution (#1150)

Every commit produced inside the clipool container can be attributed back to the
originating agent and session via two independent channels:

- **Committer field** — `GIT_COMMITTER_NAME` / `GIT_COMMITTER_EMAIL` override the
  image-baked template identity on the commit object itself (full mode only).
- **Message trailers** — `Lyra-Session-Id` and `Lyra-Agent` are appended by the
  `prepare-commit-msg` hook (`deploy/factory-gh/hooks/prepare-commit-msg`) whenever
  `FACTORY_SESSION_ID` and `FACTORY_AGENT` are present in the subprocess environment.

### Querying attribution

```bash
git log -1 --format='%cn|%ce|%(trailers:key=Lyra-Session-Id,valueonly)|%(trailers:key=Lyra-Agent,valueonly)'
```

Expected output by mode:

```
# Full mode (agent_name + agent_email both present)
research-assistant|research-assistant@lyra.internal|ses_abc123|research-assistant

# Trailers-only mode (agent_name present, agent_email absent)
lyra[bot]|lyra-bot@users.noreply.github.com|ses_abc123|research-assistant
```

In trailers-only mode the committer falls back to the image-baked template identity,
but the trailers still carry the full session + agent attribution.

### Two modes

The spawn-time gate in `CliPoolWorkerMixin._spawn()` is layered:

| Condition | Behaviour |
|---|---|
| `agent_name` absent | No identity vars injected; subprocess uses the template identity entirely. |
| `agent_name` present, `agent_email` absent | **Trailers-only mode**: `FACTORY_AGENT` + `FACTORY_SESSION_ID` injected; hook appends trailers; committer stays template. |
| `agent_name` + `agent_email` both present | **Full mode**: all four vars injected; committer identity AND trailers carry attribution. |

Trailers-only mode is the **production steady state today** — `AgentRow` does not yet
model an `email` field, so the hub publishes `agent_name` only. This is not a degraded
fallback: both `Lyra-Session-Id` and `Lyra-Agent` are present in every commit, which
satisfies the attribution goal for reviewers and audit tooling.

### Push identity is unchanged

`git push` authentication is owned by `git-credential-factory-gh` (GitHub App). Setting
`GIT_COMMITTER_EMAIL` affects commit metadata only — it does not gate push authorization.
GitHub maps the push to the App's bot identity server-side regardless of what the
committer field contains.

### Forward path

When `AgentRow` gains an `email` field (tracked as #1244), the hub will publish
`agent_email` and production will flip to full mode automatically — no code change is
required in this slice.

### Out of scope

Author identity (`GIT_AUTHOR_*`) is not overridden — it stays image-baked. Only the
committer field and the two trailers carry agent provenance.
