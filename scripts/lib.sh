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
  unset OPENBAO_COMPOSE_PLACEHOLDER
  case ${LITELLM_UI_PASSWORD_SOURCE:-env} in
    env) ;;
    openbao) export COMPOSE_FILE="$COMPOSE_FILE:$ROOT_DIR/compose.openbao.yaml" ;;
    openbao-local)
      export COMPOSE_FILE="$COMPOSE_FILE:$ROOT_DIR/compose.openbao.yaml:$ROOT_DIR/compose.openbao-local.yaml"
      export OPENBAO_ADDR=https://openbao:8200 OPENBAO_CACERT=/run/openbao-auth/ca.pem
      ;;
    *) die 'LITELLM_UI_PASSWORD_SOURCE must be env, openbao or openbao-local' ;;
  esac
  case ${LITELLM_MASTER_KEY_SOURCE:-env} in
    env) ;;
    openbao-local)
      [[ ${LITELLM_UI_PASSWORD_SOURCE:-env} == openbao-local ]] || die 'The OpenBao master key requires the local OpenBao service'
      export COMPOSE_FILE="$COMPOSE_FILE:$ROOT_DIR/compose.openbao-master.yaml"
      # Compose interpolates the required base value before merging the overlay.
      # The overlay clears this throwaway value before Docker creates the gateway.
      OPENBAO_COMPOSE_PLACEHOLDER=$(openssl rand -hex 32)
      export OPENBAO_COMPOSE_PLACEHOLDER
      ;;
    *) die 'LITELLM_MASTER_KEY_SOURCE must be env or openbao-local' ;;
  esac
}

read_admin_key() {
  if [[ ${LITELLM_MASTER_KEY_SOURCE:-env} == openbao-local ]]; then
    python3 scripts/openbao-local.py read-master
  else
    [[ -n ${LITELLM_MASTER_KEY:-} && $LITELLM_MASTER_KEY != GENERATE_ME ]] || die 'Missing admin key'
    printf '%s' "$LITELLM_MASTER_KEY"
  fi
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
