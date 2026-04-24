# Quadlet Adoption — Roxabi Ecosystem

Let:
  M₁ := `roxabituwer` (192.168.1.16, Ubuntu 26.04 LTS, rootless Podman 5.x)
  P  := project short name (e.g. `voicecli`, `imagecli`, `llmcli`, `2ndbrain`)
  LIB := `~/.local/lib/roxabi/deploy-lib.sh` — shared deploy library

Reference implementation: `lyra/` — all patterns here are derived from it.

---

## 1. When to adopt

**Signal:** the project has a long-running daemon on M₁ currently managed by supervisord.

Check the supervisord conf at `~/projects/conf.d/<program>.conf`. If the program is running `autostart=false`, has a `state_dir` under `~/.<project>/`, and communicates via NATS or exposes an HTTP port — it is a Quadlet migration candidate.

Phase plan → `docs/PROD-MIGRATION-STRATEGY.md` §3. Lyra (Phase 1) is the reference. voiceCLI (Phase 2) is the next target. imageCLI, llmCLI, 2ndBrain follow.

---

## 2. Prerequisites

| Requirement | Check |
|---|---|
| Podman 5.x | `podman --version` — ships with Ubuntu 26.04 apt, no PPA |
| Linger enabled | `loginctl show-user $USER \| grep Linger` → must be `yes` |
| Project runtime dir | `~/.<project>/` exists |
| nkey seeds migrated | seeds out of `~/.lyra/nkeys/` into `~/.<project>/nkeys/` — ADR-055 D4 procedure |
| `~/.<project>/env/` | directory exists, will hold per-service `.env` files |

Enable linger if not set:

```bash
loginctl enable-linger "$USER"
```

---

## 3. Unit templates

Reference: `lyra/deploy/quadlet/`. Each unit file is a Quadlet descriptor that `systemd --user daemon-reload` converts to a `.service` unit.

### Files each project must write

| File | Required | Notes |
|---|---|---|
| `<project>.network` | yes | Isolated bridge; `Driver=bridge` |
| `<project>-data.volume` | yes | Named volume for `~/.<project>/` runtime state |
| `<project>-logs.volume` | if needed | Named volume for log output |
| `<project>-<service>.container` | yes, one per daemon | Follow ADR-053 + ADR-054 hardening |
| `<project>-nats.container` | NATS-using projects only | Port 4223+N per ADR-055 D2 — see table below |

**Per-project NATS port assignments (ADR-055 D2):**

| Project | Port |
|---|---|
| lyra | 4223 |
| voiceCLI | 4224 |
| Next project | 4225 |

HTTP-only projects (forge, intel, idna, live) skip `<project>-nats.container` and use their own isolated `<project>.network`.

### Container file checklist (ADR-053 + ADR-054)

```ini
[Container]
ContainerName=<project>-<service>
Image=localhost/<project>-<service>:latest   # D1: project-named, no roxabi- prefix
Network=<project>.network
UserNS=keep-id                               # ADR-054 D2: map container UID to host UID
ReadOnly=true
DropCapability=ALL
NoNewPrivileges=true
EnvironmentFile=%h/.<project>/env/<service>.env   # D4: ~/.<project>/env/
Secret=<project>-nats-<identity>,type=mount,target=/run/secrets/<identity>.seed

[Service]
Restart=on-failure
RestartSec=5s
```

Replace `<project>`, `<service>`, `<identity>` with actual values. GPU projects add:

```ini
AddDevice=nvidia.com/gpu=all   # CDI; validated for RTX 3080 (Risk 5 closed 2026-04-24)
```

---

## 4. Credentials

### Env files

Location: `~/.<project>/env/<service>.env`
Mode: `0600` (enforced by deploy script)

```bash
mkdir -p ~/.<project>/env
chmod 700 ~/.<project>/env
cp deploy/quadlet/<service>.env.example ~/.<project>/env/<service>.env
chmod 600 ~/.<project>/env/<service>.env
# fill in secrets
```

### nkey seeds (NATS-using projects)

nkey seeds are delivered as Podman secrets — not bind-mounts.

**Naming:** `<project>-nats-<identity>`

Bootstrap (run once after seed path migration from `~/.lyra/nkeys/` per ADR-055 D4):

```bash
podman secret create --replace <project>-nats-tts ~/.<project>/nkeys/<identity>.seed
```

Verify: `podman secret ls`

Reference the secret in the container unit:

```ini
Secret=<project>-nats-tts,type=mount,target=/run/secrets/tts.seed
```

---

## 5. Deploy script skeleton

Each project ships `scripts/deploy-quadlet.sh` — a thin wrapper that sets project variables and delegates all logic to LIB.

**Sourcing contract:**

```sh
source "${LYRA_DEPLOY_LIB:-$HOME/.local/lib/roxabi/deploy-lib.sh}"
```

LIB must be installed before the first deploy (`make quadlet-install-deploy-lib` in the lyra checkout).

### Full example — imageCLI (1 service, NATS-using)

```sh
#!/bin/bash
# Deploy script for imageCLI — Quadlet/podman path.
set -euo pipefail
umask 0077
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

export PATH="$HOME/.local/bin:$PATH"
source "$HOME/.local/bin/env" 2>/dev/null || true  # uv

# ── Project variables ─────────────────────────────────────────────────────────

PROJECT="imagecli"
PROJECT_DIR="$HOME/projects/imageCLI"
IMAGE="localhost/imagecli-gen:latest"
DOCKERFILE="Dockerfile"
HUB_SERVICE="imagecli-gen"
ADAPTER_SERVICES=""            # single service: no adapters
ENV_FILES_DIR="$HOME/.imagecli/env"
ENV_FILES="gen"
LOG_FILE="$HOME/.local/state/imagecli/logs/deploy.log"
FAIL_FILE="$HOME/.local/state/imagecli/deploy_failed_shas.txt"

# EXTRA_REPOS=""               # no extra repos for imagecli

# ── Source library and run ────────────────────────────────────────────────────

source "${LYRA_DEPLOY_LIB:-$HOME/.local/lib/roxabi/deploy-lib.sh}"
run_deploy "$@"
```

### EXTRA_REPOS — hooking a dependency

If the project bakes a dependency repo into its image (like Lyra bakes voiceCLI):

```sh
_mydep_upgrade_hook() {
    cd "$PROJECT_DIR"
    timeout 60 uv sync --all-extras --upgrade-package mydep 2>&1 | tee -a "$LOG_FILE"
}

EXTRA_REPOS="mydep:$HOME/projects/mydep:_mydep_upgrade_hook"
```

Format: `<name>:<path>:<hook-function-name>` — hook is called after `git pull` succeeds on that repo.

---

## 6. Makefile skeleton

Minimum targets each project needs:

```make
QUADLET_DIR      := $(HOME)/.config/containers/systemd
DEPLOY_LIB_INSTALL_DIR := $(HOME)/.local/lib/roxabi

.PHONY: quadlet-install quadlet-install-deploy-lib quadlet-upgrade-lib quadlet-secrets-install

quadlet-install:  ## install Quadlet units + reload
	@mkdir -p "$(QUADLET_DIR)"
	@cp deploy/quadlet/<project>.network          "$(QUADLET_DIR)/"
	@cp deploy/quadlet/<project>-data.volume      "$(QUADLET_DIR)/"
	@cp deploy/quadlet/<project>-<service>.container "$(QUADLET_DIR)/"
	@systemctl --user daemon-reload
	@echo "Quadlet units installed."
	@if [ ! -f "$(DEPLOY_LIB_INSTALL_DIR)/deploy-lib.sh" ]; then \
		echo "Hint: run 'make quadlet-install-deploy-lib' in ~/projects/lyra to install deploy-lib.sh"; \
	fi

quadlet-secrets-install:  ## create Podman secrets from ~/.<project>/nkeys/
	@podman secret create --replace <project>-nats-<identity> ~/.<project>/nkeys/<identity>.seed
	@echo "Podman secrets installed."
```

Note: `quadlet-install-deploy-lib` and `quadlet-upgrade-lib` are defined in Lyra's Makefile — run them from the lyra checkout, not the project's own Makefile.

---

## 7. Bootstrap checklist

Run in order on M₁ for a new project. Steps 1–2 done from M₂ (or the lyra checkout on M₁).

```
1. [ ] Install deploy-lib.sh
       cd ~/projects/lyra && make quadlet-install-deploy-lib

2. [ ] Install Quadlet units
       cd ~/projects/<project> && make quadlet-install

3. [ ] Create Podman secrets
       make quadlet-secrets-install

4. [ ] Create env files
       mkdir -p ~/.<project>/env && chmod 700 ~/.<project>/env
       cp deploy/quadlet/<service>.env.example ~/.<project>/env/<service>.env
       chmod 600 ~/.<project>/env/<service>.env
       # fill in secrets

5. [ ] Verify unit generation
       systemctl --user list-units '<project>-*'
       systemctl --user status <project>-<service>.service

6. [ ] First manual start
       systemctl --user start <project>-<service>.service
       journalctl --user -u <project>-<service> -f

7. [ ] First deploy run (dry-run path — nothing changed, expect exit 0)
       bash scripts/deploy-quadlet.sh

8. [ ] Register deploy timer (optional — follow Lyra's lyra-monitor pattern)
       cp deploy/<project>-deploy.timer ~/.config/systemd/user/
       systemctl --user daemon-reload
       systemctl --user enable --now <project>-deploy.timer
```

---

## 8. Coordination triggers

Independent deploys are the default. Batch coordination is required only for shared-infra changes. Cross-link: ADR-055 D6.

| Event | Required action |
|---|---|
| Shared NATS `auth.conf` change | All NATS-using containers restart after `systemctl reload nats.service` |
| Phase 4: shared NATS container | All NATS projects update `NATS_URL` → `nats://lyra-nats:4222`, coordinated restart window |
| `roxabi.network` create / rename | All projects on shared network update `Network=` directive |
| NATS contract breaking bump (ADR-049) | Per ADR-049 protocol — consumers update before producer deploys |
| `deploy-lib.sh` breaking change | `DEPLOY_LIB_VERSION` major bump; all projects run `make quadlet-upgrade-lib` before next deploy |

Everything else (logic, models, non-NATS features) deploys independently.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Permission denied` on seed file | Missing `UserNS=keep-id` — container UID ≠ host UID | Add `UserNS=keep-id` to `.container` unit; `daemon-reload` |
| Unit not generated after install | `daemon-reload` not run, or Quadlet parse error | `systemctl --user daemon-reload`; check `journalctl --user -u systemd-user-generators -b` |
| Port collision on NATS start | Another per-project NATS already on that port | Check port table (§3); assign the next unused port |
| `deploy-lib.sh: not found` | Library not installed, or `LYRA_DEPLOY_LIB` points to wrong path | `cd ~/projects/lyra && make quadlet-install-deploy-lib` |
| Tests fail after pull — deploy loops | Bad SHA in fail-file preventing retry | Delete `$FAIL_FILE` to force retry once the fix lands on staging |
| Container exits immediately | Missing env file, missing nkey secret, `config.toml` absent | `podman logs <container>`; cross-check env file and secret list |
| `daemon-reload` after unit edit has no effect | Edited file in project dir, not in `~/.config/containers/systemd/` | Re-run `make quadlet-install`; `daemon-reload` again |
