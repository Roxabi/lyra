# factory — — Deployment & Operations

> Last updated: 2026-07-02

## Overview

Production on M₁ runs the active Quadlet fleet on `roxabi.network`, communicating over NATS.
`docs/architecture/CURRENT.generated.md` (Process Topology) is the generated SSoT enumerating
every declared component; the active-vs-disabled split comes from the `disabled = true` flags in
`deploy/quadlet.toml` (the Langfuse observability stack and `factory-otel-collector` ship
disabled). Derive any count from those two files rather than hand-maintaining it here. The core
message path is the hub, the telegram/discord adapters
and the clipool worker (diagram + table below); the full active set also includes
`factory-dashboard`, `factory-socialmedia-adapter`, `factory-ingress`, `factory-cloudflared`,
the `factory-loki` / `factory-promtail` / `factory-otel` observability units, and the llmCLI
cloud gateway. The cloud gateway — `factory-litellm` (LiteLLM proxy) plus the
`llmcli-xai-forwarder` / `llmcli-fw-forwarder` relays — is vendored from Roxabi/llmCLI:
deployment (host placement, converge lifecycle, digest pin) is owned in this repo because the
always-on M₁ hub must answer cloud LLM traffic 24/7, while code and image stay with llmCLI
(built and published by llmCLI CI, pinned by digest — not auto-updated). Local GPU inference
remains in Roxabi/llmCLI on the M₂ worker.

```
┌──────────────┐  NATS inbound   ┌─────────────┐  NATS cmd    ┌──────────────┐
│   Adapters   │────────────────→│     Hub      │────────────→│   CliPool    │
│  TG / DC     │←────────────────│             │←────────────│  (Claude)    │
└──────────────┘  NATS outbound  └──────┬──────┘  NATS reply  └──────┬───────┘
                                        │
                    + NATS bus + gh-helper + turn-writer + blobstore + omp
```

Core message-path and base-infrastructure units:

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

> Deployment-level summary. Bus/routing detail is owned by [messaging.md](messaging.md); identity/authorization detail by [security-routing.md](security-routing.md).

| Responsibility | Mechanism |
|---|---|
| Receive platform messages | NATS `factory.inbound.<platform>.<bot_id>` |
| Identity + authorization | Ban list via `Authenticator`; `agent_grants` is the sole access SSoT (ADR-090) |
| Rate limiting | Per-user throttle (middleware stage 4) |
| STT | Audio → text (middleware stage 5) |
| Routing | `(platform, bot_id, scope_id)` → agent binding |
| Pool lifecycle | Create, resume, TTL eviction, session flush |
| Session mapping | `lyra_session_id → cli_session_id` in `turns.db` |
| Command dispatch | `/slash` commands |
| Dispatch to CliPool | Pass message + resume UUID |
| Dispatch responses | NATS `factory.outbound.<platform>.<bot_id>` |

### Middleware pipeline (in order)

10 stages: trace → platform validation → identity resolution → rate limit → STT → binding resolution → agent authorization (ADR-090) → message prep → command dispatch → submit to CliPool.

→ See [security-routing.md](security-routing.md) for the identity/authorization stages; the assembled order lives in `src/factory/core/hub/middleware/middleware.py`.

---

## Adapters — platform bridges

> Deployment-level summary. Adapter pipeline detail (stages, media, audio) is owned by [adapters.md](adapters.md).

Discord writes `discord.db` (thread ownership + session cache) from its private volume. Telegram has no local store.

| Responsibility | Detail |
|---|---|
| Platform auth | HMAC webhook (Telegram) / gateway token (Discord) |
| Normalize | Platform event → `InboundMessage(trust=PUBLIC)` |
| Publish inbound | → NATS `factory.inbound.<platform>.<bot_id>` |
| Receive outbound | ← NATS `factory.outbound.<platform>.<bot_id>` → platform API |
| Thread tracking | `discord.db` (Discord only) |

> Adapters **must never** gate access — transport auth only, always send `PUBLIC`
> (C3 pattern). Access enforcement is exclusively Hub-side: ban list + `agent_grants`
> (ADR-090); the legacy trust-gating stages were removed (#2121).

---

## CliPool — Claude subprocess runner

> Deployment-level summary. Worker lifecycle and tooling detail is owned by [workers-tooling.md](workers-tooling.md).

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

All access enforcement is Hub-side. Adapters are untrusted normalizers.
→ See `security-routing.md` for trust levels, Authenticator design, GuardChain, and NATS infra hardening.

| Layer | Mechanism | Location |
|---|---|---|
| Transport auth | Telegram HMAC webhook secret; Discord gateway token | Adapter container |
| Access enforcement | C3 — adapters always send PUBLIC; hub applies the ban list (`Authenticator`) and `agent_grants` (ADR-090) | Hub middleware |
| `auth.db` | Identity grants, ban list, cross-platform aliases | Hub container (`~/.roxabi/factory/auth.db`) |
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
| `factory.jobs.claude` | Hub → CliPool | Submit turn + resume UUID |
| `factory.clipool.heartbeat` | CliPool → Hub | Periodic worker health announcements |
| `factory.clipool.control` | Hub → CliPool | Control commands (reset, drain) |

---

## Migration Delta

| | Before | Status |
|---|---|---|
| Hub ↔ Adapter | Already NATS (3-process mode) | Same, containerized |
| Hub ↔ CliPool | In-process (stdio, method calls) | ✅ Done (#941) — NATS protocol (`factory.jobs.claude` / `factory.clipool.heartbeat`) |
| DBs | All in `~/.roxabi/factory/` on one host | Split across volumes per container |
| Session resume | In-process `_resume_session_ids` dict | Hub sends UUID over NATS |

---

## Quadlet ecosystem (ADR-055)

> ADR-055 absorbs ADR-053 (deployment topology), ADR-054 (credential store + UID rework), ADR-056 (container publishing workflow), ADR-068 (SELinux `:z` Quadlet bind-volume policy).

### 7 cross-project conventions

Seven design questions deferred from ADR-053 are closed here. Each applies to all roxabi projects on M₁ adopting Quadlet. → ADR-055

- **D1 — Image registry namespace:** Project-named, no `roxabi-` prefix. CI/prod images go to `ghcr.io/roxabi/<project>`; local dev builds use `localhost/<project>-<service>:dev`. The `roxabi-` prefix is reserved for genuinely shared SDKs (e.g. `roxabi-nats`), not per-project container images.
- **D2 — NATS topology (historical):** the phased plan — per-project NATS containers on incrementing ports during migration, consolidating at Phase 4 — never happened as phased. Reality: a single big-bang consolidation put every bus participant on the one `factory-nats` container (port 4222) on `roxabi.network`; the legacy host `nats.service` was retired and `factory-nats` is the sole NATS server on the hub host.
- **D3 — Podman network:** NATS-bus participants share `roxabi.network` (in effect today); HTTP-only projects (forge, intel, idna, live) use isolated per-project networks. Topology follows communication intent.
- **D4 — Env file path:** `~/.<project>/env/<service>.env` per project. factory uses `~/.roxabi/factory/env/hub.env`; voiceCLI uses `~/.voicecli/env/tts.env`. Centralizing under `~/.roxabi/env/` is deferred — the per-project runtime root is already established.
- **D5 — Deploy script:** Shared shell library at `deploy/lib/deploy-common.sh` (change-gate, drift classification, host-role guards). Superseded in practice by the self-converging production host: the `factory-quadlet-sync` timer pulls `origin/staging` and runs `make converge` on every HEAD advance (fetch-retry hardened against network flaps, #2118); the `factory-post-autoupdate` timer polls GHCR image digests and converges on drift; `podman-auto-update` runs staggered as a catch-up net. Converge's change-gate classifies the drift and scopes the restarts: docs/tests/CI-only commits are code-only and skip the fleet restart, auth-only drift restarts `factory-nats` alone, and runtime-path changes are structural and run the full converge (#2126). `make converge` remains the manual path on the hub host.
- **D6 — Upgrade coordination:** Independent releases by default; batch coordination only for shared-infra breaking changes (NATS auth.conf change, the NATS single-server consolidation — now done, `roxabi.network` rename, breaking NATS contract version bump per ADR-049).
- **D7 — Shared infra home:** `roxabi-factory` repo. NATS config, auth.conf, nkey issuance, Quadlet patterns, and deploy scripts live here because the factory project (formerly Lyra) created the patterns. No `roxabi-infra` repo will be created.

### Container publishing workflow

CI builds container images and pushes them to GHCR via `.github/workflows/publish.yml` (bake pipeline). Publishing is CI-gated and post-merge only: `publish.yml` fires via `workflow_run` after CI succeeds on a push to `staging` (release tags publish directly), and PRs get a build-only Dockerfile check in CI instead (#2047). Images are content-addressed: provenance/SBOM attestations and the baked build-revision were dropped, so a commit that leaves image content unchanged does not move the pushed digest (#2120) — the converge code-only skip depends on this. Key invariants resolved in ADR-056: actions are SHA-pinned (no floating action tags), semver parsing strips the `factory/` tag prefix, `FACTORY_IMAGE` (local build) is separated from `GHCR_IMAGE` (registry name). Production hosts pull from GHCR via `podman auto-update` — images are never built on the production host. → ADR-056

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
- Deploys are role-guarded: `make converge` and `make quadlet-install` refuse to run on hosts lacking the `factory-hub` role (#2117)
- Credentials use `type=mount` Podman secrets (nkey seeds, NATS auth tokens) — **exception:** `auth.conf` (public ACL bundle, `U…` keys only, no private seeds) is delivered as an inline bind mount for live SIGHUP reload; private NKey seed files remain `type=mount` per ADR-054 D5 (ADR-085)
- All Quadlet bind-mount volumes carry `:z` for portability
- All containers use `UserNS=keep-id:uid=1500,gid=1500` (host UID 1000)
- Deploy coordination is independent per project; batch only for shared-infra breaking changes (e.g. NATS auth.conf changes)
- The roxabi-factory repo is the ecosystem infra home — no `roxabi-infra` repo

### See also

- CliPool OAuth token (`type=env` exception) → `workers-tooling.md` (ADR-071)
- Security audit infra → `security-routing.md` (ADR-057)
- Cross-project NATS contracts → `contracts.md`
