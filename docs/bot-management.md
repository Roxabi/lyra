# Bot Management

Bots are stored in **`~/.lyra/config.db`** (SQLite, table `bots`). TOML files are seed sources only — run `lyra bot init` to import them into the DB before use.

## Database Tables

| Table | Purpose |
|-------|---------|
| `bots` | Bot configurations (11 columns — see schema below) |
| `bot_agent_map` | Maps `(platform, bot_id)` → `agent_name` |

## Bot Configuration

Bots are configured in `~/.lyra/config.toml` (`[[telegram.bots]]`, `[[discord.bots]]`, `[[auth.telegram_bots]]`, `[[auth.discord_bots]]`). The hub reads bot metadata from `BotStore` (table `bots`), not from `config.toml` directly.

```bash
# Seeding (required before first hub boot since #1416)
lyra bot init                     # import config.toml → BotStore (skip existing)
lyra bot init --force             # overwrite existing rows

# Secret management
lyra bot secret install <platform> <bot_id>   # create Podman secret for bot token
lyra bot secret install <platform> <bot_id>-webhook
```

**Rule:** `lyra bot init` is idempotent. Run it after every `config.toml` edit that changes bot definitions.

## CLI Commands (per platform)

Bot management commands are grouped under `lyra agent <platform>` (`telegram` or `discord`). Each platform exposes the same 9 verbs.

```bash
# Listing & inspection
lyra agent telegram list                     # all Telegram bots in DB
lyra agent discord list                      # all Discord bots in DB
lyra agent telegram show <bot_id>            # full bot record
lyra agent discord show <bot_id>             # full bot record

# Creation & editing
lyra agent telegram add <bot_id> --agent foo --webhook-enabled
lyra agent telegram edit <bot_id>            # interactive field editor
lyra agent telegram patch <bot_id> --webhook-enabled true
lyra agent telegram patch <bot_id> --agent foo
lyra agent telegram patch <bot_id> --owner-users "123,456"
lyra agent telegram remove <bot_id>          # delete + cascade bot_agent_map cleanup
lyra agent telegram remove <bot_id> --yes    # skip confirmation

# Agent assignment
lyra agent telegram assign <bot_id> --agent foo
lyra agent telegram unassign <bot_id>        # revert to empty agent

# Validation
lyra agent telegram validate <bot_id>        # check agent exists, owners non-empty, secret present
```

**Valid trust levels:** `owner`, `trusted`, `public`, `blocked` (default: `blocked`).

**Patchable fields:** `agent`, `webhook_enabled`, `default_trust`, `owner_users`, `trusted_users`, `trusted_roles`, `auto_thread`, `thread_hot_hours`. Typer rejects unknown flags (`--webhook-enabel` → shell error).

## DB Schema Reference

**bots table** (11 columns):

| Column | Type | Default |
|--------|------|---------|
| `platform` | TEXT (PK part) | — |
| `bot_id` | TEXT (PK part) | — |
| `agent` | TEXT | `""` |
| `webhook_enabled` | INTEGER | `0` |
| `default_trust` | TEXT | `"blocked"` |
| `owner_users` | TEXT | `'[]'` |
| `trusted_users` | TEXT | `'[]'` |
| `trusted_roles` | TEXT | `'[]'` |
| `auto_thread` | INTEGER | `0` |
| `thread_hot_hours` | INTEGER | `24` |
| `updated_at` | TEXT | `datetime('now')` |

## Workflows

### First-time deploy

```bash
# 1. Seed BotStore from config.toml (idempotent — skips existing rows)
lyra bot init

# 2. Render adapter templates + daemon-reload
make quadlet-install
```

### Add a new bot

```bash
lyra agent discord add newbot --agent foo
lyra bot secret install discord newbot
make quadlet-install
```

### Update a bot

```bash
lyra agent telegram patch main --webhook-enabled true
make quadlet-install   # restart adapter so Quadlet picks up Secret= changes
```

### Remove a bot

```bash
lyra agent discord remove oldbot
make quadlet-install   # re-renders Quadlet without the removed bot's Secret= line
```

## Migration runbook

Existing deployments that already have bots in `config.toml`:

```bash
lyra bot init
```

This is a **no-op** if rows already exist (idempotent skip). No data loss. Run once per host after upgrading to the BotStore-based flow (#1416).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `lyra agent <platform> show <bot_id>` returns "not found" | Bot not seeded | `lyra bot init` |
| `lyra agent <platform> validate <bot_id>` fails on secret | Podman secret missing | `lyra bot secret install <platform> <bot_id>` |
| `lyra agent <platform> patch` fails with "no fields provided" | All flag values are `None` | Provide at least one `--field value` |
| Adapter fails to start after adding bot | Quadlet not re-rendered | `make quadlet-install` (re-renders templates + restarts adapter) |
| `lyra bot init` reports 0 seeded, N skipped | Rows already exist | Use `--force` to overwrite, or this is expected |
