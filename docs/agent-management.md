# Agent Management

Agents are stored in **`~/.lyra/config.db`** (SQLite). TOML files are seed sources only — run `lyra agent init` to import them into the DB before use.

## Database Tables

| Table | Purpose |
|-------|---------|
| `agents` | Agent configurations (24 columns) |
| `bot_agent_map` | Maps `(platform, bot_id)` → `agent_name` |
| `agent_runtime_state` | Runtime status (idle/active/error, pool_count) |

## TOML Search Locations

Precedence (later overrides earlier):

1. `src/lyra/agents/` — bundled system defaults
2. `~/.lyra/agents/` — user-level overrides (machine-specific, gitignored)

Override via `LYRA_VAULT_DIR` env var: `$LYRA_VAULT_DIR/agents/`.

## CLI Commands

```bash
# Seeding & sync
lyra agent init                     # import TOMLs → DB (skip existing)
lyra agent init --force             # overwrite existing rows

# Listing & inspection
lyra agent list                     # DB agents with status + bot assignments
lyra agent list --agents-dir PATH   # list TOML files instead
lyra agent show <name>              # full config dump from DB

# Editing (DB-only, no TOML sync)
lyra agent edit <name>              # interactive field editor
lyra agent patch <name> --json '{"model": "claude-opus-4-6"}'

# Creation & deletion
lyra agent create                   # interactive wizard → TOML file
lyra agent delete <name>            # remove from DB (refuses if bots assigned)
lyra agent delete <name> --yes      # skip confirmation

# Bot assignment
lyra agent assign <agent> --platform telegram --bot <bot_id>
lyra agent unassign --platform telegram --bot <bot_id>

# Validation & refinement
lyra agent validate <name>          # check backend, model, JSON fields
lyra agent refine <name>            # LLM-guided profile refinement
```

## Validation Rules

- **Name**: `[a-zA-Z0-9_-]+`
- **Backend**: `claude-cli` | `ollama` | `litellm`
- **Model**: non-empty string
- **JSON fields**: `tools_json`, `plugins_json`, `permissions_json` must be valid JSON arrays; `workspaces_json`, `commands_json` must be valid objects
- **Smart routing**: `enabled=true` is deprecated (no backend supports it)

## Workflows

### Create a new agent

```bash
lyra agent create           # wizard prompts → ~/.lyra/agents/<name>.toml
lyra agent init             # import to DB
lyra agent validate <name>  # verify config
```

### Edit existing agent

```bash
lyra agent edit <name>      # interactive DB edit
# OR
lyra agent patch <name> --json '{"model": "claude-sonnet-4-5"}'
```

TOML edits require `lyra agent init --force` + restart to take effect.

### Assign bot to agent

```bash
lyra agent assign researcher --platform telegram --bot 123456
lyra agent list              # shows assignment
```

## Workspaces & cwd

| Field | Scope | Location |
|-------|-------|----------|
| `cwd` | Process spawn | `config.toml [defaults]` (NOT in agent TOML) |
| `workspaces` | Per-pool override | Agent DB row (`workspaces_json`) |

Workspaces: `/workspace <key>` switches pool's cwd for the session.

## DB Schema Reference

**agents table** (24 columns):

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
| `persona_json` | TEXT | NULL |
| `voice_json` | TEXT | NULL |
| `fallback_language` | TEXT | `'en'` |
| `patterns_json` | TEXT | NULL |
| `passthroughs_json` | TEXT | NULL |
| `show_tool_recap` | INTEGER | 1 |
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
