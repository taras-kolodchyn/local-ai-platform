#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

require_command openssl

generate_hex() {
  openssl rand -hex "$1"
}

replace_value() {
  local key=$1
  local value=$2
  local temp
  temp=$(mktemp "${TMPDIR:-/tmp}/local-ai-env.XXXXXX")
  awk -F= -v key="$key" -v value="$value" '
    BEGIN { replaced = 0 }
    $1 == key { print key "=" value; replaced = 1; next }
    { print }
    END { if (!replaced) print key "=" value }
  ' .env > "$temp"
  mv "$temp" .env
}

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  ok "Created .env template"
else
  ok "Existing .env preserved"
fi

ensure_secret() {
  local key=$1
  local value=$2
  local current
  current=$(awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' .env)
  if [[ -z "$current" || "$current" == "GENERATE_ME" ]]; then
    replace_value "$key" "$value"
    ok "Generated local-only $key"
  fi
}

ensure_secret LITELLM_MASTER_KEY "sk-local-$(generate_hex 24)"
ensure_secret POSTGRES_PASSWORD "$(generate_hex 24)"
ensure_secret REDIS_PASSWORD "$(generate_hex 24)"
ensure_secret GRAFANA_ADMIN_PASSWORD "$(generate_hex 18)"
ensure_secret MCP_POSTGRES_PASSWORD "$(generate_hex 24)"

load_env

for variable in LITELLM_MASTER_KEY POSTGRES_PASSWORD REDIS_PASSWORD GRAFANA_ADMIN_PASSWORD MCP_POSTGRES_PASSWORD; do
  value=${!variable:-}
  [[ -n "$value" && "$value" != "GENERATE_ME" ]] || die "$variable is unset or still uses GENERATE_ME"
done

mkdir -p .local/codex .local/hermes .local/hermes-container .local/logs .local/smoke
chmod 700 .local .local/codex .local/hermes .local/hermes-container .local/smoke
# Trusted containers write metadata-only JSON logs here; Alloy reads the same
# bind mount without access to the Docker socket.
chmod 777 .local/logs

umask 077
printf '%s' "$LITELLM_MASTER_KEY" > .local/litellm-master-key

# The master key bootstraps health checks. `provision-client-key.sh` replaces
# this file with a scoped virtual key as soon as LiteLLM is ready.
if [[ ! -s .local/litellm-api-key ]]; then
  printf '%s' "$LITELLM_MASTER_KEY" > .local/litellm-api-key
fi
printf 'LITELLM_API_KEY=%s\n' "$(tr -d '\r\n' < .local/litellm-api-key)" > .local/client.compose.env

for service in litellm retrieval ingestion mcp-tools postgres redis; do
  touch ".local/logs/${service}.log"
done
chmod 600 .local/litellm-master-key .local/litellm-api-key .local/client.compose.env
chmod 666 .local/logs/*.log

ok "Local directories and secret files are ready"
