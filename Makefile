ifeq (lyra,$(firstword $(MAKECMDGOALS)))
  LYRA_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  $(eval $(LYRA_CMD):;@:)
endif

ifeq (remote,$(firstword $(MAKECMDGOALS)))
  REMOTE_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  $(eval $(REMOTE_CMD):;@:)
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

.PHONY: lyra register deploy remote test lint typecheck format

lyra:
ifeq ($(LYRA_CMD),stop)
	$(ensure_hub)
	@$(SUPERVISORCTL) stop lyra
else ifeq ($(LYRA_CMD),reload)
	$(ensure_hub)
	@$(SUPERVISORCTL) stop lyra
	@sleep 1
	@$(SUPERVISORCTL) start lyra
else ifeq ($(LYRA_CMD),logs)
	$(ensure_hub)
	@$(SUPERVISORCTL) tail -f lyra
else ifeq ($(LYRA_CMD),errors)
	$(ensure_hub)
	@$(SUPERVISORCTL) tail -f lyra stderr
else ifeq ($(LYRA_CMD),status)
	$(ensure_hub)
	@$(SUPERVISORCTL) status
else ifeq ($(LYRA_CMD),)
	@$(SUPERVISOR_START)
	@$(SUPERVISORCTL) start lyra
else
	$(ensure_hub)
	@$(SUPERVISORCTL) $(LYRA_CMD) lyra
endif

register:
	@echo "Registering lyra with lyra-stack..."
	@if [ ! -d "$(LYRA_STACK_DIR)" ]; then \
		echo "Error: lyra-stack not found at $(LYRA_STACK_DIR)"; \
		echo "       Clone it or set LYRA_STACK_DIR=/path/to/lyra-stack"; \
		exit 1; \
	fi
	@mkdir -p "$(LYRA_STACK_DIR)/conf.d"
	@ln -sf "$(abspath supervisor/conf.d/lyra.conf)" "$(LYRA_STACK_DIR)/conf.d/lyra.conf"
	@mkdir -p supervisor/logs
	@if [ -S "$(LYRA_STACK_DIR)/supervisor.sock" ]; then \
		$(SUPERVISORCTL) reread && $(SUPERVISORCTL) update; \
	fi
	@echo "Done. Run 'make lyra' to start."

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
