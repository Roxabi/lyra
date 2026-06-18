# AGENTS.md — factory-send plugin

## What this plugin is

`factory-send` is a **Claude Code plugin** (external to factory core) that lets Claude push
messages, images, and voice notes to users via running factory bots — without waiting for
a user to speak first.

## Transport: direct API, not NATS

This plugin does **not** route through the factory hub or NATS. It calls platform APIs
directly:

- Telegram → `https://api.telegram.org/bot{token}/send{Message,Photo,Voice}`
- Discord  → `https://discord.com/api/v10/channels/{id}/messages`

There is no `factory.outbound.*` NATS subject involved. The skill reads credentials
locally and makes HTTP calls inline.

## Auth / identity model

Tokens are stored encrypted in `~/.roxabi/factory/config.db` (`bot_secrets` table), encrypted
with a Fernet key at `~/.roxabi/factory/keyring.key`. The skill decrypts at call time, holds the
token in a local variable only, and never prints or persists it. Populated by
`factory bot secret install` (creates Podman secret) and read by the skill from `config.db`.

Target identity (who to send to) is resolved from `~/.roxabi/factory/turns.db` — the plugin
queries recent turns to surface known `chat_id` (Telegram) or `channel_id`/`thread_id`
(Discord), then asks the user to confirm if ambiguous.

## Platform support matrix

| Platform  | text | image (file) | image (URL) | voice/audio |
|-----------|------|-------------|-------------|-------------|
| Telegram  | yes  | yes         | yes         | yes (.ogg)  |
| Discord   | yes  | yes         | no          | no          |

## Constraints (baked into the skill)

- Telegram: bot cannot initiate with a user who has never messaged it first.
- Discord: bot must have `Send Messages` permission in the target channel.
- Both: `factory bot secret install` must have run to populate `bot_secrets`.

## Skill entry point

`skills/send/SKILL.md` — single skill, 4-step flow:
resolve args → find target ID → send → confirm.

## factory cross-references

- `~/.roxabi/factory/turns.db` — turn history (target ID discovery)
- `~/.roxabi/factory/config.db` — bot secrets
- `~/.roxabi/factory/keyring.key` — encryption key
- factory adapters (`src/factory/adapters/`) own the inbound side; this plugin owns
  the proactive outbound side independently.
