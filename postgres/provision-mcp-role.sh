#!/usr/bin/env bash

set -euo pipefail

: "${MCP_POSTGRES_PASSWORD:?MCP_POSTGRES_PASSWORD is required}"

psql -X --set=ON_ERROR_STOP=1 <<'SQL'
\getenv mcp_password MCP_POSTGRES_PASSWORD
BEGIN;
SELECT pg_advisory_xact_lock(hashtextextended('local-ai-platform/mcp_reader', 0));
SELECT 'CREATE ROLE mcp_reader'
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcp_reader')
\gexec
ALTER ROLE mcp_reader LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS VALID UNTIL 'infinity';
SELECT format('ALTER ROLE mcp_reader PASSWORD %L', :'mcp_password')
\gexec
ALTER ROLE mcp_reader SET default_transaction_read_only = on;
ALTER ROLE mcp_reader SET statement_timeout = '5s';
ALTER ROLE mcp_reader IN DATABASE local_ai RESET default_transaction_read_only;
ALTER ROLE mcp_reader IN DATABASE local_ai RESET statement_timeout;
REVOKE ALL ON DATABASE local_ai FROM mcp_reader;
REVOKE ALL ON SCHEMA public FROM mcp_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM mcp_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM mcp_reader;
GRANT CONNECT ON DATABASE local_ai TO mcp_reader;
GRANT USAGE ON SCHEMA public TO mcp_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO mcp_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO mcp_reader;
COMMIT;
SQL
