#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

case "${1:-}" in
  report|verify) ;;
  *) die "Usage: host-ports.sh report|verify" ;;
esac

ports=(3000 4000 8000 8001 9090 12434)
listeners=$(
  IFS=,
  # lsof can return nonzero if only some of the requested ports are in use.
  lsof -nP "-iTCP:${ports[*]}" -sTCP:LISTEN -Fn 2>/dev/null || true
)
for port in "${ports[@]}"; do
  endpoints=$(awk -v port="$port" '/^n/ && $0 ~ (":" port "$")' <<< "$listeners")
  if [[ $1 == report ]]; then
    if [[ -n "$endpoints" ]]; then
      info "Port $port is already in use (acceptable when this stack is running)"
    else
      ok "Port $port available"
    fi
  else
    [[ -n "$endpoints" ]] || die "Expected listener is missing on port $port"
    if grep -Eq '(\*:|0\.0\.0\.0:|\[::\]:)' <<< "$endpoints"; then
      die "Port $port is exposed beyond loopback"
    fi
  fi
done
