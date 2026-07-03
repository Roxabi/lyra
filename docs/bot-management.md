# Bot Management

Bots are stored in **`~/.roxabi/factory/config.db`** (SQLite, table `bots`). TOML files are seed sources only — run `factory bot init` to import them into the DB before use.

## Database Tables

| Table | Purpose |
|-------|---------|
| `bots` | Bot adapter/runtime config — see schema below (SSoT `src/factory/core/agent/schema/bot_schema.py`) |
| `bot_agent_map` | Maps `(platform, bot_id)` → `agent_name` |

## Bot Configuration

Bots are configured in `~/.roxabi/factory/config.toml` (`[[telegram.bots]]`, `[[discord.bots]]`, `[[auth.telegram_bots]]`, `[[auth.discord_bots]]`). The hub reads bot metadata from `BotStore` (table `bots`), not from `config.toml` directly. Standalone adapters read a **read-only mirror** from JetStream KV (`factory-state`, keys `roster.telegram` / `roster.discord`) — they never open `config.db`.

```bash
# Seeding (required before first hub boot since #1416)
factory bot init                     # import config.toml → BotStore (skip existing)
factory bot init --force             # overwrite existing rows

# Secret management
factory bot secret install <platform> <bot_id>   # create Podman secret for bot token
factory bot secret install <platform> <bot_id>-webhook
```

**Rule:** `factory bot init` is idempotent. Run it after every `config.toml` edit that changes bot definitions.

When `NATS_URL` is set (typical on the hub host), `factory bot init` also **dual-writes** the adapter-facing roster projection into `factory-state` KV (`roster.telegram`, `roster.discord`). NATS failures are logged as warnings and do not fail init — the hub republishes the full roster on its next boot anyway.

## Deprecation Timeline

As of this release, the four TOML bot sections are **deprecated and seed-only**. Runtime roster sources:

| Consumer | Source | Notes |
|---|---|---|
| Hub, Quadlet render, CLI | `BotStore` (`~/.roxabi/factory/config.db`) | Write SSoT on the hub host |
| Standalone adapters | `factory-state` KV (`roster.<platform>`) | Read mirror; hub publishes on boot |

| Deprecated section | Replacement |
|---|---|
| `[[telegram.bots]]` | BotStore via `factory bot init` |
| `[[discord.bots]]` | BotStore via `factory bot init` |
| `[[auth.telegram_bots]]` | BotStore via `factory bot init` |
| `[[auth.discord_bots]]` | BotStore via `factory bot init` |

At runtime, presence of any of these sections logs a one-time `DeprecationWarning`.

**Migration path:**

1. Run `factory bot init` to seed BotStore from your existing TOML sections.
2. Verify with `factory agent telegram list` / `factory agent discord list`.
3. Remove the four deprecated sections from `config.toml`.

**Removal schedule:** The four sections will be removed in the `next major` release (`v1.0.0`; current is `0.2.1`). Until then they remain parsable and are consumed only by `factory bot init`.

## CLI Commands (per platform)

Bot management commands are grouped under `factory agent <platform>` (`telegram` or `discord`). Each platform exposes the same 9 verbs.

```bash
# Listing & inspection
factory agent telegram list                     # all Telegram bots in DB
factory agent discord list                      # all Discord bots in DB
factory agent telegram show <bot_id>            # full bot record
factory agent discord show <bot_id>             # full bot record

# Creation & editing
factory agent telegram add <bot_id> --agent foo --webhook-enabled
factory agent telegram edit <bot_id>            # interactive field editor
factory agent telegram patch <bot_id> --webhook-enabled true
factory agent telegram patch <bot_id> --agent foo
factory agent telegram patch <bot_id> --public-bot "@handle"   # ADR-090 deny pointer
factory agent telegram remove <bot_id>          # delete + cascade bot_agent_map cleanup
factory agent telegram remove <bot_id> --yes    # skip confirmation

# Agent assignment
factory agent telegram assign <bot_id> --agent foo
factory agent telegram unassign <bot_id>        # revert to empty agent

# Validation
factory agent telegram validate <bot_id>        # check agent exists + Podman secret present
```

**Per-user trust/authorization is not a bot column** — ADR-090 moved it into the `agent_grants` table (`auth.db`), managed via `factory agent grant` / `factory agent revoke` / `factory agent auth list`. The `bots` table carries adapter/runtime config only. `public_bot` is a deny-pointer handle, not a trust level.

**Patchable fields:** `agent`, `webhook_enabled`, `auto_thread`, `thread_hot_hours`, `public_bot`. Typer rejects unknown flags (`--webhook-enabel` → shell error).

## DB Schema Reference

**bots table** — DDL SSoT: `src/factory/core/agent/schema/bot_schema.py` (`_CREATE_BOTS`). The four trust columns (`default_trust`, `owner_users`, `trusted_users`, `trusted_roles`) were removed by ADR-090 — authorization now lives in `agent_grants`.

| Column | Type | Default |
|--------|------|---------|
| `platform` | TEXT NOT NULL (PK part) | — |
| `bot_id` | TEXT NOT NULL (PK part) | — |
| `agent` | TEXT NOT NULL | — |
| `webhook_enabled` | INTEGER NOT NULL | `0` |
| `auto_thread` | INTEGER NOT NULL | `0` |
| `thread_hot_hours` | INTEGER NOT NULL | `24` |
| `updated_at` | TEXT | — |
| `public_bot` | TEXT | — |

## Workflows

### First-time deploy

```bash
# 1. Seed BotStore from config.toml (idempotent — skips existing rows)
#    Dual-writes roster.* to KV when NATS_URL is set
factory bot init

# 2. Render adapter templates + daemon-reload (no config.db mounts on adapters)
make quadlet-install

# 3. Start hub before adapters — hub publishes roster.* before hub.ready
make factory reload   # or: systemctl --user start factory-hub, then adapters
```

Deploy **hub first** (or at least concurrently with adapters on a fresh KV). Adapter-only rollout without a hub publish leaves `roster.*` absent and adapters exit fatally at boot.

### Add a new bot

```bash
factory agent discord add newbot --agent foo
factory bot secret install discord newbot
make quadlet-install
```

### Update a bot

```bash
factory agent telegram patch main --webhook-enabled true
make quadlet-install   # restart adapter so Quadlet picks up Secret= changes
```

### Remove a bot

```bash
factory agent discord remove oldbot
make quadlet-install   # re-renders Quadlet without the removed bot's Secret= line
```

## Migration runbook

Existing deployments that already have bots in `config.toml`:

```bash
factory bot init
```

This is a **no-op** if rows already exist (idempotent skip). No data loss. Run once per host after upgrading to the BotStore-based flow (#1416).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `factory agent <platform> show <bot_id>` returns "not found" | Bot not seeded | `factory bot init` |
| `factory agent <platform> validate <bot_id>` fails on secret | Podman secret missing | `factory bot secret install <platform> <bot_id>` |
| `factory agent <platform> patch` fails with "no fields provided" | All flag values are `None` | Provide at least one `--field value` |
| Adapter fails to start after adding bot | Quadlet not re-rendered | `make quadlet-install` (re-renders templates + restarts adapter) |
| Adapter exits: `roster missing or invalid in factory-state KV` | Hub not running, or hub booted before BotStore was seeded | `factory bot init` on hub host, restart hub, then adapters |
| `factory bot init` reports 0 seeded, N skipped | Rows already exist | Use `--force` to overwrite, or this is expected |
