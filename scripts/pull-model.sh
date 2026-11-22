#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

load_env
require_command docker

bash scripts/ensure-docker.sh

if [[ "$(uname -s)" == "Darwin" ]]; then
  info "Enabling Docker Model Runner with loopback TCP port 12434..."
  docker desktop enable model-runner --tcp=12434 >/dev/null
else
  # Docker Engine serves the Model Runner TCP endpoint on port 12434 by default;
  # there is no `docker desktop` command to enable it.
  docker model version >/dev/null 2>&1 ||
    die "Docker Model Runner plugin is missing. Install it with 'sudo apt-get install docker-model-plugin' and retry."
  info "Docker Model Runner plugin present; TCP port 12434 is enabled by default on Docker Engine"
fi

for _ in $(seq 1 60); do
  docker model status >/dev/null 2>&1 && break
  sleep 2
done
docker model status >/dev/null 2>&1 || die "Docker Model Runner did not become ready"
ok "Docker Model Runner healthy"

info "Pulling the coding and embedding models in parallel (the coding model can download about 16.5 GB)..."
docker model pull "$QWEN_MODEL" &
coding_pull=$!
trap 'kill "$coding_pull" 2>/dev/null || true' EXIT

pull_status=0
docker model pull "$EMBEDDING_MODEL" || pull_status=$?
wait "$coding_pull" || pull_status=$?
trap - EXIT
((pull_status == 0)) || exit "$pull_status"
ok "Qwen coding model available"
ok "Qwen embedding model available"

python3 scripts/pull-reranker.py
ok "Pinned local reranker available"

docker model configure --context-size "$QWEN_CONTEXT_SIZE" "$QWEN_MODEL" >/dev/null
docker model configure --mode embedding "$EMBEDDING_MODEL" >/dev/null
python3 scripts/model-config.py runtime-env >/dev/null
ok "Model runtime configuration applied"
