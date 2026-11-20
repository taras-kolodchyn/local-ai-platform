#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

load_env
require_command curl
require_command jq
admin_key=$(read_admin_key)

[[ -n "${GITHUB_MCP_AUTHORIZATION:-}" ]] || die "GITHUB_MCP_AUTHORIZATION is unset"

payload=$(jq -cn '{
  key_alias: "local-ai-agents-connected",
  models: ["local-qwen", "local-embeddings"],
  rpm_limit: 60,
  max_parallel_requests: 2,
  object_permission: {
    mcp_servers: ["local_retrieval", "local_tools", "github"],
    mcp_tool_permissions: {
      local_retrieval: ["search_code", "get_chunk"],
      local_tools: ["list_files", "read_text_file", "git_status", "git_log", "postgres_select"],
      github: ["get_me", "get_file_contents", "list_branches", "list_commits", "get_commit", "search_code", "search_repositories", "issue_read", "pull_request_read"]
    }
  }
}')

client_key=''
if [[ -s .local/litellm-connected-api-key ]]; then
  candidate=$(read_secret_file .local/litellm-connected-api-key)
  fingerprint=$(printf '%s' "$candidate" | shasum -a 256 | cut -d ' ' -f 1)
  lookup=$(curl --silent --show-error --write-out $'\n%{http_code}' \
    --header @/dev/fd/3 3<<<"Authorization: Bearer $admin_key" \
    "http://127.0.0.1:4000/key/info?key=$fingerprint")
  case "${lookup##*$'\n'}" in
    200)
      jq -e --argjson wanted "$payload" '.info.key_alias == $wanted.key_alias and
        (.info.models | sort) == ($wanted.models | sort) and
        (.info.object_permission.mcp_servers | sort) == ($wanted.object_permission.mcp_servers | sort) and
        .info.object_permission.mcp_tool_permissions == $wanted.object_permission.mcp_tool_permissions' \
        <<< "${lookup%$'\n'*}" >/dev/null || die "Stored connected key has an unexpected owner or tool scope"
      client_key=$candidate ;;
    404) ;; # A reset of the gateway database also resets its key registry.
    *) die "Could not verify the stored connected key" ;;
  esac
fi

if [[ -z "$client_key" ]]; then
  response=$(curl --fail --silent --show-error \
    --request POST http://127.0.0.1:4000/key/generate \
    --header @/dev/fd/3 3<<<"Authorization: Bearer $admin_key" \
    --header 'Content-Type: application/json' --data "$payload")
  client_key=$(jq -r '.key // empty' <<< "$response")
fi
[[ "$client_key" == sk-* ]] || die "LiteLLM did not return a connected-mode virtual key"

umask 077
printf '%s' "$client_key" > .local/litellm-connected-api-key
{
  printf 'export LITELLM_API_KEY=%q\n' "$client_key"
  printf 'export LITELLM_API_AUTHORIZATION=%q\n' "Bearer $client_key"
  printf 'export GITHUB_MCP_AUTHORIZATION=%q\n' "$GITHUB_MCP_AUTHORIZATION"
} > .local/connected-client.env
chmod 600 .local/litellm-connected-api-key .local/connected-client.env
ok "Connected-mode scoped LiteLLM key is ready"
