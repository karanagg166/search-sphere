.PHONY: help build up down down-v restart status logs logs-api logs-worker logs-web logs-qdrant logs-postgres shell-api shell-worker shell-web shell-db shell-redis test lint format clean

help: ## Show available commands
	@echo "Search Sphere Monorepo Commands:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

build: ## Build all Docker service images
	docker compose build

up: ## Start all services in the background (detached mode)
	docker compose up -d

dev: ## Start all services with logs attached
	docker compose up

down: ## Stop all running services
	docker compose down

down-v: ## Stop all services and remove persistent volumes
	docker compose down -v

restart: ## Restart all services
	docker compose restart

status: ## Show status of all services
	docker compose ps

logs: ## View and stream logs from all services
	docker compose logs -f

logs-api: ## View and stream logs from the FastAPI backend service
	docker compose logs -f api

logs-web: ## View and stream logs from the Next.js frontend service
	docker compose logs -f web

shell-api: ## Open an interactive bash shell in the API container
	docker compose exec api bash

shell-web: ## Open an interactive shell in the Next.js web container
	docker compose exec web sh

test: ## Run test suite in the API container
	docker compose exec api pytest

lint: ## Run linter and type-checker in the API container
	docker compose exec api ruff check .
	docker compose exec api mypy src

format: ## Format Python code with ruff
	docker compose exec api ruff format .

clean: ## Remove temporary python cache and stopped containers
	docker compose down --remove-orphans
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
