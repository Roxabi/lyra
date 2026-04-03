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

SUPERVISORCTL  := deploy/supervisor/supervisorctl.sh
SUPERVISOR_START := deploy/supervisor/start.sh
HUB_PID        := deploy/supervisor/supervisord.pid

define ensure_supervisor
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

.PHONY: lyra telegram discord monitor comfyui register deploy remote nats-install test lint typecheck format

ifeq (monitor,$(firstword $(MAKECMDGOALS)))
  MONITOR_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  $(eval $(MONITOR_CMD):;@:)
endif

ifeq (comfyui,$(firstword $(MAKECMDGOALS)))
  COMFYUI_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  $(eval $(COMFYUI_CMD):;@:)
endif

ifneq (remote,$(firstword $(MAKECMDGOALS)))
lyra:
ifeq ($(LYRA_CMD),stop)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) stop lyra_hub
	@$(SUPERVISORCTL) stop lyra_telegram
	@$(SUPERVISORCTL) stop lyra_discord
else ifeq ($(LYRA_CMD),reload)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) restart lyra_hub
	@$(SUPERVISORCTL) restart lyra_telegram
	@$(SUPERVISORCTL) restart lyra_discord
else ifeq ($(LYRA_CMD),logs)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) tail -f lyra_hub
else ifeq ($(LYRA_CMD),errors)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) tail -f lyra_hub stderr
else ifeq ($(LYRA_CMD),status)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) status lyra_hub
	@$(SUPERVISORCTL) status lyra_telegram
	@$(SUPERVISORCTL) status lyra_discord
else ifeq ($(LYRA_CMD),)
	@$(SUPERVISOR_START)
	@$(SUPERVISORCTL) start lyra_hub
	@$(SUPERVISORCTL) start lyra_telegram
	@$(SUPERVISORCTL) start lyra_discord
else
	$(ensure_supervisor)
	@$(SUPERVISORCTL) $(LYRA_CMD) lyra_hub
	@$(SUPERVISORCTL) $(LYRA_CMD) lyra_telegram
	@$(SUPERVISORCTL) $(LYRA_CMD) lyra_discord
endif

telegram:
ifeq ($(TELEGRAM_CMD),stop)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) stop lyra_telegram
else ifeq ($(TELEGRAM_CMD),reload)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) stop lyra_telegram
	@sleep 1
	@$(SUPERVISORCTL) start lyra_telegram
else ifeq ($(TELEGRAM_CMD),logs)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) tail -f lyra_telegram
else ifeq ($(TELEGRAM_CMD),errors)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) tail -f lyra_telegram stderr
else ifeq ($(TELEGRAM_CMD),status)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) status lyra_telegram
else ifeq ($(TELEGRAM_CMD),)
	@$(SUPERVISOR_START)
	@$(SUPERVISORCTL) start lyra_telegram
else
	$(ensure_supervisor)
	@$(SUPERVISORCTL) $(TELEGRAM_CMD) lyra_telegram
endif

discord:
ifeq ($(DISCORD_CMD),stop)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) stop lyra_discord
else ifeq ($(DISCORD_CMD),reload)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) stop lyra_discord
	@sleep 1
	@$(SUPERVISORCTL) start lyra_discord
else ifeq ($(DISCORD_CMD),logs)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) tail -f lyra_discord
else ifeq ($(DISCORD_CMD),errors)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) tail -f lyra_discord stderr
else ifeq ($(DISCORD_CMD),status)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) status lyra_discord
else ifeq ($(DISCORD_CMD),)
	@$(SUPERVISOR_START)
	@$(SUPERVISORCTL) start lyra_discord
else
	$(ensure_supervisor)
	@$(SUPERVISORCTL) $(DISCORD_CMD) lyra_discord
endif

endif # ifneq remote

# ── Machine 2 local tools (not managed by supervisor) ────────────────────────
COMFYUI_DIR := $(HOME)/ComfyUI
COMFYUI_PID := /tmp/comfyui.pid
COMFYUI_LOG := /tmp/comfyui.log  # overwritten on each start (intentional)

comfyui:
ifeq ($(COMFYUI_CMD),logs)
	@tail -f $(COMFYUI_LOG)
else ifeq ($(COMFYUI_CMD),stop)
	@kill $$(cat $(COMFYUI_PID) 2>/dev/null) 2>/dev/null && echo "ComfyUI stopped" || echo "ComfyUI not running"
	@rm -f $(COMFYUI_PID)
else ifeq ($(COMFYUI_CMD),status)
	@pgrep -f "$(COMFYUI_DIR)/venv/bin/python" > /dev/null && echo "ComfyUI running" || echo "ComfyUI not running"
else
	@[ -d "$(COMFYUI_DIR)" ] || { echo "Error: ComfyUI not found at $(COMFYUI_DIR)"; exit 1; }
	@pgrep -qf "$(COMFYUI_DIR)/venv/bin/python" && echo "ComfyUI already running. Use 'make comfyui stop'." && exit 0 || true
	@echo "Starting ComfyUI at http://localhost:8188 (log → $(COMFYUI_LOG), overwritten each start)"
	@cd $(COMFYUI_DIR); nohup venv/bin/python main.py --listen 127.0.0.1 --port 8188 > $(COMFYUI_LOG) 2>&1 & echo $$! > $(COMFYUI_PID); echo "Started — PID $$(cat $(COMFYUI_PID))"
endif

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
	@echo "Registering lyra supervisor..."
	@mkdir -p "$(HOME)/.local/state/lyra/logs"
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
	@SVC="$(word 1,$(REMOTE_CMD))"; \
	ACTION="$(word 2,$(REMOTE_CMD))"; \
	SCTL="$(REMOTE_SCTL)"; \
	case "$$SVC" in \
	  lyra)     PROGS="lyra_hub lyra_telegram lyra_discord" ;; \
	  hub)      PROGS="lyra_hub" ;; \
	  telegram) PROGS="lyra_telegram" ;; \
	  discord)  PROGS="lyra_discord" ;; \
	  reload|start|stop|status|logs|errors|"") \
	    ACTION="$$SVC"; PROGS="lyra_hub lyra_telegram lyra_discord" ;; \
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

nats-install:
	@bash deploy/nats/install.sh

test:
	uv run pytest -v

lint:
	uv run ruff check .

typecheck:
	uv run pyright

format:
	uv run ruff format .
