#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

load_env
require_command curl
require_command jq
require_command docker
require_command python3

key=$(read_secret_file .local/litellm-api-key)
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
output_dir=.local/benchmarks
umask 077
work_dir=$(mktemp -d "${TMPDIR:-/tmp}/local-ai-benchmark.XXXXXX")
samples="$work_dir/samples.jsonl"
mkdir -p "$output_dir"
trap 'rm -rf "$work_dir"' EXIT

if [[ ${1:-} == --context ]]; then
  info "Comparing repository context selection on local and cross-file questions..."
  result="$output_dir/${timestamp}-context-$$.json"
  INDEX_REPO_PATH="$ROOT_DIR" docker compose --profile tools run --rm -T --no-deps \
    --env PYTHONPATH=/workspace/retrieval/src --entrypoint python ingestion \
    /workspace/scripts/benchmark-context.py "$(git rev-parse HEAD)" > "$work_dir/context.json"
  mv "$work_dir/context.json" "$result"
  ok "Paired context measurements written to $result"
  jq -e '.pairs_per_case as $n | all(.summary[]; .focused.passed == $n)' "$result" >/dev/null \
    || die "Focused answers or citations failed; see the paired measurements"
  exit 0
fi

model_runner_version=$(docker model version 2>/dev/null \
  | awk '/Version:/ && !seen {version=$2; seen=1} END {if (seen) print version}')
[[ -n "$model_runner_version" ]] || model_runner_version=unavailable

chat_payload=$(jq -cn '{model:"local-qwen",messages:[{role:"user",content:"In one sentence, explain what a loopback address is."}],temperature:0,max_tokens:96,cache:{"no-cache":true,"no-store":true}}')
info "Preparing the platform retrieval index before timing model loads..."
python3 scripts/retrieval_probe.py index >/dev/null
retrieval_payload=$(python3 scripts/retrieval_probe.py query)

# Unloading keeps the artifacts on disk but makes run 1 a genuine model-load
# sample for both generation and embeddings. Runs 2-4 reuse the loaded models.
docker model unload "$QWEN_MODEL" "$EMBEDDING_MODEL" >/dev/null
info "Models unloaded from memory; recording one cold and three warm samples..."

for run in 1 2 3 4; do
  if [[ "$run" == "1" ]]; then
    phase=cold
  else
    phase=warm
  fi
  chat_meta=$(curl --fail --silent --show-error --max-time 1800 \
    --output "$work_dir/chat.json" \
    --write-out '{"status":%{http_code},"seconds":%{time_total}}' \
    --header "Authorization: Bearer $key" --header 'Content-Type: application/json' \
    --data "$chat_payload" http://127.0.0.1:4000/v1/chat/completions)
  usage=$(jq -ec '.usage | select(.completion_tokens > 0)' "$work_dir/chat.json")
  jq -cn --argjson run "$run" --arg phase "$phase" --argjson http "$chat_meta" --argjson usage "$usage" \
    '{kind:"chat",phase:$phase,run:$run,http:$http,usage:$usage,
      output_tokens_per_second:(if $http.seconds > 0 then (($usage.completion_tokens // 0) / $http.seconds) else 0 end)}' >> "$samples"

  retrieval_meta=$(curl --fail --silent --show-error --max-time 300 \
    --output "$work_dir/retrieval.json" \
    --write-out '{"status":%{http_code},"seconds":%{time_total}}' \
    --header 'Content-Type: application/json' --data "$retrieval_payload" \
    http://127.0.0.1:8000/search)
  count=$(jq -ec '.results' "$work_dir/retrieval.json" | python3 scripts/retrieval_probe.py check)
  jq -cn --argjson run "$run" --arg phase "$phase" --argjson http "$retrieval_meta" --argjson count "$count" \
    '{kind:"retrieval",phase:$phase,run:$run,http:$http,result_count:$count}' >> "$samples"
done

if [[ "$(uname -s)" == "Darwin" ]]; then
  os_name=macOS
  os_version=$(sw_vers -productVersion 2>/dev/null || printf unavailable)
  memory_bytes=$(sysctl -n hw.memsize 2>/dev/null || printf 0)
else
  os_name=$(uname -s)
  os_version=$( ( . /etc/os-release 2>/dev/null && printf '%s' "${VERSION_ID:-${VERSION:-}}" ) || true)
  [[ -n "$os_version" ]] || os_version=unavailable
  memory_kib=$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null || printf 0)
  memory_bytes=$(( memory_kib * 1024 ))
fi

result="$output_dir/${timestamp}.json"
jq -s \
  --arg recorded_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg commit "$(git rev-parse HEAD 2>/dev/null || printf uncommitted)" \
  --arg os "$os_name" \
  --arg os_version "$os_version" \
  --arg architecture "$(uname -m)" \
  --argjson memory_bytes "$memory_bytes" \
  --arg docker_server "$(docker version --format '{{.Server.Version}}' 2>/dev/null || printf unavailable)" \
  --arg model_runner "$model_runner_version" \
  --arg context_size "$QWEN_CONTEXT_SIZE" \
  --arg model "$QWEN_MODEL" \
  '{recorded_at:$recorded_at,commit:$commit,host:{os:$os,os_version:$os_version,architecture:$architecture,memory_bytes:$memory_bytes},runtime:{docker_server:$docker_server,model_runner:$model_runner,model:$model,context_size:($context_size|tonumber)},samples:.}' \
  "$samples" > "$result"

chmod 600 "$result"
ok "Benchmark metadata written to $result"
