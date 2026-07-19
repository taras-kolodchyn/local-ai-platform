#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

load_env
[[ -f .env.connected ]] || die "Copy .env.connected.example to .env.connected and add a dedicated read-only token"
chmod 600 .env.connected
set -a
# shellcheck disable=SC1091
source .env.connected
set +a

case "${GITHUB_MCP_AUTHORIZATION:-}" in
  *REPLACE*|"") die "Replace the placeholder in .env.connected" ;;
  "Bearer "*) ;;
  *) die "GITHUB_MCP_AUTHORIZATION must contain a complete Bearer header value" ;;
esac

curl --fail --silent --show-error --max-time 5 http://127.0.0.1:4000/health/liveliness >/dev/null \
  || die "The offline stack is not running; run make up first"

info "Starting the opt-in read-only GitHub MCP profile..."
docker compose --profile connected up --detach --wait --wait-timeout 120 github-mcp
bash scripts/provision-connected-key.sh
bash scripts/configure-connected.sh
bash scripts/connected-smoke.sh
ok "Connected mode is ready; source .local/connected-client.env before launching a connected client"
