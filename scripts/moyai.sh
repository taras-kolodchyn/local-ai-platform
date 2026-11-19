#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

moyai_compose=(docker compose --project-name local-ai-moyai --file compose.moyai.yaml)

case "${1:-up}" in
  up)
    require_command ssh-keygen
    changed=$(python3 scripts/moyai-prepare.py)
    existing=$("${moyai_compose[@]}" ps -q moyai)
    "${moyai_compose[@]}" up --detach --build --wait --wait-timeout 300
    if [[ "$changed" == changed && -n "$existing" ]]; then
      "${moyai_compose[@]}" up --detach --wait --wait-timeout 120 \
        --force-recreate --no-deps moyai
    fi
    python3 scripts/moyai-prepare.py --ui connect
    # UI provisioning can succeed even if the context service stopped after the initial wait.
    "${moyai_compose[@]}" up --detach --no-deps --no-recreate \
      --wait --wait-timeout 120 repository-context
    ok "Local Moyai is ready at http://127.0.0.1:8787 (user: admin)"
    info "Private password: $ROOT_DIR/.local/moyai/admin-password"
    ;;
  down)
    "${moyai_compose[@]}" down
    ;;
  status)
    "${moyai_compose[@]}" ps
    ;;
  *) die 'Usage: scripts/moyai.sh up|down|status' ;;
esac
