#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

load_env
require_command curl
require_command jq
require_command docker

key=$(read_secret_file .local/litellm-api-key)
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
output_dir=.local/benchmarks
samples=$(mktemp "${TMPDIR:-/tmp}/local-ai-benchmark.XXXXXX")
mkdir -p "$output_dir"
trap 'rm -f "$samples"' EXIT

model_runner_version=$(docker model version 2>/dev/null \
  | awk '/Version:/ && !seen {version=$2; seen=1} END {if (seen) print version}')
[[ -n "$model_runner_version" ]] || model_runner_version=unavailable

chat_payload=$(jq -cn '{model:"local-qwen",messages:[{role:"user",content:"In one sentence, explain why bounded retries matter."}],temperature:0,max_tokens:96,cache:{"no-cache":true,"no-store":true}}')
retrieval_payload=$(jq -cn '{query:"What is the required maximum retry delay?",repository:"rust-service",limit:3}')

# Unloading keeps the artifacts on disk but makes run 1 a genuine model-load
# sample for both generation and embeddings. Runs 2-4 reuse the loaded models.
docker model unload "$QWEN_MODEL" "$EMBEDDING_MODEL" >/dev/null 2>&1 || true
info "Models unloaded from memory; recording one cold and three warm samples..."

for run in 1 2 3 4; do
  if [[ "$run" == "1" ]]; then
    phase=cold
  else
    phase=warm
  fi
  chat_meta=$(curl --silent --show-error --max-time 1800 \
    --output "$output_dir/.chat-${timestamp}-${run}.json" \
    --write-out '{"status":%{http_code},"seconds":%{time_total}}' \
    --header "Authorization: Bearer $key" --header 'Content-Type: application/json' \
    --data "$chat_payload" http://127.0.0.1:4000/v1/chat/completions)
  usage=$(jq -c '.usage // {}' "$output_dir/.chat-${timestamp}-${run}.json")
  jq -cn --argjson run "$run" --arg phase "$phase" --argjson http "$chat_meta" --argjson usage "$usage" \
    '{kind:"chat",phase:$phase,run:$run,http:$http,usage:$usage,
      output_tokens_per_second:(if $http.seconds > 0 then (($usage.completion_tokens // 0) / $http.seconds) else 0 end)}' >> "$samples"

  retrieval_meta=$(curl --silent --show-error --max-time 300 \
    --output "$output_dir/.retrieval-${timestamp}-${run}.json" \
    --write-out '{"status":%{http_code},"seconds":%{time_total}}' \
    --header 'Content-Type: application/json' --data "$retrieval_payload" \
    http://127.0.0.1:8000/search)
  count=$(jq '.count // 0' "$output_dir/.retrieval-${timestamp}-${run}.json")
  jq -cn --argjson run "$run" --arg phase "$phase" --argjson http "$retrieval_meta" --argjson count "$count" \
    '{kind:"retrieval",phase:$phase,run:$run,http:$http,result_count:$count}' >> "$samples"
done

result="$output_dir/${timestamp}.json"
jq -s \
  --arg recorded_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg commit "$(git rev-parse HEAD 2>/dev/null || printf uncommitted)" \
  --arg macos "$(sw_vers -productVersion)" \
  --arg architecture "$(uname -m)" \
  --argjson memory_bytes "$(sysctl -n hw.memsize)" \
  --arg docker_desktop "$(docker version --format '{{.Server.Version}}' 2>/dev/null || printf unavailable)" \
  --arg model_runner "$model_runner_version" \
  --arg context_size "$QWEN_CONTEXT_SIZE" \
  --arg model "$QWEN_MODEL" \
  '{recorded_at:$recorded_at,commit:$commit,host:{macos:$macos,architecture:$architecture,memory_bytes:$memory_bytes},runtime:{docker_server:$docker_desktop,model_runner:$model_runner,model:$model,context_size:($context_size|tonumber)},samples:.}' \
  "$samples" > "$result"

rm -f "$output_dir"/.chat-"$timestamp"-*.json "$output_dir"/.retrieval-"$timestamp"-*.json
chmod 600 "$result"
ok "Benchmark metadata written to $result"
