#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

require_command openssl
require_command python3

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
ensure_secret HEADROOM_PROXY_TOKEN "$(generate_hex 32)"
ensure_secret GRAFANA_ADMIN_PASSWORD "$(generate_hex 18)"
ensure_secret MCP_POSTGRES_PASSWORD "$(generate_hex 24)"

replace_value WORKSPACE_UID "$(id -u)"
replace_value WORKSPACE_GID "$(id -g)"

load_env

for variable in LITELLM_MASTER_KEY POSTGRES_PASSWORD REDIS_PASSWORD HEADROOM_PROXY_TOKEN GRAFANA_ADMIN_PASSWORD MCP_POSTGRES_PASSWORD; do
  value=${!variable:-}
  [[ -n "$value" && "$value" != "GENERATE_ME" ]] || die "$variable is unset or still uses GENERATE_ME"
done

mkdir -p .local/logs .local/smoke
mkdir -p .local/repositories/snapshots
chmod 755 .local/repositories .local/repositories/snapshots
if [[ ! -f .local/repositories/catalog.json ]]; then
  printf '%s\n' '{"schema_version":1,"repositories":[],"indexed_generations":[]}' > .local/repositories/catalog.json
fi
chmod 644 .local/repositories/catalog.json
if [[ ! -f .local/model-runtime.env ]]; then
  printf '%s\n' 'EMBEDDING_ARTIFACT_ID=' > .local/model-runtime.env
fi
chmod 700 .local .local/smoke
# Trusted containers write metadata-only JSON logs here; Alloy reads the same
# bind mount without access to the Docker socket.
chmod 777 .local/logs

umask 077
# Compose mounts these files before the gateway registers the virtual keys.
touch .local/litellm-api-key
if [[ ! -s .local/litellm-metrics-key ]]; then
  printf 'sk-metrics-%s' "$(generate_hex 32)" > .local/litellm-metrics-key
fi

for service in litellm retrieval ingestion mcp-tools postgres redis; do
  touch ".local/logs/${service}.log"
done
chmod 600 .local/model-runtime.env
chmod 666 .local/logs/*.log

if [[ ! -s .local/vector-backend-key ]]; then
  openssl rand -hex 32 > .local/vector-backend-key
fi

# Files mounted as Compose secrets must be readable by the unprivileged service
# UIDs baked into each image (e.g. retrieval/ingestion run as 10001, Prometheus
# as nobody). Non-swarm Compose bind-mounts secrets with their host ownership
# and ignores secrets.*.{uid,gid,mode}, so a 0600 host file owned by the admin
# user is unreadable in the container on rootful Docker Engine. World-read keeps
# them reachable for any service UID; the 0700 .local directory keeps them
# private from other host users, as with the container log files above.
chmod 644 .local/litellm-api-key .local/litellm-metrics-key .local/vector-backend-key

python3 scripts/model-config.py render >/dev/null

ok "Local directories, secret files and resolved model configuration are ready"
