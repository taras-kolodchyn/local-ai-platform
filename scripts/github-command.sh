#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

# Only the destination comes from .env. The issue and merge grant belong to
# this Make invocation, and unrelated platform secrets stay out of its process.
{
  IFS= read -r -d '' MOYAI_GITHUB_REPOSITORY &&
    IFS= read -r -d '' MOYAI_GITHUB_PROJECT_PATH
} < <(load_env && printf '%s\0' "${MOYAI_GITHUB_REPOSITORY:-}" "${MOYAI_GITHUB_PROJECT_PATH:-}") || die 'Could not load the GitHub target settings'
export MOYAI_GITHUB_REPOSITORY MOYAI_GITHUB_PROJECT_PATH
exec python3 "$@"
