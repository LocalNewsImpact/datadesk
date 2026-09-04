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

# Postgres for the test suite, on 5434 so it cannot be confused with the
# crawler's test database (5432) or the scratch instance (5433).
TEST_DB_ENV = DATADESK_TEST_DB_HOST=127.0.0.1 DATADESK_TEST_DB_PORT=5434 \
	DATADESK_TEST_DB_USER=datadesk DATADESK_TEST_DB_PASSWORD=datadesk

.PHONY: test-db
test-db: ## Start the test database and wait for it
	docker compose -f docker-compose.test.yml up -d --wait

.PHONY: test-db-down
test-db-down: ## Stop the test database
	docker compose -f docker-compose.test.yml down

.PHONY: test
test: $(VENV) test-db ## Run the test suite (Postgres, as production is)
# The same two steps CI's `tests` job runs, in the same order. A model
# changed without its migration passes pytest and fails the build, so
# the check belongs here rather than being discovered on a pull request.
	$(TEST_DB_ENV) $(PY) manage.py makemigrations --check --dry-run
	$(TEST_DB_ENV) $(PY) -m pytest

# Both need the crawler's real database — the Cloud SQL Auth Proxy, or
# Cloud Run's socket. Not part of `check`, which must run offline.
.PHONY: crawler-schema
crawler-schema: $(VENV) ## Do the unmanaged models still match the crawler's schema?
	$(PY) manage.py check_crawler_schema

.PHONY: smoke-queries
smoke-queries: $(VENV) ## Do the console's read paths run against the real databases?
	$(PY) manage.py smoke_queries

.PHONY: check
# Run this before pushing. `lint` is CI's lint job (ruff, black, isort,
# mypy) and `test` is CI's tests job (makemigrations --check, pytest).
# Running ruff and black alone passes locally and fails on the pull
# request, which is slower for everyone.
check: lint test ## Everything CI runs — run before pushing

.PHONY: clean
clean: ## Remove build and cache artefacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache staticfiles db.sqlite3 crawler.sqlite3
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
