#!/usr/bin/env bash
source "$(dirname "$0")/lib.sh"
load_env
info "Preparing persistent workspace services..."
docker compose --profile workspace build workspace-development
python3 scripts/provision-workspace.py vectors
python3 scripts/provision-workspace.py profiles
python3 scripts/install-workspace-skills.py
python3 scripts/provision-workspace.py skills
python3 scripts/source-snapshot.py
docker compose --profile workspace up --detach --no-deps --wait --wait-timeout 120 \
  workspace-development workspace-review workspace-research
python3 scripts/provision-workspace.py routes
python3 scripts/vector-smoke.py
python3 scripts/memory-smoke.py
python3 scripts/workspace-smoke.py
ok "Persistent workspace services verified"
