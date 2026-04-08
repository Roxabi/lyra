SUPERVISOR_HUB ?= $(HOME)/projects
HUB_SERVICES   := lyra telegram discord lyra-stt lyra-tts
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
endef

.PHONY: lyra telegram discord lyra-stt lyra-tts monitor register deploy remote update nats-setup nats-install nats-deploy test lint typecheck format

# ── Supervisor services ──────────────────────────────────────────────────────

LYRA_PROGRAMS := lyra_hub lyra_telegram lyra_discord
ifneq ($(shell grep -s '^LYRA_STT_ENABLED=1' .env),)
  LYRA_PROGRAMS += lyra_stt
endif
ifneq ($(shell grep -s '^LYRA_TTS_ENABLED=1' .env),)
  LYRA_PROGRAMS += lyra_tts
endif

lyra:
ifndef _IS_LYRA_SUBCMD
	$(call lyra_svc,$(LYRA_PROGRAMS))
endif

telegram:
ifndef _IS_LYRA_SUBCMD
	$(call lyra_svc,lyra_telegram)
endif

discord:
ifndef _IS_LYRA_SUBCMD
	$(call lyra_svc,lyra_discord)
endif

lyra-stt:
ifndef _IS_LYRA_SUBCMD
	$(call lyra_svc,lyra_stt)
endif

lyra-tts:
ifndef _IS_LYRA_SUBCMD
	$(call lyra_svc,lyra_tts)
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

register:
	@echo "Registering lyra with supervisor hub..."
	@$(HUB_GEN_MK) lyra "$(abspath .)" lyra telegram discord monitor
	$(call hub-link-conf,lyra_hub,supervisor/conf.d/lyra_hub.conf)
	$(call hub-link-conf,lyra_telegram,supervisor/conf.d/lyra_telegram.conf)
	$(call hub-link-conf,lyra_discord,supervisor/conf.d/lyra_discord.conf)
	$(call hub-link-conf,lyra_stt,supervisor/conf.d/lyra_stt.conf)
	$(call hub-link-conf,lyra_tts,supervisor/conf.d/lyra_tts.conf)
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

# ── Supervisor config reload ──────────────────────────────────────────────────

SCTL := $(or $(SUPERVISORCTL),$(CURDIR)/deploy/supervisor/supervisorctl.sh)

# Local supervisorctl dispatch — mirrors svc.sh but uses SCTL directly.
# Avoids routing through the hub's supervisorctl.sh (wrong socket on prod).
define lyra_svc
	@case "$(SVC_CMD)" in \
		reload)         $(SCTL) restart $(1) ;; \
		start)          $(SCTL) start   $(1) ;; \
		stop)           $(SCTL) stop    $(1) ;; \
		logs)           $(SCTL) tail -f $(1) ;; \
		errlogs|errors) $(SCTL) tail -f $(1) stderr ;; \
		status|"")      $(SCTL) status  $(1) ;; \
		*) echo "Unknown action: $(SVC_CMD)"; exit 1 ;; \
	esac
endef

update:
	@$(SCTL) reread && $(SCTL) update

# ── Deploy + remote ──────────────────────────────────────────────────────────

deploy:
	$(require_machine1)
	@echo "Deploying to Machine 1 ($(DEPLOY_HOST))..."
	@ssh $(DEPLOY_HOST) "cd $(DEPLOY_DIR) && bash scripts/deploy.sh"

REMOTE_SCTL := ~/projects/lyra/deploy/supervisor/supervisorctl.sh

# make remote [service] [action]
#   Services: lyra, hub, telegram, discord (default: all lyra programs)
#   Actions:  reload, start, stop, status, logs, errors (default: status)
remote:
	$(require_machine1)
	@SVC="$(word 1,$(_LYRA_CMD))"; \
	ACTION="$(word 2,$(_LYRA_CMD))"; \
	SCTL="$(REMOTE_SCTL)"; \
	case "$$SVC" in \
	  lyra)     PROGS="lyra_hub lyra_telegram lyra_discord" ;; \
	  hub)      PROGS="lyra_hub" ;; \
	  telegram) PROGS="lyra_telegram" ;; \
	  discord)  PROGS="lyra_discord" ;; \
	  stt)      PROGS="lyra_stt" ;; \
	  tts)      PROGS="lyra_tts" ;; \
	  reload|start|stop|status|update|logs|errors|"") \
	    ACTION="$$SVC"; PROGS="lyra_hub lyra_telegram lyra_discord" ;; \
	  *) echo "Unknown service: $$SVC"; exit 1 ;; \
	esac; \
	case "$${ACTION:-status}" in \
	  reload)  ssh $(DEPLOY_HOST) "$$SCTL restart $$PROGS" ;; \
	  start)   ssh $(DEPLOY_HOST) "$$SCTL start $$PROGS" ;; \
	  stop)    ssh $(DEPLOY_HOST) "$$SCTL stop $$PROGS" ;; \
	  status)  ssh $(DEPLOY_HOST) "$$SCTL status $$PROGS" ;; \
	  update)  ssh $(DEPLOY_HOST) "$$SCTL reread && $$SCTL update" ;; \
	  logs)    FIRST=$${PROGS%% *}; ssh $(DEPLOY_HOST) "$$SCTL tail -f $$FIRST" ;; \
	  errors)  FIRST=$${PROGS%% *}; ssh $(DEPLOY_HOST) "$$SCTL tail -f $$FIRST stderr" ;; \
	  *) echo "Unknown action: $$ACTION"; exit 1 ;; \
	esac

# ── Dev tools ────────────────────────────────────────────────────────────────

nats-setup:
	@bash deploy/nats/setup.sh

nats-install: nats-setup  ## deprecated alias

nats-deploy:              ## run NATS setup on prod, then reload supervisor conf
	$(require_machine1)
	@echo "Running NATS setup on $(DEPLOY_HOST)..."
	@ssh $(DEPLOY_HOST) "cd $(DEPLOY_DIR) && bash deploy/nats/setup.sh"
	@echo "Reloading supervisor config on $(DEPLOY_HOST)..."
	@ssh $(DEPLOY_HOST) "$(REMOTE_SCTL) reread && $(REMOTE_SCTL) update"

test:
	uv run pytest -v

lint:
	uv run ruff check .

typecheck:
	uv run pyright

format:
	uv run ruff format .
