# Lyra — Deployment & Operations

> Last updated: 2026-06-17

## Overview

Production on M₁ runs **nine Quadlet containers** on `roxabi.network`, communicating over NATS.
The core message path is four NATS-connected processes (hub, telegram adapter, discord adapter,
clipool worker); the remaining five units provide infrastructure (NATS, gh-helper, turn-writer,
blobstore, omp).

```
┌──────────────┐  NATS inbound   ┌─────────────┐  NATS cmd    ┌──────────────┐
│   Adapters   │────────────────→│     Hub      │────────────→│   CliPool    │
│  TG / DC     │←────────────────│             │←────────────│  (Claude)    │
└──────────────┘  NATS outbound  └──────┬──────┘  NATS reply  └──────┬───────┘
                                        │
                    + NATS bus + gh-helper + turn-writer + blobstore + omp
```

| Container | Role |
|---|---|
| `factory-nats` | NATS server (TLS, nkey auth, JetStream) |
| `factory-hub` | Routing, pools, memory, command dispatch |
| `factory-telegram` | Telegram adapter |
| `factory-discord` | Discord adapter |
| `factory-clipool` | CliPool NATS worker (Claude subprocesses) |
| `factory-gh-helper` | GitHub App token-mint helper |
| `factory-turn-writer` | JetStream subscriber-writer for `turns.db` |
| `factory-blobstore` | HTTP BlobStore |
| `factory-omp` | OmpWorker NATS runtime backend |

Manifest: `deploy/quadlet.toml`. Operator guide: `docs/DEPLOYMENT.md`.

---

## Hub — central brain

| Responsibility | Mechanism |
|---|---|
| Receive platform messages | NATS `factory.inbound.<platform>.<bot_id>` |
| Auth / trust resolution | C3 pattern — adapters always send PUBLIC, hub resolves |
| Rate limiting | Per-user throttle (middleware stage 4) |
| STT | Audio → text (middleware stage 5) |
| Routing | `(platform, bot_id, scope_id)` → agent binding |
| Pool lifecycle | Create, resume, TTL eviction, session flush |
| Session mapping | `lyra_session_id → cli_session_id` in `turns.db` |
| Command dispatch | `/slash` commands |
| Dispatch to CliPool | Pass message + resume UUID |
| Dispatch responses | NATS `factory.outbound.<platform>.<bot_id>` |

### Middleware pipeline (in order)

10 stages: trace → platform validation → trust resolution → trust guard → rate limit → STT → binding resolution → pool create → command dispatch → submit to CliPool.

→ See `ARCHITECTURE.md` (Inbound Message Pipeline) for the full annotated 10-stage table.

---

## Adapters — platform bridges

Discord writes `discord.db` (thread ownership + session cache) from its private volume. Telegram has no local store.

| Responsibility | Detail |
|---|---|
| Platform auth | HMAC webhook (Telegram) / gateway token (Discord) |
| Normalize | Platform event → `InboundMessage(trust=PUBLIC)` |
| Publish inbound | → NATS `factory.inbound.<platform>.<bot_id>` |
| Receive outbound | ← NATS `factory.outbound.<platform>.<bot_id>` → platform API |
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

CliPool does **not** need `~/.roxabi/factory/` — it only receives the resume UUID as a
command argument from the Hub over NATS.

---

## Security

All trust resolution is Hub-side. Adapters are untrusted normalizers.
→ See `security-routing.md` for trust levels, Authenticator design, GuardChain, and NATS infra hardening.

| Layer | Mechanism | Location |
|---|---|---|
| Transport auth | Telegram HMAC webhook secret; Discord gateway token | Adapter container |
| Trust resolution | C3 — adapters always send PUBLIC, hub resolves via Authenticator | Hub middleware stage 2–3 |
| `auth.db` | Identity grants, trust assignments, cross-platform aliases | Hub container (`~/.roxabi/factory/auth.db`) |
| Secrets | Bot tokens delivered as Podman secrets (`type=mount`) — see ADR-074 | `/run/secrets/bot_token-<bot_id>` inside adapter containers |
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

| Volume | File | Container(s) | Access | Contents |
|---|---|---|---|---|
| `factory-data.volume` | `~/.roxabi/factory/auth.db` | Hub only | rw | Auth grants, identity aliases |
| `factory-data.volume` | `~/.roxabi/factory/config.db` | Hub only | rw | Agent registry, user prefs (bot secrets removed — see ADR-074) |
| `factory-data.volume` | `~/.roxabi/factory/turns.db` | Hub (rw, via turn-writer ADR-075) | rw | Conversation turns, pool sessions, lyra→cli session map |
| `factory-data.volume` | `~/.roxabi/factory/keyring.key` | Hub only | rw | Encryption key for `config.db` sibling stores (see ADR-074) |
| `factory-discord-data.volume` | `~/.roxabi/factory/discord/discord.db` | Discord only | rw | Thread ownership store |
| `factory-data.volume` | `~/.roxabi/factory/config.toml` | Hub (inline bind, ro) | ro | Runtime config (per-bot entries) |
| inline bind | `~/.roxabi/factory/config.toml` | Telegram, Discord (inline bind, ro) | ro | Runtime config (per-bot entries) |
| `factory-jetstream.volume` | `~/.roxabi/factory/nats/jetstream` | NATS | rw | JetStream persistence |
| `~/.claude/` (inline) | `~/.claude/` | CliPool | rw | Claude session `.jsonl` files (required for `--resume`) |
| inline bind | `~/.roxabi/factory/nkeys/auth.conf` | NATS (factory-nats) | ro | Public ACL bundle (`U…` nkeys + permission blocks); inline bind mount for live SIGHUP reload (ADR-085) |
| `factory-omp-sessions.volume` | `/home/factory/.config/omp-pi` | OmpWorker | rw | omp agent.db, models.db (SQLite session state — persists across restarts, #1813) |

`factory-data.volume` is mounted **only** by `factory-hub` (#1721). Adapters use per-file inline binds for `config.toml` and the Discord-private `factory-discord-data.volume` for `discord.db`. `factory-discord-data.volume` is a named podman-managed volume (podman auto-creates it; no host bind required), mounted inside the Discord container at `/home/factory/.roxabi/factory/discord` — a separate mount point that keeps `discord.db` isolated from the hub's bind. Telegram mounts no data volume — last-session is resolved via `turns.db` (path-3, `get_last_session`). Discord resolves last-session the same way; `discord.db` is the thread-ownership store only.

---

## NATS Topics

| Topic | Direction | Purpose |
|---|---|---|
| `factory.inbound.telegram.<bot_id>` | Adapter → Hub | Telegram messages |
| `factory.inbound.discord.<bot_id>` | Adapter → Hub | Discord messages |
| `factory.outbound.telegram.<bot_id>` | Hub → Adapter | Responses to Telegram |
| `factory.outbound.discord.<bot_id>` | Hub → Adapter | Responses to Discord |
| `factory.clipool.cmd` | Hub → CliPool | Submit turn + resume UUID |
| `factory.clipool.heartbeat` | CliPool → Hub | Periodic worker health announcements |
| `factory.clipool.control` | Hub → CliPool | Control commands (reset, drain) |

---

## Migration Delta

| | Before | Status |
|---|---|---|
| Hub ↔ Adapter | Already NATS (3-process mode) | Same, containerized |
| Hub ↔ CliPool | In-process (stdio, method calls) | ✅ Done (#941) — NATS protocol (`factory.clipool.cmd` / `factory.clipool.heartbeat`) |
| DBs | All in `~/.roxabi/factory/` on one host | Split across volumes per container |
| Session resume | In-process `_resume_session_ids` dict | Hub sends UUID over NATS |

---

## Quadlet ecosystem (ADR-055)

> ADR-055 absorbs ADR-053 (deployment topology), ADR-054 (credential store + UID rework), ADR-056 (container publishing workflow), ADR-068 (SELinux `:z` Quadlet bind-volume policy).

### 7 cross-project conventions

Seven design questions deferred from ADR-053 are closed here. Each applies to all roxabi projects on M₁ adopting Quadlet. → ADR-055

- **D1 — Image registry namespace:** Project-named, no `roxabi-` prefix. CI/prod images go to `ghcr.io/roxabi/<project>`; local dev builds use `localhost/<project>-<service>:dev`. The `roxabi-` prefix is reserved for genuinely shared SDKs (e.g. `roxabi-nats`), not per-project container images.
- **D2 — NATS topology:** Per-project NATS during migration; shared NATS at Phase 4. Each project runs its own `<project>-nats.container` on an incrementing port (Lyra: 4223, voiceCLI: 4224, …) until Phase 4 consolidates onto a single `nats.container` at port 4222 on `roxabi.network`.
- **D3 — Podman network:** NATS-bus participants share `roxabi.network` at Phase 4; HTTP-only projects (forge, intel, idna, live) use isolated per-project networks. Topology follows communication intent.
- **D4 — Env file path:** `~/.<project>/env/<service>.env` per project. Lyra uses `~/.roxabi/factory/env/hub.env`; voiceCLI uses `~/.voicecli/env/tts.env`. Centralizing under `~/.roxabi/env/` is deferred — the per-project runtime root is already established.
- **D5 — Deploy script:** Shared shell library at `deploy/lib/deploy-common.sh`, installed to `~/.local/lib/roxabi/deploy-lib.sh` at bootstrap. Superseded in practice by `podman auto-update.timer` (GHCR registry auto-pull) + `make converge` as manual fallback (#1035).
- **D6 — Upgrade coordination:** Independent releases by default; batch coordination only for shared-infra breaking changes (NATS auth.conf change, Phase 4 NATS consolidation, `roxabi.network` rename, breaking NATS contract version bump per ADR-049).
- **D7 — Shared infra home:** `roxabi-factory` repo. NATS config, auth.conf, nkey issuance, Quadlet patterns, and deploy scripts live here because Lyra created the patterns. No `roxabi-infra` repo will be created.

### Container publishing workflow

CI builds container images and pushes them to GHCR via `.github/workflows/publish.yml` (bake pipeline). Key invariants resolved in ADR-056: actions are SHA-pinned (no floating action tags), semver parsing strips the `factory/` tag prefix, `FACTORY_IMAGE` (local build) is separated from `GHCR_IMAGE` (registry name). Production hosts pull from GHCR via `podman auto-update` — images are never built on the production host. → ADR-056

### Credential store

File-based credentials (nkey seeds, NATS auth tokens) are delivered as Podman secrets using `type=mount`, placing the secret at a predictable path inside the container without exposing it as an environment variable. Naming convention: `<project>-nats-<identity>` (e.g. `factory-nats-hub`). All containers use `UserNS=keep-id:uid=1500,gid=1500` so container processes run as host UID 1000 (`mickael`) — files in `~/.roxabi/factory/` are readable without `chown`. The `factory-data.volume` is a bind-mount of `%h/.roxabi/factory` (`Type=none; Device=%h/.roxabi/factory; Options=bind`), mounted **only** by `factory-hub` (#1721). The Discord-private `factory-discord-data.volume` is a **named** podman-managed volume (no host bind; podman auto-creates it), mounted only by `factory-discord` at `/home/factory/.roxabi/factory/discord` — the Discord container does not mount the hub's `factory-data.volume` parent. All data mounts use `:z`. → ADR-054

### SELinux Z-label policy

All Quadlet bind-mount volumes carry `:z` for portability. On the current production host (Ubuntu 26.04 LTS, AppArmor-only), `:z` is a no-op at runtime. On SELinux hosts, `:z` relabels the bind-mounted directory so the container process can read it — it is load-bearing. Do not drop `:z` from bind-mounts. The JetStream volume (`factory-jetstream.volume`) uses `Device=%h/.roxabi/factory/nats/jetstream` and retains `Volume=factory-jetstream.volume:/var/lib/nats/jetstream:z` in `factory-nats.container`. → ADR-068

### Historical: supervisord era (ADR-041)

> supervisord-based hub-and-spoke supervision was retired in #1036. Production is now fully Quadlet (`deploy/quadlet/*.container`, `systemctl --user`). The `supervisord.conf` + `conf.d/` pattern was removed in #886; the final programs migrated to Quadlet in #1036. See git history pre-#886 for the original supervisord layout.

---

## Autodeploy per-project manifests (ADR-043)

> ADR-043 status: Superseded — `roxabi-autodeploy` was not built; deployment is per-project Quadlet (ADR-055). The manifest design below is preserved as historical context.

ADR-043 proposed replacing the hardcoded `lyra/scripts/deploy.sh` (a 60s systemd timer that hardcoded repo paths, branch names, and restart sequences) with per-project `deploy/auto-deploy.yml` manifests discovered by a generic Python runner (`roxabi-ops/bin/autodeploy`). Each project would opt in by adding a manifest; the runner would pull, run pre-restart steps (uv sync, pytest), restart services with gate checks, and fire cross-project triggers.

The manifest schema supported `pre_restart` steps with per-step `on_fail: rollback`, named `services` with `gate:` stability checks, and a `triggers:` section for cross-project venv re-syncs. Two-phase ordering: all pulls first (failures isolated per repo), then all restarts in declared order with gates honored. The runner fixed bug M16 (fetch timeout swallowing the exit code) via `subprocess.run` with explicit returncode checks.

The proposal was superseded by the Quadlet + `podman auto-update` path before `roxabi-ops` was built. → ADR-043

### Key invariants

- All production deployment uses Quadlet (no supervisord)
- Container images come from GHCR, never built on the production host
- Credentials use `type=mount` Podman secrets (nkey seeds, NATS auth tokens) — **exception:** `auth.conf` (public ACL bundle, `U…` keys only, no private seeds) is delivered as an inline bind mount for live SIGHUP reload; private NKey seed files remain `type=mount` per ADR-054 D5 (ADR-085)
- All Quadlet bind-mount volumes carry `:z` for portability
- All containers use `UserNS=keep-id:uid=1500,gid=1500` (host UID 1000)
- Deploy coordination is independent per project; batch only for NATS auth.conf changes or Phase 4 consolidation
- Lyra repo is the ecosystem infra home — no `roxabi-infra` repo

### See also

- CliPool OAuth token (`type=env` exception) → `workers-tooling.md` (ADR-071)
- Security audit infra → `security-routing.md` (ADR-057)
- Cross-project NATS contracts → `contracts.md`
