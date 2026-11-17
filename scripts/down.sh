#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

require_command docker

# Shutdown must also work before bootstrap. Real .env values override these
# repository defaults, while Compose can parse the template without credentials.
set -a
source .env.example
set +a
env_file=.env.example
if [[ -f .env ]]; then
  load_env
  env_file=.env
else
  warn '.env is absent; using repository defaults for model unloading'
fi

failed=0
info 'Stopping Moyai before removing the shared gateway network...'
if ! bash scripts/moyai.sh down; then
  warn 'Moyai shutdown failed; continuing the remaining cleanup'
  failed=1
fi

info 'Stopping all platform containers, including enabled profiles...'
if ! docker compose --env-file "$env_file" --profile '*' down --remove-orphans; then
  warn 'Core shutdown failed; attempting model unloading as well'
  failed=1
fi

info 'Unloading the configured coding and embedding models from memory...'
if ! docker model unload "$QWEN_MODEL" "$EMBEDDING_MODEL"; then
  warn 'Model unloading failed; memory release was not confirmed'
  failed=1
fi

info 'Models still resident in Docker Model Runner:'
if ! docker model ps; then
  warn 'Could not read resident-model status'
  failed=1
fi

if (( failed )); then
  die 'Shutdown was incomplete; resolve the reported errors and retry make down'
fi
ok 'Platform containers stopped and model unload requests completed; resident inventory shown above'
info 'Data and downloaded model artifacts are preserved'
