#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

load_env

mkdir -p .local/hermes
temp=$(mktemp "${TMPDIR:-/tmp}/hermes-config.XXXXXX")
cat > "$temp" <<EOF
model:
  default: local-qwen
  provider: custom
  base_url: http://127.0.0.1:4000/v1
  api_key: \${LITELLM_API_KEY}
  context_length: $QWEN_CONTEXT_SIZE

custom_providers:
  - name: local-litellm
    base_url: http://127.0.0.1:4000/v1
    key_env: LITELLM_API_KEY
    api_mode: chat_completions
    models:
      local-qwen:
        context_length: $QWEN_CONTEXT_SIZE

mcp_servers:
  local_retrieval:
    url: http://127.0.0.1:4000/local_retrieval/mcp
    headers:
      Authorization: Bearer \${LITELLM_API_KEY}
    tools:
      include: [local_retrieval-search_code, local_retrieval-get_chunk]
      prompts: false
      resources: false
  local_tools:
    url: http://127.0.0.1:4000/local_tools/mcp
    headers:
      Authorization: Bearer \${LITELLM_API_KEY}
    tools:
      include: [local_tools-list_files, local_tools-read_text_file, local_tools-git_status, local_tools-git_log, local_tools-postgres_select]
      prompts: false
      resources: false
EOF
mv "$temp" .local/hermes/config.yaml
chmod 600 .local/hermes/config.yaml
ok "Hermes configuration generated at .local/hermes/config.yaml"
info "Use the generated config as a reviewed template; do not overwrite ~/.hermes automatically."

cat > "$temp" <<EOF
model:
  default: local-qwen
  provider: custom
  base_url: http://litellm:4000/v1
  api_key: \${LITELLM_API_KEY}
  context_length: $QWEN_CONTEXT_SIZE

mcp_servers:
  local_retrieval:
    url: http://litellm:4000/local_retrieval/mcp
    headers:
      Authorization: Bearer \${LITELLM_API_KEY}
    tools:
      include: [local_retrieval-search_code, local_retrieval-get_chunk]
      prompts: false
      resources: false
  local_tools:
    url: http://litellm:4000/local_tools/mcp
    headers:
      Authorization: Bearer \${LITELLM_API_KEY}
    tools:
      include: [local_tools-list_files, local_tools-read_text_file, local_tools-git_status, local_tools-git_log, local_tools-postgres_select]
      prompts: false
      resources: false
EOF
mv "$temp" .local/hermes-container/config.yaml
chmod 600 .local/hermes-container/config.yaml
ok "Containerized Hermes configuration generated at .local/hermes-container/config.yaml"
