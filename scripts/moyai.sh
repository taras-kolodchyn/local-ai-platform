#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

case "${1:-up}" in
  up)
    require_command ssh-keygen
    changed=$(python3 scripts/moyai-prepare.py)
    existing=$(docker compose -f compose.moyai.yaml ps -q moyai)
    docker compose -f compose.moyai.yaml up --detach --build --wait --wait-timeout 300
    if [[ "$changed" == changed && -n "$existing" ]]; then
      docker compose -f compose.moyai.yaml up --detach --wait --wait-timeout 120 \
        --force-recreate --no-deps moyai
    fi
    python3 scripts/moyai-prepare.py --ui connect
    ok "Local Moyai is ready at http://127.0.0.1:8787 (user: admin)"
    info "Private password: $ROOT_DIR/.local/moyai/admin-password"
    ;;
  down)
    docker compose -f compose.moyai.yaml down
    ;;
  status)
    docker compose -f compose.moyai.yaml ps
    ;;
  *) die 'Usage: scripts/moyai.sh up|down|status' ;;
esac
