#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"
source "$(dirname "$0")/moyai-volume.sh"

moyai_compose=(docker compose --project-name local-ai-moyai --file compose.moyai.yaml)

case "${1:-up}" in
  up)
    require_command ssh-keygen
    CONTEXT_BRIDGE_REVISION=$(shasum -a 256 integrations/moyai/context_bridge.py | cut -d' ' -f1)
    export CONTEXT_BRIDGE_REVISION
    changed=$(python3 scripts/moyai-prepare.py)
    existing=$("${moyai_compose[@]}" ps -q moyai)
    python3 scripts/build-images.py "${moyai_compose[@]:2}"
    start_moyai_services "${moyai_compose[@]:2}"
    if [[ "$changed" == changed && -n "$existing" ]]; then
      compose_wait "Moyai refresh" "${moyai_compose[@]:2}" up --detach --wait --wait-timeout 120 \
        --force-recreate --no-deps moyai
    fi
    python3 scripts/moyai-prepare.py --ui connect
    # UI provisioning can succeed even if the context service stopped after the initial wait.
    if context_state=$("${moyai_compose[@]}" ps --all --format '{{.State}} {{.Health}}' repository-context) && \
       [[ "$context_state" == 'running healthy' ]]; then
      ok "Repository context is running and healthy"
    else
      compose_wait "Repository context" "${moyai_compose[@]:2}" up --detach --no-deps --no-recreate \
        --wait --wait-timeout 120 repository-context
    fi
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
