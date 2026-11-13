PYTHON ?= python3
VENV := .venv
UVICORN := ../$(VENV)/bin/uvicorn
PYTEST := ../$(VENV)/bin/pytest
ALEMBIC := ../$(VENV)/bin/alembic
RUFF := $(VENV)/bin/ruff
MYPY := ../$(VENV)/bin/mypy

.PHONY: help install env run test lint format typecheck migrate check docker-up docker-down docker-logs lock

help:
	@echo "AutoEscrow V2 development targets:"
	@echo "  make install     Create .venv and install backend dependencies"
	@echo "  make env         Copy .env.example to .env and generate a JWT secret"
	@echo "  make lock        Regenerate backend/requirements.lock from the venv"
	@echo "  make run         Run the API with autoreload (requires Postgres and Redis)"
	@echo "  make migrate     Apply Alembic migrations"
	@echo "  make test        Run the test suite (applies migrations automatically)"
	@echo "  make lint        Check lint and formatting"
	@echo "  make format      Auto-fix lint issues and format code"
	@echo "  make typecheck   Run mypy"
	@echo "  make check       Run lint, typecheck and tests"
	@echo "  make docker-up   Build and start the full local stack"
	@echo "  make docker-down Stop the local stack"

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/python -m pip install --upgrade pip
	$(VENV)/bin/python -m pip install -c backend/requirements.lock -e "./backend[dev]"

env:
	@test -f .env || cp .env.example .env
	@$(PYTHON) backend/scripts/setup_env.py

lock:
	@$(VENV)/bin/python -m pip freeze --exclude-editable | sort > backend/requirements.lock
	@echo "Wrote backend/requirements.lock"

run:
	cd backend && $(UVICORN) autoescrow.main:app --reload --host 0.0.0.0 --port 8000

test:
	cd backend && $(PYTEST)

lint:
	$(RUFF) check backend
	$(RUFF) format --check backend

format:
	$(RUFF) check --fix backend
	$(RUFF) format backend

typecheck:
	cd backend && $(MYPY) src

migrate:
	cd backend && $(ALEMBIC) upgrade head

check: lint typecheck test

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f api
