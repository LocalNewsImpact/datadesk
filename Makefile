# One-command local setup and the same checks CI runs.
#
#   make setup     first time: venv, deps, .env, database
#   make check     everything CI will run, before you push
#
.DEFAULT_GOAL := help
SHELL := /bin/bash
VENV  := .venv
PY    := $(VENV)/bin/python
PIP   := $(VENV)/bin/pip

# Development env vars live in .env; export them for manage.py targets.
define WITH_ENV
set -a; [ -f .env ] && source .env; set +a;
endef

.PHONY: help
help: ## Show these targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk -F':.*?## ' '{printf "  \033[1m%-16s\033[0m %s\n", $$1, $$2}'

# --- setup ------------------------------------------------------------------

.PHONY: setup
setup: $(VENV) .env migrate ## Provision everything for a new checkout
	@echo
	@echo "Ready. 'make run' starts the server, 'make check' runs what CI runs."
	@echo "First time? 'make superuser' to create an admin login."

$(VENV):
	python3 -m venv $(VENV)
	$(PIP) install --quiet --upgrade pip
	$(PIP) install --quiet -r requirements-dev.txt

.env:
	cp .env.example .env
	@echo "wrote .env from .env.example"

# --- django -----------------------------------------------------------------

.PHONY: migrate
migrate: $(VENV) ## Apply database migrations (local sqlite)
	@$(WITH_ENV) $(PY) manage.py migrate

.PHONY: migrations
migrations: $(VENV) ## Generate migrations after a model change
	@$(WITH_ENV) $(PY) manage.py makemigrations

.PHONY: superuser
superuser: $(VENV) ## Create an admin login
	@$(WITH_ENV) $(PY) manage.py createsuperuser

.PHONY: run
run: $(VENV) migrate ## Start the server at http://localhost:8000/
	@$(WITH_ENV) $(PY) manage.py runserver

# --- checks -----------------------------------------------------------------

.PHONY: lint
lint: $(VENV) ## Lint, format and type checks (ruff, black, isort, mypy)
	$(VENV)/bin/ruff check .
	$(VENV)/bin/black --check .
	$(VENV)/bin/isort --check-only .
	$(VENV)/bin/mypy .

.PHONY: fmt
fmt: $(VENV) ## Apply formatting and safe fixes
	$(VENV)/bin/ruff check --fix .
	$(VENV)/bin/black .
	$(VENV)/bin/isort .

.PHONY: test
test: $(VENV) ## Run the test suite (sqlite, no services needed)
	$(PY) -m pytest

.PHONY: check
check: lint test ## Everything CI runs

.PHONY: clean
clean: ## Remove build and cache artefacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache staticfiles db.sqlite3
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
