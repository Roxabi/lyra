# Recipes use bash for brace expansion + pipefail (`rm -f .../factory*.{a,b,c}`,
# `podman save | ssh … | podman load`). Default /bin/sh is dash on Debian/Ubuntu,
# which silently skips unmatched brace expansions.
SHELL := /bin/bash -o pipefail

SUPERVISOR_HUB ?= $(HOME)/projects
HUB_SERVICES   := factory telegram discord nats clipool
-include $(SUPERVISOR_HUB)/hub.mk

# Fallback SVC_CMD parsing — used when hub.mk is not present (e.g. prod).
ifndef SVC_CMD
ifneq (,$(filter $(HUB_SERVICES),$(firstword $(MAKECMDGOALS))))
  SVC_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  ifneq (,$(SVC_CMD))
    $(eval $(SVC_CMD):;@:)
  endif
endif
endif

# Sub-command parsing for multi-word targets (remote, monitor, deploy).
# These are NOT in HUB_SERVICES because their sub-commands can collide
# with real target names (e.g. `make remote telegram reload`).
_FACTORY_MULTI := monitor deploy remote
ifneq (,$(filter $(_FACTORY_MULTI),$(firstword $(MAKECMDGOALS))))
  _FACTORY_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  _IS_FACTORY_SUBCMD := true
  ifneq (,$(_FACTORY_CMD))
    $(eval $(_FACTORY_CMD):;@:)
  endif
endif

DEPLOY_HOST := $(shell grep '^DEPLOY_HOST=' .env 2>/dev/null | cut -d= -f2)
DEPLOY_DIR := $(shell grep '^DEPLOY_DIR=' .env 2>/dev/null | cut -d= -f2)

define require_machine1
	@[ -n "$(DEPLOY_HOST)" ] || { echo "Error: DEPLOY_HOST not set in .env"; exit 1; }
	@[ -n "$(DEPLOY_DIR)" ] || { echo "Error: DEPLOY_DIR not set in .env"; exit 1; }
	@case "$(DEPLOY_HOST)" in *[\'\"\$$\\\;\&\|\`]*) echo "Error: DEPLOY_HOST contains shell metacharacters"; exit 1 ;; esac
	@case "$(DEPLOY_DIR)" in *[\'\"\$$\\\;\&\|\`]*) echo "Error: DEPLOY_DIR contains shell metacharacters"; exit 1 ;; esac
endef

.PHONY: build push factory telegram discord nats clipool monitor quadlet-preflight quadlet-install quadlet-sync-install quadlet-secrets-install quadlet-authconf-merged quadlet-lint deploy full-deploy converge remote nats-setup nats-regen-authconf nats-add-identity test test-integration voice-smoke lint typecheck format hooks-install quality-debt-report quality-debt-classify

# ── Container image build + transfer ─────────────────────────────────────────

# Local build tag — used by `make build` and `make push`
FACTORY_IMAGE ?= localhost/factory:dev

# Registry image reference — used when pulling from CI-published artifacts
GHCR_IMAGE ?= ghcr.io/roxabi/factory:latest

build:                 ## build factory image locally
	podman build -f Dockerfile -t $(FACTORY_IMAGE) .

# Manual fallback — canonical publish path is CI (see .github/workflows/publish.yml).
push:                  ## save image and load on $(DEPLOY_HOST) via ssh
	$(require_machine1)
	@echo "Transferring $(FACTORY_IMAGE) → $(DEPLOY_HOST)..."
	podman save $(FACTORY_IMAGE) | ssh $(DEPLOY_HOST) "podman load"

# ── Service control (Quadlet units via systemd --user) ───────────────────────

FACTORY_HUB_UNIT      := factory-hub
FACTORY_TELEGRAM_UNIT := factory-telegram
FACTORY_DISCORD_UNIT  := factory-discord
FACTORY_WEB_UNIT      := factory-web
FACTORY_NATS_UNIT     := factory-nats
FACTORY_CLIPOOL_UNIT  := factory-clipool

# Container list — SSoT is deploy/quadlet.toml via quadlet_containers() (#1979,
# #1988). Derived once at parse time so adding/renaming a container in
# quadlet.toml flows into `make factory` and the nats-regen-authconf restart set
# with no Makefile edit. The individual FACTORY_*_UNIT vars above remain for
# per-unit targets (telegram:, nats:, …).
FACTORY_ALL_CONTAINERS := $(shell bash -c 'source deploy/lib/quadlet-units.sh && quadlet_containers 2>/dev/null')
# App units = every container except the bare NATS server, which is infra
# managed on its own (`make nats`, converge, #1390) and must not be bounced by a
# routine `make factory reload`. Same set as FACTORY_NATS_CLIENTS below today.
FACTORY_UNITS          := $(filter-out $(FACTORY_NATS_UNIT),$(FACTORY_ALL_CONTAINERS))

# $(call factory_sctl,<unit1> [unit2 ...]) — dispatches SVC_CMD to systemctl --user.
# Defaults (empty SVC_CMD) to `status`. `logs`/`errors` tail the first unit.
define factory_sctl
	@case "$(SVC_CMD)" in \
		reload)         systemctl --user restart $(1) ;; \
		start)          systemctl --user start   $(1) ;; \
		stop)           systemctl --user stop    $(1) ;; \
		status|"")      systemctl --user status  $(1) || true ;; \
		logs)           journalctl --user -u $(firstword $(1)) -f ;; \
		errlogs|errors) journalctl --user -u $(firstword $(1)) -f -p err ;; \
		*) echo "Unknown action: $(SVC_CMD). Use: start|stop|status|reload|logs|errors"; exit 1 ;; \
	esac
endef

factory:
ifndef _IS_FACTORY_SUBCMD
	$(call factory_sctl,$(FACTORY_UNITS))
endif

telegram:
ifndef _IS_FACTORY_SUBCMD
	$(call factory_sctl,$(FACTORY_TELEGRAM_UNIT))
endif

discord:
ifndef _IS_FACTORY_SUBCMD
	$(call factory_sctl,$(FACTORY_DISCORD_UNIT))
endif

web:
ifndef _IS_FACTORY_SUBCMD
	$(call factory_sctl,$(FACTORY_WEB_UNIT))
endif

nats:
ifndef _IS_FACTORY_SUBCMD
	$(call factory_sctl,$(FACTORY_NATS_UNIT))
endif

clipool:
ifndef _IS_FACTORY_SUBCMD
	$(call factory_sctl,$(FACTORY_CLIPOOL_UNIT))
endif

# ── Monitor — DEPRECATED (#1035) ─────────────────────────────────────────────
# Host-timer monitoring is superseded by Monitoring v2 (NATS event stream +
# Tauri desktop dashboard). The unit has been disabled on prod. This target is
# preserved as a no-op shim until #1035 lands; running it prints a pointer.

monitor:
	@echo "make monitor — DEPRECATED."
	@echo "Host-timer monitoring is superseded by Monitoring v2 (#1035 — NATS + Tauri)."
	@echo "Existing prod has been disabled. This target will be removed when #1035 lands."

# ── Quadlet install paths ────────────────────────────────────────────────────

QUADLET_DIR            := $(HOME)/.config/containers/systemd

quadlet-preflight:  ## advisory pre-flight checks before Quadlet install (non-blocking)
	@_unprivport=$$(sysctl -n net.ipv4.ip_unprivileged_port_start 2>/dev/null || echo 1024); \
	if [ "$${_unprivport}" -gt 4222 ]; then \
		echo "WARNING: net.ipv4.ip_unprivileged_port_start=$${_unprivport} (>4222)."; \
		echo "         Rootless :4222 binding will fail silently."; \
		echo "         Fix: sudo sysctl -w net.ipv4.ip_unprivileged_port_start=4222"; \
		echo "         (or persist in /etc/sysctl.d/99-rootless-ports.conf)"; \
	fi

quadlet-lint:  ## lint Quadlet unit files: dryrun parse check + inline-comment guard (issue #1083) + template purity (issue #1369)
	@echo "==> quadlet --dryrun"
	@QUADLET_UNIT_DIRS=$(CURDIR)/deploy/quadlet /usr/libexec/podman/quadlet --dryrun --user
	@echo "==> inline-comment check"
	@_bad=0; \
	for f in deploy/quadlet/*.container deploy/quadlet/*.container.tmpl deploy/quadlet/*.volume deploy/quadlet/*.network; do \
	    [ -f "$$f" ] || continue; \
	    if grep -Pn '^\s*[^#;].*[[:space:]]#' "$$f"; then \
	        echo "ERROR: $$f has inline # comments on value lines (Quadlet does not strip them)"; \
	        _bad=1; \
	    fi; \
	done; \
	[ $$_bad -eq 0 ] || exit 1
	@echo "==> template purity check"
	@bash tools/check_quadlet_template_purity.sh
	@echo "quadlet-lint passed"

quadlet-install: quadlet-preflight  ## install Quadlet units → reload + verify (NO_RESTART=1 skips restart/verify)
	@mkdir -p "$(QUADLET_DIR)"
	@uv run --frozen factory bot init
	@rm -f "$(QUADLET_DIR)"/factory*.{network,volume,container,pod} "$(QUADLET_DIR)"/factory*.{network,volume,container,pod} "$(QUADLET_DIR)/nats.container" \
	       "$(QUADLET_DIR)/roxabi.network" "$(QUADLET_DIR)/factory-nats.container"
	@for f in deploy/quadlet/*.network deploy/quadlet/*.volume deploy/quadlet/*.pod deploy/quadlet/*.container; do \
		cp "$$f" "$(QUADLET_DIR)/"; \
	done
	@install -d -m 0700 "$(HOME)/.roxabi/factory/nats/jetstream"
	@chmod 0700 "$(HOME)/.roxabi/factory/nats"
	@chmod 0700 "$(HOME)/.roxabi/factory/nats/jetstream"
	@uv run --frozen python tools/render_quadlet.py \
		--platform telegram \
		--db "$(HOME)/.roxabi/factory/config.db" \
		--tmpl deploy/quadlet/factory-telegram.container.tmpl \
		--dest "$(QUADLET_DIR)/factory-telegram.container"
	@uv run --frozen python tools/render_quadlet.py \
		--platform discord \
		--db "$(HOME)/.roxabi/factory/config.db" \
		--tmpl deploy/quadlet/factory-discord.container.tmpl \
		--dest "$(QUADLET_DIR)/factory-discord.container"
	@echo "Quadlet units copied."
	@if [ "$(NO_RESTART)" = "1" ]; then \
		echo "NO_RESTART=1 — skipping daemon-reload, restart, and verification."; \
	else \
		bash deploy/quadlet-install-verify.sh; \
	fi

QUADLET_SYNC_SRC := deploy/systemd
QUADLET_SYNC_DST := $(HOME)/.config/systemd/user

quadlet-sync-install:  ## install systemd sync timers + services → daemon-reload + enable
	@mkdir -p "$(QUADLET_SYNC_DST)"
	@cp "$(QUADLET_SYNC_SRC)/factory-quadlet-sync.service"      "$(QUADLET_SYNC_DST)/"
	@cp "$(QUADLET_SYNC_SRC)/factory-quadlet-sync.timer"        "$(QUADLET_SYNC_DST)/"
	@cp "$(QUADLET_SYNC_SRC)/factory-post-autoupdate.service"  "$(QUADLET_SYNC_DST)/"
	@cp "$(QUADLET_SYNC_SRC)/factory-post-autoupdate.timer"    "$(QUADLET_SYNC_DST)/"
	@cp "$(QUADLET_SYNC_SRC)/factory-deploy-failure.service"    "$(QUADLET_SYNC_DST)/"
	@cp "$(QUADLET_SYNC_SRC)/factory-operator-logrotate.service" "$(QUADLET_SYNC_DST)/"
	@cp "$(QUADLET_SYNC_SRC)/factory-operator-logrotate.timer"   "$(QUADLET_SYNC_DST)/"
	@mkdir -p "$(QUADLET_SYNC_DST)/podman-auto-update.timer.d"
	@cp "$(QUADLET_SYNC_SRC)/podman-auto-update.timer.d/override.conf" "$(QUADLET_SYNC_DST)/podman-auto-update.timer.d/"
	@echo "Sync units copied to $(QUADLET_SYNC_DST)"
	@systemctl --user daemon-reload
	@systemctl --user restart podman-auto-update.timer
	@systemctl --user enable --now factory-quadlet-sync.timer
	@systemctl --user enable --now factory-post-autoupdate.timer
	@systemctl --user enable --now factory-operator-logrotate.timer
	@echo "[ok] factory-quadlet-sync.timer + factory-post-autoupdate.timer + factory-operator-logrotate.timer enabled."

quadlet-authconf-merged:  ## render merged auth.conf (factory + voicecli identities) → ~/.roxabi/factory/nkeys/auth.conf
	@factory-acl genkeys --emit-merged-authconf

# ADR-054 Decision 5: file-based credentials (NATS auth.conf + nkey seeds) are
# delivered to containers as Podman secrets. deploy/install.sh --secrets-only is
# the single source of truth for the full secret set (driven by the secrets-policy
# manifest — nats seeds, blobstore/turn-writer/omp seeds, factory_blobstore_token,
# factory-litellm-key, factory-gh-pem, factory-claude-oauth). This target delegates
# to it so the Makefile and the installer can never drift (#1929). FACTORY_NKEYS_DIR
# is still consumed by the cold-path nats-* targets below.
FACTORY_NKEYS_DIR := $(HOME)/.roxabi/factory/nkeys
quadlet-secrets-install:  ## (re)create Podman secrets — delegates to install.sh --secrets-only (SSoT)
	@./deploy/install.sh --secrets-only

# ── Deploy + remote ──────────────────────────────────────────────────────────

# `deploy` and `full-deploy` were SSH-from-dev-machine remote deploy verbs. They
# are RETIRED (#1930) — the production host converges itself via `make converge`
# (run locally on M₁) plus the factory-quadlet-sync / factory-post-autoupdate
# timers. Both targets now fail fast and redirect; the old SSH recipes live in
# git history. See deploy/AGENTS.md § "Atomic deploy — make converge".
deploy full-deploy:  ## RETIRED — remote SSH deploy; run `make converge` on the production host
	@echo "ERROR: 'make $@' (remote SSH deploy) is retired (#1930)." >&2
	@echo "       Run 'make converge' on the production host (M₁), or let the" >&2
	@echo "       factory-quadlet-sync / factory-post-autoupdate timers converge automatically." >&2
	@exit 1

converge:  ## atomic, idempotent, change-gated local deploy
	@bash deploy/converge.sh

# make remote [service] [action]
#   service: factory or empty → all factory-* + voicecli-* units | <shortname> → factory-<shortname>
#   action:  reload | start | stop | status (default) | logs | errors
remote:
	$(require_machine1)
	@ssh $(DEPLOY_HOST) '\
	set -eu; \
	SVC="$(word 1,$(_FACTORY_CMD))"; ACTION="$(word 2,$(_FACTORY_CMD))"; \
	QDIR=$$HOME/.config/containers/systemd; \
	rdisc() { ls "$$QDIR"/factory-*.container "$$QDIR"/voicecli-*.container 2>/dev/null | xargs -n1 basename | sed "s/\.container$$//" | tr "\n" " "; }; \
	if   [ -z "$$SVC" ] || [ "$$SVC" = factory ]; then PROGS=$$(rdisc); FIRST=factory-hub; \
	elif [ -f "$$QDIR/factory-$$SVC.container" ];     then PROGS="factory-$$SVC"; FIRST="$$PROGS"; \
	elif [ -f "$$QDIR/voicecli-$$SVC.container" ]; then PROGS="voicecli-$$SVC"; FIRST="$$PROGS"; \
	else ACTION="$$SVC"; PROGS=$$(rdisc); FIRST=factory-hub; fi; \
	case "$${ACTION:-status}" in \
	  reload)  systemctl --user restart $$PROGS ;; \
	  start)   systemctl --user start   $$PROGS ;; \
	  stop)    systemctl --user stop    $$PROGS ;; \
	  status)  systemctl --user status  $$PROGS || true ;; \
	  logs)    journalctl --user -u $$FIRST -f ;; \
	  errors)  journalctl --user -u $$FIRST -f -p err ;; \
	  *)       echo "Unknown action: $$ACTION"; exit 1 ;; \
	esac'

# ── Dev tools ────────────────────────────────────────────────────────────────

# Services that hold NATS subject auth and must restart whenever `auth.conf` is
# regenerated or a new identity is added. Derived from deploy/quadlet.toml
# (#1988) = every container minus the bare `factory-nats` server, which the
# target restarts separately before this list. See FACTORY_ALL_CONTAINERS.
FACTORY_NATS_CLIENTS := $(filter-out $(FACTORY_NATS_UNIT),$(FACTORY_ALL_CONTAINERS))

# Cold-path key bootstrap for the containerised NATS (#1930). The host NATS was
# retired (big-bang consolidation) — this no longer installs a server, system
# user, TLS, or firewall rule; it only renders the nkey seeds + auth.conf that
# the factory-nats container bind-mounts. `--ack-external-distribution` pre-acks
# the external-seed fan-out guard for a fresh box (operator still scp's external
# seeds, e.g. voice-client → M₂). Routine deploys converge via `make converge`.
nats-setup:  ## cold-path: render nkey seeds + auth.conf for the container NATS (deploy via `make converge`)
	@factory-acl genkeys --ack-external-distribution

nats-regen-specs:             ## re-render ACL spec table + parity fixture from acl-matrix.json
	@uv run --frozen python scripts/render_acl_spec.py
	@uv run --frozen python scripts/render_acl_parity.py
	@echo "[ok] ACL spec + parity fixture regenerated"

nats-regen-authconf:          ## re-render auth.conf from acl-matrix.json, restart factory-nats + all NATS clients (#1390)
	@factory-acl genkeys --regen-authconf
	@test -s "$(FACTORY_NKEYS_DIR)/auth.conf" \
		|| { echo "ERROR: $(FACTORY_NKEYS_DIR)/auth.conf missing or empty after genkeys"; exit 1; }
	@# auth.conf only — seed rotation is a different runbook (nkey-rotation.md).
	@# Restart, not HUP — see docs/ops/nats-authconf-update.md.
	@systemctl --user restart factory-nats
	@systemctl --user is-active --wait factory-nats \
		|| { echo "ERROR: factory-nats failed to reach active state"; exit 1; }
	@# Derived list must be non-empty — an empty FACTORY_NATS_CLIENTS (quadlet.toml
	@# unreadable / helper failure) would silently skip the #1390 client restarts.
	@test -n "$(FACTORY_NATS_CLIENTS)" \
		|| { echo "ERROR: FACTORY_NATS_CLIENTS empty — quadlet_containers() failed; refusing to skip client restarts (#1390)"; exit 1; }
	@# All NATS clients hold stale subject auth after an ACL change (#1390).
	@failed=""; \
	for svc in $(FACTORY_NATS_CLIENTS); do \
	  if systemctl --user is-active --quiet $$svc; then \
	    systemctl --user restart $$svc || { echo "ERROR: restart $$svc failed"; failed="$$failed $$svc"; }; \
	  fi; \
	done; \
	[ -z "$$failed" ] || { echo "ERROR: restart failed for:$$failed"; exit 1; }

nats-add-identity:  ## add a single NATS identity rootless; idempotent after full-consistency (seed+secret present)
	@test -n "$(NAME)" || { echo "usage: make nats-add-identity NAME=<x>"; exit 2; }
	@echo "$(NAME)" | grep -qE '^[a-zA-Z0-9][a-zA-Z0-9_-]*$$' \
	  || { echo "error: NAME must match [a-zA-Z0-9][a-zA-Z0-9_-]* — got '$(NAME)'"; exit 1; }
	@out=$$(uv run --frozen --project . factory-acl genkeys --add-identity "$(NAME)"); \
	rc=$$?; \
	if [ $$rc -ne 0 ]; then echo "$$out" >&2; echo "factory-acl failed (exit $$rc) — aborting"; exit $$rc; fi; \
	state=$$(echo "$$out" | grep -oE 'STATE=(noop|repaired|added)'); \
	echo "factory-acl: $$state"; \
	if [ "$$state" = "STATE=noop" ] && podman secret inspect "factory-nats-$(NAME)" >/dev/null 2>&1; then \
	  echo "no-op: $(NAME) already provisioned + Podman secret present locally"; \
	  exit 0; \
	fi; \
	podman secret create --replace "factory-nats-$(NAME)" "$(FACTORY_NKEYS_DIR)/$(NAME).seed"; \
	if systemctl --user is-active --quiet factory-nats; then \
	  systemctl --user reload factory-nats; \
	  echo "reloaded factory-nats (SIGHUP)"; \
	else \
	  echo "factory-nats not active — auth.conf updated on host, will load on next start"; \
	fi

test:
	uv run pytest -v

test-integration:
	@echo "Starting integration environment..."
	docker compose -f docker/docker-compose.test.yml up -d --wait --wait-timeout 30
	@echo "Running integration tests..."
	NATS_URL=nats://localhost:4222 uv run pytest tests/ -v -m nats_integration 2>&1; \
	EXIT=$$?; \
	docker compose -f docker/docker-compose.test.yml down -v; \
	exit $$EXIT

# voice-smoke: round-trip TTS→STT via NATS to verify voicecli nats-serve workers are answering.
# Decision: uses `factory voice-smoke` CLI (self-contained, no Telegram dependency). See #689.
voice-smoke:
	uv run factory voice-smoke

lint:
	uv run ruff check .

typecheck:
	uv run pyright

format:
	uv run ruff format .

hooks-install:  ## install pre-commit + pre-push git hooks (see CONTRIBUTING.md)
	uv run pre-commit install
	uv run pre-commit install --hook-type pre-push

# dep-graph and corpus migrated to roxabi-dashboard (2026-04-22).
# Run via dashboard: `uv run --project ../roxabi-dashboard roxabi-corpus sync`
# Graph API: GET http://localhost:8000/api/graph

# ── Quality-debt tooling ──────────────────────────────────────────────────────

quality-debt-report:  ## audit suppression annotations → artifacts/quality-debt-report.json
	uv run python tools/audit_quality_debt.py --root . --out artifacts/quality-debt-report.json


quality-debt-classify:  ## dry-run classification of untagged debt entries
	uv run python tools/classify_quality_debt.py --dry-run
