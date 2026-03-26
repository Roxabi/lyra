ifeq (lyra,$(firstword $(MAKECMDGOALS)))
  LYRA_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  $(eval $(LYRA_CMD):;@:)
endif

ifeq (remote,$(firstword $(MAKECMDGOALS)))
  REMOTE_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  $(eval $(REMOTE_CMD):;@:)
endif

ifeq (telegram,$(firstword $(MAKECMDGOALS)))
  TELEGRAM_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  $(eval $(TELEGRAM_CMD):;@:)
endif

ifeq (discord,$(firstword $(MAKECMDGOALS)))
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

MACHINE1 := $(shell grep '^MACHINE1_HOST=' .env 2>/dev/null | cut -d= -f2)
MACHINE1_DIR := $(shell grep '^MACHINE1_DIR=' .env 2>/dev/null | cut -d= -f2)

define require_machine1
	@[ -n "$(MACHINE1)" ] || { echo "Error: MACHINE1_HOST not set in .env"; exit 1; }
	@[ -n "$(MACHINE1_DIR)" ] || { echo "Error: MACHINE1_DIR not set in .env"; exit 1; }
endef

.PHONY: lyra telegram discord register deploy remote test lint typecheck format

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
	@echo "Done. Run 'make telegram' and 'make discord' to start."

deploy:
	$(require_machine1)
	@echo "Deploying to Machine 1 ($(MACHINE1))..."
	@ssh $(MACHINE1) "cd $(MACHINE1_DIR) && bash scripts/deploy.sh"

remote:
	$(require_machine1)
	@ssh $(MACHINE1) "cd $(MACHINE1_DIR) && make lyra $(REMOTE_CMD)"

test:
	uv run pytest -v

lint:
	uv run ruff check .

typecheck:
	uv run pyright

format:
	uv run ruff format .
