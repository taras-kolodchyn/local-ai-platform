#!/usr/bin/env bash

mcp_role_current() {
  local result
  # The service address and require_auth force a real password check, even when
  # PostgreSQL trusts local socket connections during initialization.
  result=$(PGPASSWORD="$MCP_POSTGRES_PASSWORD" docker compose exec -T -e PGPASSWORD postgres \
    psql -XwqAt -v ON_ERROR_STOP=1 \
    -d 'host=postgres dbname=local_ai user=mcp_reader require_auth=scram-sha-256 connect_timeout=3' \
    < "$ROOT_DIR/postgres/check-mcp-role.sql") || return 1
  [[ $result == t ]]
}

start_core() {
  local up=(up --detach --no-build --wait --wait-timeout 420)
  # Reconcile the intended database container before checking its live catalogs.
  compose_wait PostgreSQL up --detach --no-build postgres
  if mcp_role_current 2>/dev/null; then
    info "MCP role and grants are current; skipping mcp-provisioner"
    compose_wait Core -f "$COMPOSE_FILE" -f "$ROOT_DIR/compose.mcp-current.yaml" "${up[@]}"
  else
    info "Provisioning MCP role and grants"
    compose_wait Core "${up[@]}"
    mcp_role_current || die "MCP login or grants are still incorrect after provisioning"
  fi
}
