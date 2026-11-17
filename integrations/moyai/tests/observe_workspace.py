"""Trusted smoke observer: read the controller registry and inspect the actual SSH filesystem."""
import asyncio
import json
import sqlite3
import sys

from moyai.models import Session
from pydantic import TypeAdapter
from pydantic_ai.workspaces import WorkspaceRef
from pydantic_ai_harness.ssh_workspace import SSHWorkspaceBackend

from local_config import workspace_directory
from moyai_workspace import DESTINATION, SSH_ARGS


async def observe(session_id):
    with sqlite3.connect('file:/data/moyai.db?mode=ro', uri=True) as connection:
        row = connection.execute(
            f'SELECT workspace, workspace_scope FROM "{Session.__tablename__}" WHERE id=?',
            (session_id,),
        ).fetchone()
    if not row or not row[0] or not row[1]:
        raise RuntimeError('The controller has no bound workspace for this session')
    directory = workspace_directory(session_id, row[1])
    backend = SSHWorkspaceBackend(DESTINATION, working_dir=directory, ssh_args=SSH_ARGS)
    if backend.ref != TypeAdapter(WorkspaceRef).validate_json(row[0]):
        raise RuntimeError('The controller workspace reference does not match its session scope')
    code = '''import hashlib, json, os
from pathlib import Path
files={name:hashlib.sha256(Path(name).read_bytes()).hexdigest() if Path(name).is_file() else None
       for name in ('calc.py','test_calc.py')}
print(json.dumps({'directory':str(Path.cwd()),'uid':os.geteuid(),'files':files}))
'''
    result = await backend.run(['python', '-I', '-c', code], timeout=15)
    if result.exit_code:
        raise RuntimeError('Trusted workspace inspection failed')
    print(json.dumps(json.loads(result.stdout)))


if __name__ == '__main__':
    asyncio.run(observe(sys.argv[1]))
