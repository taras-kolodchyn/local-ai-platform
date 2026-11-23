#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

load_env
require_command curl
require_command jq
admin_key=$(read_admin_key)

write_client_env() {
  local key=$1
  umask 077
  # Leave a valid live mount readable throughout repeated startup.
  if ! printf '%s' "$key" | cmp -s .local/litellm-api-key -; then
    printf '%s' "$key" > .local/litellm-api-key
  fi
  printf 'export LITELLM_API_KEY=%q\n' "$key" > .local/client.env
  # The key file is mounted into the unprivileged retrieval/ingestion containers
  # as a Compose secret, so it must stay world-readable inside the 0700 .local
  # directory; client.env is only read by host tooling and stays owner-only.
  chmod 644 .local/litellm-api-key
  chmod 600 .local/client.env
}

if [[ -s .local/litellm-api-key ]]; then
  existing_key=$(read_secret_file .local/litellm-api-key)
  status=$(curl --silent --output /dev/null --write-out '%{http_code}' \
    --header "Authorization: Bearer $existing_key" \
    http://127.0.0.1:4000/v1/models || true)
  # Bootstrap uses the master credential only until the gateway is ready.
  # A successful auth check alone must not preserve that placeholder for clients.
  if [[ "$status" == 200 && "$existing_key" != "$admin_key" ]]; then
    # Add the new alias in place so running clients keep their credential and grants.
    key_hash=$(printf '%s' "$existing_key" | shasum -a 256 | cut -d' ' -f1)
    models=$(curl --fail --silent --show-error \
      --header @/dev/fd/3 3<<<"Authorization: Bearer $admin_key" \
      "http://127.0.0.1:4000/key/info?key=$key_hash" | jq -ce '.info.models | arrays')
    if jq -e 'length > 0 and index("local-reranker") == null' <<< "$models" >/dev/null; then
      jq -cn --arg key "$key_hash" --argjson models "$models" \
        '{key: $key, models: ($models + ["local-reranker"])}' | \
        curl --fail --silent --show-error --output /dev/null \
          --header @/dev/fd/3 3<<<"Authorization: Bearer $admin_key" --header 'Content-Type: application/json' \
          --data-binary @- http://127.0.0.1:4000/key/update
    fi
    write_client_env "$existing_key"
    ok "Existing scoped LiteLLM virtual key is valid and was preserved"
    exit 0
  fi
fi

payload=$(jq -cn '{
  key_alias: "local-ai-agents",
  models: ["local-qwen", "local-embeddings", "local-reranker"],
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
  --header @/dev/fd/3 3<<<"Authorization: Bearer $admin_key" \
  --header 'Content-Type: application/json' \
  --data "$payload")

client_key=$(jq -r '.key // empty' <<< "$response")
[[ "$client_key" == sk-* ]] || die "LiteLLM did not return a virtual key"

write_client_env "$client_key"
ok "Scoped LiteLLM virtual key generated (value not displayed)"
