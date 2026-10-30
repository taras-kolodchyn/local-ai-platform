"""Run inside the provisioner with a disposable database in integration checks."""
import os
import pytest
import psycopg


@pytest.mark.skipif(not os.getenv('MEMORY_TEST_DSN'), reason='requires provisioned integration database')
def test_schema_and_database_isolation():
    with psycopg.connect(os.environ['MEMORY_TEST_DSN'], autocommit=True) as connection:
        connection.execute('CREATE TABLE IF NOT EXISTS isolation_probe (value int)')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute('SELECT * FROM memory_review.isolation_probe')
        assert connection.execute("SELECT has_database_privilege(current_user, 'local_ai', 'CONNECT')").fetchone() == (False,)
