#!/usr/bin/env bash

set -euo pipefail

: "${MCP_POSTGRES_PASSWORD:?MCP_POSTGRES_PASSWORD is required}"

psql --set=ON_ERROR_STOP=1 --set=mcp_password="$MCP_POSTGRES_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE mcp_reader LOGIN PASSWORD %L', :'mcp_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcp_reader')
\gexec
SELECT format('ALTER ROLE mcp_reader PASSWORD %L', :'mcp_password')
\gexec
ALTER ROLE mcp_reader SET default_transaction_read_only = on;
ALTER ROLE mcp_reader SET statement_timeout = '5s';
GRANT CONNECT ON DATABASE local_ai TO mcp_reader;
GRANT USAGE ON SCHEMA public TO mcp_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcp_reader;
SQL
