-- Run in a fresh SCRAM-authenticated session as mcp_reader. Check direct grants
-- so that privileges inherited from PUBLIC cannot mask an incomplete restore.
SET search_path = pg_catalog;
WITH required_grants(acl, privilege) AS (
    SELECT datacl, 'CONNECT' FROM pg_database WHERE datname = current_database()
    UNION ALL
    SELECT nspacl, 'USAGE' FROM pg_namespace WHERE oid = 'public'::regnamespace
    UNION ALL
    SELECT relacl, 'SELECT' FROM pg_class
    WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r', 'p', 'v', 'm', 'f')
    UNION ALL
    SELECT (SELECT defaclacl FROM pg_default_acl
            WHERE defaclrole = 'localai'::regrole AND defaclnamespace = 'public'::regnamespace
              AND defaclobjtype = 'r'), 'SELECT'
)
SELECT current_setting('default_transaction_read_only') = 'on'
   AND current_setting('statement_timeout') = '5s'
   AND EXISTS (SELECT FROM pg_roles WHERE rolname = current_user
               AND NOT (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls))
   AND EXISTS (SELECT FROM pg_db_role_setting
               WHERE setrole = current_user::regrole AND setdatabase = 0
                 AND setconfig @> ARRAY['default_transaction_read_only=on', 'statement_timeout=5s'])
   AND NOT EXISTS (
       SELECT FROM required_grants AS required
       WHERE (SELECT bool_and(granted.privilege_type = required.privilege AND NOT granted.is_grantable)
              FROM aclexplode(required.acl) AS granted
              WHERE granted.grantee = current_user::regrole) IS NOT TRUE
   );
