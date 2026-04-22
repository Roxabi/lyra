# Recipes use bash for brace expansion + pipefail (`rm -f .../lyra*.{a,b,c}`,
# `podman save | ssh … | podman load`). Default /bin/sh is dash on Debian/Ubuntu,
# which silently skips unmatched brace expansions.
SHELL := /bin/bash -o pipefail

SUPERVISOR_HUB ?= $(HOME)/projects
HUB_SERVICES   := lyra telegram discord
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
LYRA_SUPERVISORCTL_PATH := $(shell grep '^LYRA_SUPERVISORCTL_PATH=' .env 2>/dev/null | cut -d= -f2)

define require_machine1
	@[ -n "$(DEPLOY_HOST)" ] || { echo "Error: DEPLOY_HOST not set in .env"; exit 1; }
	@[ -n "$(DEPLOY_DIR)" ] || { echo "Error: DEPLOY_DIR not set in .env"; exit 1; }
endef

.PHONY: build push lyra telegram discord monitor register quadlet-install deploy remote update nats-setup nats-deploy test test-integration voice-smoke lint typecheck format gen-conf

# ── Container image build + transfer ─────────────────────────────────────────

LYRA_IMAGE := localhost/lyra:latest

build:                 ## build lyra image locally (localhost/lyra:latest)
	podman build -f Dockerfile -t $(LYRA_IMAGE) .

push:                  ## save image and load on $(DEPLOY_HOST) via ssh
	$(require_machine1)
	@echo "Transferring $(LYRA_IMAGE) → $(DEPLOY_HOST)..."
	podman save $(LYRA_IMAGE) | ssh $(DEPLOY_HOST) "podman load"

# ── Service control (Quadlet units via systemd --user) ───────────────────────

LYRA_UNITS := lyra_hub lyra_telegram lyra_discord

# $(call lyra_sctl,<unit1> [unit2 ...]) — dispatches SVC_CMD to systemctl or supervisorctl.
# Uses supervisorctl if LYRA_SUPERVISORCTL_PATH is set, else systemctl (default install).
# Defaults (empty SVC_CMD) to `start`. `logs`/`errors` tail the first unit.
define lyra_sctl
	@if [ -n "$(LYRA_SUPERVISORCTL_PATH)" ]; then \
		case "$(SVC_CMD)" in \
			reload|"")      $(LYRA_SUPERVISORCTL_PATH) restart $(1) ;; \
			start)          $(LYRA_SUPERVISORCTL_PATH) start $(1) ;; \
			stop)           $(LYRA_SUPERVISORCTL_PATH) stop $(1) ;; \
			status)         $(LYRA_SUPERVISORCTL_PATH) status $(1) || true ;; \
			logs)           $(LYRA_SUPERVISORCTL_PATH) tail -f $(firstword $(1)) ;; \
			errlogs|errors) $(LYRA_SUPERVISORCTL_PATH) tail -f $(firstword $(1)) stderr ;; \
			*) echo "Unknown action: $(SVC_CMD). Use: start|stop|status|reload|logs|errors"; exit 1 ;; \
		esac; \
	else \
		case "$(SVC_CMD)" in \
			reload)         systemctl --user restart $(1) ;; \
			start|"")       systemctl --user start   $(1) ;; \
			stop)           systemctl --user stop    $(1) ;; \
			status)         systemctl --user status  $(1) || true ;; \
			logs)           journalctl --user -u $(firstword $(1)) -f ;; \
			errlogs|errors) journalctl --user -u $(firstword $(1)) -f -p err ;; \
			*) echo "Unknown action: $(SVC_CMD). Use: start|stop|status|reload|logs|errors"; exit 1 ;; \
		esac; \
	fi
endef

lyra:
ifndef _IS_LYRA_SUBCMD
	$(call lyra_sctl,$(LYRA_UNITS))
endif

telegram:
ifndef _IS_LYRA_SUBCMD
	$(call lyra_sctl,lyra_telegram)
endif

discord:
ifndef _IS_LYRA_SUBCMD
	$(call lyra_sctl,lyra_discord)
endif

# ── Monitor (systemd timer, not supervisor) ──────────────────────────────────

monitor:
	@case "$(_LYRA_CMD)" in \
		status) systemctl --user status lyra-monitor.timer lyra-monitor.service 2>&1 || true; \
			echo ""; systemctl --user list-timers lyra-monitor.timer 2>/dev/null || true ;; \
		logs)   journalctl --user -u lyra-monitor.service -f ;; \
		run)    echo "Triggering manual monitoring run..."; systemctl --user start lyra-monitor.service ;; \
		enable) systemctl --user enable --now lyra-monitor.timer; echo "Monitor timer enabled." ;; \
		disable) systemctl --user disable --now lyra-monitor.timer; echo "Monitor timer disabled." ;; \
		"")     systemctl --user status lyra-monitor.timer 2>&1 || true ;; \
		*)      echo "Usage: make monitor [status|logs|run|enable|disable]" ;; \
	esac

# ── Registration ─────────────────────────────────────────────────────────────

SYSTEMD_USER_DIR := $(HOME)/.config/systemd/user
QUADLET_DIR      := $(HOME)/.config/containers/systemd

register:
	@echo "Registering lyra with supervisor hub..."
	@$(HUB_GEN_MK) lyra "$(abspath .)" lyra telegram discord monitor remote deploy
	$(call hub-link-conf,lyra_hub,deploy/supervisor/conf.d/lyra_hub.conf)
	$(call hub-link-conf,lyra_telegram,deploy/supervisor/conf.d/lyra_telegram.conf)
	$(call hub-link-conf,lyra_discord,deploy/supervisor/conf.d/lyra_discord.conf)
	@mkdir -p "$(HOME)/.local/state/lyra/logs"
	$(hub_reread)
	@echo ""
	@echo "Installing lyra systemd service..."
	@mkdir -p "$(SYSTEMD_USER_DIR)"
	@cp "$(abspath deploy/lyra.service)" "$(SYSTEMD_USER_DIR)/lyra.service"
	@echo ""
	@echo "Installing monitoring systemd timer..."
	@cp "$(abspath deploy/lyra-monitor.service)" "$(SYSTEMD_USER_DIR)/lyra-monitor.service"
	@cp "$(abspath deploy/lyra-monitor.timer)"   "$(SYSTEMD_USER_DIR)/lyra-monitor.timer"
	@systemctl --user daemon-reload
	@systemctl --user enable --now lyra.service
	@systemctl --user enable lyra-monitor.timer
	@echo ""
	@echo "Done."
	@echo "  Supervisor: lyra.service is running. Use 'make lyra status' or 'systemctl --user status lyra'."
	@echo "  Monitor:    run 'make monitor enable' to start the health check timer."
	@echo "  Secrets:    ensure TELEGRAM_TOKEN, ANTHROPIC_API_KEY, TELEGRAM_ADMIN_CHAT_ID are in .env"

quadlet-install:  ## install Quadlet units to ~/.config/containers/systemd/ + reload
	@mkdir -p "$(QUADLET_DIR)"
	@rm -f "$(QUADLET_DIR)"/lyra*.{network,volume,container} "$(QUADLET_DIR)/nats.container"
	@cp deploy/quadlet/lyra.network                    "$(QUADLET_DIR)/lyra.network"
	@cp deploy/quadlet/lyra-data.volume                "$(QUADLET_DIR)/lyra-data.volume"
	@cp deploy/quadlet/lyra-logs.volume                "$(QUADLET_DIR)/lyra-logs.volume"
	@cp deploy/quadlet/lyra-config.volume              "$(QUADLET_DIR)/lyra-config.volume"
	@cp deploy/quadlet/lyra-nkey-hub.volume            "$(QUADLET_DIR)/lyra-nkey-hub.volume"
	@cp deploy/quadlet/lyra-nkey-llm-worker.volume     "$(QUADLET_DIR)/lyra-nkey-llm-worker.volume"
	@cp deploy/quadlet/lyra-nkey-monitor.volume        "$(QUADLET_DIR)/lyra-nkey-monitor.volume"
	@cp deploy/quadlet/lyra-nkey-telegram-adapter.volume "$(QUADLET_DIR)/lyra-nkey-telegram-adapter.volume"
	@cp deploy/quadlet/lyra-nkey-discord-adapter.volume  "$(QUADLET_DIR)/lyra-nkey-discord-adapter.volume"
	@cp deploy/quadlet/lyra-nats-auth.volume           "$(QUADLET_DIR)/lyra-nats-auth.volume"
	@cp deploy/quadlet/nats.container                  "$(QUADLET_DIR)/nats.container"
	@cp deploy/quadlet/lyra-hub.container              "$(QUADLET_DIR)/lyra-hub.container"
	@cp deploy/quadlet/lyra-telegram.container         "$(QUADLET_DIR)/lyra-telegram.container"
	@cp deploy/quadlet/lyra-discord.container          "$(QUADLET_DIR)/lyra-discord.container"
	@systemctl --user daemon-reload
	@echo "Quadlet units installed."

# ── Supervisor config reload (remote prod only until cutover #611) ──────────

SCTL := $(or $(SUPERVISORCTL),$(CURDIR)/deploy/supervisor/supervisorctl.sh)

update:
	@$(SCTL) reread && $(SCTL) update

# ── Deploy + remote ──────────────────────────────────────────────────────────

deploy:
	$(require_machine1)
	@echo "Deploying to Machine 1 ($(DEPLOY_HOST))..."
	@ssh $(DEPLOY_HOST) "cd $(DEPLOY_DIR) && bash scripts/deploy.sh"

REMOTE_SCTL := ~/projects/lyra/deploy/supervisor/supervisorctl.sh

# make remote [service] [action]
#   service: lyra or empty → all lyra_* programs | <shortname> → lyra_<shortname>
#   action:  reload | start | stop | status (default) | logs | errors | update
#   Single SSH call — service/action disambiguation happens on the remote.
remote:
	$(require_machine1)
	@ssh $(DEPLOY_HOST) '\
	SVC="$(word 1,$(_LYRA_CMD))"; ACTION="$(word 2,$(_LYRA_CMD))"; \
	SCTL=$(REMOTE_SCTL); \
	CONF=$(DEPLOY_DIR)/deploy/supervisor/conf.d; \
	rdisc() { grep -rh "^\[program:lyra_" "$$CONF" | tr -d "[]" | cut -d: -f2 | tr "\n" " "; }; \
	if   [ -z "$$SVC" ] || [ "$$SVC" = lyra ]; then PROGS=$$(rdisc); FIRST=lyra_hub; \
	elif [ -f "$$CONF/lyra_$$SVC.conf" ];       then PROGS="lyra_$$SVC"; FIRST="$$PROGS"; \
	else ACTION="$$SVC"; PROGS=$$(rdisc); FIRST=lyra_hub; fi; \
	case "$${ACTION:-status}" in \
	  reload)  $$SCTL restart $$PROGS ;; \
	  start)   $$SCTL start $$PROGS ;; \
	  stop)    $$SCTL stop $$PROGS ;; \
	  status)  $$SCTL status $$PROGS ;; \
	  update)  $$SCTL reread && $$SCTL update ;; \
	  logs)    $$SCTL tail -f $$FIRST ;; \
	  errors)  $$SCTL tail -f $$FIRST stderr ;; \
	  *)       echo "Unknown action: $$ACTION"; exit 1 ;; \
	esac'

# ── Dev tools ────────────────────────────────────────────────────────────────

nats-setup:
	@bash deploy/nats/setup.sh

nats-deploy:              ## run NATS setup on prod, then reload supervisor conf
	$(require_machine1)
	@echo "Running NATS setup on $(DEPLOY_HOST)..."
	@ssh $(DEPLOY_HOST) "cd $(DEPLOY_DIR) && bash deploy/nats/setup.sh"
	@echo "Reloading supervisor config on $(DEPLOY_HOST)..."
	@ssh $(DEPLOY_HOST) "$(REMOTE_SCTL) reread && $(REMOTE_SCTL) update"

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

# ── Supervisor config generation ─────────────────────────────────────────────

gen-conf:              ## generate supervisord conf.d from agents.yml
	uv run deploy/gen-supervisor-conf.py
