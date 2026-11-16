"""Verify real PostgreSQL isolation with an automatically provisioned test database."""
import os
import pytest
import psycopg


def test_schema_and_database_isolation(memory_database):
    assert os.getenv('MEMORY_TEST_DSN'), 'The DB test must provision MEMORY_TEST_DSN automatically'
    with psycopg.connect(os.environ['MEMORY_TEST_DSN'], autocommit=True) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS isolation_probe (value int)')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute('SELECT * FROM memory_review.isolation_probe')
        assert connection.execute("SELECT has_database_privilege(current_user, 'local_ai', 'CONNECT')").fetchone() == (False,)
