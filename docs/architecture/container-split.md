# Lyra — Container Split Architecture

## Overview

5 containers communicating over NATS.

| Container | Process | Role |
|---|---|---|
| `lyra-hub` | `lyra hub` | Hub process, NATS-connected |
| `lyra-telegram` | `lyra adapter telegram` | Telegram platform adapter |
| `lyra-discord` | `lyra adapter discord` | Discord platform adapter |
| `lyra-clipool` | `lyra adapter clipool` | CliPool NATS worker (claude-cli subprocess pool) |
| `lyra-nats` | nats-server | NATS message broker |

```
┌──────────────┐  NATS inbound   ┌─────────────┐  NATS cmd    ┌──────────────┐
│   Adapters   │────────────────→│     Hub      │────────────→│   CliPool    │
│  TG / DC     │←────────────────│             │←────────────│  (Claude)    │
└──────────────┘  NATS outbound  └──────┬──────┘  NATS reply  └──────┬───────┘
                                        │                             │
                                   ~/.lyra/                     ~/.claude/
                                   (volume)                     (volume)
```

---

## Hub — central brain

| Responsibility | Mechanism |
|---|---|
| Receive platform messages | NATS `lyra.inbound.<platform>.<bot_id>` |
| Auth / trust resolution | C3 pattern — adapters always send PUBLIC, hub resolves |
| Rate limiting | Per-user throttle (middleware stage 4) |
| STT | Audio → text (middleware stage 5) |
| Routing | `(platform, bot_id, scope_id)` → agent binding |
| Pool lifecycle | Create, resume, TTL eviction, session flush |
| Session mapping | `lyra_session_id → cli_session_id` in `turns.db` |
| Command dispatch | `/slash` commands |
| Dispatch to CliPool | Pass message + resume UUID |
| Dispatch responses | NATS `lyra.outbound.<platform>.<bot_id>` |

### Middleware pipeline (in order)

| Stage | Middleware | Role |
|---|---|---|
| 0 | TraceMiddleware | Per-turn `trace_id` via contextvars |
| 1 | ValidatePlatformMiddleware | Drop unknown platforms |
| 2 | ResolveTrustMiddleware | Auth lookup via `Authenticator` + `AuthStore` |
| 3 | TrustGuardMiddleware | Drop BLOCKED users |
| 4 | RateLimitMiddleware | Per-user throttle |
| 5 | SttMiddleware | Audio → text |
| 6 | ResolveBindingMiddleware | Route `(platform, bot_id, scope_id)` → agent |
| 7 | CreatePoolMiddleware | Pool get-or-create, command configure |
| 8 | CommandMiddleware | `/slash` command dispatch |
| 9 | SubmitToPoolMiddleware | Session resume + submit to CliPool |

---

## Adapters — platform bridges

Both adapters write `turns.db` (conversation turns + pool sessions, held open for full lifetime). Discord also writes `discord.db` (thread ownership + session cache).

| Responsibility | Detail |
|---|---|
| Platform auth | HMAC webhook (Telegram) / gateway token (Discord) |
| Normalize | Platform event → `InboundMessage(trust=PUBLIC)` |
| Publish inbound | → NATS `lyra.inbound.<platform>.<bot_id>` |
| Receive outbound | ← NATS `lyra.outbound.<platform>.<bot_id>` → platform API |
| Thread tracking | `discord.db` (Discord only) |

> Adapters **must never** derive trust level — always send `PUBLIC`. Trust is
> resolved exclusively by the Hub (C3 pattern).

---

## CliPool — Claude subprocess runner

| Responsibility | Detail |
|---|---|
| Spawn `claude` processes | `--input-format stream-json`, optional `--resume <uuid>` |
| Stream I/O | stdin/stdout NDJSON with Claude |
| Report session ID | Claude sends `{"type":"system","subtype":"init","session_id":"..."}` → forwarded to Hub |
| Session files | `~/.claude/projects/<cwd>/<uuid>.jsonl` |

### Session ID flow

```
First run:
  Hub spawns claude (no --resume)
  Claude stdout → {"type": "system", "subtype": "init", "session_id": "<cli_sid>"}
  Parser extracts cli_sid (cli_streaming_parser.py:53)
  Hub persists: turns.db → pool_sessions (lyra_sid → cli_sid)

Resume (restart / reply-to):
  Hub looks up cli_sid from turns.db
  Hub passes --resume <cli_sid> to CliPool
  Claude reads ~/.claude/projects/<cwd>/<cli_sid>.jsonl → continues
```

CliPool does **not** need `~/.lyra/` — it only receives the resume UUID as a
command argument from the Hub over NATS.

---

## Security

All trust resolution is Hub-side. Adapters are untrusted normalizers.

| Layer | Mechanism | Location |
|---|---|---|
| Transport auth | Telegram HMAC webhook secret; Discord gateway token | Adapter |
| Trust resolution | C3 — adapters always send PUBLIC, hub resolves via Authenticator | Hub middleware stage 2–3 |
| Trust levels | `OWNER > TRUSTED > PUBLIC > BLOCKED` | `core/auth/authenticator.py` |
| Cross-platform identity | `tg:user:X ↔ dc:user:Y` aliases | `auth.db → identity_aliases` |
| Secrets | Bot tokens AES-encrypted via `LyraKeyring` | `config.db`, key in `keyring.key` |
| Admin | `[admin].user_ids` in TOML → OWNER across all bots | Config + AuthStore |
| NATS channel | TLS + auth tokens required in production | Infrastructure |

---

## Session Continuity

```
Turn 1:      Hub spawns claude → Claude returns session_id in init envelope
             Hub stores: turns.db.pool_sessions (lyra_sid → cli_sid)

Turn N:      Hub sends cli_sid to CliPool → --resume <cli_sid>
             Claude reads ~/.claude/.../<cli_sid>.jsonl → continues

Compact:     At 80% of 200k token window:
             → partial L3 snapshot written to vault
             → history replaced with [summary] + last 10 turns

TTL evict:   Pool idle 7d (or /clear):
             → flush_session() → L3 semantic memory upserted to vault

Next session: build_system_prompt() recalls L3:
             last 5 session summaries + concept search + preferences
             injected as [MEMORY] and [PREFERENCES] blocks
```

### reply-to session routing (`message_index.db`)

Maps `(pool_id, platform_msg_id) → session_id`.  
When a user replies to an old message, Hub resolves the original session and
resumes it rather than starting fresh.

---

## Volumes

| File | Container(s) | Access | Contents |
|---|---|---|---|
| `~/.lyra/auth.db` | Hub | rw | Auth grants, identity aliases |
| `~/.lyra/config.db` | Hub, Telegram, Discord | Hub: rw · Adapters: ro (startup only) | Agent registry, bot secrets (encrypted), user prefs |
| `~/.lyra/turns.db` | Hub, Telegram, Discord | rw | Conversation turns, pool sessions, lyra→cli session map |
| `~/.lyra/message_index.db` | Hub | rw | reply-to session routing index |
| `~/.lyra/keyring.key` | Hub, Telegram, Discord | Hub: rw · Adapters: ro (startup only) | Encryption key for `config.db` secrets |
| `~/.lyra/discord.db` | Discord | rw | Thread ownership + session cache |
| `~/.claude/` | CliPool | rw | Claude session `.jsonl` files (required for `--resume`) |

Adapter mounts are per-file inline binds (not the full `lyra-data.volume`) — adapters never touch `auth.db` or `message_index.db`.

> **Credential boundary:** `~/.claude/` (Claude session files + credentials) is mounted exclusively in `lyra-clipool`. The Hub no longer requires access to Claude credentials — it only sends UUIDs over NATS and receives streaming events back.

---

## NATS Topics

| Subject | Direction | Semantics |
|---|---|---|
| `lyra.inbound.telegram.<bot_id>` | Adapter → Hub | Telegram messages |
| `lyra.inbound.discord.<bot_id>` | Adapter → Hub | Discord messages |
| `lyra.outbound.telegram.<bot_id>` | Hub → Adapter | Responses to Telegram |
| `lyra.outbound.discord.<bot_id>` | Hub → Adapter | Responses to Discord |
| `lyra.clipool.cmd` | Hub → CliPool | Send a claude-cli command; reply-to is ephemeral inbox |
| `lyra.clipool.control` | Hub → CliPool | Control operations (reset/resume_and_reset/switch_cwd) |
| `lyra.clipool.heartbeat` | CliPool → Hub | Worker liveness every 30s |

**CliPool subject design notes:**

- All CliPool subjects are **flat** (not per-pool-id) — `pool_id` is routed via payload
- Reply-to for `lyra.clipool.cmd` is an **ephemeral inbox** (`_INBOX.<random>`) — streaming chunks arrive on the inbox, not a fixed subject
- Workers subscribe to `lyra.clipool.cmd` with queue group `clipool-workers` for future multi-instance scaling

---

## Migration Delta

| | Today | Target |
|---|---|---|
| Hub ↔ Adapter | Already NATS (3-process mode) | Same, containerized |
| Hub ↔ CliPool | **In-process** (stdio, method calls) | **New: NATS protocol** |
| DBs | All in `~/.lyra/` on one host | Split across volumes per container |
| Session resume | In-process `_resume_session_ids` dict | Hub sends UUID over NATS |

**The hard part:** replacing the in-process `Hub → CliPool` stdio bridge with a
NATS-based protocol. All other separation already exists in 3-process mode.
