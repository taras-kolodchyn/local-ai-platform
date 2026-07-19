#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

load_env

mkdir -p .local/codex
temp=$(mktemp "${TMPDIR:-/tmp}/codex-config.XXXXXX")
cat > "$temp" <<EOF
model = "local-qwen"
model_provider = "litellm"
model_context_window = $QWEN_CONTEXT_SIZE
sandbox_mode = "workspace-write"
approval_policy = "on-request"
allow_login_shell = false

[model_providers.litellm]
name = "Local LiteLLM"
base_url = "http://127.0.0.1:4000/v1"
env_key = "LITELLM_API_KEY"
wire_api = "responses"
request_max_retries = 1
stream_max_retries = 1
stream_idle_timeout_ms = 1800000

[mcp_servers.local_retrieval]
url = "http://127.0.0.1:4000/local_retrieval/mcp"
bearer_token_env_var = "LITELLM_API_KEY"
required = true
enabled_tools = ["local_retrieval-search_code", "local_retrieval-get_chunk"]
default_tools_approval_mode = "approve"
startup_timeout_sec = 30
tool_timeout_sec = 300

[mcp_servers.local_tools]
url = "http://127.0.0.1:4000/local_tools/mcp"
bearer_token_env_var = "LITELLM_API_KEY"
required = true
enabled_tools = ["local_tools-list_files", "local_tools-read_text_file", "local_tools-git_status", "local_tools-git_log", "local_tools-postgres_select"]
default_tools_approval_mode = "approve"
startup_timeout_sec = 30
tool_timeout_sec = 60
EOF
mv "$temp" .local/codex/config.toml
chmod 600 .local/codex/config.toml
ok "Codex configuration generated at .local/codex/config.toml"
info "Use: source .local/client.env && CODEX_HOME=\"$ROOT_DIR/.local/codex\" codex"
