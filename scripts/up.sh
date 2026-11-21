#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

bash scripts/bootstrap.sh
load_env
bash scripts/ensure-docker.sh
bash scripts/pull-model.sh
bash scripts/doctor.sh

info "Checking Compose images and starting services..."
python3 scripts/build-images.py
docker compose up --detach --no-build --wait --wait-timeout 420
ok "Container health checks passed"

bash scripts/provision-client-key.sh
python3 scripts/provision-metrics-key.py

# Retrieval reads keys on demand. An atomically replaced host file can leave a
# single-file bind mount on the old inode until the container is recreated.
if ! docker compose exec -T retrieval cmp -s /run/secrets/litellm_client_key - < .local/litellm-api-key ||
   ! docker compose exec -T retrieval cmp -s /run/secrets/vector_backend_key - < .local/vector-backend-key; then
  docker compose up --detach --no-deps --force-recreate --wait --wait-timeout 120 retrieval
fi

# Stop old profile containers and retire their routes. Data removal is a separate
# explicit migration; ordinary startup keeps Moyai as the single default agent.
python3 scripts/reconcile-agents.py --stop-runtimes
python3 scripts/provision-workspace.py moyai-skills
bash scripts/moyai.sh up
bash scripts/smoke-test.sh

printf '\nLiteLLM: http://127.0.0.1:4000\n'
printf 'Grafana: http://127.0.0.1:3000\n'
printf 'Retrieval: http://127.0.0.1:8000\n'
printf 'Moyai: http://127.0.0.1:8787\n'
