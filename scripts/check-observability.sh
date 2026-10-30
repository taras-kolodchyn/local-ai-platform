#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

require_command curl
require_command jq

timeout=${MONITORING_TIMEOUT_SECONDS:-60}
[[ "$timeout" =~ ^[1-9][0-9]*$ ]] || die "MONITORING_TIMEOUT_SECONDS must be a positive integer"
deadline=$((SECONDS + timeout))
reason="Monitoring has not been checked"

fetch() {
  local remaining=$((deadline - SECONDS))
  ((remaining > 0)) || return 1
  ((remaining <= 3)) || remaining=3
  curl --fail --silent --max-time "$remaining" --user-agent 'Platform Health Check' "$1"
}

check_monitoring() {
  local metrics grafana targets
  reason="Retrieval metrics endpoint unavailable"
  metrics=$(fetch "${RETRIEVAL_BASE_URL:-http://127.0.0.1:8000}/metrics") || return 1
  reason="Expected retrieval metric is missing"
  grep -q 'local_ai_retrieval_requests_total' <<< "$metrics" || return 1

  reason="Prometheus readiness endpoint unavailable"
  fetch "${PROMETHEUS_BASE_URL:-http://127.0.0.1:9090}/-/ready" >/dev/null || return 1
  reason="Grafana database is not healthy"
  grafana=$(fetch "${GRAFANA_BASE_URL:-http://127.0.0.1:3000}/api/health") || return 1
  jq -e '.database == "ok"' <<< "$grafana" >/dev/null 2>&1 || return 1

  reason="Prometheus target API unavailable"
  targets=$(fetch "${PROMETHEUS_BASE_URL:-http://127.0.0.1:9090}/api/v1/targets") || return 1
  if jq -e '.status == "success" and (.data.activeTargets | type == "array" and length > 0 and all(.[]; .health == "up"))' \
      <<< "$targets" >/dev/null 2>&1; then
    return 0
  fi
  reason=$(jq -r '
    if .status != "success" or (.data.activeTargets | type) != "array" then
      "Invalid Prometheus target response"
    elif (.data.activeTargets | length) == 0 then
      "No active Prometheus targets"
    else
      "Prometheus targets not healthy: " + ([.data.activeTargets[] | select(.health != "up") |
        "\(.labels.job // "unknown") (\(.health // "unknown")): \(.lastError // "no scrape result yet")"] | join("; "))
    end' <<< "$targets" 2>/dev/null) || reason="Invalid Prometheus target response"
  return 1
}

info "Waiting for monitoring and a healthy Prometheus scrape (up to ${timeout}s)..."
last_failure=""
while ((SECONDS < deadline)); do
  if check_monitoring; then
    ok "Prometheus, Grafana, and all configured scrape targets are healthy"
    exit 0
  fi
  if ((SECONDS >= deadline)); then
    # Keep the last observed failure when the deadline interrupts a later probe.
    [[ -z "$last_failure" ]] || reason=$last_failure
    break
  fi
  last_failure=$reason
  sleep 1
done
die "Monitoring did not become ready within ${timeout}s: $reason"
