#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

load_env
require_command curl
require_command jq
require_command lsof

key=$(read_secret_file .local/litellm-api-key)
base=http://127.0.0.1:4000
smoke=.local/smoke
mkdir -p "$smoke"
chmod 700 "$smoke"

api_post() {
  local endpoint=$1
  local payload=$2
  local output=$3
  curl --fail --silent --show-error --max-time 1800 \
    --header "Authorization: Bearer $key" \
    --header 'Content-Type: application/json' \
    --data "$payload" \
    "$base$endpoint" > "$output"
}

info "Checking health and authenticated model discovery..."
curl --fail --silent --show-error http://127.0.0.1:8000/health | jq -e '.status == "ok"' >/dev/null
curl --fail --silent --show-error \
  --header "Authorization: Bearer $key" "$base/v1/models" \
  | jq -e '[.data[].id] | index("local-qwen") != null and index("local-embeddings") != null' >/dev/null
ok "Gateway and retrieval health checks passed"

for port in 3000 4000 8000 8001 9090 12434; do
  listeners=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  [[ -n "$listeners" ]] || die "Expected listener is missing on port $port"
  if grep -Eq '(\*:|0\.0\.0\.0:|\[::\]:)' <<< "$listeners"; then
    die "Port $port is exposed beyond loopback"
  fi
done
ok "All host listeners are loopback-only"

embedding_payload=$(jq -cn '{model:"local-embeddings",input:["bounded exponential backoff in Rust"],encoding_format:"float",cache:{"no-cache":true,"no-store":true}}')
api_post /v1/embeddings "$embedding_payload" "$smoke/embedding.json"
jq -e '.data[0].embedding | length == 1024' "$smoke/embedding.json" >/dev/null
ok "Embedding route returned 1024 dimensions"

chat_payload=$(jq -cn '{model:"local-qwen",messages:[{role:"user",content:"Reply with exactly READY."}],temperature:0,max_tokens:32}')
api_post /v1/chat/completions "$chat_payload" "$smoke/chat.json"
jq -e '.choices[0].message.content | type == "string" and length > 0' "$smoke/chat.json" >/dev/null
ok "Chat Completions route returned model output"

responses_payload=$(jq -cn '{
  model:"local-qwen",
  input:"Call multiply with a=6 and b=7. Do not calculate it yourself.",
  tools:[{type:"function",name:"multiply",description:"Multiply two integers",parameters:{type:"object",properties:{a:{type:"integer"},b:{type:"integer"}},required:["a","b"],additionalProperties:false}}],
  tool_choice:{type:"function",name:"multiply"},
  max_output_tokens:256
}')
api_post /v1/responses "$responses_payload" "$smoke/responses-tool-call.json"
call_id=$(jq -r '[.output[]? | select(.type == "function_call")][0].call_id // empty' "$smoke/responses-tool-call.json")
call_name=$(jq -r '[.output[]? | select(.type == "function_call")][0].name // empty' "$smoke/responses-tool-call.json")
call_args=$(jq -c '[.output[]? | select(.type == "function_call")][0].arguments // empty' "$smoke/responses-tool-call.json")
response_id=$(jq -r '.id // empty' "$smoke/responses-tool-call.json")
[[ -n "$call_id" && "$call_name" == "multiply" && -n "$call_args" ]] || die "Responses bridge did not preserve the forced function call"
ok "Responses bridge preserved a function call"

roundtrip_payload=$(jq -cn \
  --arg call_id "$call_id" \
  --arg previous_response_id "$response_id" \
  '{model:"local-qwen",previous_response_id:$previous_response_id,input:[
    {type:"function_call_output",call_id:$call_id,output:"42"}
  ],max_output_tokens:128}')
api_post /v1/responses "$roundtrip_payload" "$smoke/responses-tool-result.json"
jq -e '[.output[]?.content[]?.text // empty, .output_text // empty] | join(" ") | contains("42")' "$smoke/responses-tool-result.json" >/dev/null
ok "Responses bridge completed a tool-result round trip"

stream_payload=$(jq -cn '{model:"local-qwen",input:"Reply with exactly STREAM-READY.",stream:true,max_output_tokens:64}')
curl --fail --silent --show-error --no-buffer --max-time 1800 \
  --header "Authorization: Bearer $key" --header 'Content-Type: application/json' \
  --data "$stream_payload" "$base/v1/responses" > "$smoke/responses-stream.txt"
grep -q '^data:' "$smoke/responses-stream.txt"
grep -Eq 'response\.completed|\[DONE\]' "$smoke/responses-stream.txt"
ok "Responses streaming route completed"

cache_sentinel="CACHE-SMOKE-$(date -u +%s)-$RANDOM"
cache_payload=$(jq -cn --arg sentinel "$cache_sentinel" '{model:"local-qwen",messages:[{role:"user",content:("Return only " + $sentinel + ".")}],temperature:0,max_tokens:32,cache:{"use-cache":true}}')
curl --fail --silent --show-error --max-time 1800 \
  --dump-header "$smoke/cache-first.headers" \
  --header "Authorization: Bearer $key" --header 'Content-Type: application/json' \
  --data "$cache_payload" "$base/v1/chat/completions" > "$smoke/cache-first.json"
curl --fail --silent --show-error --max-time 1800 \
  --dump-header "$smoke/cache-second.headers" \
  --header "Authorization: Bearer $key" --header 'Content-Type: application/json' \
  --data "$cache_payload" "$base/v1/chat/completions" > "$smoke/cache-second.json"
first_content=$(jq -r '.choices[0].message.content' "$smoke/cache-first.json")
second_content=$(jq -r '.choices[0].message.content' "$smoke/cache-second.json")
[[ -n "$first_content" && "$first_content" == "$second_content" ]] || die "Explicit cache requests returned different content"
grep -Eiq '^x-litellm-cache-key:[[:space:]]*[^[:space:]]+' "$smoke/cache-second.headers" \
  || die "Second explicit-cache response did not contain LiteLLM's documented cache-hit key header"
ok "Explicit Redis cache miss/hit path verified"

info "Indexing the bundled Rust fixture through LiteLLM embeddings..."
INDEX_REPO_PATH="$ROOT_DIR/examples/rust-service" docker compose --profile tools run --rm ingestion index /workspace --repository rust-service \
  > "$smoke/ingestion.json"
jq -e '.files_indexed >= 2 and (.chunks_embedded + .chunks_reused) >= 2' "$smoke/ingestion.json" >/dev/null

search_payload=$(jq -cn '{query:"What is the required maximum retry delay?",repository:"rust-service",limit:3}')
curl --fail --silent --show-error --max-time 300 \
  --header 'Content-Type: application/json' --data "$search_payload" \
  http://127.0.0.1:8000/search > "$smoke/search.json"
jq -e '.count >= 1 and any(.results[]; .path == "docs/architecture-rules.md" or .path == "src/lib.rs")' "$smoke/search.json" >/dev/null
ok "Incremental pgvector ingestion and retrieval passed"

mcp_request=$(jq -cn '{jsonrpc:"2.0",id:1,method:"tools/list",params:{}}')
curl --fail --silent --show-error --max-time 60 \
  --header "Authorization: Bearer $key" \
  --header 'Content-Type: application/json' \
  --header 'Accept: application/json, text/event-stream' \
  --data "$mcp_request" "$base/local_retrieval/mcp" > "$smoke/mcp-tools.sse"
awk '/^data: / {sub(/^data: /, ""); print; exit}' "$smoke/mcp-tools.sse" > "$smoke/mcp-tools.json"
jq -e '[.result.tools[].name] | index("local_retrieval-search_code") != null and index("local_retrieval-get_chunk") != null' "$smoke/mcp-tools.json" >/dev/null
ok "Retrieval tools are discoverable through the LiteLLM MCP Gateway"

mcp_retrieval_call=$(jq -cn '{jsonrpc:"2.0",id:2,method:"tools/call",params:{name:"local_retrieval-search_code",arguments:{query:"required maximum retry delay",repository:"rust-service",limit:2}}}')
curl --fail --silent --show-error --max-time 300 \
  --header "Authorization: Bearer $key" \
  --header 'Content-Type: application/json' \
  --header 'Accept: application/json, text/event-stream' \
  --data "$mcp_retrieval_call" "$base/local_retrieval/mcp" > "$smoke/mcp-retrieval-call.sse"
awk '/^data: / {sub(/^data: /, ""); print; exit}' "$smoke/mcp-retrieval-call.sse" > "$smoke/mcp-retrieval-call.json"
jq -e '.result.isError == false and (.result.structuredContent.result | fromjson | any(.[]; .path == "docs/architecture-rules.md" or .path == "README.md"))' "$smoke/mcp-retrieval-call.json" >/dev/null
ok "Retrieval MCP tool call returned indexed policy context"

curl --fail --silent --show-error --max-time 60 \
  --header "Authorization: Bearer $key" \
  --header 'Content-Type: application/json' \
  --header 'Accept: application/json, text/event-stream' \
  --data "$mcp_request" "$base/local_tools/mcp" > "$smoke/mcp-offline-tools.sse"
awk '/^data: / {sub(/^data: /, ""); print; exit}' "$smoke/mcp-offline-tools.sse" > "$smoke/mcp-offline-tools.json"
jq -e '[.result.tools[].name] | index("local_tools-list_files") != null and index("local_tools-git_status") != null and index("local_tools-postgres_select") != null' "$smoke/mcp-offline-tools.json" >/dev/null
ok "Filesystem, Git, and read-only PostgreSQL tools are discoverable through the LiteLLM MCP Gateway"

mcp_postgres_call=$(jq -cn '{jsonrpc:"2.0",id:3,method:"tools/call",params:{name:"local_tools-postgres_select",arguments:{sql:"SELECT count(*) AS chunks FROM source_chunks",max_rows:1}}}')
curl --fail --silent --show-error --max-time 60 \
  --header "Authorization: Bearer $key" \
  --header 'Content-Type: application/json' \
  --header 'Accept: application/json, text/event-stream' \
  --data "$mcp_postgres_call" "$base/local_tools/mcp" > "$smoke/mcp-postgres-call.sse"
awk '/^data: / {sub(/^data: /, ""); print; exit}' "$smoke/mcp-postgres-call.sse" > "$smoke/mcp-postgres-call.json"
jq -e '.result.isError == false and (.result.structuredContent.result | fromjson | .[0].chunks >= 1)' "$smoke/mcp-postgres-call.json" >/dev/null
ok "Read-only PostgreSQL MCP tool call returned bounded metadata"

curl --fail --silent --show-error http://127.0.0.1:8000/metrics \
  | grep -q 'local_ai_retrieval_requests_total'
curl --fail --silent --show-error http://127.0.0.1:9090/-/ready >/dev/null
curl --fail --silent --show-error http://127.0.0.1:3000/api/health \
  | jq -e '.database == "ok"' >/dev/null
prometheus_targets=$(curl --fail --silent --show-error http://127.0.0.1:9090/api/v1/targets)
jq -e '[.data.activeTargets[] | select(.health != "up")] | length == 0' <<< "$prometheus_targets" >/dev/null
ok "Prometheus, Grafana, and all configured scrape targets are healthy"

if rg -n 'CACHE-SMOKE-|bounded exponential backoff in Rust|What is the required maximum retry delay' .local/logs; then
  die "A synthetic prompt sentinel leaked into metadata-only logs"
fi
ok "Synthetic prompt sentinels are absent from collected logs"

chmod 600 "$smoke"/*
ok "End-to-end smoke tests passed"
