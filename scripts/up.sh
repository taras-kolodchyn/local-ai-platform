#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

bash scripts/bootstrap.sh
bash scripts/ensure-docker.sh
bash scripts/pull-model.sh
bash scripts/doctor.sh

info "Building and starting pinned Compose services..."
docker compose up --detach --build --wait --wait-timeout 420
ok "Container health checks passed"

bash scripts/provision-client-key.sh
bash scripts/configure-codex.sh
bash scripts/configure-hermes.sh

# Retrieval reads the key file for each embedding request, but restarting makes
# the credential transition explicit and keeps future implementations honest.
docker compose restart retrieval >/dev/null
docker compose up --detach --wait --wait-timeout 120 retrieval >/dev/null

bash scripts/smoke-test.sh

printf '\nLiteLLM: http://127.0.0.1:4000\n'
printf 'Grafana: http://127.0.0.1:3000\n'
printf 'Retrieval: http://127.0.0.1:8000\n'
printf 'Codex config: %s/.local/codex/config.toml\n' "$ROOT_DIR"
printf 'Hermes config: %s/.local/hermes/config.yaml\n' "$ROOT_DIR"
