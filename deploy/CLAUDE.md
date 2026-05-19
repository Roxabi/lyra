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
Operator scripts: `scripts/rotate-claude-oauth.sh`, `scripts/rotate-gh-key.sh` — run manually
on rotation events.

---

## Hardening invariants (∀ `.container` file)

`NoNewPrivileges=true` | `ReadOnly=true` | `DropCapability=all`
`UserNS=keep-id:uid=1500,gid=1500` for lyra units (UID 1500 = `lyra`)
Secrets via `type=mount` (tmpfs) — ¬env vars, ¬volume wrappers for credentials.
¬inline `#` comments after `Volume=` values — Quadlet passes them to Podman as mount options.

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
