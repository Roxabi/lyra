ifeq (lyra,$(firstword $(MAKECMDGOALS)))
  LYRA_CMD := $(wordlist 2,$(words $(MAKECMDGOALS)),$(MAKECMDGOALS))
  $(eval $(LYRA_CMD):;@:)
endif

SUPERVISORCTL := ./supervisor/scripts/supervisorctl.sh
SUPERVISOR_START := ./supervisor/scripts/start.sh
SUPERVISOR_STOP := ./supervisor/scripts/stop.sh
SUPERVISOR_DIR := ./supervisor
SUPERVISOR_PID := $(SUPERVISOR_DIR)/supervisord.pid

define ensure_supervisor
	@if [ ! -f "$(SUPERVISOR_PID)" ] || ! kill -0 $$(cat "$(SUPERVISOR_PID)" 2>/dev/null) 2>/dev/null; then \
		echo "supervisord not running, starting..."; \
		$(SUPERVISOR_START); \
	fi
endef

MACHINE1 := mickael@192.168.1.16
MACHINE1_DIR := ~/projects/lyra

.PHONY: lyra deploy test lint typecheck format

lyra:
ifeq ($(LYRA_CMD),stop)
	@$(SUPERVISOR_STOP)
else ifeq ($(LYRA_CMD),reload)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) stop lyra
	@sleep 1
	@$(SUPERVISORCTL) start lyra
else ifeq ($(LYRA_CMD),logs)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) tail -f lyra
else ifeq ($(LYRA_CMD),errors)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) tail -f lyra stderr
else ifeq ($(LYRA_CMD),status)
	$(ensure_supervisor)
	@$(SUPERVISORCTL) status
else ifeq ($(LYRA_CMD),)
	@$(SUPERVISOR_START)
else
	$(ensure_supervisor)
	@$(SUPERVISORCTL) $(LYRA_CMD) lyra
endif

deploy:
	@echo "Deploying to Machine 1 ($(MACHINE1))..."
	@ssh $(MACHINE1) "cd $(MACHINE1_DIR) && bash scripts/deploy.sh"

test:
	uv run pytest -v

lint:
	uv run ruff check .

typecheck:
	uv run pyright

format:
	uv run ruff format .
