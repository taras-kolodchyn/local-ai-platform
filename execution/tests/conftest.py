"""Disposable PostgreSQL for memory integration tests, shared by local and CI runs."""
import importlib.util
import os
from pathlib import Path
import secrets
import subprocess
import time
import uuid

import psycopg
from psycopg.conninfo import make_conninfo
import pytest

ROOT = Path(__file__).resolve().parents[2]
POSTGRES_IMAGE = 'pgvector/pgvector:0.8.7-pg17-bookworm'


def docker(*arguments, **kwargs):
    return subprocess.run(['docker', *arguments], capture_output=True, text=True,
                          check=True, timeout=180, **kwargs).stdout.strip()


@pytest.fixture(scope='session')
def memory_database():
    """Export MEMORY_TEST_DSN for a provisioned database; never skip missing Docker."""
    if os.environ.get('MEMORY_TEST_DSN'):
        yield
        return

    name = 'local-ai-memory-test-' + uuid.uuid4().hex
    admin_password = secrets.token_hex(24)
    started = False
    stage = 'starting disposable PostgreSQL (a running Docker engine is required)'
    try:
        docker('run', '--detach', '--name', name,
               '--security-opt', 'no-new-privileges:true',
               '--tmpfs', '/var/lib/postgresql/data:rw,nosuid,nodev',
               '--publish', '127.0.0.1::5432',
               '--env', 'POSTGRES_USER=localai', '--env', 'POSTGRES_DB=local_ai',
               '--env', 'POSTGRES_PASSWORD', POSTGRES_IMAGE,
               env=os.environ | {'POSTGRES_PASSWORD': admin_password})
        started = True
        stage = 'resolving the loopback PostgreSQL port'
        binding = docker('port', name, '5432/tcp')
        host, port = binding.rsplit(':', 1)
        if host != '127.0.0.1' or not 0 < int(port) < 65536:
            raise ValueError('Unexpected PostgreSQL port binding')
        admin_dsn = make_conninfo(host=host, port=port, dbname='local_ai',
                                  user='localai', password=admin_password, connect_timeout=1)
        stage = 'waiting for PostgreSQL readiness'
        deadline = time.monotonic() + 60
        while True:
            try:
                with psycopg.connect(admin_dsn, autocommit=True) as connection:
                    # The production provisioner grants this pre-existing MCP role access.
                    connection.execute('CREATE ROLE mcp_reader NOLOGIN')
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('PostgreSQL did not become ready') from None
                time.sleep(0.5)

        stage = 'running the production memory provisioner'
        spec = importlib.util.spec_from_file_location('memory_provisioner', ROOT / 'postgres/provision-memory.py')
        provisioner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(provisioner)
        passwords = {profile: secrets.token_hex(24) for profile in provisioner.PROFILES}
        provisioner.provision(admin_dsn, passwords)
        os.environ['MEMORY_TEST_DSN'] = make_conninfo(
            admin_dsn, dbname='workspace_memory', user='memory_development',
            password=passwords['development'])
    except Exception:
        # Pytest tracebacks can include function arguments; keep credentials private.
        pytest.fail(f'Memory DB test setup failed while {stage}', pytrace=False)
    else:
        yield
    finally:
        os.environ.pop('MEMORY_TEST_DSN', None)
        try:
            result = subprocess.run(['docker', 'rm', '--force', '--volumes', name],
                                    capture_output=True, text=True, timeout=30)
            if started and result.returncode:
                pytest.fail(f'Could not remove disposable DB container {name}', pytrace=False)
        except (OSError, subprocess.SubprocessError):
            if started:
                pytest.fail(f'Could not remove disposable DB container {name}', pytrace=False)
