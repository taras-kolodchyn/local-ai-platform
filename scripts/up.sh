#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"
source "$(dirname "$0")/mcp-provision.sh"

timed_step "Bootstrap" bash scripts/bootstrap.sh
load_env
timed_step "Docker readiness" bash scripts/ensure-docker.sh
timed_step "Model preparation" bash scripts/pull-model.sh --for-up
timed_step "Host checks" bash scripts/doctor.sh --runtime-checked

info "Checking Compose images and starting services..."
timed_step "Core images" python3 scripts/build-images.py
start_core
ok "Container health checks passed"

bash scripts/provision-client-key.sh
python3 scripts/provision-metrics-key.py

# Retrieval reads keys on demand. An atomically replaced host file can leave a
# single-file bind mount on the old inode until the container is recreated.
if ! docker compose exec -T retrieval cmp -s /run/secrets/litellm_client_key - < .local/litellm-api-key ||
   ! docker compose exec -T retrieval cmp -s /run/secrets/vector_backend_key - < .local/vector-backend-key; then
  compose_wait "Retrieval refresh" up --detach --no-deps --force-recreate --wait --wait-timeout 120 retrieval
fi

# Stop old profile containers and retire their routes. Data removal is a separate
# explicit migration; ordinary startup keeps Moyai as the single default agent.
python3 scripts/reconcile-agents.py --stop-runtimes
python3 scripts/provision-workspace.py moyai-skills
# Only the successful index result goes to the smoke process. Keep Moyai's
# progress visible while the gateway probes run on the other side of the pipe.
prepare_search() (
  timed_step "Moyai setup" bash scripts/moyai.sh up >&2 &
  moyai_pid=$!
  trap 'wait "$moyai_pid" || :' EXIT
  index_result=$(timed_step Indexing python3 scripts/retrieval_probe.py index)
  wait "$moyai_pid"
  trap - EXIT
  printf '%s\n' "$index_result"
)

info "Starting Moyai, indexing platform policy documents and checking the gateway in parallel..."
prepare_search | timed_step "Smoke checks" bash scripts/smoke-test.sh --await-startup

printf '\nLiteLLM: http://127.0.0.1:4000\n'
printf 'Grafana: http://127.0.0.1:3000\n'
printf 'Retrieval: http://127.0.0.1:8000\n'
printf 'Moyai: http://127.0.0.1:8787\n'
