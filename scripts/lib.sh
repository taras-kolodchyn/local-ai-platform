#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

cd "$ROOT_DIR"

info() {
  printf '  %s\n' "$*"
}

ok() {
  printf '✓ %s\n' "$*"
}

warn() {
  printf '! %s\n' "$*" >&2
}

die() {
  printf '✗ %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

load_env() {
  [[ -f .env ]] || die ".env is missing; run make bootstrap"
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
}

read_secret_file() {
  local path=$1
  [[ -s "$path" ]] || die "Required generated secret is missing: $path"
  tr -d '\r\n' < "$path"
}

wait_for_url() {
  local url=$1
  local timeout=${2:-120}
  local elapsed=0
  until curl --fail --silent --show-error --max-time 3 "$url" >/dev/null 2>&1; do
    ((elapsed += 2))
    ((elapsed < timeout)) || return 1
    sleep 2
  done
}
