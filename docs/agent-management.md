# Agent Management

Agents are stored in **`~/.lyra/config.db`** (SQLite). TOML files are seed sources only — run `lyra agent init` to import them into the DB before use.

## Database Tables

| Table | Purpose |
|-------|---------|
| `agents` | Agent configurations (24 columns — see `effort` below) |
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
lyra agent patch <name> --effort high   # set extended-thinking effort
lyra agent patch <name> --effort none   # disable extended thinking (store NULL)

# Creation & deletion
lyra agent create                   # interactive wizard → TOML file
lyra agent create <name> --backend claude-cli --model <model> [--effort medium]
                                    # non-interactive: creates directly in DB
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
- **Backend**: `claude-cli` | `nats`
- **Model**: non-empty string
- **JSON fields**: `tools_json`, `plugins_json`, `permissions_json` must be valid JSON arrays; `workspaces_json`, `commands_json` must be valid objects
- **Smart routing**: `enabled=true` is deprecated (no backend supports it)
- **Effort**: `low` | `medium` | `high` | `xhigh` | `max` | `none` (NULL). Controls Anthropic extended-thinking token budget. `none` (or absent) = thinking disabled. New agents default to `medium` via `lyra agent create`; existing agents keep `NULL` after migration. Changing `effort` on a live agent requires pool restart to take effect.

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

## Deprecated alias

`lyra-agent` is a deprecated entry point. Invoking it prints:

```
Warning: lyra-agent is deprecated, use 'lyra agent ...' instead.
```

Use `lyra agent <subcommand>` instead.

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
  `prepare-commit-msg` hook (`deploy/lyra-gh/hooks/prepare-commit-msg`) whenever
  `LYRA_SESSION_ID` and `LYRA_AGENT` are present in the subprocess environment.

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
| `agent_name` present, `agent_email` absent | **Trailers-only mode**: `LYRA_AGENT` + `LYRA_SESSION_ID` injected; hook appends trailers; committer stays template. |
| `agent_name` + `agent_email` both present | **Full mode**: all four vars injected; committer identity AND trailers carry attribution. |

Trailers-only mode is the **production steady state today** — `AgentRow` does not yet
model an `email` field, so the hub publishes `agent_name` only. This is not a degraded
fallback: both `Lyra-Session-Id` and `Lyra-Agent` are present in every commit, which
satisfies the attribution goal for reviewers and audit tooling.

### Push identity is unchanged

`git push` authentication is owned by `git-credential-lyra-gh` (GitHub App). Setting
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
