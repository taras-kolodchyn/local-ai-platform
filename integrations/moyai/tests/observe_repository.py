"""Read controller evidence and independently hash the actual SSH repository."""
import asyncio
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys

from pydantic_ai_harness.ssh_workspace import SSHWorkspaceBackend
from local_config import workspace_directory
from moyai_workspace import DESTINATION, SSH_ARGS


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


async def observe(session_id):
    with sqlite3.connect('file:/data/local-tasks.db?mode=ro', uri=True) as db:
        row = db.execute('SELECT authority,state FROM tasks WHERE session_id=?', (session_id,)).fetchone()
        if not row:
            raise RuntimeError('No attached repository task')
        authority, state = row[0], json.loads(row[1])
        authentic = []
        for receipt in state['receipts']:
            key = db.execute('SELECT key FROM check_keys WHERE session_id=? AND operation_id=?',
                             (session_id, receipt['operation_id'])).fetchone()
            payload = {k:v for k,v in receipt.items() if k != 'receipt_mac'}
            authentic.append(bool(key and hmac.compare_digest(
                hmac.digest(bytes.fromhex(key[0]), canonical(payload), 'sha256').hex(),
                receipt.get('receipt_mac', ''))))
        events = [{'phase':phase,'timestamp':ts} for phase,ts in db.execute(
            'SELECT phase,ts FROM events WHERE session_id=? ORDER BY ts,rowid', (session_id,))]
    directory = workspace_directory(session_id, authority)
    backend = SSHWorkspaceBackend(DESTINATION, working_dir=directory, ssh_args=SSH_ARGS)
    code = '''import hashlib,json,os,stat
from pathlib import Path
root=Path('repository')
rows=[]
for directory,dirs,files in os.walk(root,followlinks=False):
    for name in dirs:
        assert not (Path(directory)/name).is_symlink()
    for name in files:
        path=Path(directory)/name
        info=path.lstat()
        assert stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size<=512*1024
        data=path.read_bytes()
        rows.append({'path':path.relative_to(root).as_posix(),'sha256':hashlib.sha256(data).hexdigest(),'size':len(data)})
rows.sort(key=lambda row:row['path'])
assert len(rows)<=512
print(json.dumps({'uid':os.geteuid(),'files':rows}))
'''
    result = await backend.run(['python3', '-I', '-c', code], timeout=20)
    if result.exit_code:
        raise RuntimeError('Independent repository inspection failed')
    actual = json.loads(result.stdout)
    actual['tree_hash'] = hashlib.sha256(canonical(actual['files'])).hexdigest()
    manifest_path = Path('/run/local-repositories') / state['binding']['manifest']
    manifest = json.loads(manifest_path.read_text())
    originals = {r['path']:r for r in manifest['files']}
    current = {r['path']:r for r in actual['files']}
    def editable(name):
        import re
        return name in ('src/lib.rs','README.md') or re.fullmatch(r'tests/[A-Za-z0-9_-]+\.rs',name)
    actual['protected_unchanged'] = (all(current.get(name)==row for name,row in originals.items() if not editable(name))
                                      and all(name in originals or editable(name) for name in current))
    actual['receipt_authenticity'] = authentic
    actual['events'] = events
    print(json.dumps(actual))


if __name__ == '__main__':
    asyncio.run(observe(sys.argv[1]))
