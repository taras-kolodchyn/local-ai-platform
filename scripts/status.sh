#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

bash scripts/bootstrap.sh >/dev/null

printf '%s\n' 'Compose services:'
docker compose --profile connected ps
printf '\n%s\n' 'Docker Model Runner models:'
docker model list 2>/dev/null || warn "Docker Model Runner unavailable"
printf '\n%s\n' 'Endpoints:'
printf '  LiteLLM    http://127.0.0.1:4000\n'
printf '  Retrieval  http://127.0.0.1:8000\n'
printf '  MCP gateway http://127.0.0.1:4000/<server>/mcp\n'
printf '  MCP debug   http://127.0.0.1:8001/mcp/\n'
printf '  Grafana    http://127.0.0.1:3000\n'
printf '  Prometheus http://127.0.0.1:9090\n'
