ifeq (remote,$(firstword $(MAKECMDGOALS)))
  REMOTE_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  $(eval $(REMOTE_CMD):;@:)
else ifeq (lyra,$(firstword $(MAKECMDGOALS)))
  LYRA_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  $(eval $(LYRA_CMD):;@:)
else ifeq (telegram,$(firstword $(MAKECMDGOALS)))
  TELEGRAM_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  $(eval $(TELEGRAM_CMD):;@:)
else ifeq (discord,$(firstword $(MAKECMDGOALS)))
  DISCORD_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  $(eval $(DISCORD_CMD):;@:)
endif

LYRA_STACK_DIR ?= $(HOME)/projects/lyra-stack
SUPERVISORCTL  := $(LYRA_STACK_DIR)/scripts/supervisorctl.sh
SUPERVISOR_START := $(LYRA_STACK_DIR)/scripts/start.sh
HUB_PID        := $(LYRA_STACK_DIR)/supervisord.pid

define ensure_hub
	@if [ ! -d "$(LYRA_STACK_DIR)" ]; then \
		echo "Error: lyra-stack not found at $(LYRA_STACK_DIR)"; \
		echo "       Clone it or set LYRA_STACK_DIR=/path/to/lyra-stack"; \
		exit 1; \
	fi
	@if [ ! -f "$(HUB_PID)" ] || ! kill -0 $$(cat "$(HUB_PID)" 2>/dev/null) 2>/dev/null; then \
		echo "Hub supervisord not running, starting..."; \
		$(SUPERVISOR_START); \
	fi
endef

DEPLOY_HOST := $(shell grep '^DEPLOY_HOST=' .env 2>/dev/null | cut -d= -f2)
DEPLOY_DIR := $(shell grep '^DEPLOY_DIR=' .env 2>/dev/null | cut -d= -f2)

define require_machine1
	@[ -n "$(DEPLOY_HOST)" ] || { echo "Error: DEPLOY_HOST not set in .env"; exit 1; }
	@[ -n "$(DEPLOY_DIR)" ] || { echo "Error: DEPLOY_DIR not set in .env"; exit 1; }
endef

.PHONY: lyra telegram discord monitor register deploy remote test lint typecheck format

ifeq (monitor,$(firstword $(MAKECMDGOALS)))
  MONITOR_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  $(eval $(MONITOR_CMD):;@:)
endif

ifneq (remote,$(firstword $(MAKECMDGOALS)))
lyra:
ifeq ($(LYRA_CMD),stop)
	$(ensure_hub)
	@$(SUPERVISORCTL) stop lyra_telegram
	@$(SUPERVISORCTL) stop lyra_discord
else ifeq ($(LYRA_CMD),reload)
	$(ensure_hub)
	@$(SUPERVISORCTL) restart lyra_telegram
	@$(SUPERVISORCTL) restart lyra_discord
else ifeq ($(LYRA_CMD),logs)
	$(ensure_hub)
	@$(SUPERVISORCTL) tail -f lyra_telegram
else ifeq ($(LYRA_CMD),errors)
	$(ensure_hub)
	@$(SUPERVISORCTL) tail -f lyra_telegram stderr
else ifeq ($(LYRA_CMD),status)
	$(ensure_hub)
	@$(SUPERVISORCTL) status lyra_telegram
	@$(SUPERVISORCTL) status lyra_discord
else ifeq ($(LYRA_CMD),)
	@$(SUPERVISOR_START)
	@$(SUPERVISORCTL) start lyra_telegram
	@$(SUPERVISORCTL) start lyra_discord
else
	$(ensure_hub)
	@$(SUPERVISORCTL) $(LYRA_CMD) lyra_telegram
	@$(SUPERVISORCTL) $(LYRA_CMD) lyra_discord
endif

telegram:
ifeq ($(TELEGRAM_CMD),stop)
	$(ensure_hub)
	@$(SUPERVISORCTL) stop lyra_telegram
else ifeq ($(TELEGRAM_CMD),reload)
	$(ensure_hub)
	@$(SUPERVISORCTL) stop lyra_telegram
	@sleep 1
	@$(SUPERVISORCTL) start lyra_telegram
else ifeq ($(TELEGRAM_CMD),logs)
	$(ensure_hub)
	@$(SUPERVISORCTL) tail -f lyra_telegram
else ifeq ($(TELEGRAM_CMD),errors)
	$(ensure_hub)
	@$(SUPERVISORCTL) tail -f lyra_telegram stderr
else ifeq ($(TELEGRAM_CMD),status)
	$(ensure_hub)
	@$(SUPERVISORCTL) status lyra_telegram
else ifeq ($(TELEGRAM_CMD),)
	@$(SUPERVISOR_START)
	@$(SUPERVISORCTL) start lyra_telegram
else
	$(ensure_hub)
	@$(SUPERVISORCTL) $(TELEGRAM_CMD) lyra_telegram
endif

discord:
ifeq ($(DISCORD_CMD),stop)
	$(ensure_hub)
	@$(SUPERVISORCTL) stop lyra_discord
else ifeq ($(DISCORD_CMD),reload)
	$(ensure_hub)
	@$(SUPERVISORCTL) stop lyra_discord
	@sleep 1
	@$(SUPERVISORCTL) start lyra_discord
else ifeq ($(DISCORD_CMD),logs)
	$(ensure_hub)
	@$(SUPERVISORCTL) tail -f lyra_discord
else ifeq ($(DISCORD_CMD),errors)
	$(ensure_hub)
	@$(SUPERVISORCTL) tail -f lyra_discord stderr
else ifeq ($(DISCORD_CMD),status)
	$(ensure_hub)
	@$(SUPERVISORCTL) status lyra_discord
else ifeq ($(DISCORD_CMD),)
	@$(SUPERVISOR_START)
	@$(SUPERVISORCTL) start lyra_discord
else
	$(ensure_hub)
	@$(SUPERVISORCTL) $(DISCORD_CMD) lyra_discord
endif

endif # ifneq remote

SYSTEMD_USER_DIR := $(HOME)/.config/systemd/user

monitor:
ifeq ($(MONITOR_CMD),status)
	@systemctl --user status lyra-monitor.timer lyra-monitor.service 2>&1 || true
	@echo ""
	@systemctl --user list-timers lyra-monitor.timer 2>/dev/null || true
else ifeq ($(MONITOR_CMD),logs)
	@journalctl --user -u lyra-monitor.service -f
else ifeq ($(MONITOR_CMD),run)
	@echo "Triggering manual monitoring run..."
	@systemctl --user start lyra-monitor.service
else ifeq ($(MONITOR_CMD),enable)
	@systemctl --user enable --now lyra-monitor.timer
	@echo "Monitor timer enabled."
else ifeq ($(MONITOR_CMD),disable)
	@systemctl --user disable --now lyra-monitor.timer
	@echo "Monitor timer disabled."
else ifeq ($(MONITOR_CMD),)
	@systemctl --user status lyra-monitor.timer 2>&1 || true
else
	@echo "Usage: make monitor [status|logs|run|enable|disable]"
endif

register:
	@echo "Registering lyra with lyra-stack..."
	@if [ ! -d "$(LYRA_STACK_DIR)" ]; then \
		echo "Error: lyra-stack not found at $(LYRA_STACK_DIR)"; \
		echo "       Clone it or set LYRA_STACK_DIR=/path/to/lyra-stack"; \
		exit 1; \
	fi
	@mkdir -p "$(LYRA_STACK_DIR)/conf.d"
	@ln -sf "$(abspath supervisor/conf.d/lyra_telegram.conf)" "$(LYRA_STACK_DIR)/conf.d/lyra_telegram.conf"
	@ln -sf "$(abspath supervisor/conf.d/lyra_discord.conf)"  "$(LYRA_STACK_DIR)/conf.d/lyra_discord.conf"
	@if [ -L "$(LYRA_STACK_DIR)/conf.d/lyra.conf" ]; then rm "$(LYRA_STACK_DIR)/conf.d/lyra.conf"; fi
	@mkdir -p "$(HOME)/.local/state/lyra/logs"
	@if [ -S "$(LYRA_STACK_DIR)/supervisor.sock" ]; then \
		$(SUPERVISORCTL) reread && $(SUPERVISORCTL) update; \
	fi
	@echo ""
	@echo "Installing monitoring systemd timer..."
	@mkdir -p "$(SYSTEMD_USER_DIR)"
	@cp "$(abspath deploy/lyra-monitor.service)" "$(SYSTEMD_USER_DIR)/lyra-monitor.service"
	@cp "$(abspath deploy/lyra-monitor.timer)"   "$(SYSTEMD_USER_DIR)/lyra-monitor.timer"
	@systemctl --user daemon-reload
	@systemctl --user enable lyra-monitor.timer
	@echo ""
	@echo "Done."
	@echo "  Adapters: run 'make telegram' and 'make discord' to start."
	@echo "  Monitor:  run 'make monitor enable' to start the health check timer."
	@echo "  Secrets:  ensure TELEGRAM_TOKEN, ANTHROPIC_API_KEY, TELEGRAM_ADMIN_CHAT_ID are in .env"

deploy:
	$(require_machine1)
	@echo "Deploying to Machine 1 ($(DEPLOY_HOST))..."
	@ssh $(DEPLOY_HOST) "cd $(DEPLOY_DIR) && bash scripts/deploy.sh"

REMOTE_SCTL := ~/projects/lyra-stack/scripts/supervisorctl.sh

# make remote [service] [action]
#   Services: lyra, telegram, discord, tts, stt (default: all)
#   Actions:  reload, start, stop, status, logs, errors (default: status)
remote:
	$(require_machine1)
	@SVC="$(word 1,$(REMOTE_CMD))"; \
	ACTION="$(word 2,$(REMOTE_CMD))"; \
	SCTL="$(REMOTE_SCTL)"; \
	case "$$SVC" in \
	  lyra)     PROGS="lyra_telegram lyra_discord" ;; \
	  telegram) PROGS="lyra_telegram" ;; \
	  discord)  PROGS="lyra_discord" ;; \
	  tts)      PROGS="voicecli_tts" ;; \
	  stt)      PROGS="voicecli_stt" ;; \
	  reload|start|stop|status|logs|errors|"") \
	    ACTION="$$SVC"; PROGS="lyra_telegram lyra_discord voicecli_tts voicecli_stt" ;; \
	  *) echo "Unknown service: $$SVC"; exit 1 ;; \
	esac; \
	case "$${ACTION:-status}" in \
	  reload)  ssh $(DEPLOY_HOST) "$$SCTL restart $$PROGS" ;; \
	  start)   ssh $(DEPLOY_HOST) "$$SCTL start $$PROGS" ;; \
	  stop)    ssh $(DEPLOY_HOST) "$$SCTL stop $$PROGS" ;; \
	  status)  ssh $(DEPLOY_HOST) "$$SCTL status $$PROGS" ;; \
	  logs)    FIRST=$${PROGS%% *}; ssh $(DEPLOY_HOST) "$$SCTL tail -f $$FIRST" ;; \
	  errors)  FIRST=$${PROGS%% *}; ssh $(DEPLOY_HOST) "$$SCTL tail -f $$FIRST stderr" ;; \
	  *) echo "Unknown action: $$ACTION"; exit 1 ;; \
	esac

test:
	uv run pytest -v

lint:
	uv run ruff check .

typecheck:
	uv run pyright

format:
	uv run ruff format .
