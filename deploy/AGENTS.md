# AGENTS.md — deploy/

## Scope

Container deployment artifacts for factory production (Podman Quadlet reference implementation).
Subdirs: `quadlet/` | `nats/` | `scripts/` | `lib/` | `factory-gh/` | `systemd/` | `omp/` (omp runtime config → factory LiteLLM, #1811 — see `omp/README.md`) | `omp-base/` (omp binary carrier image → `ghcr.io/roxabi/factory-omp-base`, #1810 — see `omp-base/README.md`)

¬docker, ¬docker-compose for prod. Runtime stack: **Podman 5.x (native on Ubuntu 26.04 LTS)
+ Quadlet generators + systemd user units**.

factory is the **reference implementation** for the Roxabi Quadlet pattern.
Cross-repo adoption checklist → `docs/ops/container-publishing.md § Cross-repo adoption`.

---

## Unit naming convention

Authoritative unit manifest: `deploy/quadlet.toml`.
Pattern: `factory-<component>.container` → `ContainerName=factory-<component>` → `factory-<component>.service`.
Telegram and discord units are rendered from `.container.tmpl` at deploy time (bot-token injection).
Network: all units attach to `roxabi.network` (defined in `quadlet/roxabi.network`).

---

## NATS server

`factory-nats.container` is part of the deploy bundle — the sole NATS server on the production hub host.
The host `nats.service` is retired (big-bang consolidation). Hub and adapters declare
`After=factory-nats.service` / `Requires=factory-nats.service` so systemd boots NATS first.

NATS config: `nats/nats-container.conf` (bind-mounted read-only).
Auth credentials: `auth.conf` delivered as an **inline bind mount** (`Volume=%h/.roxabi/factory/nkeys/auth.conf:/etc/nats/nkeys/auth.conf:ro,z`) — NOT a Podman secret. See ADR-085. Private NKey seeds remain `type=mount` Podman secrets per ADR-054.
NATS version: pinned by digest in `factory-nats.container` — ¬autoupdate, bump manually.

---

## Image lifecycle (CI → GHCR → Quadlet)

Full pattern → `docs/ops/container-publishing.md`

| Phase | Image tag | `AutoUpdate=` |
|---|---|---|
| Staging validation | `ghcr.io/roxabi/factory:staging` | `registry` |
| Post-release prod pin | `ghcr.io/roxabi/factory:X.Y.Z` | none (edit manually) |

CI: push to `staging` → `publish.yml` → `ghcr.io/roxabi/factory:staging`.
Prod pull: `podman-auto-update.timer` fires every 5 min, checks digest, restarts on change.
Rollback: edit `Image=` to previous semver tag → `systemctl --user daemon-reload` → restart.

**Schema-floor bumps** (wire-protocol change): stop auto-update timer, restart hub + telegram
+ discord atomically, re-enable timer. `factory-clipool` excluded (not a RenderEvent receiver).

---

## Provisioning

`provision.sh` — production host post-install script. Run once per machine, or after a full wipe.

```bash
curl -fsSL https://raw.githubusercontent.com/Roxabi/roxabi-factory/staging/deploy/provision.sh | bash
```

Who runs it: operator — ¬automated, ¬CI. Idempotent for most steps.
`quadlet-install-verify.sh` — smoke-check that all Quadlet units loaded cleanly after
`systemctl --user daemon-reload`.

Secrets bootstrap: `make quadlet-secrets-install` (installs Podman secrets from host key files).
BotStore bootstrap: `make quadlet-install` runs `factory bot init` as its first step (seeds
`~/.roxabi/factory/config.db` from `config.toml` — idempotent, skip-existing). Required since #1416.
Operator scripts: `scripts/rotate-claude-oauth.sh`, `scripts/rotate-gh-key.sh` — run manually
on rotation events.

---

## Atomic deploy — `make converge`

`make converge` (→ `deploy/converge.sh`) is the **atomic, idempotent, change-gated** local
deploy verb on the production host. It reconciles the running system with the desired state declared in
`staging` (factory + optionally voiceCLI) without operator intervention.

### Properties

| Property | Mechanism |
|---|---|
| **Change-gated** | Computes a convergence fingerprint (`git HEAD` + rendered Quadlet unit checksums + `auth.conf` SHA). If the current state matches the last recorded stamp (`~/.roxabi/factory/.converge-stamp`), the script exits immediately with `Already converged — nothing to do.` |
| **Idempotent** | Running `make converge` twice on an unchanged tree is a no-op. Individual steps (git pull, `make quadlet-install`, `factory-acl genkeys`, secret install, restarts) are each idempotent or guarded. |
| **Atomic** | A `flock` file lock (`/run/user/<uid>/factory-deploy.lock`) prevents concurrent converges. If the lock is held, the second invocation exits 0 silently. The full sequence (pull → install → regen auth → secrets → restart NATS → restart clients) is executed as a single critical section. |

### Convergence sequence

1. **Change-gate** — skip if already converged.
2. **Pull** — `git pull origin staging` in `~/projects/roxabi-factory` (and `~/projects/voiceCLI` if present).
3. **Install Quadlet units** — `make quadlet-install NO_RESTART=1` (renders units, copies to `~/.config/containers/systemd`, `daemon-reload`, seeds BotStore).
4. **Regenerate auth.conf** — `factory-acl genkeys --regen-authconf` (renders `nkeys/` → `auth.conf`).
5. **Install secrets** — `make quadlet-secrets-install` (recreates Podman secrets from host key files; `factory-nats-auth` is no longer a secret — `auth.conf` is now an inline bind mount per ADR-085).
6. **Restart NATS** — operator-path choices for targeted operations:
   - **Pure identity add** (`make nats-add-identity`): atomic write to `auth.conf` on host → `systemctl --user reload factory-nats` (fires `ExecReload=` → `podman kill --signal=HUP factory-nats`). Zero client restarts, zero dropped connections.
   - **ACL permission change** (`make nats-regen-authconf`): atomic write to `auth.conf` on host → `systemctl --user restart factory-nats` (required per #1390 — stale-subject-auth risk on ACL changes). Waits for `is-active`.
   Converge always **restarts** factory-nats on any drift (auth → factory-nats only; structural → factory-nats + clients) — it never reloads, because it cannot prove a change is a pure identity-add (#1390). Clients reconnect automatically via `allow_reconnect`.
7. **Restart factory clients** — `factory-hub`, `factory-telegram`, `factory-discord`, `factory-clipool`, `factory-turn-writer`, `factory-gh-helper`, `factory-blobstore`, `factory-omp` (unconditional `systemctl restart` — also starts units that were inactive; any failure aborts the converge). Restarted only on **structural** drift. On **auth-only** drift, converge restarts factory-nats alone; clients reconnect via `allow_reconnect` without explicit restart.
8. **Restart voiceCLI** — `voicecli-tts`, `voicecli-stt` (if voiceCLI directory exists).
9. **Record stamp** — writes the new convergence fingerprint to `~/.roxabi/factory/.converge-stamp`.

### Trigger wiring

Three systemd user timers drive convergence **automatically**:

| Timer | Period | Service | Role |
|---|---|---|---|
| `podman-auto-update.timer` | `*:0/5` (5 min) | `podman-auto-update.service` | Host-static apt unit (installed by `provision.sh`/`install.sh`); drop-in sets `OnCalendar=*:0/5`. Polls GHCR digests for containers labelled `io.containers.autoupdate=registry`; pulls and restarts on new digest. |
| `factory-quadlet-sync.timer` | `*:0/5` (5 min) | `factory-quadlet-sync.service` | Pulls `origin/staging` for roxabi-factory. If `deploy/quadlet/**`, Makefile, or `tools/render_quadlet.py` changed, runs `make quadlet-install` (conditional, no full converge). |
| `factory-post-autoupdate.timer` | `*:2/5` (5 min, offset +2 min — #1751) | `factory-post-autoupdate.service` | Checks whether `podman-auto-update` has pulled a new image digest. On digest change, triggers the full `make converge` sequence (including auth.conf regen + secret refresh + restarts). Fires 2 min after `factory-quadlet-sync` so the `.converge-stamp` short-circuit in `converge.sh` deduplicates the two converge runs when both are triggered on the same staging merge. |

`podman-auto-update` handles **image pulls** (CI-driven, registry-labelled containers).
`factory-quadlet-sync` handles **unit/template changes** (code-driven); fires at `*:0/5`.
`factory-post-autoupdate` handles **post-pull convergence** (restarts + auth.conf regen); fires at `*:2/5` (2 min later) so the stamp short-circuit prevents a redundant converge when quadlet-sync already ran.
All three are required for fully hands-off deploys.

### Failure notification path

`factory-quadlet-sync.service` and `factory-post-autoupdate.service` both declare:

```ini
OnFailure=factory-deploy-failure.service
```

`factory-deploy-failure.service` is a `Type=oneshot` unit that logs a structured error message
to the systemd journal via `systemd-cat` (tag `factory-deploy-failure`, priority `err`).
Monitor: `journalctl --user -t factory-deploy-failure -f`

### Manual usage

```bash
# Full atomic converge (operator-initiated)
make converge

# Check convergence state without changing anything (prints none|auth|structural)
bash -c 'source deploy/lib/deploy-common.sh; _classify_drift "$(read_convergence_state)" "$(compute_convergence_state)"'
```

---

## Hardening invariants (∀ `.container` file)

`NoNewPrivileges=true` | `ReadOnly=true` | `DropCapability=all`
`UserNS=keep-id:uid=1500,gid=1500` for factory units (container UID 1500)
Secrets via `type=mount` (tmpfs) — ¬env vars, ¬volume wrappers for credentials.
Operational consequence: `type=mount` secrets are bound at container init — `--replace` updates the store but the in-container tmpfs file is stale. ACL permission changes require container restart (not HUP) to refresh (#1390). See [`docs/ops/nats-authconf-update.md`](../docs/ops/nats-authconf-update.md).
**Carve-out (ADR-085):** `auth.conf` (the public ACL bundle — `U…` nkeys + permission blocks, no private seeds) is delivered as an **inline bind mount**, not a `type=mount` secret. This allows live SIGHUP reload for pure identity-add operations without client restarts. Private NKey seed files (e.g. `factory-nats-hub.seed`) remain `type=mount` per ADR-054 D5.
¬inline `#` comments after `Volume=` values — Quadlet passes them to Podman as mount options.

### Secret naming convention

NATS-related secrets use hyphens (`factory-nats-<role>`) — this predates the underscore
convention and is preserved for NATS NKey compatibility. Non-NATS secrets (bearer tokens,
API keys) use underscores (`factory_<service>_<purpose>`, e.g. `factory_blobstore_token`).
Mixing styles is intentional and tracked; do not "normalize" without coordinating
with the operator (Mickael).

Bot per-platform secrets follow the hyphen convention:

| Secret name | In-container target | Mode | Notes |
|---|---|---|---|
| `factory-bot-<platform>-<bot_id>` | `bot_token-<bot_id>` | 0400 | Bot token; always emitted per bot |
| `factory-bot-<platform>-<bot_id>-webhook` | `bot_webhook-<bot_id>` | 0400 | Telegram webhook secret; only emitted when `[[auth.<platform>_bots]].webhook_enabled = true` |

### Known residual risk — blobstore PublishPort Tailscale fallback (#1330)

`factory-blobstore.container` binds PublishPort to `${TAILSCALE_IPV4}:8449:8449` (resolved at
provision time via `tailscale ip -4 | head -1`). If `TAILSCALE_IPV4` is unset or `tailscale0`
is absent at container start, Podman falls back to `0.0.0.0:8449` (LAN-exposed). The bearer
token (`factory_blobstore_token`) is then the **sole** auth boundary. Accepted for V8; Phase 2
(network policy / per-identity tokens) will address this systematically.

The `ExecStartPre=` guard strips all whitespace before the `-n` test (POSIX `tr -d`) so
empty, unset, **and whitespace-only** values are all rejected at the systemd layer (#1368).
Prior to #1368, `[ -n "   " ]` was TRUE in POSIX sh — a whitespace-only value passed the
guard and Podman's downstream parse error provided fail-closed behaviour by accident, not
by design. The guard is now the authoritative rejection point.

### Known residual risk — clipool `core.hooksPath` override (tracked #1245)

The clipool unit sets `core.hooksPath = /opt/factory-gh/hooks` via `GIT_CONFIG_GLOBAL`
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
- ADR-055 (supersedes archived ADR-054) — UserNS + secret delivery decisions
