#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

bash scripts/bootstrap.sh
load_env
if [[ ${LITELLM_UI_PASSWORD_SOURCE:-env} == openbao ]]; then
  for file in role-id secret-id; do
    [[ -s .local/openbao/auth/$file ]] || die "OpenBao mode requires .local/openbao/auth/$file"
  done
fi
bash scripts/ensure-docker.sh
if [[ ${LITELLM_UI_PASSWORD_SOURCE:-env} == openbao-local ]]; then
  python3 scripts/openbao-local.py start
fi
bash scripts/pull-model.sh
bash scripts/doctor.sh

info "Building and starting pinned Compose services..."
docker compose up --detach --build --wait --wait-timeout 420
ok "Container health checks passed"

# Rendered file changes do not change Compose's service hash. Recreate the
# gateway so context/description-only edits and replaced bind files are loaded.
docker compose up --detach --force-recreate --no-deps --wait --wait-timeout 120 litellm

bash scripts/provision-client-key.sh

# Retrieval reads the key file for each embedding request, but restarting makes
# the credential transition explicit and keeps future implementations honest.
docker compose restart retrieval >/dev/null
docker compose up --detach --wait --wait-timeout 120 retrieval >/dev/null

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
