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

configure_compose_scope() {
  export COMPOSE_PROJECT_NAME=local-ai-platform
  export COMPOSE_FILE="$ROOT_DIR/compose.yaml"
  unset COMPOSE_PROFILES COMPOSE_PATH_SEPARATOR
}

# Public scripts own this localhost platform. Inherited Compose overrides must
# not redirect them to another project or implicitly enable connected profiles.
configure_compose_scope

load_env() {
  [[ -f .env ]] || die ".env is missing; run make bootstrap"
  set -a
  # Fill absent settings from repository defaults before applying .env. This
  # matches model-config.py and prevents inherited model overrides from routing
  # startup and shutdown to a different reference than the generated gateway.
  if [[ -f .env.example ]]; then
    # shellcheck disable=SC1091
    source .env.example
  fi
  # shellcheck disable=SC1091
  source .env
  set +a
  configure_compose_scope
}

read_admin_key() {
  [[ -n ${LITELLM_MASTER_KEY:-} && $LITELLM_MASTER_KEY != GENERATE_ME ]] || die 'Missing admin key'
  printf '%s' "$LITELLM_MASTER_KEY"
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
