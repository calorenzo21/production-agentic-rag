.DEFAULT_GOAL := help
.PHONY: help dev down prod prod-down check format smoke prod-smoke backup prod-backup restore prod-restore

PROD := COMPOSE_FILE=compose.yaml:compose.prod.yaml

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "%-14s %s\n", $$1, $$2}'

dev: ## Start development mode (needs .env, copied from .env.example)
	docker compose up --build --detach --wait --wait-timeout 600

down: ## Stop development mode
	docker compose down

prod: ## Generate missing secrets and start production mode
	scripts/generate-secrets.sh
	$(PROD) docker compose up --build --detach --wait --wait-timeout 600

prod-down: ## Stop production mode
	$(PROD) docker compose down

format: ## Format the code and apply safe lint fixes
	uv run ruff format .
	uv run ruff check --fix .

check: ## Check formatting, lint and types, and run the tests that need no stack
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pytest

smoke: ## Test a running development mode end to end
	STACK_MODE=dev uv run pytest tests/stack

prod-smoke: ## Test a running production mode end to end
	STACK_MODE=prod uv run pytest tests/stack

backup: ## Back up development mode's databases
	scripts/backup.sh

prod-backup: ## Back up production mode's databases
	$(PROD) scripts/backup.sh

restore: ## Restore development mode's databases: make restore BACKUP=backups/<timestamp>
	scripts/restore.sh $(BACKUP)

prod-restore: ## Restore production mode's databases: make prod-restore BACKUP=backups/<timestamp>
	$(PROD) scripts/restore.sh $(BACKUP)
