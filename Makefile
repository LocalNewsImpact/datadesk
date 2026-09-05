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

# Postgres for the test suite.
#
# Locally: docker-compose.test.yml on 5435 -- the crawler has 5432 and
# 5433, the Source Directory 5434 (lnic-contracts docs/shared-ci.md).
#
# In CI: a service container the shared workflow provides
# (lnic-contracts python-checks.yml), which announces itself through the
# standard PG* variables. Those win where they are set, and no compose
# database is started -- one `make test` that means the same thing in
# both places, which is the whole point of the shared pattern.
DATADESK_TEST_DB_HOST     ?= $(if $(PGHOST),$(PGHOST),127.0.0.1)
DATADESK_TEST_DB_PORT     ?= $(if $(PGPORT),$(PGPORT),5435)
DATADESK_TEST_DB_USER     ?= $(if $(PGUSER),$(PGUSER),datadesk)
DATADESK_TEST_DB_PASSWORD ?= $(if $(PGPASSWORD),$(PGPASSWORD),datadesk)
export DATADESK_TEST_DB_HOST DATADESK_TEST_DB_PORT
export DATADESK_TEST_DB_USER DATADESK_TEST_DB_PASSWORD

.PHONY: test-db
test-db: $(VENV) ## Start the local test database and wait for it
	docker compose -f docker-compose.test.yml up -d --wait
# Whoever answers on the port must be this container. A host process
# already listening there is not an error to Docker Desktop: the
# container starts, `docker ps` reports the binding, `--wait` passes (the
# health check runs inside), and the host process keeps answering -- so
# the suite would run against it. Another container on the port fails
# loudly; a process does not. So: the cluster's identifier, asked inside
# the container and asked through the host port, must be the same one.
	@inside=$$(docker compose -f docker-compose.test.yml exec -T postgres \
	    psql -U datadesk -tAc 'select system_identifier from pg_control_system()'); \
	outside=$$($(PY) -c "import psycopg; print(psycopg.connect( \
	    host='$(DATADESK_TEST_DB_HOST)', port='$(DATADESK_TEST_DB_PORT)', \
	    user='$(DATADESK_TEST_DB_USER)', password='$(DATADESK_TEST_DB_PASSWORD)', \
	    dbname='datadesk', connect_timeout=5 \
	    ).execute('select system_identifier from pg_control_system()').fetchone()[0])" 2>/dev/null); \
	if [ -z "$$inside" ] || [ "$$inside" != "$$outside" ]; then \
	  echo "what answers on $(DATADESK_TEST_DB_HOST):$(DATADESK_TEST_DB_PORT) is not the test database" >&2; \
	  echo "(container cluster $${inside:-unknown}, port answered $${outside:-nothing})." >&2; \
	  echo "Something else holds the port: lsof -nP -iTCP:$(DATADESK_TEST_DB_PORT) -sTCP:LISTEN" >&2; \
	  exit 1; \
	fi; \
	echo "test database on $(DATADESK_TEST_DB_HOST):$(DATADESK_TEST_DB_PORT) (cluster $$inside)"

.PHONY: test-db-down
test-db-down: ## Stop the local test database
	docker compose -f docker-compose.test.yml down

.PHONY: test
test: $(VENV) ## Run the test suite (Postgres, as production is)
# Start the compose database only when nothing else has provided one. A
# `docker compose up` on a runner that already has a Postgres service is
# a second database and a slower, more confusing failure.
	@if [ -z "$(PGHOST)" ]; then $(MAKE) --no-print-directory test-db; fi
# The same two steps in the same order. A model changed without its
# migration passes pytest and fails the build, so the check belongs here
# rather than being discovered on a pull request.
	$(PY) manage.py makemigrations --check --dry-run
	$(PY) -m pytest --cov --cov-report=xml --cov-report=term
# The suite's coverage floor, from the package every repository installs.
# The shared workflow runs the same file after this target in CI.
	$(PY) -m lnic_contracts.coverage_floor coverage.xml

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
