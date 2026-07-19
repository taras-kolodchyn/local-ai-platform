#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

require_command curl
require_command jq

[[ -f .env.connected ]] || die ".env.connected is missing"
set -a
# shellcheck disable=SC1091
source .env.connected
set +a
[[ -n "${GITHUB_MCP_AUTHORIZATION:-}" ]] || die "GITHUB_MCP_AUTHORIZATION is unset"

key=$(read_secret_file .local/litellm-connected-api-key)
output=.local/smoke/mcp-github-tools.json
raw_output=.local/smoke/mcp-github-tools.sse
request=$(jq -cn '{jsonrpc:"2.0",id:1,method:"tools/list",params:{}}')

curl --fail --silent --show-error --max-time 120 \
  --header "x-litellm-api-key: Bearer $key" \
  --header "Authorization: $GITHUB_MCP_AUTHORIZATION" \
  --header 'Content-Type: application/json' \
  --header 'Accept: application/json, text/event-stream' \
  --data "$request" http://127.0.0.1:4000/github/mcp > "$raw_output"
awk '/^data: / {sub(/^data: /, ""); print; exit}' "$raw_output" > "$output"

jq -e '[.result.tools[].name] | index("github-get_me") != null and index("github-search_code") != null' "$output" >/dev/null
if jq -er '.result.tools[].name' "$output" | rg -q '(^|_)(create|delete|merge|push|rerun|update|write)(_|$)'; then
  die "Connected GitHub MCP exposed a write-capable tool"
fi
ok "Connected GitHub MCP exposes only the reviewed read-only tool allowlist"

if docker compose logs --no-color github-mcp litellm 2>/dev/null | grep -Fq -- "$GITHUB_MCP_AUTHORIZATION"; then
  die "GitHub authorization value appeared in service logs"
fi
ok "Connected-mode authorization value is absent from service logs"
chmod 600 "$output" "$raw_output"
