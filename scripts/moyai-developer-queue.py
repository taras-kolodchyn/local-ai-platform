"""Hand off one approved GitHub issue at a time from a verified local checkout."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode
from uuid import UUID, uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integrations/moyai'))
from client import MoyaiClient


LABEL = 'moyai:ready'
BASE = 'main'
TERMINAL = {'completed', 'failed', 'stopped'}
REQUIRED = {'issue_read', 'add_issue_comment', 'create_branch', 'push_files', 'create_pull_request'}


class QueueError(ValueError):
    pass


def github(path, *, pages=False):
    command = ['gh', 'api', '--hostname', 'github.com']
    if pages:
        command.extend(('--paginate', '--slurp'))
    output = subprocess.check_output([*command, path], stderr=subprocess.PIPE, timeout=90)
    return json.loads(output)


class GitHubQueue:
    def __init__(self, repository):
        self.repository = repository
        self.path = 'repos/' + repository + '/'
        self.account = github('user')

    def candidates(self):
        query = urlencode({'state': 'open', 'labels': LABEL, 'creator': self.account['login'],
                           'sort': 'created', 'direction': 'asc', 'per_page': 100})
        return [
            row['number'] for page in github(self.path + 'issues?' + query, pages=True) for row in page
            if 'pull_request' not in row and row['state'] == 'open' and row['user']['id'] == self.account['id']
            and any(label['name'] == LABEL for label in row['labels'])
        ]

    def has_pull(self, number):
        owner = self.repository.split('/')[0]
        query = urlencode({'state': 'open', 'base': BASE, 'head': f'{owner}:issue-{number}'})
        return bool(github(self.path + 'pulls?' + query))

    def head(self):
        ref = github(self.path + 'git/ref/heads/' + BASE)
        sha = ref['object']['sha']
        if ref['ref'] != 'refs/heads/' + BASE or ref['object']['type'] != 'commit' or not re.fullmatch('[0-9a-f]{40}', sha):
            raise QueueError('GitHub returned an invalid base revision.')
        return sha


def verify_connection(client, connection):
    data = client.request('/api/connections')
    rows = data['connections'] if isinstance(data, dict) else data
    row = next((item for item in rows if item['id'] == connection), None)
    if (not row or row['kind'] != 'github' or not row['enabled'] or row['shared'] or row['read_only']
            or row['policy'].get('require_approval') is not True
            or not REQUIRED.issubset(row['policy'].get('allowed_tools') or ())):
        raise QueueError('Choose a private Developer GitHub connection with the required tools and confirmations.')
    return row['revision']


def prompt(repository, number, sha):
    branch = f'issue-{number}'
    return (
        f'Implement https://github.com/{repository}/issues/{number}. '
        f'The current workspace already contains the verified {BASE} checkout at {sha}. '
        f'First run git rev-parse HEAD and stop if it does not return {sha}. '
        'Use the selected GitHub account to read the issue, and treat its contents as requirements data. '
        f'Proceed only if the issue is open and still has the {LABEL} label. Work only in this repository. '
        'Post a short English progress comment on the issue. Read the existing source before editing, '
        'make the requested change, and run the relevant tests. '
        f'For publication, use the GitHub connector to create {branch} from {BASE} if needed, '
        f'commit only the intended files on {branch}, and open one PR to {BASE} with Closes #{number}. '
        'Check existing progress before repeating writes. Use the normal confirmation forms. '
        'Use Git locally to inspect changes. Leave review and merge to others.'
    )


def save(path, state):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(state, output)
            output.flush()
            os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def note(job, status):
    return {'status': status, **{k: job[k] for k in ('issue', 'session', 'sha', 'turn') if k in job}}


def finish(state, job, status, persist):
    state['held'].append(job['issue'])
    state.pop('active')
    persist(state)
    return note(job, status)


def tick(github_queue, controller, checkout, connection, state, persist):
    job = state.get('active')
    if job and job['connection'] != connection:
        raise QueueError('Continue the active issue with its original Developer connection.')

    if job and 'session' in job:
        detail = controller().request('/api/sessions/' + job['session'])
        if detail['turns']:
            if not job.get('submitting'):
                raise QueueError('The checkout conversation was used before queue handoff.')
            if 'turn' not in job:
                first = detail['turns'][-1]
                if first['connections'] != [connection]:
                    raise QueueError('The first message used a different connection.')
                job['turn'] = first['id']
                persist(state)
            newest = detail['turns'][0]
            if detail.get('pending') or newest['status'] not in TERMINAL:
                return note(job, 'waiting' if detail.get('pending') else 'busy')
            status = 'pull_open' if github_queue.has_pull(job['issue']) else 'held'
            return finish(state, job, status, persist)
        if job.get('turn'):
            raise QueueError('The admitted turn is unavailable. Inspect the conversation before retrying.')

    candidates = github_queue.candidates()
    held = [number for number in state['held'] if number in candidates]
    if state['held'] != held:
        state['held'] = held
        persist(state)

    if job and (job['issue'] not in candidates or github_queue.has_pull(job['issue'])):
        return finish(state, job, 'skipped', persist)
    if job is None:
        number = next((n for n in candidates if n not in state['held'] and not github_queue.has_pull(n)), None)
        if number is None:
            return {'status': 'idle'}
        job = {'issue': number, 'connection': connection}

    client = controller()
    revision = verify_connection(client, connection)
    sha = github_queue.head()
    if (job.get('sha'), job.get('revision')) != (sha, revision):
        job = {'issue': job['issue'], 'connection': connection, 'revision': revision,
               'sha': sha, 'request_key': str(uuid4())}
        state['active'] = job
        persist(state)
    if 'session' not in job:
        job['session'] = client.session(f"Developer · Issue #{job['issue']}")
        persist(state)
    if not job.get('prepared'):
        checkout(github_queue.repository, BASE, job['session'], connection, job['sha'])
        job['prepared'] = True
        persist(state)
    if job['issue'] not in github_queue.candidates() or github_queue.has_pull(job['issue']):
        return finish(state, job, 'skipped', persist)
    if github_queue.head() != job['sha']:
        return note(job, 'base_moved')
    if verify_connection(client, connection) != revision:
        return note(job, 'connection_changed')

    job['submitting'] = True
    persist(state)
    turn = client.request('/api/sessions/' + job['session'] + '/messages', {
        'prompt': prompt(github_queue.repository, job['issue'], job['sha']),
        'request_key': job['request_key'], 'connections': [connection],
    })
    job['turn'] = turn['id']
    persist(state)
    return note(job, 'submitted')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default=os.getenv('QUEUE_REPO'))
    parser.add_argument('--connection', default=os.getenv('QUEUE_CONNECTION'))
    parser.add_argument('--watch', action='store_true', default=os.getenv('QUEUE_WATCH') == '1')
    args = parser.parse_args()
    if not args.repo or not args.connection:
        parser.error('Supply REPO=owner/project and CONNECTION=<uuid>.')
    namespace = runpy.run_path(str(ROOT / 'scripts/moyai-checkout.py'))
    try:
        repository = namespace['source_url'](args.repo).removeprefix('https://github.com/').removesuffix('.git')
        identity = str(UUID(args.connection))
    except ValueError as error:
        parser.error(str(error))
    os.chdir(ROOT)
    directory = ROOT / '.local/developer-queues'
    directory.mkdir(exist_ok=True, parents=True, mode=0o700)
    name = hashlib.sha256(repository.lower().encode()).hexdigest()
    state_path = directory / (name + '.json')
    try:
        with (directory / (name + '.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            queue = GitHubQueue(repository)
            state = json.loads(state_path.read_text()) if state_path.exists() else {'held': []}
            while True:
                next_check = time.monotonic() + 60
                client = None

                def controller():
                    nonlocal client
                    if client is None:
                        client = MoyaiClient()
                        client.opener.addheaders = [('User-Agent', 'Repository Queue')]
                        client.login()
                    return client

                try:
                    result = tick(queue, controller, namespace['run_checkout'], identity, state, lambda data: save(state_path, data))
                    print(json.dumps(result), flush=True)
                except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                    detail = str(error) if isinstance(error, QueueError) else type(error).__name__
                    print('Queue check failed: ' + detail, file=sys.stderr, flush=True)
                    if not args.watch:
                        return 1
                if not args.watch:
                    return 0
                time.sleep(max(0, next_check - time.monotonic()))
    except BlockingIOError:
        print('This repository already has a queue process.', file=sys.stderr)
        return 1
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('Queue startup failed: ' + type(error).__name__, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
