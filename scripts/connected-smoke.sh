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

jq -e '[.result.tools[].name] | sort == [
  "github-get_commit", "github-get_file_contents", "github-get_me", "github-issue_read",
  "github-list_branches", "github-list_commits", "github-pull_request_read",
  "github-search_code", "github-search_repositories"]' "$output" >/dev/null \
  || die "Connected GitHub MCP returned an unexpected tool list"
ok "Connected GitHub MCP exposes only the reviewed read-only tool allowlist"

docker compose logs --no-color github-mcp litellm | python3 -c '
import os, sys
token = os.environ["GITHUB_MCP_AUTHORIZATION"].removeprefix("Bearer ").encode()
matches = sum(token in line for line in sys.stdin.buffer)
if matches:
    sys.exit("GitHub authorization value appeared in service logs")
'
ok "Connected-mode authorization value is absent from service logs"
chmod 600 "$output" "$raw_output"
