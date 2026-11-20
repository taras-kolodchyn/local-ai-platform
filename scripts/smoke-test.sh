#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

load_env
require_command curl
require_command jq
require_command lsof
require_command python3

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
python3 scripts/check-model-bindings.py

curl --fail --silent --show-error --max-time 10 http://127.0.0.1:8787/health >/dev/null
python3 scripts/moyai-prepare.py --ui check
python3 scripts/reconcile-agents.py --check
ok "Local Moyai is healthy and legacy workspace agent registrations are absent"

ports=(3000 4000 8000 8001 9090 12434)
if [[ ${LITELLM_UI_PASSWORD_SOURCE:-env} == openbao-local ]]; then
  ports+=(8200)
fi
for port in "${ports[@]}"; do
  listeners=$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
  [[ -n "$listeners" ]] || die "Expected listener is missing on port $port"
  if grep -Eq '(\*:|0\.0\.0\.0:|\[::\]:)' <<< "$listeners"; then
    die "Port $port is exposed beyond loopback"
  fi
done
ok "All host listeners are loopback-only"

info "Checking fresh LiteLLM and Grafana dashboard logins..."
if [[ ${LITELLM_UI_PASSWORD_SOURCE:-env} == openbao* ]]; then
  docker compose exec -T litellm cat /run/openbao-ui/ui-password |
    python3 scripts/check-dashboard-login.py --litellm-password-stdin
else
  python3 scripts/check-dashboard-login.py
fi

embedding_payload=$(jq -cn '{model:"local-embeddings",input:["loopback listeners in the offline profile"],encoding_format:"float",cache:{"no-cache":true,"no-store":true}}')
api_post /v1/embeddings "$embedding_payload" "$smoke/embedding.json"
jq -e '.data[0].embedding | length == 1024' "$smoke/embedding.json" >/dev/null
ok "Embedding route returned 1024 dimensions"

rank_payload=$(jq -cn '{model:"local-reranker",query:"local service bind address",documents:["stylesheet background color","bind local services to the loopback address"]}')
api_post /v1/rerank "$rank_payload" "$smoke/rerank.json"
jq -e '[.results[].index] == [1,0]' "$smoke/rerank.json" >/dev/null
ok "Local reranker prioritized the matching source through LiteLLM"

chat_payload=$(jq -cn '{model:"local-qwen",messages:[{role:"user",content:"Reply with exactly READY."}],temperature:0,max_tokens:32}')
api_post /v1/chat/completions "$chat_payload" "$smoke/chat.json"
jq -e '.choices[0].message.content | type == "string" and length > 0' "$smoke/chat.json" >/dev/null
ok "Chat Completions route returned model output"
python3 scripts/compression-smoke.py

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

info "Indexing the two platform policy documents through LiteLLM embeddings..."
python3 scripts/retrieval_probe.py index > "$smoke/ingestion.json"
search_payload=$(python3 scripts/retrieval_probe.py query)
curl --fail --silent --show-error --max-time 300 \
  --header 'Content-Type: application/json' --data "$search_payload" \
  http://127.0.0.1:8000/search > "$smoke/search.json"
jq -ec '.results' "$smoke/search.json" | python3 scripts/retrieval_probe.py check >/dev/null
ok "Hybrid pgvector search cited the platform loopback policy"

python3 scripts/vector-smoke.py --indexed

mcp_request=$(jq -cn '{jsonrpc:"2.0",id:1,method:"tools/list",params:{}}')
curl --fail --silent --show-error --max-time 60 \
  --header "Authorization: Bearer $key" \
  --header 'Content-Type: application/json' \
  --header 'Accept: application/json, text/event-stream' \
  --data "$mcp_request" "$base/local_retrieval/mcp" > "$smoke/mcp-tools.sse"
awk '/^data: / {sub(/^data: /, ""); print; exit}' "$smoke/mcp-tools.sse" > "$smoke/mcp-tools.json"
jq -e '[.result.tools[].name] | index("local_retrieval-search_code") != null and index("local_retrieval-get_chunk") != null' "$smoke/mcp-tools.json" >/dev/null
ok "Retrieval tools are discoverable through the LiteLLM MCP Gateway"

mcp_retrieval_call=$(jq -cn --argjson search "$search_payload" '{jsonrpc:"2.0",id:2,method:"tools/call",params:{name:"local_retrieval-search_code",arguments:($search + {path_prefix:"docs/"})}}')
curl --fail --silent --show-error --max-time 300 \
  --header "Authorization: Bearer $key" \
  --header 'Content-Type: application/json' \
  --header 'Accept: application/json, text/event-stream' \
  --data "$mcp_retrieval_call" "$base/local_retrieval/mcp" > "$smoke/mcp-retrieval-call.sse"
awk '/^data: / {sub(/^data: /, ""); print; exit}' "$smoke/mcp-retrieval-call.sse" > "$smoke/mcp-retrieval-call.json"
jq -ec 'select(.result.isError == false) | .result.structuredContent.result | fromjson |
  select(all(.[]; .path | startswith("docs/")))' "$smoke/mcp-retrieval-call.json" \
  | python3 scripts/retrieval_probe.py check >/dev/null
ok "Path-scoped MCP search cited the platform loopback policy"

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

bash scripts/check-observability.sh

if rg -q -F -e 'CACHE-SMOKE-' -e 'loopback listeners in the offline profile' \
  -e "$(jq -r '.query' <<< "$search_payload")" .local/logs; then
  die "A synthetic prompt sentinel leaked into metadata-only logs"
fi
ok "Synthetic prompt sentinels are absent from collected logs"

chmod 600 "$smoke"/*
ok "End-to-end smoke tests passed"
