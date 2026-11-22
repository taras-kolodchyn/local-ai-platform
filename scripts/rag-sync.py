"""Maintain branch-scoped search indexes for explicitly connected repositories on macOS."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.local/rag'
LABEL = 'local.workspace.repository-index-' + hashlib.sha256(os.fsencode(ROOT)).hexdigest()[:12]
DOMAIN = f'gui/{os.getuid()}'
JOB = DOMAIN + '/' + LABEL
PLIST = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
PROVISION = runpy.run_path(str(ROOT / 'scripts/provision-workspace.py'))


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def source(value, *, allow_missing=False):
    if Path(value).is_absolute() and (allow_missing or Path(value).is_dir()):
        path = Path(value).resolve()
        suffix = hashlib.sha256(os.fsencode(path)).hexdigest()[:12]
        return str(path), 'connected/local/' + suffix
    match = re.fullmatch(r'(?:https://github\.com/)?([\w.-]+)/([\w.-]+?)(?:\.git)?/?', value, re.ASCII)
    if not match or any(part in ('.', '..') for part in match.groups()):
        raise ValueError('Expected REPO=owner/project or an absolute Git repository path')
    name = '/'.join(match.groups()).lower()
    return 'https://github.com/' + name + '.git', 'connected/github/' + name


def run(stage, args, **kwargs):
    try:
        return subprocess.run(args, check=True, stderr=subprocess.PIPE, timeout=kwargs.pop('timeout', 900),
                              **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(stage + ' failed (' + type(exc).__name__ + ')') from None


def git(*args, **kwargs):
    return run('Git ' + args[0], ['git', '-c', 'core.hooksPath=/dev/null', *args],
               env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}, **kwargs)


def save(path, state):
    PROVISION['private_write'](path, json.dumps(state, indent=2) + '\n')


@contextmanager
def lock(directory):
    with (directory / 'lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def export_tree(bare, commit, target):
    # Never create links or apply modes, owners or extended attributes from Git.
    with tempfile.TemporaryFile(dir=bare.parent) as archive:
        # Export the tree so an empty revision is a valid empty tar stream.
        git('archive', '--format=tar', commit + '^{tree}', cwd=bare, stdout=archive)
        archive.seek(0)
        with tarfile.open(fileobj=archive, mode='r|') as tree:
            for member in tree:
                parts = Path(member.name).parts
                if not parts or Path(member.name).is_absolute() or '..' in parts or '.git' in parts:
                    raise ValueError('Unexpected Git archive path')
                if member.isfile() and member.size <= 1_000_000:
                    dest = target.joinpath(*parts)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with dest.open('xb') as output:
                        shutil.copyfileobj(tree.extractfile(member), output)
                    dest.chmod(0o644)


def publish(directory, state, ref):
    with tempfile.TemporaryDirectory(dir=directory) as tmp:
        bare = Path(tmp) / 'objects.git'
        git('init', '--bare', '--quiet', str(bare))
        git('fetch', '--quiet', '--no-tags', '--depth=1', state['origin'], ref, cwd=bare, timeout=300)
        commit = git('rev-parse', 'FETCH_HEAD^{commit}', cwd=bare, stdout=subprocess.PIPE,
                     text=True).stdout.strip()
        target = Path(tmp) / 'source'
        target.mkdir()
        target.chmod(0o755)
        export_tree(bare, commit, target)
        outcome = run('Repository ingestion', ['bash', 'scripts/compose.sh', '--profile', 'tools',
            'run', '--rm', '--no-deps', '--pull', 'never', 'ingestion', 'index', '/workspace',
            '--repository', state['scope'], '--revision', state['branch'], commit], cwd=ROOT,
            env={**os.environ, 'INDEX_REPO_PATH': str(target)},
            stdout=subprocess.PIPE)
    counts = json.loads(outcome.stdout)
    sid = counts['store_id']
    owner = PROVISION['gateway']((ROOT / '.local/workspace-owner-key').read_text().strip())
    PROVISION['provision_vectors'](owner, [{'id': sid, 'repository': state['scope'], 'branch': state['branch']}],
                                  (ROOT / '.local/vector-backend-key').read_text().strip())
    return {'observed': commit, 'indexed': commit, 'published_at': now(), 'store': sid, 'files': counts['files_indexed']}


def synchronize(directory, initial=None):
    path = directory / 'state.json'
    with lock(directory):
        state = json.loads(path.read_text()) if path.exists() else initial
        if state is None:
            return  # A poll can discover the path before disconnect takes its lock.
        if state.get('disconnecting'):
            if initial:
                raise ValueError('Finish the pending disconnect before reconnecting')
            return
        try:
            ref = 'refs/heads/' + state['branch']
            tip = git('ls-remote', '--exit-code', '--refs', state['origin'], ref,
                      stdout=subprocess.PIPE, text=True, timeout=60).stdout.strip().split('\t')
            if len(tip) != 2 or tip[1] != ref:
                raise ValueError('Unexpected remote ref')
            state['observed'] = tip[0]
            if initial or tip[0] != state.get('indexed'):
                state.update(publish(directory, state, ref))
            state.pop('error', None)
        except Exception as exc:
            state['error'] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            raise
        finally:
            state['checked_at'] = now()
            save(path, state)


def remove_index(state, checkpoint):
    scope, branch = state['scope'], state['branch']
    def sql(statement):
        return run('Index cleanup', ['bash', 'scripts/compose.sh', 'exec', '-T', 'postgres',
            'psql', '-XqtA', '-U', 'localai', '-d', 'local_ai', '-v', 'ON_ERROR_STOP=1',
            '-v', 'scope=' + scope, '-v', 'branch=' + branch], cwd=ROOT,
            input=statement, stdout=subprocess.PIPE, text=True).stdout.strip()

    # Ingestion may have registered the store before its first checkpoint was saved.
    predicate = "repository = :'scope' AND branch = :'branch'"
    sid = sql(f"SELECT id FROM workspace_vector_stores WHERE {predicate} AND generation_id IS NULL;")
    ids = set(state.get('removing_stores', []))
    ids.update(filter(None, [sid, state.get('store')]))
    state['removing_stores'] = sorted(ids)
    save(checkpoint, state)
    sql(f"""BEGIN;
        DELETE FROM source_chunks WHERE {predicate} AND generation_id = 'legacy';
        DELETE FROM ingestion_runs WHERE {predicate};
        DELETE FROM workspace_vector_stores WHERE {predicate} AND generation_id IS NULL;
        COMMIT;""")
    if ids:
        owner = PROVISION['gateway']((ROOT / '.local/workspace-owner-key').read_text().strip())
        existing = {item['vector_store_id'] for item in owner('GET', '/vector_store/list')['data']}
        for store in ids & existing:
            owner('POST', '/vector_store/delete', {'vector_store_id': store})
    registry = PROVISION['LOCAL'] / 'workspace-registry.json'
    if registry.exists():
        data = json.loads(registry.read_text())
        data.get('stores', {}).pop(scope + ':' + branch, None)
        save(registry, data)


def disconnect(directory, scope, branch):
    checkpoint = directory / 'state.json'
    if not checkpoint.exists():
        return
    with lock(directory):
        state = json.loads(checkpoint.read_text())
        if (state['scope'], state['branch']) != (scope, branch):
            raise ValueError('Connection scope mismatch')
        state['disconnecting'] = True
        save(checkpoint, state)
        with PROVISION['vector_registry_lock']():
            remove_index(state, checkpoint)
        if not any(path != checkpoint for path in STATE.glob('*/state.json')):
            if loaded():
                run('Unload schedule', ['launchctl', 'bootout', JOB])
            PLIST.unlink(missing_ok=True)
        checkpoint.unlink()
        # Keep the lock inode for polls already queued and for a later reconnect.


def loaded():
    return subprocess.run(['launchctl', 'print', JOB], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def install():
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    search_path = os.pathsep.join(dict.fromkeys([
        *(str(Path(binary).parent) for name in ('docker', 'git', 'gh')
          if (binary := shutil.which(name))), '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin',
        '/usr/sbin', '/sbin']))
    data = plistlib.dumps({'Label': LABEL, 'ProgramArguments': [sys.executable, str(Path(__file__).resolve()), 'poll'],
        'WorkingDirectory': str(ROOT), 'StartInterval': 60, 'RunAtLoad': True,
        'EnvironmentVariables': {'PATH': search_path}})
    if not PLIST.exists() or PLIST.read_bytes() != data:
        if loaded():
            run('Unload previous schedule', ['launchctl', 'bootout', JOB])
        PROVISION['private_write'](PLIST, data.decode())
    if not loaded():
        run('Load schedule', ['launchctl', 'bootstrap', DOMAIN, str(PLIST)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['connect', 'disconnect', 'poll', 'status'])
    action = parser.parse_args().action
    if action in ('connect', 'disconnect'):
        origin, scope = source(os.environ.get('RAG_REPO', ''), allow_missing=action == 'disconnect')
        branch = os.environ.get('RAG_BRANCH', 'main')
        git('check-ref-format', 'refs/heads/' + branch)
        directory = STATE / hashlib.sha256((scope + ':' + branch).encode()).hexdigest()
        STATE.mkdir(parents=True, exist_ok=True)
        with lock(STATE):
            if action == 'disconnect':
                disconnect(directory, scope, branch)
                print(f'Disconnected {scope} ({branch}).')
                return
            directory.mkdir(exist_ok=True, mode=0o700)
            install()
            synchronize(directory, {'origin': origin, 'scope': scope, 'branch': branch})
        print(f'Following {scope} ({branch}). Run make rag-status for its searchable revision.')
        return
    if action == 'status':
        print('Schedule:', 'loaded (60s)' if loaded() else 'inactive')
    errors = False
    for path in sorted(STATE.glob('*/state.json')):
        if action == 'status':
            try:
                content = path.read_text()
            except FileNotFoundError:
                continue
            print(json.dumps(json.loads(content), ensure_ascii=False))
        else:
            try:
                synchronize(path.parent)
            except Exception:
                errors = True
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError) as error:
        sys.exit(str(error))
