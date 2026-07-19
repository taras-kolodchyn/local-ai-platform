#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

load_env
require_command docker

bash scripts/ensure-docker.sh

info "Enabling Docker Model Runner with loopback TCP port 12434..."
docker desktop enable model-runner --tcp=12434 >/dev/null

for _ in $(seq 1 60); do
  docker model status >/dev/null 2>&1 && break
  sleep 2
done
docker model status >/dev/null 2>&1 || die "Docker Model Runner did not become ready"
ok "Docker Model Runner healthy"

info "Pulling pinned coding model (this can download about 16.5 GB)..."
docker model pull "$QWEN_MODEL"
ok "Qwen coding model available"

info "Pulling pinned embedding model..."
docker model pull "$EMBEDDING_MODEL"
ok "Qwen embedding model available"

docker model configure --context-size "$QWEN_CONTEXT_SIZE" "$QWEN_MODEL" >/dev/null
docker model configure --mode embedding "$EMBEDDING_MODEL" >/dev/null
ok "Model runtime configuration applied"
