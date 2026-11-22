#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

load_env
require_command docker

if (( $# == 0 )); then
  bash scripts/ensure-docker.sh
elif ! [[ $# == 1 && $1 == --for-up ]]; then
  die 'Usage: pull-model.sh [--for-up]'
fi

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

retries=0
until docker model status >/dev/null 2>&1; do
  ((retries < 60)) || die "Docker Model Runner did not become ready"
  retries=$((retries + 1))
  sleep 2
done
ok "Docker Model Runner healthy"

check_version() {
  docker "$1" version >/dev/null || die "$2 version check failed"
  ok "$2 available"
}

workers=()
start_worker() {
  "$@" &
  workers+=("$!")
}

info "Pulling the coding and embedding models and verifying the pinned reranker in parallel..."
trap 'for pid in $(jobs -pr); do kill "$pid" 2>/dev/null || true; done; wait' EXIT
start_worker docker model pull "$QWEN_MODEL"
start_worker docker model pull "$EMBEDDING_MODEL"
start_worker python3 scripts/pull-reranker.py
if [[ ${1:-} == --for-up ]]; then
  start_worker check_version compose 'Docker Compose'
  start_worker check_version model 'Docker Model Runner plugin'
fi

status=0
for pid in "${workers[@]}"; do
  wait "$pid" || status=$?
done
trap - EXIT
((status == 0)) || exit "$status"
ok "Qwen coding model available"
ok "Qwen embedding model available"
ok "Pinned local reranker available"

docker model configure --context-size "$QWEN_CONTEXT_SIZE" "$QWEN_MODEL" >/dev/null
docker model configure --mode embedding "$EMBEDDING_MODEL" >/dev/null
python3 scripts/model-config.py runtime-env >/dev/null
ok "Model runtime configuration applied"
