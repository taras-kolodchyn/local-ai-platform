#!/usr/bin/env python3
"""Local, opt-in GitHub issue intake and create-only draft PR publisher."""
import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integrations/moyai'))
from github_app import AppError, Installation, request as app_request
from client import MoyaiClient
from github_issue import ISSUES, REPO, atomic_json, issue_number
from repository_backend import bounded_read, canonical, editable
from repository_ledger import ARTIFACT_PATHS, validate_checks, verify_artifact

STATE = ROOT / '.local/github-issues'
PROJECT = 'examples/rust-service'
BASE = 'main'
COMPOSE = ['docker', 'compose', '-f', 'compose.moyai.yaml', '-f', 'compose.moyai-issues.yaml']


class Refused(ValueError):
    """Bounded, operator-readable error suitable for returning to the chat."""


def require(condition, message):
    if not condition:
        raise Refused(message)


def run(args, *, data=None, env=None, timeout=60):
    return subprocess.run(args, cwd=ROOT, env=env, input=data, capture_output=True,
                          check=True, timeout=timeout).stdout


def git(repo, *args, data=None, index=None):
    env = {'PATH':os.environ['PATH'], 'GIT_CONFIG_NOSYSTEM':'1', 'GIT_CONFIG_GLOBAL':os.devnull,
           'GIT_TERMINAL_PROMPT':'0', 'LC_ALL':'C'}
    if index is not None:
        env['GIT_INDEX_FILE'] = str(index)
    return run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(repo), *args], data=data, env=env, timeout=120)


class GitHub:
    def __init__(self, operator_issue=None, *, installation=None):
        self.operator_issue = operator_issue
        self.installation = installation

    def api(self, endpoint, body=None):
        if self.installation is not None:
            return self.installation.api(endpoint, body)
        if self.operator_issue is None:
            path = STATE / 'installation-token'
            require(path.is_file() and not path.is_symlink() and path.stat().st_mode & 0o077 == 0,
                    'Provide a private GitHub installation-token file or select one issue with --operator-issue.')
            token = path.read_text().strip()
            require(bool(token), 'The dedicated GitHub token is empty.')
            return app_request('/repos/' + REPO + '/' + endpoint, token, body)
        env = {**os.environ, 'GH_PROMPT_DISABLED':'1', 'GH_NO_UPDATE_NOTIFIER':'1'}
        args = ['gh', 'api', '--hostname', 'github.com', '-H', 'X-GitHub-Api-Version: 2026-03-10',
                'repos/' + REPO + '/' + endpoint]
        if body is not None:
            args += ['--method', 'POST', '--input', '-']
        return json.loads(run(args, data=None if body is None else canonical(body), env=env))

    def ticket(self, number):
        require(self.operator_issue is None or number == self.operator_issue, 'This publisher is enabled for another issue.')
        row = self.api(f'issues/{number}')
        require(row['html_url'] == ISSUES + str(number) and 'pull_request' not in row and row['state'] == 'open',
                'The selected URL must be an open issue in local-ai-platform.')
        require(self.operator_issue is not None or any(label['name'] == 'moyai:rust-retry' for label in row['labels']),
                'This publisher supports issues assigned the moyai:rust-retry verification profile.')
        body = row['body'] or ''
        require(len(body) <= 16384, 'Issue text exceeds the context limit for this profile.')
        return {'title':row['title'], 'body':body}


def prepare(base):
    require(bool(re.fullmatch('[a-f0-9]{40}', base)), 'Invalid GitHub main commit.')
    repo = STATE / 'sources' / base
    repo.parent.mkdir(exist_ok=True)
    if not repo.exists():
        with tempfile.TemporaryDirectory(dir=repo.parent) as temp:
            checkout = Path(temp) / 'checkout'
            git(ROOT, 'clone', '--depth', '1', '--single-branch', '--branch', BASE,
                'https://github.com/' + REPO + '.git', str(checkout))
            require(git(checkout, 'rev-parse', 'HEAD').decode().strip() == base,
                    'GitHub main moved during preparation. Restart the publisher.')
            checkout.rename(repo)
    spec = importlib.util.spec_from_file_location('prepare_repository', ROOT / 'scripts/prepare-repository.py')
    registration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(registration)
    catalog = ROOT / '.local/repositories'
    manifest = registration.sibling('repository-snapshot').prepare_snapshot(
        repo / PROJECT, catalog, 'rust-retry', repository_id='repo_github_' + base)
    require(manifest['git']['head'] == base and manifest['git']['dirty'] is False
            and manifest['git']['subtree'] == PROJECT, 'GitHub checkout differs from its pinned base.')
    # The indexer reuses a completed generation only for the active embedding fingerprint.
    binding = registration.index_prepared(catalog, manifest)
    return manifest, binding


def verified_patch(client, job):
    session = job['session']
    task = client.request(f'/api/sessions/{session}/repository-task')
    require(task['phase'] == 'completed' and not task['pending'] and task['baseline_failed']
            and task['binding'] == job['binding']
            and task['github_issue'] == {'number':job['number'], 'turn':job['turn']},
            'The finished native task does not match this issue and pinned source.')
    bodies = []
    for path in ARTIFACT_PATHS:
        native = task['files'][path]
        uuid.UUID(native['id'])
        data = client.request('/api/files/' + native['id'], raw=True)
        verify_artifact(data, native['sha256'])
        verify_artifact(data, task['artifacts'][path])
        bodies.append(data)
    patch, report, result = bodies
    result, report = json.loads(result), json.loads(report)
    receipt = task['receipts'][-1]
    require(result['outcome'] == 'passed' and result['binding'] == job['binding']
            and result['task_id'] == report['task_id'] == task['task_id'] == receipt['task_id']
            and result['artifacts'] == {p:task['artifacts'][p] for p in ARTIFACT_PATHS[:2]}
            and report['baseline_and_attempts'] == task['receipts']
            and receipt['state'] == 'passed' and receipt['tree_hash'] == result['verified_tree'] == task['verified_tree'],
            'Trusted check receipts and delivered artifacts disagree.')
    validate_checks(receipt)
    return patch, result['verified_tree'], receipt['checks']


def reproduce(repo, base, manifest, patch, verified):
    """Apply only to a temporary Git index. Hash staged blobs; never execute the checkout."""
    with tempfile.TemporaryDirectory() as temp:
        index = Path(temp) / 'index'
        def staged(*args, data=None):
            return git(repo, *args, data=data, index=index)
        staged('read-tree', base)
        expected = {}
        for row in manifest['files']:
            data = staged('show', ':' + PROJECT + '/' + row['path'])
            require(hashlib.sha256(data).hexdigest() == row['sha256'] and len(data) == row['size'],
                    'Registered snapshot bytes differ from the GitHub base tree.')
            expected[row['path']] = row
        staged('apply', '--cached', '--whitespace=nowarn', '--directory=' + PROJECT, '-', data=patch)
        names = staged('diff', '--cached', '--no-renames', '--name-only', '-z', base).decode().split('\0')[:-1]
        entries = []
        for path in names:
            prefix = PROJECT + '/'
            name = path.removeprefix(prefix)
            require(path.startswith(prefix) and editable(name), 'Patch includes a path outside the Rust profile.')
            listing = staged('ls-files', '--stage', '-z', '--', path)
            require(listing.startswith(b'100644 ') and listing.endswith(b'\t' + path.encode() + b'\0'),
                    'Only regular non-executable source files may be published.')
            blob = staged('show', ':' + path)
            expected[name] = {'path':name, 'sha256':hashlib.sha256(blob).hexdigest(), 'size':len(blob)}
            entries.append({'path':path, 'mode':'100644', 'type':'blob', 'content':blob.decode()})
        require(bool(entries) and hashlib.sha256(canonical([expected[n] for n in sorted(expected)])).hexdigest() == verified,
                'Applying the native patch does not reproduce the independently checked tree.')
        return staged('write-tree').decode().strip(), entries


def publish(api, repo, manifest, job, patch, digest, checks, save, *, installation=None):
    number, base = job['number'], job['base']
    prefix = f'codex/app-{installation.app_id}' if installation else 'codex'
    branch = f'{prefix}/issue-{number}'
    def refs():
        return [r['object']['sha'] for r in api(f'git/matching-refs/heads/{branch}') if r['ref'] == 'refs/heads/' + branch]
    tree, entries = reproduce(repo, base, manifest, patch, digest)
    require(job.get('tree', tree) == tree, 'Previously reserved publication has a different tree.')
    if job.get('pr'):
        prs = [api(f'pulls/{int(job["pr"]["number"])}')]
    else:
        # Include closed and retargeted PRs before creating or restoring any ref.
        query = 'pulls?state=all&head=' + REPO.split('/')[0] + ':' + branch
        prs = api(query)
    if not job.get('commit'):
        require(not prs and not refs(), 'The issue branch was already used. The publisher cannot claim it.')
        base_tree = git(repo, 'rev-parse', base + '^{tree}').decode().strip()
        remote = api('git/trees', {'base_tree':base_tree, 'tree':entries})
        require(remote['sha'] == tree, 'GitHub tree differs from the verified local index.')
        commit = api('git/commits', {'message':f'Fix Rust retry ceiling (#{number})', 'tree':tree, 'parents':[base]})
        require(commit['tree']['sha'] == tree and [p['sha'] for p in commit['parents']] == [base]
                and re.fullmatch('[a-f0-9]{40}', commit['sha']), 'GitHub commit does not match the verified tree and parent.')
        job.update(tree=tree, commit=commit['sha'])
        save(job)  # Persist the exact commit before any visible ref is created.
    head = job['commit']
    if not prs and not refs():
        api('git/refs', {'ref':'refs/heads/' + branch, 'sha':head})
    require(refs() == [head], 'The issue branch has a different head.')
    if not prs:
        sha = hashlib.sha256(patch).hexdigest()
        body = (f'Closes #{number}\n\nMoyai verified the Rust example locally against base `{base}`. '
                f'The failing baseline was reproduced. The repaired tree passed {checks["project_tests_run"]} '
                f'project tests and {checks["oracle_tests_run"]} protected tests. '
                'The added regression suite detected the previous 60-second ceiling.\n\n'
                f'Patch SHA-256: `{sha}`\n')
        created = api('pulls', {'head':branch, 'base':BASE, 'draft':True,
                                'title':f'Fix the Rust example retry ceiling (#{number})', 'body':body})
        prs = [api(f'pulls/{int(created["number"])}')]
    require(len(prs) == 1, 'Expected exactly one pull request for the issue branch.')
    pr = prs[0]
    require(pr['draft'] is True and pr['state'] == 'open' and pr['head']['sha'] == head
            and pr['head']['ref'] == branch and pr['base']['ref'] == BASE and pr['base']['sha'] == base
            and pr['head']['repo']['full_name'] == pr['base']['repo']['full_name'] == REPO,
            'GitHub did not confirm the expected open draft at the verified commit.')
    result = {'number':pr['number'], 'head':head}
    if installation is not None:
        author = pr.get('user') or {}
        require(author.get('type') == 'Bot' and author.get('login') == installation.bot,
                'The draft PR author differs from the registered GitHub App bot.')
        result.update(author=author['login'], app_id=installation.app_id)
    return result


class Publisher:
    def __init__(self, github, client, manifest, binding):
        self.github, self.client = github, client
        self.manifest, self.binding = manifest, binding

    def authorize_turn(self, number, session, turn):
        chat = self.client.request(f'/api/sessions/{session}?include_messages=true&include_activity=false')
        current = chat['session']
        require(current['visibility'] == 'private' and current['surface'] == 'web'
                and current['active_turn'] == turn and not current.get('swarm_id') and not current.get('parent_session_id')
                and any(t['id'] == turn and t['status'] == 'running' and t['actor_id'] == current['owner_id']
                        and not t.get('parent_turn') for t in chat['turns']), 'The originating private turn is no longer running.')
        own_url = any(m['turn_id'] == turn and m['role'] == 'user' and m['is_self'] and not m.get('source')
                      and not m.get('deleted') and not m.get('edited_after_start') and not m.get('sent_as_goal')
                      and issue_number(m['content']) == number for m in chat['messages'])
        require(own_url, 'A direct operator issue URL is required.')

    def dispatch(self, request):
        number, session, turn = (request[k] for k in ('number','session','turn'))
        require(type(number) is int and 0 < number < 10**10, 'Invalid issue number.')
        require(all(str(uuid.UUID(v)) == v for v in (session, turn)), 'Invalid conversation identity.')
        require(self.github.operator_issue in (None, number), 'Issue outside the operator grant.')
        self.authorize_turn(number, session, turn)
        claim_directory = STATE
        if self.github.installation is not None:
            claim_directory = STATE / f'app-{self.github.installation.app_id}'
            claim_directory.mkdir(mode=0o700, exist_ok=True)
        jobfile = claim_directory / f'issue-{number}.json'
        job = json.loads(jobfile.read_text()) if jobfile.exists() else None
        def save(value):
            atomic_json(jobfile, value)
        action = request['action']
        if action == 'open':
            if job:
                require(job['session'] == session, 'This issue already belongs to another Moyai conversation.')
                require(self.github.ticket(number) == job['issue'], 'The issue changed since intake. Review its updated scope.')
                require(self.github.api('commits/main')['sha'] == job['base'], 'GitHub main moved since intake.')
            else:
                ticket = self.github.ticket(number)
                require(self.github.api('commits/main')['sha'] == self.manifest['git']['head'],
                        'GitHub main moved. Restart the publisher to index the new base.')
                job = {'number':number, 'session':session, 'turn':turn, 'base':self.manifest['git']['head'],
                       'binding':self.binding, 'issue':ticket}
                save(job)
            return {'issue':job['issue'], 'binding':job['binding'], 'turn':job['turn']}
        require(action == 'publish' and job and job['session'] == session, 'Publication has no matching operator issue claim.')
        ticket = self.github.ticket(number)
        require(ticket == job['issue'], 'The issue changed since intake. Review its updated scope.')
        patch, digest, checks = verified_patch(self.client, job)
        directory = ROOT / '.local/repositories/snapshots' / job['binding']['snapshot_id']
        manifest = json.loads((directory / 'manifest.json').read_text())
        require(manifest['git']['head'] == job['base'] and manifest['git']['subtree'] == PROJECT
                and not manifest['git']['dirty'], 'The snapshot differs from the pinned GitHub source.')
        require(self.github.api('commits/main')['sha'] == job['base'], 'GitHub main moved since verification.')
        def checked_api(endpoint, body=None):
            if body is not None:
                if endpoint in {'git/refs', 'pulls'}:
                    require(self.github.ticket(number) == job['issue'], 'The issue changed since intake. Review its updated scope.')
                    require(self.github.api('commits/main')['sha'] == job['base'], 'GitHub main moved since verification.')
                self.authorize_turn(number, session, turn)
            return self.github.api(endpoint, body)
        result = publish(checked_api, STATE/'sources'/job['base'], manifest, job, patch, digest, checks, save,
                         installation=self.github.installation)
        save({**job, 'pr':result})
        return result


def serve(publisher, exchange):
    lease = uuid.uuid4().hex
    stop = threading.Event()
    def heartbeat():
        while not stop.is_set():
            atomic_json(exchange/'lease.json', {'id':lease, 'updated':time.time()})
            stop.wait(3)
    heartbeat_thread = threading.Thread(target=heartbeat)
    heartbeat_thread.start()
    try:
        print('GitHub issue publisher ready. Paste the issue URL into a private Moyai chat.', flush=True)
        while True:
            for path in sorted((exchange/'requests').glob('*.json')):
                if not re.fullmatch(r'[a-f0-9]{64}\.json', path.name):
                    continue
                try:
                    request = json.loads(bounded_read(exchange/'requests', path.name, 1024))
                except (OSError, ValueError):
                    continue
                if not isinstance(request, dict) or set(request) != {'action', 'number', 'session', 'turn', 'lease'}:
                    continue
                reply = exchange/'replies'/path.name
                if request.get('lease') != lease or (reply.exists() and json.loads(reply.read_text()).get('lease') == lease):
                    continue
                try:
                    response = {'result':publisher.dispatch(request)}
                except Exception as exc:
                    reason = str(exc) if isinstance(exc, (Refused, AppError)) else type(exc).__name__
                    print(f'Issue operation {request["action"]} failed: {reason}', flush=True)
                    response = {'error':reason}
                atomic_json(reply, {**response, 'lease':lease})
            time.sleep(0.25)
    finally:
        stop.set()
        heartbeat_thread.join()
        (exchange/'lease.json').unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', action='store_true', help='Use only the private GitHub App installation.')
    parser.add_argument('--operator-issue', type=int, default=os.environ.get('MOYAI_OPERATOR_ISSUE') or None, help='Limit the selected publisher identity to one operator-approved issue.')
    args = parser.parse_args()
    os.chdir(ROOT)
    STATE.mkdir(exist_ok=True, parents=True, mode=0o700)
    exchange = STATE / 'exchange'
    exchange.mkdir(exist_ok=True)
    (exchange/'requests').mkdir(exist_ok=True)
    (exchange/'requests').chmod(0o777)  # Only this directory is mounted for the nonroot controller.
    (exchange/'replies').mkdir(exist_ok=True)
    with (STATE/'publisher.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        github = GitHub(args.operator_issue, installation=Installation() if args.app else None)
        if args.operator_issue is not None:
            github.ticket(args.operator_issue)
        print('Preparing the pinned GitHub main snapshot and its local context index…', flush=True)
        manifest, binding = prepare(github.api('commits/main')['sha'])
        client = MoyaiClient()
        subprocess.run([*COMPOSE, 'up', '--detach', '--no-deps', '--build', '--wait', 'moyai'], cwd=ROOT, check=True)
        client.login()
        serve(Publisher(github, client, manifest, binding), exchange)


if __name__ == '__main__':
    with contextlib.suppress(KeyboardInterrupt):
        main()
