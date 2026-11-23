"""Resolve operator-enrolled read credentials in the controller only."""
import asyncio
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from uuid import UUID

PREFIX = 'repository-reader:v1:'


def reference(authority):
    encoded = json.dumps(authority, sort_keys=True, separators=(',', ':')).encode()
    return PREFIX + hashlib.sha256(encoded).hexdigest()


def record_path(directory, connection):
    return directory / 'repository-readers' / str(UUID(connection))


def store(security, directory, connection, grant, *, enroll):
    path = record_path(directory, connection)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    with (path.parent / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if grant['expires'] <= time.time():
            raise ValueError('The supplied credential has expired.')
        if path.exists():
            if enroll:
                raise ValueError('Reader already enrolled.')
            previous = json.loads(security.unseal(path.read_text()))
            if previous['authority'] != grant['authority']:
                raise ValueError('GitHub authority changed. Enroll a new connection and conversation.')
            if previous['expires'] > grant['expires']:
                raise ValueError('A newer credential is already installed.')
        with tempfile.NamedTemporaryFile(dir=path.parent, mode='w', delete=False) as output:
            temporary = Path(output.name)
            try:
                output.write(security.seal(grant))
                output.flush()
                os.fsync(output.fileno())
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)


def resolve(security, directory, identity, credential, read_only):
    if not isinstance(credential, str) or not credential.startswith(PREFIX):
        return credential
    if not read_only or not identity.startswith('connection_'):
        raise ValueError('A private reader connection is required.')
    path = record_path(directory, identity.removeprefix('connection_'))
    grant = json.loads(security.unseal(path.read_text()))
    if reference(grant['authority']) != credential or grant['expires'] <= time.time():
        raise ValueError('Repository reader is expired or its authority has changed.')
    return grant['token']


def install_readers(security, directory):
    from moyai.connections import SERVICES
    from pydantic_ai_harness.github import GitHub

    def github(**options):
        options['auth'] = resolve(security, directory, options['id'], options['auth'], options['read_only'])
        return GitHub(**options)

    SERVICES['github'] = github


async def publish(connection, grant, enroll):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine
    from moyai.models import Connection
    from moyai.security import Security
    from moyai.settings import Settings

    settings = Settings(_env_file=None)
    security = Security(settings.master_key)
    engine = create_async_engine(settings.sqlalchemy_url)
    try:
        async with engine.connect() as db:
            query = select(Connection.credential).where(
                Connection.id == connection, Connection.kind == 'github',
                Connection.enabled.is_(True), Connection.shared.is_(False), Connection.read_only.is_(True),
            )
            credential = await db.scalar(query)
            if credential is None or security.unseal(credential).decode() != reference(grant['authority']):
                raise ValueError('Reader enrollment does not match the current GitHub authority.')
            store(security, settings.data_dir, connection, grant, enroll=enroll)
    finally:
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(publish(str(UUID(sys.argv[1])), json.load(sys.stdin), sys.argv[2] == 'enroll'))
