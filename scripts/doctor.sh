#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

failures=0

check() {
  local message=$1
  shift
  if "$@" >/dev/null 2>&1; then
    ok "$message"
  else
    warn "$message"
    failures=$((failures + 1))
  fi
}

if [[ "$(uname -s)" == "Darwin" ]]; then
  ok "macOS detected"
else
  warn "This MVP currently supports macOS only"
  failures=$((failures + 1))
fi

if [[ "$(uname -m)" == "arm64" ]]; then
  ok "Apple Silicon detected"
else
  warn "Apple Silicon arm64 is required"
  failures=$((failures + 1))
fi

memory_bytes=$(sysctl -n hw.memsize 2>/dev/null || printf '0')
memory_gib=$((memory_bytes / 1024 / 1024 / 1024))
if ((memory_gib >= 48)); then
  ok "Unified memory: ${memory_gib} GiB (64K context profile supported)"
elif ((memory_gib >= 32)); then
  warn "Unified memory: ${memory_gib} GiB; set QWEN_CONTEXT_SIZE=32768 before make up"
else
  warn "Unified memory: ${memory_gib} GiB; the default 30B model is not supported"
  failures=$((failures + 1))
fi

for command in docker curl jq git make openssl; do
  check "$command available" command -v "$command"
done

if command -v docker >/dev/null 2>&1; then
  check "Docker daemon reachable" docker info
  check "Docker Compose available" docker compose version
  check "Docker Model Runner plugin available" docker model version
  check "Docker Model Runner service reachable" docker model status
fi

free_kib=$(df -Pk . | awk 'NR == 2 {print $4}')
free_gib=$((free_kib / 1024 / 1024))
required_free_gib=30
if docker model inspect ai/qwen3-coder:30B-A3B-UD-Q4_K_XL >/dev/null 2>&1; then
  # Once the large artifact exists, reserve space for images, volumes, indexes,
  # and normal Docker Desktop copy-on-write growth rather than its download.
  required_free_gib=12
fi
if ((free_gib >= required_free_gib)); then
  ok "Free disk space: ${free_gib} GiB (minimum ${required_free_gib} GiB for current model state)"
else
  warn "At least ${required_free_gib} GiB free disk is required for the current model state; found ${free_gib} GiB"
  failures=$((failures + 1))
fi

for port in 3000 4000 8000 8001 9090 12434; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    info "Port $port is already in use (acceptable when this stack is running)"
  else
    ok "Port $port available"
  fi
done

if ((failures > 0)); then
  die "Doctor found $failures blocking issue(s)"
fi

ok "Host checks passed"
