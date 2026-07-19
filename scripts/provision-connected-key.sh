#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

load_env
require_command curl
require_command jq

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

response=$(curl --fail --silent --show-error \
  --request POST http://127.0.0.1:4000/key/generate \
  --header "Authorization: Bearer $LITELLM_MASTER_KEY" \
  --header 'Content-Type: application/json' \
  --data "$payload")

client_key=$(jq -r '.key // empty' <<< "$response")
[[ "$client_key" == sk-* ]] || die "LiteLLM did not return a connected-mode virtual key"

umask 077
printf '%s' "$client_key" > .local/litellm-connected-api-key
{
  printf 'export LITELLM_API_KEY=%q\n' "$client_key"
  printf 'export LITELLM_API_AUTHORIZATION=%q\n' "Bearer $client_key"
  printf 'export GITHUB_MCP_AUTHORIZATION=%q\n' "$GITHUB_MCP_AUTHORIZATION"
} > .local/connected-client.env
chmod 600 .local/litellm-connected-api-key .local/connected-client.env
ok "Connected-mode scoped LiteLLM key generated (values not displayed)"
