# CLAUDE.md — deploy/

## Scope

Container deployment artifacts for Lyra on prod (`roxabituwer`, M₁).
Subdirs: `quadlet/` | `nats/` | `scripts/` | `lib/` | `lyra-gh/`

¬docker, ¬docker-compose for prod. Runtime stack: **Podman 5.x (native on Ubuntu 26.04 LTS)
+ Quadlet generators + systemd user units**.

Lyra is the **reference implementation** for the Roxabi Quadlet pattern.
Cross-repo adoption checklist → `docs/ops/container-publishing.md § Cross-repo adoption`.

---

## Unit naming convention

| Unit file | Container name | Service unit |
|---|---|---|
| `quadlet/lyra-hub.container` | `lyra-hub` | `lyra-hub.service` |
| `quadlet/lyra-telegram.container` | `lyra-telegram` | `lyra-telegram.service` |
| `quadlet/lyra-discord.container` | `lyra-discord` | `lyra-discord.service` |
| `quadlet/lyra-clipool.container` | `lyra-clipool` | `lyra-clipool.service` |
| `quadlet/lyra-nats.container` | `lyra-nats` | `lyra-nats.service` |
| `quadlet/lyra-gh-helper.container` | `lyra-gh-helper` | `lyra-gh-helper.service` |
| `quadlet/lyra-blobstore.container` | `lyra-blobstore` | `lyra-blobstore.service` |

Pattern: `lyra-<component>.container` → `ContainerName=lyra-<component>`.
Network: all units attach to `roxabi.network` (defined in `quadlet/roxabi.network`).

---

## NATS server

`lyra-nats.container` is part of the deploy bundle — it is the sole NATS server on M₁.
The host `nats.service` is retired (big-bang consolidation). Hub and adapters declare
`After=lyra-nats.service` / `Requires=lyra-nats.service` so systemd boots NATS first.

NATS config: `nats/nats-container.conf` (bind-mounted read-only).
Auth credentials: Podman secret `lyra-nats-auth` (type=mount, tmpfs-backed).
NATS version: pinned by digest in `lyra-nats.container` — ¬autoupdate, bump manually.

---

## Image lifecycle (CI → GHCR → Quadlet)

Full pattern → `docs/ops/container-publishing.md`

| Phase | Image tag | `AutoUpdate=` |
|---|---|---|
| Staging validation | `ghcr.io/roxabi/lyra:staging` | `registry` |
| Post-release prod pin | `ghcr.io/roxabi/lyra:X.Y.Z` | none (edit manually) |

CI: push to `staging` → `publish.yml` → `ghcr.io/roxabi/lyra:staging`.
Prod pull: `podman-auto-update.timer` fires every 5 min, checks digest, restarts on change.
Rollback: edit `Image=` to previous semver tag → `systemctl --user daemon-reload` → restart.

**Schema-floor bumps** (wire-protocol change): stop auto-update timer, restart hub + telegram
+ discord atomically, re-enable timer. `lyra-clipool` excluded (not a RenderEvent receiver).

---

## Provisioning

`provision.sh` — M₁ post-install script. Run once per machine, or after a full wipe.

```bash
curl -fsSL https://raw.githubusercontent.com/Roxabi/lyra/staging/deploy/provision.sh | bash
```

Who runs it: operator (Mickael) — ¬automated, ¬CI. Idempotent for most steps.
`quadlet-install-verify.sh` — smoke-check that all Quadlet units loaded cleanly after
`systemctl --user daemon-reload`.

Secrets bootstrap: `make quadlet-secrets-install` (installs Podman secrets from host key files).
BotStore bootstrap: `make quadlet-install` runs `lyra bot init` as its first step (seeds
`~/.lyra/config.db` from `config.toml` — idempotent, skip-existing). Required since #1416.
Operator scripts: `scripts/rotate-claude-oauth.sh`, `scripts/rotate-gh-key.sh` — run manually
on rotation events.

---

## Hardening invariants (∀ `.container` file)

`NoNewPrivileges=true` | `ReadOnly=true` | `DropCapability=all`
`UserNS=keep-id:uid=1500,gid=1500` for lyra units (UID 1500 = `lyra`)
Secrets via `type=mount` (tmpfs) — ¬env vars, ¬volume wrappers for credentials.
Operational consequence: `type=mount` secrets are bound at container init — `--replace` updates the store but the in-container tmpfs file is stale. ACL/secret changes require container restart (not HUP) to refresh. See [`docs/ops/nats-authconf-update.md`](../docs/ops/nats-authconf-update.md).
¬inline `#` comments after `Volume=` values — Quadlet passes them to Podman as mount options.

### Secret naming convention

NATS-related secrets use hyphens (`lyra-nats-<role>`) — this predates the underscore
convention and is preserved for NATS NKey compatibility. Non-NATS secrets (bearer tokens,
API keys) use underscores (`lyra_<service>_<purpose>`, e.g. `lyra_blobstore_token`).
Mixing styles is intentional and tracked; do not "normalize" without coordinating
with the operator (Mickael).

Bot per-platform secrets follow the hyphen convention:

| Secret name | In-container target | Mode | Notes |
|---|---|---|---|
| `lyra-bot-<platform>-<bot_id>` | `bot_token-<bot_id>` | 0400 | Bot token; always emitted per bot |
| `lyra-bot-<platform>-<bot_id>-webhook` | `bot_webhook-<bot_id>` | 0400 | Telegram webhook secret; only emitted when `[[auth.<platform>_bots]].webhook_enabled = true` |

### Known residual risk — blobstore PublishPort Tailscale fallback (#1330)

`lyra-blobstore.container` binds PublishPort to `${TAILSCALE_IPV4}:8449:8449` (resolved at
provision time via `tailscale ip -4 | head -1`). If `TAILSCALE_IPV4` is unset or `tailscale0`
is absent at container start, Podman falls back to `0.0.0.0:8449` (LAN-exposed). The bearer
token (`lyra_blobstore_token`) is then the **sole** auth boundary. Accepted for V8; Phase 2
(network policy / per-identity tokens) will address this systematically.

The `ExecStartPre=` guard strips all whitespace before the `-n` test (POSIX `tr -d`) so
empty, unset, **and whitespace-only** values are all rejected at the systemd layer (#1368).
Prior to #1368, `[ -n "   " ]` was TRUE in POSIX sh — a whitespace-only value passed the
guard and Podman's downstream parse error provided fail-closed behaviour by accident, not
by design. The guard is now the authoritative rejection point.

### Known residual risk — clipool `core.hooksPath` override (tracked #1245)

The clipool unit sets `core.hooksPath = /opt/lyra-gh/hooks` via `GIT_CONFIG_GLOBAL`
so the image-baked `prepare-commit-msg` hook fires on every commit. The workspace
volume is mounted RW; a malicious subprocess (uid 1500) could write a per-repo
`.git/config` containing its own `[core] hooksPath = …` that **overrides** the
global setting at the per-repo layer. Within the single-tenant container threat
model — the subprocess is already trusted to execute arbitrary code under
`DropCapability=all` + `ReadOnly=true` — this is **accepted residual risk**.
The follow-up (#1245) tracks switching to `GIT_CONFIG_SYSTEM` (or `GIT_CONFIG_COUNT`)
so the hooksPath becomes process-immutable.

---

## Cross-references

- `docs/ops/container-publishing.md` — full CI → GHCR → Quadlet pattern + auto-update
- `docs/ARCHITECTURE.md` — hub-spoke topology
- ADR-054 — UserNS + secret delivery decisions
- Issue #929 — `podman auto-update` adoption
- Issue #652 — container hardening
