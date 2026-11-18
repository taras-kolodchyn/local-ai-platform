"""Activity-side repository adapter. All network origins and executable names are controller constants."""
import asyncio
import base64
from contextlib import ExitStack
import difflib
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import time

from local_config import workspace_directory
from repository_ledger import ARTIFACT_PATHS, Ledger, RepositoryError, clean_coverage_baseline, verify_artifact

CATALOG = Path('/run/local-repositories')
CONTEXT_URL = 'http://repository-context:8000/mcp/'
CHECK_TIMEOUT = 240
MAX_WIRE = 16 << 20
MAX_FILE = 512 << 10
MAX_TREE = 8 << 20
MAX_FILES = 512
RUST_RETRY_CONTRACT = (
    'Add new Cargo integration regressions in tests/*.rs using write_file, for example '
    'tests/retry_regression.rs. Tests embedded only in src/lib.rs are project tests, '
    'not added integration-regression evidence. All added tests must pass this repair '
    'and complete on the compiled same-API 60,000 ms counterexample; at least one '
    'must fail that counterexample. Test both public retry entry points at the 30,000 ms ceiling.'
)
SSH_ARGS = ('-i', '/run/moyai-ssh/id_ed25519', '-o', 'IdentitiesOnly=yes',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=/run/moyai-ssh/known_hosts')
GUEST = ('python3', '-I', '/opt/local-repository/guest.py')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def fingerprint(value):
    return sha256(canonical(value)).hexdigest()


def relative_name(name):
    if not isinstance(name, str) or len(name) > 240 or any(ord(c) < 32 or ord(c) == 127 for c in name) or '\\' in name:
        raise RepositoryError('Invalid repository path.')
    path = PurePosixPath(name)
    if (not path.parts or path.is_absolute() or str(path) != name
            or any(part.startswith('.') for part in path.parts)):
        raise RepositoryError('Use a relative project path without dot components.')
    return name


def bounded_read(root, name, limit, *, size=None):
    path = PurePosixPath(name)
    if path.is_absolute() or str(path) != name or not path.parts or any(part.startswith('.') for part in path.parts):
        raise RepositoryError('Invalid catalog entry path.')
    parts = path.parts
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    with ExitStack() as stack:
        fd = os.open(root, directory_flags)
        stack.callback(os.close, fd)
        for part in parts[:-1]:
            fd = os.open(part, directory_flags, dir_fd=fd)
            stack.callback(os.close, fd)
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        stream = stack.enter_context(os.fdopen(file_fd, 'rb'))
        info = os.fstat(file_fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
                or (size is not None and info.st_size != size)):
            raise RepositoryError('Catalog entry is not a bounded registered file.')
        data = stream.read(info.st_size + 1)
        if len(data) != info.st_size or os.fstat(file_fd).st_mtime_ns != info.st_mtime_ns:
            raise RepositoryError('Catalog entry changed while reading.')
        return data


def projected_tree_hash(files, name, content):
    if not editable(name) or len(content) > MAX_FILE or b'\0' in content:
        raise RepositoryError('The resulting file violates the profile path, text or size limit.')
    projected = files | {name: content}
    if len(projected) > MAX_FILES or sum(map(len,projected.values())) > MAX_TREE:
        raise RepositoryError('The resulting project exceeds the file count or tree size limit.')
    # Guest.tree returns every file as ASCII-escaped JSON. Use the same bounded
    # envelope as snapshot profile validation before committing native bytes.
    envelope = {'ok': True, 'files': [], 'tree_hash': '0'*64, 'protected_unchanged': False, 'snapshot_id': '0'*64}
    wire_size = len(canonical(envelope))
    rows = []
    for path, data in sorted(projected.items()):
        row = {'path': path, 'size': len(data), 'sha256': sha256(data).hexdigest()}
        entry = row | {'text': data.decode('utf-8')}
        wire_size += len(canonical(entry)) + bool(rows)
        if wire_size > MAX_WIRE:
            raise RepositoryError('The resulting project exceeds the escaped tree response wire limit.')
        rows.append(row)
    return fingerprint(rows)


def editable(name):
    return name in ('README.md', 'src/lib.rs') or re.fullmatch(r'tests/[A-Za-z0-9_-]+\.rs', name) is not None


def workspace_path(tool, args):
    name = args.get('path', '.')
    if name in ('.', 'repository', 'repository/') and tool in {'list_files','grep'}:
        return 'repository'
    if isinstance(name, str) and name.startswith('repository/'):
        name = name.removeprefix('repository/')
    name = relative_name(name)
    if tool in ('write_file', 'edit_file') and not editable(name):
        raise RepositoryError('The fixed profile permits edits to src/lib.rs, tests/*.rs and README.md.')
    return 'repository/' + name


def scoped_tree(reply, manifest):
    if reply.get('ok') is not True or reply.get('snapshot_id') != manifest['snapshot_id']:
        raise RepositoryError('Project tree does not belong to the pinned snapshot.')
    files = {}
    rows = []
    for entry in reply['files']:
        name = relative_name(entry['path'])
        data = entry['text'].encode()
        if (name in files or len(data) != entry['size'] or sha256(data).hexdigest() != entry['sha256']):
            raise RepositoryError('Project bytes do not match their claimed hash.')
        files[name] = data
        rows.append({k: entry[k] for k in ('path','sha256','size')})
    rows.sort(key=lambda item: item['path'])
    original = {row['path']:row for row in manifest['files']}
    current = {row['path']:row for row in rows}
    if (reply.get('tree_hash') != fingerprint(rows) or reply.get('protected_unchanged') is not True
            or any(current.get(name) != original.get(name) for name in current.keys() | original.keys()
                   if not editable(name))):
        raise RepositoryError('Protected project data or tree digest changed.')
    return files, reply['tree_hash']


def coverage_path(name, original_paths):
    return name not in original_paths and re.fullmatch(r'tests/[A-Za-z0-9_-]{1,80}\.rs', name) is not None


def source_patch(before, after, profile='rust-retry'):
    text = []
    for name in sorted(before.keys() | after.keys()):
        if before.get(name) == after.get(name):
            continue
        if not editable(name) or (profile == 'rust-retry-tests' and not coverage_path(name, before)):
            raise RepositoryError('Patch includes a protected path.')
        # Git separates diff records at LF. Other Unicode separators are file content.
        old = StringIO(before.get(name, b'').decode(), newline='\n').readlines()
        new = StringIO(after.get(name, b'').decode(), newline='\n').readlines()
        if not (old or new):
            raise RepositoryError('Cannot deliver a content-free file change.')
        old_label = 'a/' + name if name in before else '/dev/null'
        new_label = 'b/' + name if name in after else '/dev/null'
        for line in difflib.unified_diff(old, new, old_label, new_label):
            text.append(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n')
    if profile == 'rust-retry-tests' and not text:
        raise RepositoryError('The coverage patch requires a new Rust integration test.')
    if not text or (profile == 'rust-retry' and before.get('src/lib.rs') == after.get('src/lib.rs')) or not any(
        p.startswith('tests/') and after[p] != before.get(p) for p in after
    ):
        raise RepositoryError('The patch requires implementation and regression-test changes.')
    return ''.join(text).encode()


async def ssh_json(command, request, timeout):
    payload = canonical(request)
    if len(payload) > MAX_WIRE:
        raise RepositoryError('Repository request exceeds transfer limit.')
    process = await asyncio.create_subprocess_exec(
        'ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', *SSH_ARGS,
        'agent@moyai-sandbox', shlex.join(command), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    async def send():
        process.stdin.write(payload)
        await process.stdin.drain()
        process.stdin.close()

    async def receive():
        data = bytearray()
        while chunk := await process.stdout.read(65536):
            data.extend(chunk)
            if len(data) > MAX_WIRE:
                raise RepositoryError('Repository response exceeds transfer limit.')
        return data

    try:
        async with asyncio.timeout(timeout):
            _, raw = await asyncio.gather(send(), receive())
            if await process.wait():
                raise RepositoryError('Repository SSH operation failed.')
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    return parse_guest_response(raw, request.get('op'))


def parse_guest_response(raw, operation):
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise RepositoryError('Invalid guest response.')
    if 'error' in result:
        # A signed check may legitimately explain a compiler/preparation failure.
        # Identity, fresh tree, MAC and settlement are verified by the ledger.
        candidate = (operation in {'check','status','cancel'}
                     and result.get('state') in {'failed','cancelled','timeout','unknown'}
                     and all(isinstance(result.get(key), str) and result[key]
                             for key in ('task_id','operation_id','tree_hash','receipt_mac'))
                     and re.fullmatch('[a-f0-9]{64}', result['receipt_mac']))
        if not candidate:
            raise RepositoryError('Guest rejected the repository operation: ' + str(result['error'])[:256])
    return result


# Write only the three controller-selected files. Directory descriptors keep symlinks
# and concurrent directory renames from redirecting writes outside this workspace.
ARTIFACT_WRITER = '''
import base64, hashlib, json, os, re, sys, uuid
request = json.load(sys.stdin)
conversation = request['conversation_id']
assert re.fullmatch(r'[a-f0-9]{64}', conversation)
flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
root = os.open('/workspace', flags)
cwd = os.open(conversation, flags, dir_fd=root)
try: os.mkdir('repository-results', 0o700, dir_fd=cwd)
except FileExistsError: pass
directory = os.open('repository-results', flags, dir_fd=cwd)
digests = {}
for name in ('changes.patch','test-report.json','result.json'):
    content = base64.b64decode(request['files'][name], validate=True)
    temporary = '.partial-' + uuid.uuid4().hex
    fd = os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(content)
    os.rename(temporary,name,src_dir_fd=directory,dst_dir_fd=directory)
    fd = os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
    with os.fdopen(fd,'rb') as stream:
        digests['repository-results/'+name] = hashlib.file_digest(stream,'sha256').hexdigest()
print(json.dumps(digests))
'''


class NativeFiles:
    """Deliver through the same capability and object storage as Moyai's authenticated Files endpoint."""

    def __init__(self, runtime):
        from moyai.artifacts import artifact_capability
        self.database = runtime.database
        self.objects = runtime.objects
        tools = artifact_capability(self.database, self.objects).get_toolset()
        self.save = tools.tools['save_artifact'].function

    async def observe(self, ctx, turn_id, path, digest):
        from moyai.models import Artifact
        from sqlalchemy import select

        await self.database.authorize_scope(ctx.deps)
        async with self.database.sessions() as db:
            row = await db.scalar(select(Artifact).where(
                Artifact.session_id == ctx.deps.session_id, Artifact.turn_id == turn_id,
                Artifact.name == path, Artifact.content_hash == digest))
        if row is None:
            return None
        content = await self.objects.get(row.digest)
        verify_artifact(content, digest)
        return {'id':row.id, 'name':path, 'sha256':digest, 'bytes':len(content),
                'download_url':'/api/files/' + row.id}

    @staticmethod
    async def verify_source(ctx, path, digest):
        root = PurePosixPath(await ctx.workspace.realpath(await ctx.workspace.working_dir()))
        resolved = PurePosixPath(await ctx.workspace.realpath(path))
        if resolved != root / path or path not in ARTIFACT_PATHS:
            raise RepositoryError('Artifact path changed before native delivery.')
        verify_artifact(await ctx.workspace.read_bytes(str(resolved)), digest)

    async def deliver(self, ctx, turn_id, path, digest, dispatch):
        from sqlalchemy.exc import OperationalError

        try:
            return await self._deliver_once(ctx, turn_id, path, digest, dispatch)
        except (OSError, OperationalError):
            # Observe first on retry. Native Files deduplicates (turn, name, object digest).
            return await self._deliver_once(ctx, turn_id, path, digest, dispatch)

    async def _deliver_once(self, ctx, turn_id, path, digest, dispatch):
        observed = await self.observe(ctx, turn_id, path, digest)
        if observed:
            return observed
        if not dispatch or turn_id != ctx.deps.turn_id:
            raise RepositoryError('Native file delivery is unconfirmed. Its reserved upload was not repeated.')
        await self.verify_source(ctx, path, digest)
        saved = await self.save(ctx, path)
        observed = await self.observe(ctx, turn_id, path, digest)
        if observed is None or saved.get('id') != observed['id'] or saved.get('sha256') != digest:
            raise RepositoryError('Native artifact storage did not confirm the controller digest.')
        return observed


def install_task_status(app, runtime, backend):
    from fastapi import APIRouter, Depends, HTTPException
    from fastapi.responses import JSONResponse

    session_route = next(route for route in app.routes if getattr(route, 'operation_id', None) == 'getSession')
    actor = next(dep.call for dep in session_route.dependant.dependencies if dep.name == 'user')
    router = APIRouter()

    @router.get('/api/sessions/{session_id}/repository-task', operation_id='getLocalRepositoryTask')
    async def status(session_id: str, user=Depends(actor)):
        chat = await runtime.database.session(user, session_id, metadata_only=True)
        if user.id != chat.owner_id:
            raise HTTPException(403, 'Only the conversation owner can inspect this task.')
        try:
            task = await asyncio.to_thread(backend.ledger.owner_status, session_id, user.id)
        except RepositoryError as error:
            raise HTTPException(403, 'Repository attachment belongs to another requester.') from error
        if not task:
            raise HTTPException(404, 'No repository task is attached.')
        response = backend.summary(task)
        response.update({k:task.get(k) for k in ('original_tree','created','citations','plan','receipts','delivery_check')})
        return JSONResponse(response, headers={'Cache-Control':'no-store'})

    # Native Moyai mounts its SPA at the root, after the authenticated API routes.
    app.router.routes.insert(0, router.routes[0])


class RepositoryBackend:
    def __init__(self, ledger=None, catalog=CATALOG, plans=None, native_files=None):
        self._ledger = ledger
        self.catalog = Path(catalog)
        self.plans = plans
        self.native_files = native_files

    @property
    def ledger(self):
        if self._ledger is None:
            self._ledger = Ledger()
        return self._ledger

    def available(self):
        try:
            catalog = json.loads(bounded_read(self.catalog, 'catalog.json', 4 << 20))
        except FileNotFoundError:
            return []
        if catalog.get('schema_version') != 1:
            raise RepositoryError('Unsupported repository catalog.')
        return catalog['repositories']

    def snapshot(self, binding):
        snapshot_id = binding['snapshot_id']
        if not re.fullmatch('[a-f0-9]{64}', snapshot_id):
            raise RepositoryError('Invalid immutable snapshot identity.')
        directory = self.catalog/'snapshots'/snapshot_id
        manifest = json.loads(bounded_read(self.catalog, f'snapshots/{snapshot_id}/manifest.json', 1 << 20))
        if (manifest['snapshot_id'] != snapshot_id or manifest['repository_id'] != binding['repository_id']
                or manifest['verification_profile'] not in ('rust-retry', 'rust-retry-tests') or manifest['verification_profile'] != binding.get('verification_profile', 'rust-retry') or manifest['schema_version'] != 1
                or fingerprint({k:v for k,v in manifest.items() if k != 'snapshot_id'}) != snapshot_id):
            raise RepositoryError('Manifest does not match its registered identity.')
        return manifest, directory

    def originals(self, manifest, directory):
        files = {}
        total = 0
        entries = manifest['files']
        if not 1 <= len(entries) <= MAX_FILES:
            raise RepositoryError('Snapshot file count exceeds import bounds.')
        for row in entries:
            name = relative_name(row['path'])
            size = row['size']
            if (name in files or type(size) is not int or not 0 <= size <= MAX_FILE or total + size > MAX_TREE):
                raise RepositoryError('Snapshot file list or size is invalid.')
            data = bounded_read(self.catalog, (directory/'source'/name).relative_to(self.catalog).as_posix(),
                                MAX_FILE, size=size)
            total += size
            verify_artifact(data, row['sha256'])
            files[name] = data
        return files

    async def guest(self, scope, op, **fields):
        session, authority = scope
        request = {'op':op, 'conversation_id':Path(workspace_directory(session, authority)).name, **fields}
        return await ssh_json(GUEST, request, fields.get('timeout_seconds', 20) + 10)

    async def open(self, scope, repository_id, actor_id=None, *, github_issue=None, expected_binding=None):
        task = self.ledger.status(*scope)
        if task and task['binding']['repository_id'] != repository_id:
            raise RepositoryError('The conversation is already bound to a different repository.')
        if task and github_issue is not None and (task.get('github_issue') != github_issue or task['binding'] != expected_binding):
            raise RepositoryError('Issue intake requires its own pinned conversation.')
        if not task:
            binding = next((r for r in await asyncio.to_thread(self.available) if r['repository_id'] == repository_id), None)
            if not binding:
                raise RepositoryError('Choose an ID returned by list_repositories.')
            if expected_binding is not None and binding != expected_binding:
                raise RepositoryError('The registered GitHub base changed before attachment.')
            task = self.ledger.attach(*scope, binding, actor_id, github_issue)
        if task['phase'] == 'preparing':
            manifest, directory = await asyncio.to_thread(self.snapshot, task['binding'])
            original = await asyncio.to_thread(self.originals, manifest, directory)
            files = [{'path':p,'content_base64':base64.b64encode(data).decode()} for p,data in original.items()]
            try:
                tree = await self.guest(scope, 'import', manifest=manifest, files=files)
            except RepositoryError:
                # A completed import may have lost its reply. Adopt only the identical pristine snapshot.
                tree = await self.guest(scope, 'tree')
            current, digest = scoped_tree(tree, manifest)
            if current != original:
                raise RepositoryError('Workspace does not match the pristine snapshot.')
            self.ledger.imported(*scope, digest)
        return self.summary(self.ledger.status(*scope))

    @staticmethod
    def summary(task):
        if not task:
            return {'attached':False}
        result = {key:task.get(key) for key in ('task_id','phase','binding','baseline_failed','baseline_clean','attempts',
                                              'verified_tree','artifacts','files','deadline','model_calls','tool_calls',
                                              'pending','write_outcome','check_observation','github_issue')}
        if task['binding'].get('verification_profile') == 'rust-retry-tests':
            result['verification_contract'] = ('Add a new tests/*.rs integration target covering the issue requirements. '
                'Preserve every original file. New tests must pass the correct implementation and detect the old ceiling.')
        elif task['binding'].get('verification_profile') == 'rust-retry':
            result['verification_contract'] = RUST_RETRY_CONTRACT
        if task.get('receipts'):
            latest = task['receipts'][-1]
            result['latest_check'] = {'state':latest['state'], 'operation_id':latest['operation_id'],
                                      'tree_hash':latest['tree_hash'], 'commands':[
                {'kind':cmd.get('kind'),'exit_code':cmd.get('exit_code')} for cmd in latest.get('commands',[])[:16]]}
        return result

    async def search(self, scope, query, limit):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        import httpx2

        task = self.ledger.status(*scope)
        if not task or not 1 <= limit <= 8 or not 2 <= len(query.strip()) <= 1000:
            raise RepositoryError('Search requires a pinned task, bounded query and limit (1–8).')
        binding = task['binding']
        ids = {k:binding[k] for k in ('repository_id','snapshot_id','generation_id')}
        async with asyncio.timeout(40), httpx2.AsyncClient(
            headers={'Authorization':'Bearer '+os.environ['MOYAI_REPOSITORY_CONTEXT_TOKEN']},
            follow_redirects=False, trust_env=False, timeout=35,
        ) as client, streamable_http_client(CONTEXT_URL, http_client=client, terminate_on_close=False,
                                           max_sse_event_size=65536) as streams:
            async with ClientSession(streams[0], streams[1], read_timeout_seconds=35) as session:
                await session.initialize()
                result = await session.call_tool('search_repository', {**ids,'query':query,'limit':limit})
        value = result.structured_content
        if result.is_error or not isinstance(value, dict) or any(value.get(k) != v for k,v in ids.items()):
            raise RepositoryError('Context bridge returned a failed or foreign scope.')
        rows = value['results']
        manifest, _ = await asyncio.to_thread(self.snapshot, binding)
        names = {r['path'] for r in manifest['files']}
        if (not isinstance(rows, list) or len(rows) > limit or len(canonical(value)) > 100_000
                or any(r.get('path') not in names or not isinstance(r.get('chunk_id'), str)
                       or type(r.get('start_line')) is not int or type(r.get('end_line')) is not int
                       or not 1 <= r['start_line'] <= r['end_line'] for r in rows)):
            raise RepositoryError('Context results contain invalid citations.')
        self.ledger.cite(*scope, [{k:r[k] for k in ('chunk_id','path','start_line','end_line')} for r in rows])
        return value

    async def check(self, scope, operation):
        task = self.ledger.status(*scope)
        for settled in task['receipts']:
            if settled['operation_id'] == operation:
                return self.check_summary(settled)
        manifest, _ = await asyncio.to_thread(self.snapshot, task['binding'])
        _, tree = scoped_tree(await self.guest(scope, 'tree'), manifest)
        if cached := self.ledger.reusable_check(*scope, tree):
            return {**self.check_summary(cached), 'reused':True,
                    'reused_operation_id':cached['operation_id'],
                    'reuse_reason':'The current tree matches the latest settled check; no Cargo work or validation attempt was repeated.'}
        decision = self.ledger.start_check(*scope, operation, tree)
        remaining = max(1, min(CHECK_TIMEOUT, int(task['deadline'] - time.time())))
        if not decision['dispatch']:
            return await self.status(scope)
        try:
            receipt = await self.guest(scope, 'check', task_id=task['task_id'], operation_id=operation,
                                       profile=manifest['verification_profile'], timeout_seconds=remaining,
                                       receipt_key=self.ledger.check_key(*scope, operation))
            await self.settle(scope, operation, receipt, manifest)
        except asyncio.CancelledError:
            self.ledger.uncertain(*scope, operation)
            await asyncio.shield(self.cancel(scope, operation, manifest))
            raise
        except Exception:
            self.ledger.uncertain(*scope, operation)
            raise
        return self.check_summary(receipt)

    @staticmethod
    def check_summary(receipt):
        remaining = 6000
        commands = []
        failure_cause = None
        for command in receipt.get('commands', []):
            item = {'kind':command['kind'], 'exit_code':command['exit_code']}
            if command['exit_code'] and command['kind'] != 'regression_counterexample' and remaining > 0:
                diagnostics = command.get('diagnostics', [])
                limit = min(remaining, 3000)
                tail_clipped = False
                if diagnostics:
                    parts = []
                    for diagnostic in diagnostics[:8]:
                        title = f"{diagnostic.get('code', '')}: {diagnostic.get('message', '')}".lstrip(': ')
                        parts.append(title + '\n' + diagnostic.get('rendered', ''))
                    output = '\n'.join(parts)
                    item['output_source'] = 'cargo_compiler_diagnostics'
                else:
                    output = command.get('output_tail', command.get('output', ''))
                    tail_bytes = output.encode('utf-8', errors='replace')
                    tail_clipped = len(tail_bytes) > limit
                    output = tail_bytes[-limit:].decode('utf-8', errors='ignore')
                    item['output_source'] = 'process_output_tail'
                encoded = output.encode('utf-8', errors='replace')
                output = encoded[:limit].decode('utf-8', errors='ignore')
                item['output'] = output
                item['untrusted_process_output'] = True
                item['output_truncated'] = command.get('output_truncated', False)
                item['diagnostics_truncated'] = command.get('diagnostics_truncated', False)
                item['summary_truncated'] = len(encoded) > limit or tail_clipped or (not diagnostics and command.get('output_tail_truncated', False))
                remaining -= len(output.encode())
                if failure_cause is None and output:
                    cause = output if diagnostics else output.encode()[-400:].decode('utf-8', errors='ignore')
                    failure_cause = f"{command['kind']} exited {command['exit_code']}: {cause}"
            commands.append(item)
        summary = {'state':receipt['state'], 'checks':receipt.get('checks',{}),
                   'tree_hash':receipt['tree_hash'], 'commands':commands}
        if receipt.get('failure_code'):
            summary['failure_code'] = receipt['failure_code']
        if isinstance(receipt.get('error'), str):
            summary['error'] = receipt['error'][:256]
        if failure_cause:
            if 'error' in summary:
                summary['verification_error'] = summary['error']
            summary['error'] = failure_cause.encode('utf-8', errors='replace')[:512].decode('utf-8', errors='ignore')
        checks = receipt.get('checks', {})
        if receipt['state'] == 'passed':
            summary['next_action'] = 'Trusted checks passed on this tree. Call finish_repository_task now to deliver the verified patch and reports; no further check is needed without edits.'
        elif clean_coverage_baseline(receipt):
            summary['next_action'] = 'The original retry policy passes. Add the requested coverage in a new tests/*.rs file.'
        elif receipt['state'] == 'failed' and checks.get('oracle_passed'):
            if not checks.get('regression_tests_added'):
                summary['next_action'] = RUST_RETRY_CONTRACT + ' Create the integration test before running checks again.'
            elif not checks.get('regression_counterexample_passed'):
                summary['next_action'] = 'Inspect regression_proofs and failing command diagnostics. Add or fix a tests/*.rs regression that passes the repaired tree and fails the compiled 60,000 ms counterexample. Change the tests before retrying.'
        return summary

    async def settle(self, scope, operation, receipt, manifest):
        if receipt.get('state') in {'unknown','running'}:
            raise RepositoryError('Verification outcome is unconfirmed. Use repository_task_status to reconcile.')
        current = self.ledger.status(*scope)
        if any(r['operation_id'] == operation for r in current['receipts']):
            return
        _, digest = scoped_tree(await self.guest(scope, 'tree'), manifest)
        self.ledger.finish_check(*scope, operation, receipt, digest)

    async def cancel(self, scope, operation, manifest):
        task = self.ledger.status(*scope)
        receipt = await self.guest(scope, 'cancel', operation_id=operation, task_id=task['task_id'],
                                   tree_hash=task['check_tree'], profile=task['binding'].get('verification_profile', 'rust-retry'),
                                   receipt_key=self.ledger.check_key(*scope,operation))
        await self.settle(scope, operation, receipt, manifest)

    async def status(self, scope, current_turn=None):
        task = self.ledger.status(*scope)
        if not task or not task['pending']:
            return self.summary(task)
        op = task['pending']
        kind = self.ledger.operation_kind(*scope, op)
        if kind == 'write' and current_turn and current_turn != task.get('write_turn'):
            tree = None
            if task.get('write_dispatch') == 'dispatched':
                manifest, _ = await asyncio.to_thread(self.snapshot, task['binding'])
                _, tree = scoped_tree(await self.guest(scope,'tree'), manifest)
            self.ledger.recover_write(*scope, op, current_turn, tree)
        elif kind == 'check':
            receipt = await self.guest(scope, 'status', operation_id=op)
            if receipt.get('state') in ('unknown','running'):
                if receipt.get('operation_id') != op:
                    raise RepositoryError('Guest status does not match the reserved operation.')
                if receipt['state'] == 'running':
                    observed = 'running'
                elif receipt.get('tracked') is False:
                    observed = 'not_yet_observed'
                elif receipt.get('tracked') is True or receipt.get('receipt_mac'):
                    observed = 'tracked_outcome_unknown'
                else:
                    observed = 'tracking_unconfirmed'
                self.ledger.observe_check(*scope, op, observed)
            else:
                manifest, _ = await asyncio.to_thread(self.snapshot, task['binding'])
                await self.settle(scope, op, receipt, manifest)
        return self.summary(self.ledger.status(*scope))

    async def finish(self, scope, ctx):
        task = self.ledger.status(*scope)
        if task['phase'] == 'completed':
            return self.summary(task)
        manifest, directory = await asyncio.to_thread(self.snapshot, task['binding'])
        after, tree = scoped_tree(await self.guest(scope, 'tree'), manifest)
        task = self.ledger.ready_to_finish(*scope, tree)
        original = await asyncio.to_thread(self.originals, manifest, directory)
        patch = source_patch(original, after, manifest['verification_profile'])
        report = canonical({'task_id':task['task_id'],'verification_profile':manifest['verification_profile'],
                            'baseline_and_attempts':task['receipts']}) + b'\n'
        files = dict(zip(ARTIFACT_PATHS, (patch, report)))
        digests = {p:sha256(b).hexdigest() for p,b in files.items()}
        result = {k:task[k] for k in ('task_id','binding','original_tree','verified_tree','citations','plan','attempts')}
        files[ARTIFACT_PATHS[2]] = canonical({**result, 'artifacts':digests, 'outcome':'passed'}) + b'\n'
        digests[ARTIFACT_PATHS[2]] = sha256(files[ARTIFACT_PATHS[2]]).hexdigest()
        self.ledger.stage_delivery(*scope, tree, digests, task['receipts'][-1]['operation_id'])
        written = await ssh_json(('python3','-I','-c',ARTIFACT_WRITER),
                                 {'conversation_id':Path(workspace_directory(*scope)).name,
                                  'files':{p.split('/')[-1]:base64.b64encode(b).decode() for p,b in files.items()}}, 25)
        if written != digests:
            raise RepositoryError('Delivered artifact hashes differ from controller bytes.')
        _, current = scoped_tree(await self.guest(scope, 'tree'), manifest)
        if current != tree:
            raise RepositoryError('Project changed after artifact staging.')
        for path, digest in digests.items():
            turn_id, dispatch = self.ledger.reserve_file(*scope, path, ctx.deps.turn_id)
            delivered = await self.native_files.deliver(ctx, turn_id, path, digest, dispatch)
            self.ledger.delivered(*scope, path, delivered)
        _, current = scoped_tree(await self.guest(scope, 'tree'), manifest)
        if current != tree:
            raise RepositoryError('Project changed during delivery.')
        self.ledger.complete(*scope, tree, turn=ctx.deps.turn_id)
        return self.summary(self.ledger.status(*scope))
