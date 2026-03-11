ifeq (lyra,$(firstword $(MAKECMDGOALS)))
  LYRA_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  $(eval $(LYRA_CMD):;@:)
endif

ifeq (remote,$(firstword $(MAKECMDGOALS)))
  REMOTE_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  $(eval $(REMOTE_CMD):;@:)
endif

SUPERVISORCTL := $(HOME)/lyra-stack/scripts/supervisorctl.sh
SUPERVISOR_START := $(HOME)/lyra-stack/scripts/start.sh
HUB_DIR := $(HOME)/lyra-stack
HUB_PID := $(HUB_DIR)/supervisord.pid

define ensure_hub
	@if [ ! -d "$(HUB_DIR)" ]; then \
		echo "Error: ~/lyra-stack not found. Set up lyra-stack first."; \
		exit 1; \
	fi
	@if [ ! -f "$(HUB_PID)" ] || ! kill -0 $$(cat "$(HUB_PID)" 2>/dev/null) 2>/dev/null; then \
		echo "Hub supervisord not running, starting..."; \
		$(SUPERVISOR_START); \
	fi
endef

MACHINE1 := $(or $(shell grep '^MACHINE1_HOST=' .env 2>/dev/null | cut -d= -f2),mickael@192.168.1.16)
MACHINE1_DIR := $(or $(shell grep '^MACHINE1_DIR=' .env 2>/dev/null | cut -d= -f2),~/projects/lyra)

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
	@echo "Registering lyra with global supervisor..."
	@if [ ! -d "$(HUB_DIR)" ]; then \
		echo "Error: ~/lyra-stack not found."; exit 1; \
	fi
	@ln -sf "$(abspath supervisor/conf.d/lyra.conf)" "$(HUB_DIR)/conf.d/lyra.conf"
	@mkdir -p supervisor/logs
	@if [ -S "$(HUB_DIR)/supervisor.sock" ]; then \
		$(SUPERVISORCTL) reread && $(SUPERVISORCTL) update; \
	fi
	@echo "Done. Run 'make lyra' to start."

deploy:
	@echo "Deploying to Machine 1 ($(MACHINE1))..."
	@ssh $(MACHINE1) "cd $(MACHINE1_DIR) && bash scripts/deploy.sh"

remote:
	@ssh $(MACHINE1) "cd $(MACHINE1_DIR) && make lyra $(REMOTE_CMD)"

test:
	uv run pytest -v

lint:
	uv run ruff check .

typecheck:
	uv run pyright

format:
	uv run ruff format .
