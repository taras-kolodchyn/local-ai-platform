#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

if (( $# )) && ! [[ $# == 1 && $1 == --runtime-checked ]]; then
  die 'Usage: doctor.sh [--runtime-checked]'
fi

require_command python3
model_configuration=$(python3 scripts/model-config.py resolve --format json)
qwen_model=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["models"][0]["reference"])' <<< "$model_configuration")

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

kernel=$(uname -s)
case "$kernel" in
  Darwin) ok "macOS detected" ;;
  Linux) ok "Linux detected" ;;
  *)
    warn "Supported hosts are macOS and Linux; found $kernel"
    failures=$((failures + 1))
    ;;
esac

case "$(uname -m)" in
  arm64 | aarch64) ok "64-bit Arm detected" ;;
  x86_64 | amd64) ok "64-bit x86 detected" ;;
  *)
    warn "A 64-bit Arm or x86 host is required"
    failures=$((failures + 1))
    ;;
esac

# "Unified memory" on Apple Silicon is the generation runtime's working set.
# On Linux the llama.cpp engine runs in a container and uses system RAM, so the
# same thresholds apply to total system memory.
if [[ "$kernel" == "Darwin" ]]; then
  memory_label="Unified memory"
  memory_bytes=$(sysctl -n hw.memsize 2>/dev/null || printf '0')
else
  memory_label="System memory"
  # `free` is present on Ubuntu (procps); fall back to /proc/meminfo.
  memory_kib=$(free -k 2>/dev/null | awk '/^Mem:/ {print $2; exit}')
  [[ -n "$memory_kib" ]] || memory_kib=$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null)
  memory_bytes=$(( ${memory_kib:-0} * 1024 ))
fi
memory_gib=$((memory_bytes / 1024 / 1024 / 1024))
if ((memory_gib >= 48)); then
  ok "${memory_label}: ${memory_gib} GiB (64K context profile supported)"
elif ((memory_gib >= 32)); then
  warn "${memory_label}: ${memory_gib} GiB; set QWEN_CONTEXT_SIZE=32768 before make up"
else
  warn "${memory_label}: ${memory_gib} GiB; the default 30B model is not supported"
  failures=$((failures + 1))
fi

for command in docker curl jq git make openssl python3; do
  check "$command available" command -v "$command"
done

# During startup, Docker readiness and model preparation already ran these four checks.
if [[ $# == 0 ]] && command -v docker >/dev/null 2>&1; then
  check "Docker daemon reachable" docker info
  check "Docker Compose available" docker compose version
  check "Docker Model Runner plugin available" docker model version
  check "Docker Model Runner service reachable" docker model status
fi

free_kib=$(df -Pk . | awk 'NR == 2 {print $4}')
free_gib=$((free_kib / 1024 / 1024))
required_free_gib=30
if docker model inspect "$qwen_model" >/dev/null 2>&1; then
  # Once the large artifact exists, reserve space for images, volumes, indexes,
  # and normal container storage growth rather than its download.
  required_free_gib=12
fi
if ((free_gib >= required_free_gib)); then
  ok "Free disk space: ${free_gib} GiB (minimum ${required_free_gib} GiB for current model state)"
else
  warn "At least ${required_free_gib} GiB free disk is required for the current model state; found ${free_gib} GiB"
  failures=$((failures + 1))
fi

bash scripts/host-ports.sh report

if ((failures > 0)); then
  die "Doctor found $failures blocking issue(s)"
fi

ok "Host checks passed"
