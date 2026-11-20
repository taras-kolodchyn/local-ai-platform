#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

# Standalone commands use the same project and .env as make up.
if [[ -f .env ]]; then
  load_env
fi
exec docker compose "$@"
