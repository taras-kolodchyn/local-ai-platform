"""Required, disposable pgvector tests; never use the live platform database."""
import os
from pathlib import Path
import secrets
import subprocess
import time
import uuid

import psycopg
from psycopg.conninfo import make_conninfo
import pytest

from local_ai_retrieval.config import Settings
from local_ai_retrieval.db import migrate

POSTGRES_IMAGE = 'pgvector/pgvector:0.8.7-pg17-bookworm'


class PrivateSettings(Settings):
    def __repr__(self):
        return '<disposable retrieval test settings>'


def docker(*arguments, **kwargs):
    return subprocess.run(['docker', *arguments], capture_output=True, text=True,
                          check=True, timeout=180, **kwargs).stdout.strip()


@pytest.fixture(scope='session')
def retrieval_database():
    name = 'local-ai-retrieval-test-' + uuid.uuid4().hex
    password = secrets.token_hex(24)
    started = False
    stage = 'starting disposable pgvector (a running Docker engine is required)'
    try:
        docker('run', '--detach', '--name', name,
               '--security-opt', 'no-new-privileges:true',
               '--tmpfs', '/var/lib/postgresql/data:rw,nosuid,nodev',
               '--publish', '127.0.0.1::5432',
               '--env', 'POSTGRES_USER=retrieval_test',
               '--env', 'POSTGRES_DB=retrieval_test',
               '--env', 'POSTGRES_PASSWORD', POSTGRES_IMAGE,
               env=os.environ | {'POSTGRES_PASSWORD': password})
        started = True
        stage = 'resolving the loopback pgvector port'
        host, port = docker('port', name, '5432/tcp').rsplit(':', 1)
        if host != '127.0.0.1' or not 0 < int(port) < 65536:
            raise ValueError('Unexpected PostgreSQL binding')
        dsn = make_conninfo(host=host, port=port, dbname='retrieval_test',
                           user='retrieval_test', password=password, connect_timeout=1)
        stage = 'waiting for pgvector readiness'
        deadline = time.monotonic() + 60
        while True:
            try:
                with psycopg.connect(dsn):
                    break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('PostgreSQL did not become ready') from None
                time.sleep(0.5)
        settings = PrivateSettings(
            database_url=dsn, litellm_base_url='http://127.0.0.1:1/v1',
            litellm_api_key_file=Path('/unused'), embedding_model_alias='local-embeddings',
            embedding_dimensions=1024, default_limit=6, max_limit=12,
            max_context_chars=24000, log_level='WARNING',
        )
        stage = 'applying retrieval migrations'
        migrate(settings)
    except Exception:
        # Neither subprocess commands nor pytest setup failures may disclose the DSN.
        pytest.fail(f'Retrieval DB setup failed while {stage}', pytrace=False)
    else:
        yield settings
    finally:
        try:
            result = subprocess.run(['docker', 'rm', '--force', '--volumes', name],
                                    capture_output=True, text=True, timeout=30)
            if started and result.returncode:
                pytest.fail(f'Could not remove disposable DB container {name}', pytrace=False)
        except (OSError, subprocess.SubprocessError):
            if started:
                pytest.fail(f'Could not remove disposable DB container {name}', pytrace=False)


@pytest.fixture
def db_settings(retrieval_database):
    with psycopg.connect(retrieval_database.database_url) as connection:
        connection.execute('TRUNCATE source_chunks, ingestion_runs, workspace_vector_stores RESTART IDENTITY')
    return retrieval_database
