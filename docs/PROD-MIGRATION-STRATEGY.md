---
title: "Roxabi Ecosystem: Prod Migration + Quadlet Adoption Strategy"
status: Phase 1 Complete (#611)
authors: claude-architect
date: 2026-04-23
scope: ecosystem
---

# Roxabi Ecosystem — Prod Migration + Quadlet Adoption Strategy

M₁ = `roxabituwer` (192.168.1.16, Ubuntu 26.04 LTS, rootless Podman 5.x)

---

## 1. Current State Audit (M₁ as of 2026-04-23)

This table is derived from `~/projects/conf.d/*.conf`, `~/projects/supervisord.conf`,
`~/.config/systemd/user/`, and `~/projects/2ndBrain/supervisor/conf.d/`. The supervisord
instance at `~/projects/` is the hub — all projects drop conf files into `conf.d/` and
register `.mk` delegation targets.

### 1a. Host systemd services (system-level)

| Unit | Type | Managed by | Port | Notes |
|---|---|---|---|---|
| `nats.service` | system unit | systemd | 4222 (TLS) | Runs `nats-server -js -c /etc/nats/nats.conf`; nkeys at `/etc/nats/nkeys/auth.conf`; TLS certs at `/etc/nats/certs/`; reloads via `systemctl reload nats.service` |

### 1b. Host systemd user services (`~/.config/systemd/user/`)

| Unit | Enabled | Purpose |
|---|---|---|
| `lyra.service` | yes (default.target.wants) | Starts Lyra supervisord (hub + adapters) via `~/projects/scripts/start.sh --all`; `Requires=nats.service` |
| `lyra-monitor.service` + `.timer` | yes | One-shot health monitor — `python -m lyra.monitoring`; not a persistent daemon |
| `lyra-stack.service` | yes (default.target.wants) | Symlink present in default.target.wants — **file content not found on this machine**; likely the future Quadlet target group; investigate on M₁ before Phase 1 |
| `leviathan-vol-sync.service` | yes | Out of scope — not a Roxabi project daemon |
| `stfolder-guard.service` + `.timer` | yes | Out of scope — not a Roxabi project daemon |

### 1c. Supervisord programs (`~/projects/conf.d/`)

| Program name | Project | Command | Port bound | State dir | NATS? | autostart |
|---|---|---|---|---|---|---|
| `lyra-hub` | `lyra` | `lyra hub` (via supervisord) | 8443 (health) | `~/.lyra/` | yes (4222, TLS, nkey) | false |
| `lyra-telegram` | `lyra` | `lyra adapter telegram` | — | `~/.lyra/` | yes | false |
| `lyra-discord` | `lyra` | `lyra adapter discord` | — | `~/.lyra/` | yes | false |
| `voicecli_tts` | `voiceCLI` | `run_voicecli.sh tts` | — | `~/.local/state/voicecli/` | yes (4222, TLS, nkey `voice-tts.seed`) | false |
| `voicecli_stt` | `voiceCLI` | `run_voicecli.sh stt` | — | `~/.local/state/voicecli/` | yes (4222, TLS, nkey `voice-stt.seed`) | false |
| `imagecli_gen` | `imageCLI` | `supervisor/scripts/run_gen.sh` | — | `~/.local/state/imagecli/` | unknown | false |
| `forge` | `roxabi-forge` | `~/.roxabi/forge/run.sh` | 8080 | `~/.roxabi/forge/` | no | false |
| `idna` | `roxabi-idna` | `uv run idna_server.py` | 8082 | `~/.roxabi/` or project dir | no | false |
| `roxabi-intel` | `roxabi-intel` | `run.sh` | 8082 (INTEL_PORT env) | `~/.roxabi/intel/` | no | false |
| `live` | `roxabi-live` | `roxabi-live` (uvicorn) | 8000 | `~/.local/state/roxabi-live/` | no | false |
| `litellm` | `litellm` (infra) | `litellm --config ~/.litellm/config.yaml --port 4000` | 4000 | `~/.litellm/` | no | false |
| `livebox_monitor` | `monitoring` (infra) | `livebox-monitor.sh` | — | `~/projects/monitoring/logs/` | no | false |

**Note — `lyra_tts.conf` and `lyra_stt.conf`:** Conf files for these names exist in the
filesystem listing but their contents returned "No such file" — the files are stubs or the
names shown were leftover directory entries. The active voice programs are `voicecli_tts`
and `voicecli_stt` (confirmed from file contents). Verify on M₁.

**Note — `knowledge_bot` (2ndBrain):** Lives in a separate supervisord instance at
`~/projects/2ndBrain/supervisor/supervisord.conf` — not the hub supervisord. It runs as
`autostart=true`. No entry in `~/projects/conf.d/`. Port: none (Telegram polling only).
State: `~/.local/state/2ndbrain/`. NATS: not observed in its conf.

**Note — `monitoring` (uptime-kuma):** `~/projects/monitoring/docker-compose.yml` defines
an `uptime-kuma` container on port 3001 via Docker Compose (`louislam/uptime-kuma:2.2.1`).
This is already container-based. Migration status: unknown — it uses Docker Compose, not
Podman Quadlet. It is not registered in the supervisord hub.

**Note — `gitnexus`:** Listed in `CLAUDE.md` as auto-discovered infra. No conf.d entry
found, no standalone project directory found — may be a Claude Code plugin
(`roxabi-plugins/plugins/gitnexus`), not a persistent daemon. **Investigate on M₁:
does `gitnexus` run a background process?**

**Note — `llmCLI`:** Has `supervisor/conf.d/llmcli_serve.conf` in its own project
directory but is **not registered in `~/projects/conf.d/`** — not currently active in the
hub supervisord. Verify whether it is deployed.

**Note — `roxabi-vault`:** Has a NATS subscriber (`NatsSubscriber`) and an MCP server
interface. No supervisord conf found in the hub or project directory. It appears to be used
as a library / CLI tool, not a persistent daemon. Confirm.

### 1d. Conflict: `idna` and `roxabi-intel` share port 8082

Both `idna.conf` (idna default port 8082) and `roxabi-intel` (INTEL_PORT=8082) use 8082.
One or both may not run simultaneously, or INTEL_PORT is overridden. **Verify on M₁.**

---

## 2. Target State (Post Full Migration)

### 2a. Host systemd (system-level) — endgame

| Unit | Status | Notes |
|---|---|---|
| `nats.service` | **Retired** | Replaced by shared `nats.container` managed as a Quadlet unit |

### 2b. Quadlet-managed units per project (endgame topology)

| Project | Container name(s) | Image | Network | Port(s) | Volume(s) | NATS |
|---|---|---|---|---|---|---|
| `lyra` | `lyra-hub`, `lyra-telegram`, `lyra-discord`, `lyra-nats` | `localhost/lyra:latest` | `lyra.network` (or shared `roxabi.network`) | 8443 (hub health) | `lyra-data`, `lyra-logs`, `lyra-config`, `lyra-nkey-*` | via `lyra-nats` container (shared or per-project — ADR TBD) |
| `voiceCLI` | `voicecli-tts`, `voicecli-stt` | `localhost/voicecli:latest` | TBD (own or shared) | GPU device passthrough | `voicecli-data`, `voicecli-nkey-*` | shared NATS (post-Phase 4) |
| `imageCLI` | `imagecli-gen` | `localhost/imagecli:latest` | TBD | GPU device passthrough | `imagecli-data` | unknown; investigate |
| `llmCLI` | `llmcli-serve` | `localhost/llmcli:latest` | TBD | TBD | `llmcli-data` | unknown; investigate |
| `2ndBrain` | `knowledge-bot` | `localhost/2ndbrain:latest` | TBD | — | `2ndbrain-data` | none observed |
| `litellm` | `litellm` | `localhost/litellm:latest` or upstream | TBD | 4000 | `litellm-config` | no |
| `monitoring` | `uptime-kuma` | `louislam/uptime-kuma:2.2.1` (already containerized) | TBD | 3001 | `uptime-kuma` | no |
| `roxabi-intel` | `roxabi-intel` | `localhost/roxabi-intel:latest` | TBD | 8082 | `intel-data` | no |
| `roxabi-forge` | `forge` | `localhost/roxabi-forge:latest` | TBD | 8080 | `forge-data` | no |
| `roxabi-live` | `roxabi-live` | `localhost/roxabi-live:latest` | TBD | 8000 | `live-data` | no |
| `roxabi-idna` | `idna` | `localhost/roxabi-idna:latest` | TBD | 8082 | `idna-data` | no |
| `roxabi-vault` | if daemonized: `roxabi-vault` | TBD | TBD | — | `vault-data` | yes (NATS subscriber) |

### 2c. Shared infrastructure (endgame)

| Resource | Decision needed | Recommendation |
|---|---|---|
| NATS | shared single `nats.container` vs per-project | One shared `roxabi-nats.container` on `roxabi.network`, port 4222; managed outside any single project repo — triggers cross-project ADR |
| Podman network | one shared `roxabi.network` vs per-project | Shared network for projects that communicate (Lyra + vault + voiceCLI); isolated networks for stateless HTTP servers (forge, intel, idna) |
| Image registry prefix | `localhost/<project>:latest` vs `localhost/roxabi-<project>:latest` | Adopt `localhost/roxabi-<project>:latest` for clarity; Lyra can stay `localhost/lyra:latest` per ADR-053 or rename at boilerplate lift |
| Env file location | `~/.<project>/env/<service>.env` vs `~/.roxabi/env/<project>/<service>.env` | Per-project convention (`~/.<project>/env/`) is consistent with `~/.lyra/` precedent; centralize under `~/.roxabi/env/` only if a future secrets manager requires it |
| Deploy script template | per-project `scripts/deploy-quadlet.sh` vs shared template | Shared template in `roxabi-boilerplate` (ADR-053 follow-up 9); each project overrides project-specific variables |
| Supervisord | retired | Retire when last project migrates; no maintenance investment in new features |

---

## 3. Migration Phases

### Phase 0: Lyra Quadlet dry run on M₂ (local dev machine)

**Scope:** Lyra only — run the Quadlet stack on the dev machine before touching prod.

**Entry criteria:**
- ADR-053 accepted
- All five follow-up implementation issues (ADR-053 §Consequences, items 1–5) are closed or have open PRs ready
- `scripts/deploy-quadlet.sh` + all `.container`/`.volume`/`.network` files are in `staging` branch

**Steps:**
1. On M₂: `make quadlet-install` — installs units to `~/.config/containers/systemd/`
2. Start stack: `systemctl --user start nats.service lyra-hub.service lyra-telegram.service lyra-discord.service`
3. Verify `systemctl --user status` shows all four units `active (running)`
4. Send a test message through each adapter; confirm hub responds
5. Run `scripts/deploy-quadlet.sh` end-to-end on M₂ to validate the deploy loop
6. Verify image digest capture and pre-restart verify (ADR-053 Decision 3) fires correctly
7. Confirm `~/.lyra/env/` layout replaces `.env.*` in working tree (ADR-053 Decision 5)

**Validation:**
- All four Quadlet services reach `active` state
- `journalctl --user -u lyra-hub` shows no startup errors
- Test message gets a response from at least one adapter
- `~/.lyra/.image-digest` exists after deploy run

**Rollback:**
```
systemctl --user stop lyra-hub lyra-telegram lyra-discord nats
systemctl --user disable lyra-hub lyra-telegram lyra-discord nats
rm -f ~/.config/containers/systemd/lyra-*.{container,volume,network} ~/.config/containers/systemd/nats.container
systemctl --user daemon-reload
```

**Exit criteria:**
- Dry run on M₂ runs cleanly for at least one full deploy cycle
- No regressions in adapter behavior
- `staging` branch contains all ADR-053 follow-ups merged

---

### Phase 1: Lyra Quadlet prod cutover on M₁ ✅ (#611)

**Scope:** Lyra only. Host `nats.service` stays on 4222. Supervisord stays. Lyra Quadlet NATS on 4223 (coexistence topology per ADR-053 §7).

**Entry criteria:**
- Phase 0 complete (M₂ dry run passed)
- M₁ has Podman 5.x from apt (ships with Ubuntu 26.04 LTS — no manual install needed)
- Linger enabled: `loginctl enable-linger $USER`
- `~/.lyra/env/hub.env` created with `LYRA_HEALTH_SECRET` set (copy from `deploy/quadlet/hub.env.example`); tokens already present in `~/.lyra/config.db` (verified via `lyra secrets list` or equivalent — since #417, tokens live in `config.db`, not env files)
- nkeys at `~/.lyra/nkeys/` are intact (no regen needed; Quadlet volumes mount same files)
- `lyra-stack.service` in `~/.config/systemd/user/default.target.wants/` investigated — confirm if it is a manual stub or a conflict with `lyra.service`

**Steps:**
1. Stop supervisord Lyra services: `cd ~/projects/lyra && make lyra stop`
2. Disable supervisord auto-start: `systemctl --user disable --now lyra.service`
3. On M₂: `make build && make push` — build image and stream to M₁
4. On M₁: `cd ~/projects/lyra && make quadlet-install`
5. Verify units generated: `systemctl --user list-units 'lyra-*' nats.service`
6. Remove `LYRA_SUPERVISORCTL_PATH` from `~/projects/lyra/.env` so Makefile dispatcher switches to systemd
7. Start Quadlet NATS first: `systemctl --user start nats.service` (this is Lyra's Quadlet NATS on 4223 — distinct from system `nats.service` on 4222)
8. Start adapters: `make lyra start`
9. Monitor for 15 minutes: `journalctl --user -u lyra-hub -f`

**Validation:**
- All four Quadlet units `active (running)`
- Bot responds to test messages on Telegram and Discord
- `systemctl --user status lyra-hub.service` shows uptime > 5 min with no restarts
- NATS coexistence verified: `ss -tlnp | grep 4222` shows system NATS; `ss -tlnp | grep 4223` shows Quadlet NATS

**Rollback:**
```
systemctl --user stop lyra-hub lyra-telegram lyra-discord nats
systemctl --user enable lyra.service
systemctl --user start lyra.service
# Re-add LYRA_SUPERVISORCTL_PATH to ~/projects/lyra/.env
```

**Cleanup after stable (>1 week):**
- Remove `~/projects/conf.d/lyra-hub.conf`, `lyra-telegram.conf`, `lyra-discord.conf` from M₁ supervisord include path
- Delete old `.env`, `.env.hub`, `.env.telegram`, `.env.discord` from `~/projects/lyra/` on M₁

**Exit criteria:**
- Lyra on Quadlet, stable for >= 1 week without manual intervention
- `lyra.service` (supervisord) fully disabled
- Monitoring shows no regression in bot response rate

---

### Phase 2: voiceCLI Quadlet — cross-project ADR trigger

**Scope:** voiceCLI (`voicecli_tts`, `voicecli_stt`). This is the first project that is not Lyra. It uses NATS (same auth.conf, same nkey pattern), has GPU requirements (RTX 3080 passthrough), and is currently partially coupled to Lyra (its nkey seeds live in `~/.lyra/nkeys/` per its supervisord conf).

**Why this phase matters:** Every cross-project design question deferred in ADR-053 §6 surfaces here for the first time. Do not start implementation until the cross-project ADR (ADR-053 follow-up 10) is accepted.

**Entry criteria:**
- Phase 1 stable (>= 1 week)
- Cross-project ADR accepted (covering: registry namespace, shared vs per-project NATS, shared network, env file convention, deploy script template)
- voiceCLI has a `deploy/quadlet/` directory modeled on Lyra's
- GPU passthrough via CDI validated on M₁ (see `docs/runbooks/cdi-gpu-validation.md`, Risk 5 mitigated 2026-04-24). Use `AddDevice=nvidia.com/gpu=all` in `.container` files (CDI syntax, not raw `/dev/nvidia0`).

**Steps:**
1. Per the cross-project ADR decisions: create `voicecli.network` or join `lyra.network`
2. Migrate nkey seeds: `voice-tts.seed` and `voice-stt.seed` from `~/.lyra/nkeys/` to `~/.voicecli/nkeys/` (or shared path per ADR) — this is a one-time rename with matching NATS auth.conf update
3. Build and push `localhost/voicecli:latest` (or namespace per ADR)
4. Install voiceCLI Quadlet units; start; validate GPU is visible to containers
5. Stop supervisord `voicecli_tts` and `voicecli_stt` programs
6. Confirm Lyra's hub still routes voice requests via NATS to the new containers

**Validation:**
- TTS and STT respond to NATS test messages
- GPU utilization visible in `nvidia-smi` during voice processing
- Lyra hub connects to voice services without config changes (NATS subject unchanged)

**Rollback:**
```
systemctl --user stop voicecli-tts voicecli-stt
cd ~/projects && supervisorctl -c supervisord.conf start voicecli_tts voicecli_stt
```

**Exit criteria:**
- voiceCLI stable on Quadlet for >= 1 week
- Nkey seed migration validated (both seeds in new path, NATS auth reloaded and verified)
- Cross-project boilerplate template (`roxabi-boilerplate`) updated with the pattern

---

### Phase 3: imageCLI + llmCLI + 2ndBrain

**Scope:** Three projects. Can be parallelized after Phase 2's cross-project ADR lands — they do not depend on each other.

**Entry criteria:**
- Phase 2 complete
- Cross-project ADR accepted (carries over from Phase 2)
- `imageCLI` NATS usage confirmed (investigate — not visible in supervisord conf)
- `llmCLI` deployment status confirmed (conf exists in project but not in hub `conf.d/` — may not be running on M₁)
- `2ndBrain` separate supervisord instance strategy decided: absorb into hub supervisord conf first, or migrate directly to Quadlet? Direct Quadlet is preferred — fewer steps

**Notes per project:**
- `imageCLI`: GPU device passthrough likely required (same CDI path as voiceCLI)
- `llmCLI`: verify if `llmcli_serve.conf` is deployed to M₁ at all before designing Quadlet units
- `2ndBrain`: runs its own supervisord instance — that instance and `lyra.service`-equivalent user unit must be stopped and removed as part of this migration; `knowledge_bot` has `autostart=true` (unlike other projects)

**Exit criteria:**
- All three projects on Quadlet, stable >= 1 week each
- No standalone supervisord processes remaining for these projects
- Hub supervisord `conf.d/` has no entries for these projects

---

### Phase 4: Retire host `nats.service`; consolidate to one NATS container on 4222

**Scope:** Switch all Quadlet projects from Lyra's per-project NATS (4223) to a single shared `nats.container` on 4222. Retire the system-level `nats.service`.

**Entry criteria:**
- All NATS-using projects (lyra, voiceCLI, roxabi-vault if daemonized) are on Quadlet
- Cross-project ADR has resolved shared vs per-project NATS — this phase assumes "shared wins"
- Shared NATS Quadlet unit is in a designated home repo (recommendation: `roxabi-production` or a new `roxabi-infra` repo — not any single project repo)
- All nkey seeds and auth.conf migrated to the shared NATS's volume layout

**Steps:**
1. Deploy shared `nats.container` on port 4222 (new Quadlet unit, managed outside Lyra's repo)
2. Update `NATS_URL` in all project env files: `nats://shared-nats:4222` (or equivalent on the shared network)
3. Restart all NATS-using Quadlet services one by one, verify connectivity
4. Stop and disable system `nats.service`: `sudo systemctl disable --now nats.service`
5. Update Lyra's `nats.container` to port 4222 (or retire it in favor of the shared unit)
6. Verify no process listens on 4222 except the shared Quadlet container: `ss -tlnp | grep 4222`

**Rollback:**
```
sudo systemctl start nats.service
# Revert NATS_URL in all env files to tls://127.0.0.1:4222
# Restart all Quadlet services
```

**Cleanup after phase:**
- Remove `nats.service` unit file from `/etc/systemd/system/` (or wherever it lives)
- Remove `nats-server` binary if installed via package; retain if also used by the shared Quadlet NATS image build
- Remove Lyra's per-project `nats.container` if replaced by shared unit
- Delete `/etc/nats/` tree only after confirming no container still mounts from it

**Exit criteria:**
- Single NATS container on port 4222
- All projects connecting successfully
- Zero `nats.service` system unit entries: `systemctl status nats.service` returns "not found"

---

### Phase 5: Retire host supervisord

**Entry criteria:**
- All projects in the deferred tier (idna, roxabi-forge, roxabi-intel, roxabi-live, roxabi-vault) have either migrated to Quadlet **or** a policy decision has been made to run them outside supervisord indefinitely (e.g., as standalone systemd user services)
- Zero active programs in `~/projects/conf.d/` and `~/projects/2ndBrain/supervisor/conf.d/`
- `lyra.service` user unit (supervisord wrapper) already disabled since Phase 1

**Steps:**
1. Stop supervisord: `cd ~/projects && supervisorctl -c supervisord.conf shutdown`
2. Remove `lyra.service` from `~/.config/systemd/user/` if not already done
3. `systemctl --user daemon-reload`
4. Verify: `ps aux | grep supervisord` returns nothing
5. Remove `~/projects/supervisord.pid`, `~/projects/supervisor.sock`
6. Optionally uninstall supervisord: `pip uninstall supervisor` (if installed in a venv) — confirm it is not a dependency of any remaining project's venv first

**Cleanup:**
- Archive `~/projects/conf.d/` (or delete — project-level conf files are in each project's `deploy/conf.d/`)
- Remove `~/projects/supervisord.conf`, `~/projects/scripts/start.sh`, `~/projects/scripts/supervisorctl.sh`
- Update `~/projects/CLAUDE.md` to reflect the new topology

**Exit criteria:**
- No supervisord process running: `systemctl --user status lyra.service` returns "disabled/dead"
- All daemons running as Quadlet units
- `ps aux | grep supervisord` empty
- `~/projects/Makefile` dispatcher updated or retired (no longer needs supervisor targets)

---

### Deferred tier policy: idna, roxabi-forge, roxabi-intel, roxabi-live, roxabi-vault

**Do they block Phase 5?** Yes, unless you make a deliberate policy decision to exclude them.

**Opinion:** supervisord cannot "coexist indefinitely" alongside Quadlet as a strategic target — it coexists during the migration window only. The endgame is systemd + Quadlet everywhere. However, these five projects are lower-priority and three of them (forge, intel, idna) are simple HTTP servers with no NATS dependency and minimal operational complexity. They do not need the full Quadlet treatment urgently.

**Recommended policy:** Set a soft deadline — deferred tier must migrate before Phase 5, but Phase 5 is not gated on a calendar date. If any deferred-tier project is still on supervisord when all other projects have migrated, the operator should make a conscious choice: migrate it (likely a half-day effort each given Lyra patterns), or convert it to a plain `systemd --user` service with `ExecStart=` (no container, no image, simpler than Quadlet). Plain systemd user services are a valid intermediate state — they eliminate supervisord without requiring containerization.

`roxabi-vault`: if it is not a persistent daemon (current evidence suggests library/CLI only), it does not need migration at all and should be removed from the deferred tier scope.

`monitoring` (uptime-kuma): already containerized via Docker Compose. Migrate the Docker Compose unit to a Podman Quadlet `.container` file — this is a direct translation and should be done in Phase 3 or Phase 4 as a low-effort cleanup.

---

## 4. Cleanup Checklist

### After Phase 1 (Lyra cutover stable)

- [ ] Remove `~/projects/conf.d/lyra-hub.conf`, `lyra-telegram.conf`, `lyra-discord.conf` from M₁ supervisord path (keep in project repo)
- [ ] Delete old `~/projects/lyra/.env`, `.env.hub`, `.env.telegram`, `.env.discord` from M₁ working tree
- [ ] Disable `lyra.service` user unit: `systemctl --user disable lyra.service`
- [ ] Lyra-owned supervisor files removed from repo in #886; host scripts (`~/projects/scripts/start.sh`, `supervisorctl.sh`) retire when supervisord is fully decommissioned (Phase 5)

### After Phase 2 (voiceCLI cutover)

- [ ] Remove `~/projects/conf.d/voicecli_tts.conf`, `voicecli_stt.conf`
- [ ] Remove `lyra_tts.conf`, `lyra_stt.conf` stub files if confirmed empty/unused
- [ ] Migrate `~/.lyra/nkeys/voice-tts.seed`, `voice-stt.seed` to new path per ADR; delete from `~/.lyra/nkeys/` once NATS auth is verified

### After Phase 3 (imageCLI + llmCLI + 2ndBrain)

- [ ] Remove `~/projects/conf.d/imagecli_gen.conf`
- [ ] Stop and remove 2ndBrain's standalone supervisord instance (`~/projects/2ndBrain/supervisor/`)
- [ ] Remove `llmcli_serve.conf` from project if llmCLI is deployed

### After Phase 4 (host nats.service retired)

- [ ] `sudo systemctl disable --now nats.service`
- [ ] Remove `/etc/nats/` tree (certs, nkeys, conf) — **only after** confirming no container mounts paths from it; move nkeys to Quadlet volumes first
- [ ] Remove `nats-server` binary if installed on host (`which nats-server`)
- [ ] Remove `deploy/nats/` from Lyra repo once NATS is managed by the shared container

### After Phase 5 (supervisord retired)

- [ ] `rm ~/projects/supervisord.pid ~/projects/supervisor.sock` (if still present)
- [ ] Archive `~/projects/conf.d/` directory
- [ ] Remove `~/projects/supervisord.conf`, `~/projects/scripts/start.sh`, `~/projects/scripts/supervisorctl.sh`
- [ ] Uninstall supervisord: verify it is not a dep in any venv, then `pip uninstall supervisor` or equivalent
- [ ] Update `~/projects/CLAUDE.md` project index to reflect Quadlet-only topology
- [ ] Remove `lyra.service` user unit file from `~/.config/systemd/user/`
- [ ] Remove `lyra-stack.service` from `~/.config/systemd/user/` (investigate what it is first)

---

## 5. Risk Register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 1 | **nkey seed UID mismatch on volume mount** — when nkey seed files are migrated from host paths to Quadlet volume mounts, container UID 1500 (ADR-053 Decision 4) may not own the seed files, causing permission-denied on NATS auth | Medium | High | Before Phase 1: run `stat ~/.lyra/nkeys/*.seed` on M₁; chown to UID 1500 or confirm Podman volume propagation handles ownership. Test on M₂ first. |
| 2 | **Shared API key leakage across project env files** — Anthropic API key (and other shared credentials) will appear in multiple project env files under `~/.<project>/env/`. A future `grep -r ANTHROPIC` across the home directory finds them all | Medium | Medium | Document in cross-project ADR that env files are per-service, not per-key. Long-term: evaluate `podman secret` as a shared secret store (ADR-053 Alternative C). Immediate: `chmod 600` enforced by deploy scripts for all env files. |
| 3 | **Rollback from Quadlet to supervisord if Quadlet fails on M₁** — rollback is a manual procedure; if hub goes down and nkey paths are already migrated, supervisord path cannot restart until paths are reverted | Low | High | Keep supervisord conf files in place until Phase 1 is stable for >= 1 week. Document rollback commands verbatim in each phase (see §3). Do not delete `.env.*` files from working tree until rollback window closes. |
| 4 | **Monitoring gap during cutover** — uptime-kuma monitors host processes; during the switch from supervisord programs to Quadlet units, its health checks may silently stop working if they are keyed on process names or HTTP endpoints that change | Medium | Medium | Before Phase 1: audit uptime-kuma monitors on M₁ for Lyra. Update check URLs/methods to Quadlet-equivalent endpoints. Verify monitoring restores after each phase. |
| 5 | ~~Rootless GPU passthrough failure for imageCLI/voiceCLI~~ **MITIGATED 2026-04-24** — CDI validated on M₁: driver 580.142, toolkit 1.19.0, Podman 5.7.0, `podman run --device nvidia.com/gpu=all` and Quadlet `AddDevice=nvidia.com/gpu=all` both pass with RTX 3080 visible. Spec persisted at `/etc/cdi/nvidia.yaml` (not tmpfs). Apt hook `/etc/apt/apt.conf.d/99-nvidia-cdi-regenerate` auto-regenerates on driver/toolkit update and aborts apt transaction on failure (no silent-drift risk). Runbook: `docs/runbooks/cdi-gpu-validation.md`. | — | — | — |
| 6 | **Port collision between idna and roxabi-intel on 8082** — both projects appear to use port 8082; if both are started simultaneously, one will fail | Medium | Low | Investigate on M₁ now (§1 audit note). Assign distinct ports before migration. |
| 7 | **Disk space: image layer overhead** — each project image adds 200–800 MB of layer storage; 10 projects × 500 MB average = ~5 GB. M₁'s disk capacity is unknown from these files | Low | Medium | Check `df -h` on M₁. Set `podman system prune` in a weekly systemd timer to remove dangling layers. Keep `localhost/<project>:rollback` tag for one generation only. |
| 8 | **gitnexus daemon status unknown** — if gitnexus runs a background process and is not captured in this audit, it will be orphaned when supervisord is retired | Low | Low | Investigate on M₁: `ps aux | grep gitnexus`. Determine whether it is a Claude Code plugin (ephemeral) or a persistent server. |
| 9 | **2ndBrain's standalone supervisord is invisible to hub** — the 2ndBrain supervisord instance is not in the hub and has no `lyra.service` equivalent; it may already be running detached from any boot management | Medium | Low | Before Phase 3: map the full process tree on M₁ (`ps aux | grep supervisord`). Add a boot path (systemd user unit) for the 2ndBrain supervisord if it is not already managed, so Phase 3 migration has a clean starting point. |
| 10 | **NATS auth.conf dual-path during Phase 4 coexistence** — during the transition from host NATS (4222) to Quadlet NATS (4223, then back to 4222), nkey seeds and auth.conf must be consistent across both. A partial migration leaves one NATS instance with stale ACLs | Medium | High | Never regen nkeys mid-phase. Plan Phase 4 as: (a) deploy shared Quadlet NATS with same auth.conf, (b) verify all clients connect, (c) stop host NATS, (d) only then regen if needed. Keep auth.conf backup per the existing rollback procedure (`DEPLOYMENT.md §10`). |

---

## 6. Cross-Project Decisions Deferred to ADR

These questions are out of scope for ADR-053, which is Lyra-scoped. They are triggered by Phase 2 and must be resolved in a new ADR before Phase 2 begins. The ADR should live in `roxabi-production` (the natural home for cross-project infra decisions) or a new `roxabi-infra` repo. If neither exists, `roxabi-boilerplate` is an acceptable interim home.

**ADR title:** "Quadlet Ecosystem Conventions: Registry, NATS, Network, Secrets, Deploy" (working title)

| Decision | Options | Stakes |
|---|---|---|
| Image registry namespace | `localhost/<project>:latest` vs `localhost/roxabi-<project>:latest` | Low — cosmetic; but needs to be settled once, not per-project |
| Shared vs per-project NATS | Each project owns a `nats.container` vs one shared container outside any project | High — determines Phase 4 scope and complexity; shared NATS requires a neutral home |
| Shared vs per-project Podman network | `roxabi.network` shared vs `<project>.network` per project | Medium — affects container-to-container communication; projects currently use NATS as the bus so direct networking is not required |
| Env file path convention | `~/.<project>/env/<service>.env` (per-project, mirrors `~/.lyra/env/`) vs `~/.roxabi/env/<project>/<service>.env` (centralized) | Medium — affects deploy script template and provisioning docs |
| Deploy script template | Each project copies `scripts/deploy-quadlet.sh` vs a shared shell library in `roxabi-boilerplate` sourced by each project | Medium — affects maintainability; a shared template means one fix propagates everywhere |
| Upgrade coordination | Projects release and deploy independently vs batch upgrades when shared infra (NATS, network) changes | Medium — independent is simpler; batch required only for breaking NATS contract changes (ADR-049 governs this already) |
| Neutral home for shared infra | Where does a shared `nats.container` unit live when it is not owned by any single project? | High — blocks Phase 4; `roxabi-production` is the natural candidate |

---

## 7. Success Metrics

Migration is complete when all of the following are true on M₁:

- `ps aux | grep supervisord` returns zero lines
- `systemctl status nats.service` returns "not found" (system unit retired)
- `systemctl --user list-units '*.service' --state=running` accounts for all active daemons as Quadlet-generated units
- `podman ps` shows all project containers in `Up` state with no restarts in the last 24 hours
- `~/.config/containers/systemd/` contains unit files for every migrated project
- No project code runs directly from `uv run` or host venv outside a container (exception: CLI tools invoked interactively, not daemons)
- Monitoring (uptime-kuma) has active checks for all services and shows no downtime attributable to migration
- Zero prod incidents logged in the post-Phase-1 window attributable to the migration path
- Deploy time for a Lyra update (build + push + restart) is not more than 2x the pre-migration deploy time
- `~/projects/conf.d/` is empty or archived

---

## 8. Timeline

All phases are "when ready" — no calendar dates. Sequencing and parallelism:

```
Phase 0 → Phase 1 → Phase 2 → Phase 3 (parallel) → Phase 4 → Phase 5
                       |
                  Cross-project ADR
                  must land before
                  Phase 2 starts
```

**Strict sequential dependencies:**
- Phase 0 must complete before Phase 1 (dry run gates prod)
- Phase 1 must be stable before Phase 2 (Lyra is the reference; patterns proven first)
- Cross-project ADR must be accepted before Phase 2 implementation starts
- Phase 4 requires all NATS-using projects migrated (lyra, voiceCLI, vault if daemonized)
- Phase 5 requires all projects migrated (including deferred tier, or deferred tier explicitly converted to plain systemd services)

**Parallelizable:**
- Phase 3 projects (imageCLI, llmCLI, 2ndBrain) can be done in parallel after the cross-project ADR lands
- Deferred tier (forge, intel, idna, live, vault) can be migrated in any order, in parallel with Phase 3

**Elapsed time estimate (solo operator, other work in parallel):**
- Phase 0: 1–2 days (implementation already done; integration testing only)
- Phase 1: 1 day + 1 week stabilization window
- Cross-project ADR: 2–3 days (write + review)
- Phase 2: 3–5 days (Dockerfile, units, GPU CDI validation, nkey migration)
- Phase 3: 1–2 weeks total (3 projects, can be spread across a week)
- Phase 4: 1–2 days once projects are migrated
- Phase 5: half a day once conf.d is empty
- Deferred tier: 0.5–1 day per project

**Total elapsed: 4–8 weeks** from Phase 0 start to Phase 5 completion, assuming no blockers on GPU CDI validation, cross-project ADR, or deferred-tier migration decisions.
