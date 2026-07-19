#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

bash scripts/bootstrap.sh >/dev/null
bash scripts/configure-hermes.sh >/dev/null

response=$(HERMES_UID="$(id -u)" HERMES_GID="$(id -g)" \
  docker compose --profile agents run --rm --no-deps hermes \
  -z "Reply with exactly HERMES-READY." --model local-qwen --provider custom)

if grep -q 'HERMES-READY' <<< "$response"; then
  ok "Hermes one-shot request passed through LiteLLM"
else
  die "Hermes did not return the expected one-shot response"
fi
