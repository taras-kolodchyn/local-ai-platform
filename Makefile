SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE := docker compose --project-name local-ai-platform --file compose.yaml
REPO ?=

.PHONY: help doctor bootstrap pull-model up down restart logs status smoke-test test lint index reindex benchmark hermes-smoke vscode-install vscode-check vscode-smoke vscode connected-up connected-smoke connected-down clean reset configure-codex configure-hermes

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "; printf "Local AI Platform\n\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

doctor: ## Check macOS, Apple Silicon, memory, Docker, DMR, ports, and tools including Cargo
	@bash scripts/doctor.sh

bootstrap: ## Create local secrets, directories, and generated support files
	@bash scripts/bootstrap.sh

pull-model: bootstrap ## Enable DMR and pull pinned Qwen artifacts
	@bash scripts/pull-model.sh

up: ## Start the complete local stack and run smoke checks
	@bash scripts/up.sh

down: ## Stop the complete stack and unload its models; preserve data and artifacts
	@bash scripts/down.sh

restart: ## Stop and start the complete stack, including Moyai and inference
	@$(MAKE) down
	@$(MAKE) up

logs: ## Follow service logs (metadata-only policy)
	@$(COMPOSE) logs --follow --tail=200

status: ## Show containers, DMR models, and endpoints
	@bash scripts/status.sh

vscode-install: ## Install or update the official Codex extension for VS Code
	@bash scripts/vscode.sh install

vscode-check: ## Verify the VS Code extension, local Codex config, MCP, and provider
	@bash scripts/vscode.sh check

vscode-smoke: ## Run one ephemeral local turn through the extension's Codex runtime
	@bash scripts/vscode.sh smoke

vscode: ## Open VS Code with the isolated local Codex configuration
	@bash scripts/vscode.sh launch

smoke-test: ## Verify live endpoints, bridge, embeddings, cache, pgvector, MCP, and metrics
	@bash scripts/smoke-test.sh

test: ## Run unit, contract, isolated PostgreSQL, and required Rust tests
	@bash scripts/test.sh

lint: ## Validate shell, Python, YAML/Compose, and configuration invariants
	@bash scripts/lint.sh

index: ## Incrementally index REPO=/absolute/path through local embeddings
	@test -n "$(REPO)" || { echo "Usage: make index REPO=/absolute/path" >&2; exit 2; }
	@test -d "$(REPO)" || { echo "Repository path does not exist: $(REPO)" >&2; exit 2; }
	@INDEX_REPO_PATH="$(abspath $(REPO))" $(COMPOSE) --profile tools run --rm ingestion index /workspace --repository "$(notdir $(abspath $(REPO)))"

reindex: ## Delete and rebuild the selected REPO=/absolute/path snapshot
	@test -n "$(REPO)" || { echo "Usage: make reindex REPO=/absolute/path" >&2; exit 2; }
	@INDEX_REPO_PATH="$(abspath $(REPO))" $(COMPOSE) --profile tools run --rm ingestion reindex /workspace --repository "$(notdir $(abspath $(REPO)))"

benchmark: ## Record local chat and retrieval latency without prompt bodies
	@bash scripts/benchmark.sh

hermes-smoke: ## Run a one-shot Hermes client check through the same LiteLLM gateway
	@bash scripts/hermes-smoke.sh

connected-up: ## Opt in to read-only GitHub MCP using .env.connected
	@bash scripts/connected-up.sh

connected-smoke: ## Verify the connected GitHub MCP allowlist and log redaction
	@bash scripts/connected-smoke.sh

connected-down: ## Stop the opt-in GitHub MCP service without touching offline data
	@$(COMPOSE) --profile connected stop github-mcp

configure-codex: bootstrap ## Generate a Codex config example under .local/
	@bash scripts/configure-codex.sh

configure-hermes: bootstrap ## Generate a Hermes config example under .local/
	@bash scripts/configure-hermes.sh

clean: ## Remove generated caches/logs but preserve databases and models
	@bash scripts/clean.sh

reset: ## Delete local containers and volumes (requires CONFIRM_RESET=yes)
	@test "$(CONFIRM_RESET)" = "yes" || { echo "Refusing destructive reset. Re-run with CONFIRM_RESET=yes" >&2; exit 2; }
	@$(COMPOSE) down --volumes --remove-orphans
	@echo "Local Compose volumes removed. DMR model artifacts were preserved."

.PHONY: vector-smoke memory-smoke runtime-smoke workspace-smoke
vector-smoke: ## Verify registered vector search and access boundaries
	@python3 scripts/vector-smoke.py

memory-smoke: ## Verify persistent semantic memory and profile isolation
	@python3 scripts/memory-smoke.py

runtime-smoke: ## Verify Mem0 activation and tools in each real agent runtime
	@python3 scripts/runtime-smoke.py

workspace-smoke: ## Verify real profile execution through the gateway
	@python3 scripts/workspace-smoke.py

.PHONY: workspace
workspace: ## Run PROFILE=development|review|research with a task from stdin
	@python3 scripts/workspace.py "$(PROFILE)"

.PHONY: moyai-up moyai-down moyai-status moyai-smoke
moyai-up: up ## Compatibility alias for the complete stack startup

moyai-down: ## Stop local Moyai without removing its files or history
	@bash scripts/moyai.sh down

moyai-status: ## Show the local Moyai services
	@bash scripts/moyai.sh status

moyai-smoke: ## Verify real Moyai file, shell, test and artifact workflows
	@python3 scripts/moyai-smoke.py $(if $(filter 1,$(RESTART)),--restart,)
