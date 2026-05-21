# Recipes use bash for brace expansion + pipefail (`rm -f .../lyra*.{a,b,c}`,
# `podman save | ssh … | podman load`). Default /bin/sh is dash on Debian/Ubuntu,
# which silently skips unmatched brace expansions.
SHELL := /bin/bash -o pipefail

SUPERVISOR_HUB ?= $(HOME)/projects
HUB_SERVICES   := lyra telegram discord nats clipool
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
_LYRA_MULTI := monitor deploy remote
ifneq (,$(filter $(_LYRA_MULTI),$(firstword $(MAKECMDGOALS))))
  _LYRA_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  _IS_LYRA_SUBCMD := true
  ifneq (,$(_LYRA_CMD))
    $(eval $(_LYRA_CMD):;@:)
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

.PHONY: build push lyra telegram discord nats clipool monitor quadlet-preflight quadlet-install quadlet-secrets-install quadlet-authconf-merged quadlet-lint deploy full-deploy remote nats-setup nats-regen-authconf test test-integration voice-smoke lint typecheck format quality-debt-report quality-debt-classify

# ── Container image build + transfer ─────────────────────────────────────────

# Local build tag — used by `make build` and `make push`
LYRA_IMAGE ?= localhost/lyra:dev

# Registry image reference — used when pulling from CI-published artifacts
GHCR_IMAGE ?= ghcr.io/roxabi/lyra:latest

build:                 ## build lyra image locally
	podman build -f Dockerfile -t $(LYRA_IMAGE) .

# Manual fallback — canonical publish path is CI (see .github/workflows/publish.yml).
push:                  ## save image and load on $(DEPLOY_HOST) via ssh
	$(require_machine1)
	@echo "Transferring $(LYRA_IMAGE) → $(DEPLOY_HOST)..."
	podman save $(LYRA_IMAGE) | ssh $(DEPLOY_HOST) "podman load"

# ── Service control (Quadlet units via systemd --user) ───────────────────────

LYRA_HUB_UNIT      := lyra-hub
LYRA_TELEGRAM_UNIT := lyra-telegram
LYRA_DISCORD_UNIT  := lyra-discord
LYRA_NATS_UNIT     := lyra-nats
LYRA_CLIPOOL_UNIT  := lyra-clipool
LYRA_UNITS         := $(LYRA_HUB_UNIT) $(LYRA_TELEGRAM_UNIT) $(LYRA_DISCORD_UNIT) $(LYRA_CLIPOOL_UNIT)

# $(call lyra_sctl,<unit1> [unit2 ...]) — dispatches SVC_CMD to systemctl --user.
# Defaults (empty SVC_CMD) to `start`. `logs`/`errors` tail the first unit.
define lyra_sctl
	@case "$(SVC_CMD)" in \
		reload)         systemctl --user restart $(1) ;; \
		start|"")       systemctl --user start   $(1) ;; \
		stop)           systemctl --user stop    $(1) ;; \
		status)         systemctl --user status  $(1) || true ;; \
		logs)           journalctl --user -u $(firstword $(1)) -f ;; \
		errlogs|errors) journalctl --user -u $(firstword $(1)) -f -p err ;; \
		*) echo "Unknown action: $(SVC_CMD). Use: start|stop|status|reload|logs|errors"; exit 1 ;; \
	esac
endef

lyra:
ifndef _IS_LYRA_SUBCMD
	$(call lyra_sctl,$(LYRA_UNITS))
endif

telegram:
ifndef _IS_LYRA_SUBCMD
	$(call lyra_sctl,$(LYRA_TELEGRAM_UNIT))
endif

discord:
ifndef _IS_LYRA_SUBCMD
	$(call lyra_sctl,$(LYRA_DISCORD_UNIT))
endif

nats:
ifndef _IS_LYRA_SUBCMD
	$(call lyra_sctl,$(LYRA_NATS_UNIT))
endif

clipool:
ifndef _IS_LYRA_SUBCMD
	$(call lyra_sctl,$(LYRA_CLIPOOL_UNIT))
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

quadlet-lint:  ## lint Quadlet unit files: dryrun parse check + inline-comment guard (issue #1083)
	@echo "==> quadlet --dryrun"
	@QUADLET_UNIT_DIRS=$(CURDIR)/deploy/quadlet /usr/libexec/podman/quadlet --dryrun --user
	@echo "==> inline-comment check"
	@_bad=0; \
	for f in deploy/quadlet/*.container deploy/quadlet/*.volume deploy/quadlet/*.network; do \
	    [ -f "$$f" ] || continue; \
	    if grep -Pn '^\s*[^#;].*[[:space:]]#' "$$f"; then \
	        echo "ERROR: $$f has inline # comments on value lines (Quadlet does not strip them)"; \
	        _bad=1; \
	    fi; \
	done; \
	[ $$_bad -eq 0 ] || exit 1
	@echo "quadlet-lint passed"

quadlet-install: quadlet-preflight  ## install Quadlet units → reload + verify (NO_RESTART=1 skips restart/verify)
	@mkdir -p "$(QUADLET_DIR)"
	@rm -f "$(QUADLET_DIR)"/lyra*.{network,volume,container,pod} "$(QUADLET_DIR)/nats.container" \
	       "$(QUADLET_DIR)/roxabi.network" "$(QUADLET_DIR)/lyra-nats.container"
	@cp deploy/quadlet/roxabi.network                  "$(QUADLET_DIR)/roxabi.network"
	@cp deploy/quadlet/lyra-data.volume                "$(QUADLET_DIR)/lyra-data.volume"
	@cp deploy/quadlet/lyra-jetstream.volume           "$(QUADLET_DIR)/lyra-jetstream.volume"
	@cp deploy/quadlet/lyra-gh-token.volume            "$(QUADLET_DIR)/lyra-gh-token.volume"
	@install -d -m 0700 "$(HOME)/.lyra/nats/jetstream"
	@chmod 0700 "$(HOME)/.lyra/nats"
	@chmod 0700 "$(HOME)/.lyra/nats/jetstream"
	@cp deploy/quadlet/lyra-nats.container             "$(QUADLET_DIR)/lyra-nats.container"
	@cp deploy/quadlet/lyra-hub.container              "$(QUADLET_DIR)/lyra-hub.container"
	@cp deploy/quadlet/lyra-telegram.container         "$(QUADLET_DIR)/lyra-telegram.container"
	@cp deploy/quadlet/lyra-discord.container          "$(QUADLET_DIR)/lyra-discord.container"
	@cp deploy/quadlet/lyra-gh.pod                     "$(QUADLET_DIR)/lyra-gh.pod"
	@cp deploy/quadlet/lyra-gh-helper.container        "$(QUADLET_DIR)/lyra-gh-helper.container"
	@cp deploy/quadlet/lyra-clipool.container          "$(QUADLET_DIR)/lyra-clipool.container"
	@echo "Quadlet units copied."
	@if [ "$(NO_RESTART)" = "1" ]; then \
		echo "NO_RESTART=1 — skipping daemon-reload, restart, and verification."; \
	else \
		bash deploy/quadlet-install-verify.sh; \
	fi

quadlet-authconf-merged:  ## render merged auth.conf (lyra + voicecli identities) → ~/.lyra/nkeys/auth.conf
	@lyra-acl genkeys --emit-merged-authconf

# ADR-054 Decision 5 (revised): file-based credentials (NATS auth.conf + nkey seeds)
# are delivered to containers as Podman secrets. Source files live in ~/.lyra/nkeys/;
# this target imports them into the user's Podman secret store. Idempotent (--replace).
LYRA_NKEYS_DIR := $(HOME)/.lyra/nkeys
quadlet-secrets-install:  ## (re)create Podman secrets from ~/.lyra/nkeys/*
	@test -d "$(LYRA_NKEYS_DIR)" || { echo "ERROR: $(LYRA_NKEYS_DIR) not found"; exit 1; }
	@podman secret create --replace lyra-nats-auth              "$(LYRA_NKEYS_DIR)/auth.conf"
	@podman secret create --replace lyra-nkey-hub               "$(LYRA_NKEYS_DIR)/hub.seed"
	@podman secret create --replace lyra-nkey-telegram-adapter  "$(LYRA_NKEYS_DIR)/telegram-adapter.seed"
	@podman secret create --replace lyra-nkey-discord-adapter   "$(LYRA_NKEYS_DIR)/discord-adapter.seed"
	@podman secret create --replace lyra-nkey-clipool-worker    "$(LYRA_NKEYS_DIR)/clipool-worker.seed"
	@if [ -f "$(HOME)/.lyra/gh-app.pem" ]; then \
		podman secret create --replace lyra-gh-pem "$(HOME)/.lyra/gh-app.pem"; \
		echo "lyra-gh-pem secret created from ~/.lyra/gh-app.pem"; \
	else \
		echo "SKIP: ~/.lyra/gh-app.pem not found — lyra-gh-pem secret not created."; \
		echo "      Copy the GitHub App PEM to ~/.lyra/gh-app.pem then re-run."; \
	fi
	@if [ -f "$(HOME)/.lyra/claude-oauth.tok" ]; then \
		tr -d '\n' < "$(HOME)/.lyra/claude-oauth.tok" | podman secret create --replace lyra-claude-oauth -; \
		echo "lyra-claude-oauth secret created from ~/.lyra/claude-oauth.tok"; \
	else \
		echo "SKIP: ~/.lyra/claude-oauth.tok not found — lyra-claude-oauth secret not created."; \
		echo "      Generate with: claude setup-token > ~/.lyra/claude-oauth.tok && chmod 600 ~/.lyra/claude-oauth.tok"; \
	fi
	@echo "Podman secrets installed. Verify: podman secret ls"

.PHONY: quadlet-bot-secrets-render
quadlet-bot-secrets-render: ## Render per-bot Secret= lines into deploy/quadlet/.bot-secrets.fragment
	@echo "Reading bot secrets from Podman store…"
	@: > deploy/quadlet/.bot-secrets.fragment
	@bots=$$(podman secret ls --filter name=lyra-bot- --format '{{.Name}}' | sort); \
	for s in $$bots; do \
	  case "$$s" in \
	    lyra-bot-telegram-*-webhook) bot=$${s#lyra-bot-telegram-}; bot=$${bot%-webhook}; target=bot_webhook-$$bot ;; \
	    lyra-bot-telegram-*)          bot=$${s#lyra-bot-telegram-};                       target=bot_token-$$bot ;; \
	    lyra-bot-discord-*-webhook)  bot=$${s#lyra-bot-discord-};  bot=$${bot%-webhook}; target=bot_webhook-$$bot ;; \
	    lyra-bot-discord-*)           bot=$${s#lyra-bot-discord-};                        target=bot_token-$$bot ;; \
	    *) echo "skip unknown $$s" >&2; continue ;; \
	  esac; \
	  echo "Secret=$$s,type=mount,target=$$target,mode=0400,uid=1500,gid=1500" >> deploy/quadlet/.bot-secrets.fragment; \
	done
	@if [ ! -s deploy/quadlet/.bot-secrets.fragment ]; then \
	  echo "No lyra-bot-* secrets found in Podman store."; \
	else \
	  echo "Wrote $$(wc -l < deploy/quadlet/.bot-secrets.fragment) Secret= line(s) to deploy/quadlet/.bot-secrets.fragment"; \
	fi

# ── Deploy + remote ──────────────────────────────────────────────────────────

deploy:
	$(require_machine1)
	@echo "Deploying quadlet units to $(DEPLOY_HOST)..."
	@ssh $(DEPLOY_HOST) '\
	set -eu; \
	export XDG_RUNTIME_DIR="/run/user/$$(id -u)"; \
	LYRA_DIR=$(DEPLOY_DIR); \
	VOICE_DIR=$$(grep "^VOICE_DEPLOY_DIR=" "$$LYRA_DIR/.env" 2>/dev/null | cut -d= -f2); \
	VOICE_DIR=$${VOICE_DIR:-$$HOME/projects/voiceCLI}; \
	echo "==> lyra: pulling staging..."; \
	cd "$$LYRA_DIR" && git pull origin staging; \
	echo "==> lyra: installing quadlet units..."; \
	make -C "$$LYRA_DIR" quadlet-install; \
	if [ -d "$$VOICE_DIR/.git" ]; then \
	    echo "==> voiceCLI: pulling staging..."; \
	    cd "$$VOICE_DIR" && git pull origin staging; \
	    echo "==> voiceCLI: installing quadlet units..."; \
	    make -C "$$VOICE_DIR" quadlet-install; \
	fi; \
	echo ""; \
	echo "Units installed + daemon-reload done."; \
	echo "To restart: make remote lyra reload  (or: systemctl --user restart voicecli-tts voicecli-stt)"'

full-deploy:  ## atomic deploy: git pull → quadlet-install → regen auth.conf → secrets → restart NATS → restart lyra
	$(require_machine1)
	@echo "Full deploy to $(DEPLOY_HOST)..."
	@ssh $(DEPLOY_HOST) '\
	set -eu; \
	export XDG_RUNTIME_DIR="/run/user/$$(id -u)"; \
	LYRA_DIR=$(DEPLOY_DIR); \
	VOICE_DIR=$$(grep "^VOICE_DEPLOY_DIR=" "$$LYRA_DIR/.env" 2>/dev/null | cut -d= -f2); \
	VOICE_DIR=$${VOICE_DIR:-$$HOME/projects/voiceCLI}; \
	echo "==> lyra: pulling staging..."; \
	cd "$$LYRA_DIR" && git pull origin staging; \
	if [ -d "$$VOICE_DIR/.git" ]; then \
	    echo "==> voiceCLI: pulling staging..."; \
	    cd "$$VOICE_DIR" && git pull origin staging; \
	    echo "==> voiceCLI: installing quadlet units..."; \
	    make -C "$$VOICE_DIR" quadlet-install; \
	fi; \
	echo "==> lyra: installing quadlet units..."; \
	make -C "$$LYRA_DIR" quadlet-install; \
	echo "==> NATS: regenerating auth.conf from updated acl-matrix.json..."; \
	sudo env "PATH=$$PATH" lyra-acl genkeys --regen-authconf; \
	echo "==> NATS: installing Podman secrets..."; \
	make -C "$$LYRA_DIR" quadlet-secrets-install; \
	echo "==> NATS: restarting (refresh mount-typed Podman secret)..."; \
	systemctl --user restart lyra-nats; \
	systemctl --user is-active --wait lyra-nats \
		|| { echo "ERROR: lyra-nats failed to reach active state"; exit 1; }; \
	echo "==> Lyra: restarting containers..."; \
	systemctl --user restart lyra-hub lyra-telegram lyra-discord lyra-clipool; \
	echo ""; \
	echo "Full deploy complete."'

# make remote [service] [action]
#   service: lyra or empty → all lyra-* + voicecli-* units | <shortname> → lyra-<shortname>
#   action:  reload | start | stop | status (default) | logs | errors
remote:
	$(require_machine1)
	@ssh $(DEPLOY_HOST) '\
	set -eu; \
	SVC="$(word 1,$(_LYRA_CMD))"; ACTION="$(word 2,$(_LYRA_CMD))"; \
	QDIR=$$HOME/.config/containers/systemd; \
	rdisc() { ls "$$QDIR"/lyra-*.container "$$QDIR"/voicecli-*.container 2>/dev/null | xargs -n1 basename | sed "s/\.container$$//" | tr "\n" " "; }; \
	if   [ -z "$$SVC" ] || [ "$$SVC" = lyra ]; then PROGS=$$(rdisc); FIRST=lyra-hub; \
	elif [ -f "$$QDIR/lyra-$$SVC.container" ];     then PROGS="lyra-$$SVC"; FIRST="$$PROGS"; \
	elif [ -f "$$QDIR/voicecli-$$SVC.container" ]; then PROGS="voicecli-$$SVC"; FIRST="$$PROGS"; \
	else ACTION="$$SVC"; PROGS=$$(rdisc); FIRST=lyra-hub; fi; \
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

nats-setup:
	@bash deploy/nats/setup.sh

nats-regen-authconf:          ## re-render auth.conf, refresh lyra-nats-auth secret only, restart NATS
	@lyra-acl genkeys --regen-authconf
	@test -s "$(LYRA_NKEYS_DIR)/auth.conf" \
		|| { echo "ERROR: $(LYRA_NKEYS_DIR)/auth.conf missing or empty after genkeys"; exit 1; }
	@# auth.conf only — seed rotation is a different runbook (nkey-rotation.md).
	@podman secret create --replace lyra-nats-auth "$(LYRA_NKEYS_DIR)/auth.conf"
	@# Restart, not HUP — see docs/ops/nats-authconf-update.md.
	@systemctl --user restart lyra-nats

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
# Decision: uses `lyra voice-smoke` CLI (self-contained, no Telegram dependency). See #689.
voice-smoke:
	uv run lyra voice-smoke

lint:
	uv run ruff check .

typecheck:
	uv run pyright

format:
	uv run ruff format .

# dep-graph and corpus migrated to roxabi-dashboard (2026-04-22).
# Run via dashboard: `uv run --project ../roxabi-dashboard roxabi-corpus sync`
# Graph API: GET http://localhost:8000/api/graph

# ── Quality-debt tooling ──────────────────────────────────────────────────────

quality-debt-report:  ## audit suppression annotations → artifacts/quality-debt-report.json
	uv run python tools/audit_quality_debt.py --root . --out artifacts/quality-debt-report.json


quality-debt-classify:  ## dry-run classification of untagged debt entries
	uv run python tools/classify_quality_debt.py --dry-run
