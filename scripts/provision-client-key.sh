#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

load_env
require_command curl
require_command jq

write_client_env() {
  local key=$1
  umask 077
  mkdir -p .local/codex
  printf '%s' "$key" > .local/litellm-api-key
  printf 'export LITELLM_API_KEY=%q\n' "$key" > .local/client.env
  printf 'LITELLM_API_KEY=%s\n' "$key" > .local/client.compose.env
  # Codex IDE clients may not inherit shell variables. Keep the provider key
  # in the ignored, isolated CODEX_HOME instead of VS Code workspace settings.
  printf 'LITELLM_API_KEY=%s\n' "$key" > .local/codex/.env
  chmod 600 .local/litellm-api-key .local/client.env .local/client.compose.env .local/codex/.env
}

if [[ -s .local/litellm-api-key ]]; then
  existing_key=$(read_secret_file .local/litellm-api-key)
  status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
    --header "Authorization: Bearer $existing_key" \
    http://127.0.0.1:4000/v1/models || true)
  if [[ "$status" == 200 ]]; then
    write_client_env "$existing_key"
    ok "Existing scoped LiteLLM virtual key is valid and was preserved"
    exit 0
  fi
fi

payload=$(jq -cn '{
  key_alias: "local-ai-agents",
  models: ["local-qwen", "local-embeddings"],
  rpm_limit: 120,
  max_parallel_requests: 4,
  object_permission: {
    mcp_servers: ["local_retrieval", "local_tools"],
    mcp_tool_permissions: {
      local_retrieval: ["search_code", "get_chunk"],
      local_tools: ["list_files", "read_text_file", "git_status", "git_log", "postgres_select"]
    }
  }
}')

response=$(curl --fail --silent --show-error \
  --request POST http://127.0.0.1:4000/key/generate \
  --header "Authorization: Bearer $LITELLM_MASTER_KEY" \
  --header 'Content-Type: application/json' \
  --data "$payload")

client_key=$(jq -r '.key // empty' <<< "$response")
[[ "$client_key" == sk-* ]] || die "LiteLLM did not return a virtual key"

write_client_env "$client_key"
ok "Scoped LiteLLM virtual key generated (value not displayed)"
