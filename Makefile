SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE := bash scripts/compose.sh
REPO ?=

.PHONY: help doctor bootstrap pull-model up down restart logs status smoke-test test lint verify index reindex benchmark connected-up connected-smoke connected-down clean reset

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "; printf "Local AI Platform\n\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

doctor: ## Check macOS, Apple Silicon, memory, Docker, DMR, ports, and host tools
	@bash scripts/doctor.sh

bootstrap: ## Create local secrets, directories, and generated support files
	@bash scripts/bootstrap.sh

pull-model: bootstrap ## Prepare DMR and the pinned Qwen and MiniLM artifacts
	@bash scripts/pull-model.sh

up: ## Start the complete local stack and run smoke checks
	@bash scripts/up.sh

down: ## Stop the complete stack and unload its models; preserve data and artifacts
	@bash scripts/down.sh

restart: ## Stop and start the complete stack, including Moyai and inference
	@$(MAKE) down
	@$(MAKE) up

logs: ## Follow core service logs (metadata-only policy)
	@$(COMPOSE) logs --follow --tail=200

status: ## Show containers, DMR models, and endpoints
	@FORMAT="$(FORMAT)" bash scripts/status.sh

smoke-test: ## Verify live endpoints, bridge, embeddings, cache, pgvector, MCP, and metrics
	@bash scripts/smoke-test.sh

.PHONY: openbao-start openbao-rotate openbao-copy-password
openbao-start: ## Start and unlock the local OpenBao server using macOS Keychain
	@source scripts/lib.sh && load_env && exec python3 scripts/openbao-local.py start

openbao-rotate: ## Generate a new LiteLLM dashboard password in local OpenBao
	@source scripts/lib.sh && load_env && exec python3 scripts/openbao-local.py rotate

openbao-copy-password: ## Copy the current LiteLLM dashboard password to the Mac clipboard
	@source scripts/lib.sh && load_env && exec python3 scripts/openbao-local.py copy-password

test: ## Run platform unit, contract, and isolated PostgreSQL tests
	@bash scripts/test.sh

lint: ## Validate shell, Python, YAML/Compose, and configuration invariants
	@bash scripts/lint.sh

verify: export MOYAI_SOURCE_REPO := $(value REPO)
verify: ## Verify platform startup, Moyai tasks, and shutdown (optional REPO=/absolute/path)
	@python3 scripts/verify.py

index reindex: export INDEX_SOURCE := $(value REPO)
index: ## Incrementally index REPO=/absolute/path through local embeddings
reindex: ## Rebuild the selected REPO=/absolute/path index
index reindex:
	@test -n "$$INDEX_SOURCE" || { echo "Usage: make $@ REPO=/absolute/path" >&2; exit 2; }
	@source_dir=$$(CDPATH= cd -- "$$INDEX_SOURCE" && pwd -P) || exit 2; \
	  INDEX_REPO_PATH="$$source_dir" $(COMPOSE) --profile tools run --rm ingestion $@ /workspace --repository "$${source_dir##*/}"

.PHONY: repository-add repository-smoke
repository-add repository-smoke: export MOYAI_SOURCE_REPO := $(value REPO)
repository-add: ## Register an immutable REPO snapshot for local Moyai tasks
	@test -n "$$MOYAI_SOURCE_REPO" || { echo "Usage: make repository-add REPO=/absolute/path" >&2; exit 2; }
	@python3 scripts/prepare-repository.py --repo "$$MOYAI_SOURCE_REPO" --profile rust-retry

repository-smoke: ## Verify a supported external repository with REPO=/absolute/path
	@test -n "$$MOYAI_SOURCE_REPO" || { echo "Usage: make repository-smoke REPO=/absolute/path" >&2; exit 2; }
	@python3 scripts/repository-smoke.py --repo "$$MOYAI_SOURCE_REPO" $(if $(filter 1,$(RESTART)),--restart,)

benchmark: ## Record local chat and retrieval latency without prompt bodies
	@bash scripts/benchmark.sh

connected-up: ## Opt in to read-only GitHub MCP using .env.connected
	@bash scripts/connected-up.sh

connected-smoke: ## Verify the connected GitHub MCP allowlist and log redaction
	@bash scripts/connected-smoke.sh

connected-down: ## Stop the opt-in GitHub MCP service without touching offline data
	@$(COMPOSE) --profile connected stop github-mcp

clean: ## Remove generated caches/logs but preserve databases and models
	@bash scripts/clean.sh

reset: export RESET_APPROVAL := $(value CONFIRM_RESET)
reset: ## Stop the stack and delete core data volumes (requires CONFIRM_RESET=yes)
	@test "$$RESET_APPROVAL" = "yes" || { echo "Refusing destructive reset. Re-run with CONFIRM_RESET=yes" >&2; exit 2; }
	@$(MAKE) down
	@$(COMPOSE) --profile '*' down --volumes --remove-orphans
	@echo "Core data volumes removed. Moyai conversations and downloaded models were preserved."

.PHONY: vector-smoke
vector-smoke: ## Verify platform documentation search and access boundaries
	@source scripts/lib.sh && load_env && exec python3 scripts/vector-smoke.py

.PHONY: moyai-down moyai-status moyai-smoke
moyai-down: ## Stop local Moyai without removing its files or history
	@bash scripts/moyai.sh down

moyai-status: ## Show the local Moyai services
	@bash scripts/moyai.sh status

moyai-smoke: ## Verify real Moyai file, shell, test and artifact workflows
	@python3 scripts/moyai-smoke.py $(if $(filter 1,$(RESTART)),--restart,)

.PHONY: github-issues
github-issues github-app-issues: export MOYAI_ISSUE_PROFILE := $(value PROFILE)
github-issues: export MOYAI_OPERATOR_ISSUE := $(value ISSUE)
github-issues: ## Run the opt-in Moyai issue publisher (ISSUE=N limits host gh authorization to one issue)
	@bash scripts/github-command.sh scripts/github-issues.py

.PHONY: github-app-setup github-app-issues
github-app-setup: ## Register the private Moyai GitHub App on localhost
	@bash scripts/github-command.sh scripts/register-github-app.py

github-app-issues: export MOYAI_OPERATOR_ISSUE := $(value ISSUE)
github-app-issues: export MOYAI_AUTO_MERGE := $(value AUTO_MERGE)
github-app-issues: ## Publish as the GitHub App (ISSUE=N AUTO_MERGE=1 also merges after verified CI)
	@bash scripts/github-command.sh scripts/github-issues.py --app
