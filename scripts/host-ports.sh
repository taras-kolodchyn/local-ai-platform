#!/usr/bin/env bash

source "$(dirname "$0")/lib.sh"

case "${1:-}" in
  report|verify) ;;
  *) die "Usage: host-ports.sh report|verify" ;;
esac

ports=(3000 4000 8000 8001 9090 12434)

# Emit one "address:port" line per listening TCP socket. Prefer ss: it reads the
# kernel socket table from procfs, so an ordinary user sees every published
# address — including Docker's root-owned docker-proxy listeners, which lsof
# hides from non-root users on rootful Docker Engine. macOS has no ss; there the
# Docker Desktop proxy runs as the invoking user, so lsof still sees the ports.
list_listeners() {
  if command -v ss >/dev/null 2>&1; then
    { ss -H -l -t -n 2>/dev/null || true; } | awk '{ print $4 }'
  else
    IFS=,
    # lsof exits nonzero when only some requested ports are in use; keep going.
    { lsof -nP "-iTCP:${ports[*]}" -sTCP:LISTEN -Fn 2>/dev/null || true; } | awk '/^n/ { print substr($0, 2) }'
  fi
}

listeners=$(list_listeners)
for port in "${ports[@]}"; do
  endpoints=$(awk -v port="$port" '$0 ~ (":" port "$")' <<< "$listeners")
  if [[ $1 == report ]]; then
    if [[ -n "$endpoints" ]]; then
      info "Port $port is already in use (acceptable when this stack is running)"
    else
      ok "Port $port available"
    fi
  else
    [[ -n "$endpoints" ]] || die "Expected listener is missing on port $port"
    while IFS= read -r endpoint; do
      address=${endpoint%:*}
      case "$address" in
        127.*|'[::1]'|'::1') continue ;;
      esac
      # On Linux the inference endpoint also serves containers on the host bridge.
      if [[ $port == 12434 && $(uname -s) == Linux ]]; then
        gateways=$(docker network inspect bridge --format '{{range .IPAM.Config}}{{println .Gateway}}{{end}}') ||
          die "Could not verify the Docker bridge address for port $port"
        bridge_address=${address#\[}
        bridge_address=${bridge_address%\]}
        if grep -Fxq -- "$bridge_address" <<< "$gateways"; then
          continue
        fi
      fi
      die "Port $port is exposed beyond loopback at $endpoint"
    done <<< "$endpoints"
  fi
done
