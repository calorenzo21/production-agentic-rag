.PHONY: help start stop restart status logs health setup format lint test test-cov smoke clean

# Default target
help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# Service management
start: ## Start all services
	docker compose up --build -d

stop: ## Stop all services
	docker compose down

restart: ## Restart all services
	docker compose restart

status: ## Show service status
	docker compose ps

logs: ## Show service logs
	docker compose logs -f

# Health checks
health: ## Check all services health
	@echo "Checking service health..."
	@set -a; . ./.env; set +a; \
	(curl -fsS http://localhost:8000/api/v1/health || echo '"API not responding"') | jq .; \
	(curl -fsS --cacert secrets/opensearch/ca.crt --user "rag_app:$$OPENSEARCH_APP_PASSWORD" https://localhost:9200/_cluster/health || echo '"OpenSearch not responding"') | jq .; \
	(curl -fsS http://localhost:8080/api/v2/monitor/health || echo '"Airflow not responding"') | jq .; \
	(curl -fsS http://localhost:11434/api/version || echo '"Ollama not responding"') | jq .

# Development
setup: ## Install dependencies, enable git hooks and generate missing secrets
	uv sync
	git config core.hooksPath .githooks
	scripts/generate-secrets.sh

format: ## Format code
	uv run ruff format

lint: ## Lint and type check
	uv run ruff check --fix
	uv run mypy src/

test: ## Run tests
	uv run pytest

test-cov: ## Run tests with coverage
	uv run pytest --cov=src --cov-report=html

smoke: ## Check a running stack, including every security Deviation
	uv run pytest tests/stack

# Cleanup
clean: ## Remove this project's containers and volumes
	docker compose down -v
