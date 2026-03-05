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

.PHONY: lyra test lint typecheck format

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

test:
	uv run pytest -v

lint:
	uv run ruff check .

typecheck:
	uv run pyright

format:
	uv run ruff format .
