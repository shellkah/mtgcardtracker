# MTG Card Tracker — developer and operator tasks.
# Run `make` or `make help` to list the available targets.

COMPOSE ?= docker compose
IMAGE   ?= ghcr.io/shellkah/mtgcardtracker:local
SERVICE ?= mtgcardtracker

.DEFAULT_GOAL := help
.PHONY: help install run test test-q clean build up down logs pull redeploy

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies from uv.lock
	uv sync

run: ## Run the app locally with autoreload (http://127.0.0.1:8000)
	uv run uvicorn app.main:app --reload

test: ## Run the test suite (verbose)
	uv run pytest -v

test-q: ## Run the test suite (quiet)
	uv run pytest -q

clean: ## Remove Python caches (keeps .venv and *.db)
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache

build: ## Build the container image locally
	docker build -t $(IMAGE) .

up: ## Start the stack (override the tag with `make up MTG_TAG=0.1.0`)
	$(COMPOSE) up -d

down: ## Stop the stack (keeps the data volume)
	$(COMPOSE) down

logs: ## Follow the app container's logs
	$(COMPOSE) logs -f $(SERVICE)

pull: ## Pull the image tag configured by MTG_TAG
	$(COMPOSE) pull

redeploy: pull up ## Pull the configured image, then restart the stack
