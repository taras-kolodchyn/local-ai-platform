"""Provision a private memory database; input credentials arrive on stdin."""
import json
import os
import sys

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

PROFILES = ('development', 'review', 'research')


def provision(dsn, passwords):
    if set(passwords) != set(PROFILES):
        raise ValueError('Invalid profile set')
    with psycopg.connect(dsn, autocommit=True) as connection:
        for profile in PROFILES:
            role = 'memory_' + profile
            if not connection.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
                connection.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(
                    sql.Identifier(role), sql.Literal(passwords[profile])))
        if not connection.execute("SELECT 1 FROM pg_database WHERE datname='workspace_memory'").fetchone():
            connection.execute('CREATE DATABASE workspace_memory')
        connection.execute('REVOKE CONNECT ON DATABASE workspace_memory FROM PUBLIC')
        # Existing login roles were verified to be localai and mcp_reader.
        connection.execute('GRANT CONNECT ON DATABASE local_ai TO localai, mcp_reader')
        connection.execute('REVOKE CONNECT ON DATABASE local_ai FROM PUBLIC')
        for profile in PROFILES:
            role = sql.Identifier('memory_' + profile)
            connection.execute(sql.SQL('GRANT CONNECT ON DATABASE workspace_memory TO {}').format(role))
            connection.execute(sql.SQL('ALTER ROLE {} IN DATABASE workspace_memory SET search_path TO {}, public').format(role, role))
            connection.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = '5s'").format(role))
    with psycopg.connect(make_conninfo(dsn, dbname='workspace_memory'), autocommit=True) as connection:
        connection.execute('CREATE EXTENSION IF NOT EXISTS vector')
        connection.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        for profile in PROFILES:
            role = sql.Identifier('memory_' + profile)
            connection.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}').format(role, role))
            connection.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM PUBLIC').format(role))
            connection.execute(sql.SQL('CREATE TABLE IF NOT EXISTS {}.isolation_probe (value integer)').format(role))
            connection.execute(sql.SQL('ALTER TABLE {}.isolation_probe OWNER TO {}').format(role, role))
    for profile in PROFILES:
        role = 'memory_' + profile
        with psycopg.connect(make_conninfo(dsn, dbname='workspace_memory', user=role,
                             password=passwords[profile]), autocommit=True) as connection:
            assert connection.execute('SELECT current_schema()').fetchone()[0] == role
            assert not connection.execute("SELECT has_database_privilege(current_user,'local_ai','CONNECT')").fetchone()[0]
            other = 'memory_review' if profile != 'review' else 'memory_development'
            try:
                connection.execute(sql.SQL('SELECT * FROM {}.isolation_probe').format(sql.Identifier(other)))
            except psycopg.errors.InsufficientPrivilege:
                pass
            else:
                raise RuntimeError('Memory schema isolation failed')
    print('Memory database and cross-profile isolation verified')


if __name__ == '__main__':
    provision(os.environ['DATABASE_URL'], json.load(sys.stdin))
