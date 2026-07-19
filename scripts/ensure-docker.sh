#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

require_command docker

if docker info >/dev/null 2>&1; then
  ok "Docker Desktop available"
  exit 0
fi

if [[ "$(uname -s)" == "Darwin" && -d /Applications/Docker.app ]]; then
  info "Starting Docker Desktop..."
  open -a Docker
  for _ in $(seq 1 90); do
    if docker info >/dev/null 2>&1; then
      ok "Docker Desktop available"
      exit 0
    fi
    sleep 2
  done
fi

die "Docker daemon is unavailable. Start Docker Desktop and retry."
