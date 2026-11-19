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
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integrations/moyai'))
from github_app import AppError, Installation, request as app_request
from client import MoyaiClient, MoyaiUnavailable
from github_issue import ISSUES, REPO, atomic_json, issue_number, issue_scope
from repository_backend import bounded_read, canonical, coverage_path, editable
from repository_ledger import ARTIFACT_PATHS, approved_review, baseline_established, clean_coverage_baseline, validate_checks, verify_artifact

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


def prepare(base, profile='rust-retry'):
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
    prefix = 'repo_github_tests_' if profile == 'rust-retry-tests' else 'repo_github_'
    manifest = registration.sibling('repository-snapshot').prepare_snapshot(
        repo / PROJECT, catalog, profile, repository_id=prefix + base)
    require(manifest['git']['head'] == base and manifest['git']['dirty'] is False
            and manifest['git']['subtree'] == PROJECT, 'GitHub checkout differs from its pinned base.')
    # The indexer reuses a completed generation only for the active embedding fingerprint.
    binding = registration.index_prepared(catalog, manifest)
    return manifest, binding


def verify_documentation_review(task, report, patch):
    history = task.get('documentation_reviews') or []
    require(approved_review(task, task['verified_tree']) and report.get('documentation_reviews') == history,
            'Publication requires an approved documentation review for the final check and tree.')
    review = history[-1]
    require(review.get('patch_sha256') == hashlib.sha256(patch).hexdigest()
            and review.get('model') == 'local-qwen' and review.get('evidence') and review.get('findings') == [],
            'The documentation review does not approve the delivered patch with source evidence.')


def verified_patch(client, job):
    session = job['session']
    task = client.request(f'/api/sessions/{session}/repository-task')
    scope = issue_scope(job['number'], job['turn'], job['issue'], job['binding'])
    if job.get('pr'):
        # Preserve the contract recorded by the controller for read-only recovery across upgrades.
        recorded = task.get('github_issue') or {}
        for key in ('retry_contract', 'caller_note_required'):
            scope.pop(key, None)
            if key in recorded:
                scope[key] = recorded[key]
    require(task['phase'] == 'completed' and not task['pending'] and baseline_established(task)
            and task['binding'] == job['binding']
            and task['github_issue'] == scope,
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
    profile = job['binding'].get('verification_profile', 'rust-retry')
    require(receipt.get('profile') == profile and report.get('verification_profile') == profile,
            'The verified report differs from the granted profile.')
    if profile == 'rust-retry-tests':
        original = task['receipts'][0]
        validate_checks(original)
        require(original['tree_hash'] == result['original_tree'] and clean_coverage_baseline(original),
                'Tests-only publication requires a verified clean original implementation.')
    if job.get('auto_merge') or task.get('documentation_reviews') or not job.get('pr'):
        verify_documentation_review(task, report, patch)
    validate_checks(receipt, retry_contract=scope.get('retry_contract'))
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
            if manifest.get('verification_profile') == 'rust-retry-tests':
                require(coverage_path(name, expected), 'Tests-only publication permits new integration tests only.')
            listing = staged('ls-files', '--stage', '-z', '--', path)
            require(listing.startswith(b'100644 ') and listing.endswith(b'\t' + path.encode() + b'\0'),
                    'Only regular non-executable source files may be published.')
            blob = staged('show', ':' + path)
            expected[name] = {'path':name, 'sha256':hashlib.sha256(blob).hexdigest(), 'size':len(blob)}
            entries.append({'path':path, 'mode':'100644', 'type':'blob', 'content':blob.decode()})
        require(bool(entries) and hashlib.sha256(canonical([expected[n] for n in sorted(expected)])).hexdigest() == verified,
                'Applying the native patch does not reproduce the independently checked tree.')
        return staged('write-tree').decode().strip(), entries


def issue_branch(number, installation):
    prefix = f'codex/app-{installation.app_id}' if installation else 'codex'
    return f'{prefix}/issue-{number}'


def publication_body(job, patch, checks, coverage):
    baseline = ('The original project and retry oracle passed before adding tests.'
                if coverage else 'The failing baseline was reproduced.')
    return (f"Closes #{job['number']}\n\nMoyai verified the Rust example locally against base `{job['base']}`. "
            f'{baseline} The verified tree passed {checks["project_tests_run"]} '
            f'project tests and {checks["oracle_tests_run"]} protected tests. '
            'The added regression suite detected the previous 60-second ceiling.\n\n'
            f'Documentation review approved the same patch.\n\nPatch SHA-256: `{hashlib.sha256(patch).hexdigest()}`\n')


def publish(api, repo, manifest, job, patch, digest, checks, save, *, installation=None):
    number, base = job['number'], job['base']
    branch = issue_branch(number, installation)
    coverage = manifest.get('verification_profile') == 'rust-retry-tests'
    title = 'Add Rust retry integration coverage' if coverage else 'Fix the Rust example retry ceiling'
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
        commit = api('git/commits', {'message':f'{title} (#{number})', 'tree':tree, 'parents':[base]})
        require(commit['tree']['sha'] == tree and [p['sha'] for p in commit['parents']] == [base]
                and re.fullmatch('[a-f0-9]{40}', commit['sha']), 'GitHub commit does not match the verified tree and parent.')
        job.update(tree=tree, commit=commit['sha'])
        save(job)  # Persist the exact commit before any visible ref is created.
    head = job['commit']
    if not prs and not refs():
        api('git/refs', {'ref':'refs/heads/' + branch, 'sha':head})
    require(refs() == [head], 'The issue branch has a different head.')
    if not prs:
        created = api('pulls', {'head':branch, 'base':BASE, 'draft':True,
                                'title':f'{title} (#{number})', 'body':publication_body(job, patch, checks, coverage)})
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
    def __init__(self, github, client, manifest, binding, *, restart_from=None, auto_merge=False):
        self.restart_from = restart_from
        self.auto_merge = auto_merge
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

        return current['owner_id']

    def restart_unpublished(self, job, owner):
        require(self.restart_from == job['session'] and self.github.operator_issue == job['number']
                and self.github.installation is not None, 'No restart grant for this issue and conversation.')
        require(not any(job.get(k) for k in ('pr','tree','commit')), 'The existing publication cannot be replaced.')
        previous = self.client.request(f"/api/sessions/{job['session']}?include_messages=false")
        chat = previous['session']
        require(chat['owner_id'] == owner and not chat['active_turn'] and chat['visibility'] == 'private'
                and chat['surface'] == 'web' and not chat.get('swarm_id') and not chat.get('parent_session_id')
                and any(t['id'] == job['turn'] and t['actor_id'] == owner and t['status'] in ('completed','failed')
                        for t in previous['turns']), 'The earlier private owner turn has not settled.')
        task = self.client.request(f"/api/sessions/{job['session']}/repository-task")
        require(task['phase'] == 'verified' and not task['pending'] and not task['artifacts']
                and task['binding'] == job['binding'] and task['github_issue']['turn'] == job['turn']
                and task['github_issue']['number'] == job['number'], 'Only an undelivered checked task can restart.')
        branch = issue_branch(job['number'], self.github.installation)
        refs = self.github.api('git/matching-refs/heads/' + branch)
        prs = self.github.api('pulls?state=all&head=' + REPO.split('/')[0] + ':' + branch)
        require(not prs and not any(r['ref'] == 'refs/heads/' + branch for r in refs),
                'An issue branch or PR already exists. The claim cannot be replaced.')
        return [*job.get('prior_attempts', []), {k:job[k] for k in ('session','turn','base','binding')}]

    def dispatch(self, request):
        number, session, turn = (request[k] for k in ('number','session','turn'))
        require(type(number) is int and 0 < number < 10**10, 'Invalid issue number.')
        require(all(str(uuid.UUID(v)) == v for v in (session, turn)), 'Invalid conversation identity.')
        require(self.github.operator_issue in (None, number), 'Issue outside the operator grant.')
        owner = self.authorize_turn(number, session, turn)
        claim_directory = STATE
        if self.github.installation is not None:
            claim_directory = STATE / f'app-{self.github.installation.app_id}'
            claim_directory.mkdir(mode=0o700, exist_ok=True)
        jobfile = claim_directory / f'issue-{number}.json'
        job = json.loads(jobfile.read_text()) if jobfile.exists() else None
        def save(value):
            atomic_json(jobfile, value)
        action = request['action']
        if job:
            require(job['binding'].get('verification_profile', 'rust-retry') == self.binding.get('verification_profile', 'rust-retry'),
                    'The granted profile differs from the original issue binding.')
        if action == 'open':
            ticket = None
            prior_attempts = []
            if job and job['session'] != session and self.restart_from:
                ticket = self.github.ticket(number)
                require(ticket == job['issue'], 'The issue changed since intake.')
                prior_attempts = self.restart_unpublished(job, owner)
                job = None  # Replaced atomically only after the new base and requirements are validated.
            if job:
                require(job['session'] == session, 'This issue already belongs to another Moyai conversation.')
                require(self.github.ticket(number) == job['issue'], 'The issue changed since intake. Review its updated scope.')
                require(self.github.api('commits/main')['sha'] == job['base'], 'GitHub main moved since intake.')
            else:
                if ticket is None:
                    ticket = self.github.ticket(number)
                require(self.github.api('commits/main')['sha'] == self.manifest['git']['head'],
                        'GitHub main moved. Restart the publisher to index the new base.')
                job = {'number':number, 'session':session, 'turn':turn, 'base':self.manifest['git']['head'],
                       'binding':self.binding, 'issue':ticket}
                if self.auto_merge:
                    job['auto_merge'] = True
                if prior_attempts:
                    job['prior_attempts'] = prior_attempts
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
                require(not job.get('pr'), 'Recovery of an already published draft is read-only.')
                if endpoint in {'git/refs', 'pulls'}:
                    require(self.github.ticket(number) == job['issue'], 'The issue changed since intake. Review its updated scope.')
                    require(self.github.api('commits/main')['sha'] == job['base'], 'GitHub main moved since verification.')
                self.authorize_turn(number, session, turn)
            return self.github.api(endpoint, body)
        result = publish(checked_api, STATE/'sources'/job['base'], manifest, job, patch, digest, checks, save,
                         installation=self.github.installation)
        save({**job, 'pr':result})
        return {**result, 'auto_merge':True} if self.auto_merge and job.get('auto_merge') else result


def verified_ci(github, job):
    """The trusted CI workflow names the PR and both event SHAs, even when pull_requests is empty."""
    pr, head, base = job['pr']['number'], job['commit'], job['base']
    branch = issue_branch(job['number'], github.installation)
    runs = github.api('actions/workflows/ci.yml/runs?event=pull_request'
                      f'&branch={branch}&head_sha={head}&per_page=1')['workflow_runs']
    if not runs:
        return None
    run = runs[0]
    require(run['event'] == 'pull_request' and run['path'] == '.github/workflows/ci.yml'
            and run['repository']['full_name'] == run['head_repository']['full_name'] == REPO
            and run['head_branch'] == branch and run['head_sha'] == head
            and run['display_title'] == f'CI PR {pr} head {head} base {base}',
            'The CI run does not identify the verified PR, head and base.')
    if run['status'] != 'completed':
        return None
    require(run['conclusion'] == 'success', f"CI run {run['id']} ended with {run['conclusion']}.")
    jobs = github.api(f"actions/runs/{int(run['id'])}/attempts/{int(run['run_attempt'])}/jobs")
    require(any(j['name'] == 'test' and j['status'] == 'completed' and j['conclusion'] == 'success'
                and j['head_sha'] == head for j in jobs['jobs']), 'The required CI test job did not pass.')
    return run['id']


def merge_verified_issue(github, client, jobfile, *, deadline=None):
    job = json.loads(jobfile.read_text())
    app, pr = github.installation, job['pr']
    require(app is not None and job.get('auto_merge') is True and github.operator_issue == job['number']
            and pr['app_id'] == app.app_id and pr['author'] == app.bot and pr['head'] == job['commit'],
            'This publication has no matching single-issue App merge grant.')
    patch, digest, checks = verified_patch(client, job)
    manifest = json.loads((ROOT/'.local/repositories/snapshots'/job['binding']['snapshot_id']/'manifest.json').read_text())
    repo = STATE/'sources'/job['base']
    tree, _ = reproduce(repo, job['base'], manifest, patch, digest)
    check_merge_examples(repo, tree, patch)
    body = publication_body(job, patch, checks, manifest.get('verification_profile') == 'rust-retry-tests')
    head, base = job['commit'], job['base']
    commit = github.api('git/commits/' + head)
    require(commit['sha'] == head and tree == job['tree'] == commit['tree']['sha']
            and [p['sha'] for p in commit['parents']] == [base], 'The published commit differs from the verified patch.')
    route = f"pulls/{int(pr['number'])}"
    branch = issue_branch(job['number'], app)
    if deadline is None:
        deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        live = github.api(route)
        require(live['number'] == pr['number'] and live['user']['login'] == app.bot and live['user']['type'] == 'Bot'
                and live['head']['repo']['full_name'] == live['base']['repo']['full_name'] == REPO
                and live['head']['sha'] == head and live['head']['ref'] == branch and live['base']['ref'] == BASE,
                'PR identity, branch or verified head changed.')
        if live['merged']:
            require((live.get('merged_by') or {}).get('login') == app.bot and job.get('ci_run'),
                    'A different actor or an unrecorded CI run merged this PR.')
            data = app.graphql('query($id:ID!){node(id:$id){... on PullRequest{id merged mergeCommit{oid}}}}',
                               {'id':live['node_id']})
            node = data['node']
            require(node['id'] == live['node_id'] and node['merged'] is True, 'GitHub did not confirm the merged PR.')
            if not node.get('mergeCommit'):
                time.sleep(5)
                continue
            job['merge_sha'] = node['mergeCommit']['oid']
            landed = github.api('git/commits/' + job['merge_sha'])
            require(landed['tree']['sha'] == tree and [p['sha'] for p in landed['parents']] == [base, head],
                    'Merged result differs from the CI base and verified tree. Inspect main.')
            atomic_json(jobfile, job)
            print(f"GitHub App merged PR #{pr['number']} at {job['merge_sha']} after CI {job['ci_run']}.", flush=True)
            return
        require(live['state'] == 'open' and live['base']['sha'] == base, 'PR closed or its pinned main base moved.')
        require((live.get('body') or '').replace('\r\n','\n').rstrip() == body.rstrip(),
                'PR description or linked issue changed after publication.')
        require(github.ticket(job['number']) == job['issue'], 'The issue changed after patch verification.')
        run_id = verified_ci(github, job)
        if not run_id:
            time.sleep(15)
            continue
        # Keep the CI identity across a lost merge response. GitHub enforces its
        # branch rules and compares the head OID again inside the merge mutation.
        job['ci_run'] = run_id
        atomic_json(jobfile, job)
        if live['draft']:
            data = app.graphql('mutation($id:ID!){markPullRequestReadyForReview(input:{pullRequestId:$id})'
                               '{pullRequest{id isDraft}}}', {'id':live['node_id']})
            ready = data['markPullRequestReadyForReview']['pullRequest']
            require(ready['id'] == live['node_id'] and ready['isDraft'] is False, 'GitHub did not confirm ready status.')
            continue
        if live['mergeable'] is None:
            time.sleep(5)
            continue
        require(live['mergeable'] is True and github.api('commits/main')['sha'] == base,
                'The PR has conflicts or its verified main base moved.')
        data = app.graphql('mutation($id:ID!,$head:GitObjectID!){mergePullRequest(input:{pullRequestId:$id,'
                           'expectedHeadOid:$head,mergeMethod:MERGE}){pullRequest{id merged}}}',
                           {'id':live['node_id'], 'head':head})
        merged = data['mergePullRequest']['pullRequest']
        require(merged['id'] == live['node_id'] and merged['merged'] is True, 'GitHub did not confirm the merge.')
    raise Refused('Timed out waiting for verified CI. Restart the same issue grant to reconcile.')


def check_merge_examples(repo, tree, patch):
    from documentation_review import (direct_retry_formula, prose_passages, reference_lines,
                                      retry_example_errors, retry_implementation)
    candidate = {'src/lib.rs':git(repo, 'show', f'{tree}:{PROJECT}/src/lib.rs')}
    refs = reference_lines(candidate, candidate)
    location = retry_implementation(candidate, refs)
    formula = direct_retry_formula(candidate) if location else None
    errors = retry_example_errors(prose_passages(patch.decode()), formula)
    if errors:
        raise Refused('The verified Git tree contradicts a documented retry example. ' + next(iter(errors.values())))


def watch_verified_issue(github, client, jobfile):
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        try:
            return merge_verified_issue(github, client, jobfile, deadline=deadline)
        except HTTPError as error:
            error.close()
            if error.code not in (408, 429, 500, 502, 503, 504):
                raise
        except (URLError, TimeoutError, ConnectionError):
            pass
        print('Temporary connection failure. Reconciling the saved publication with GitHub.', flush=True)
        time.sleep(15)
    raise Refused('GitHub remained unavailable. Restart the same issue grant to reconcile.')


@contextlib.contextmanager
def live_lease(exchange):
    lease = uuid.uuid4().hex
    stop = threading.Event()
    atomic_json(exchange/'lease.json', {'id':lease, 'updated':time.time()})
    def heartbeat():
        while not stop.wait(3):
            atomic_json(exchange/'lease.json', {'id':lease, 'updated':time.time()})
    heartbeat_thread = threading.Thread(target=heartbeat)
    heartbeat_thread.start()
    try:
        yield lease
    finally:
        stop.set()
        heartbeat_thread.join()
        (exchange/'lease.json').unlink(missing_ok=True)


def serve(publisher, exchange, lease):
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
            except MoyaiUnavailable:
                # A replacement controller can replay the same request after its API returns.
                continue
            except Exception as exc:
                reason = str(exc) if isinstance(exc, (Refused, AppError)) else type(exc).__name__
                print(f'Issue operation {request["action"]} failed: {reason}', flush=True)
                response = {'error':reason}
            atomic_json(reply, {**response, 'lease':lease})
            if request['action'] == 'publish' and response.get('result', {}).get('auto_merge'):
                return  # The chat can deliver its Files while the host waits for CI.
        time.sleep(0.25)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', action='store_true', help='Use only the private GitHub App installation.')
    parser.add_argument('--operator-issue', type=int, default=os.environ.get('MOYAI_OPERATOR_ISSUE') or None, help='Limit the selected publisher identity to one operator-approved issue.')
    parser.add_argument('--profile', choices=('rust-retry', 'rust-retry-tests'),
                        default=os.environ.get('MOYAI_ISSUE_PROFILE') or 'rust-retry')
    parser.add_argument('--restart-unpublished-from', type=uuid.UUID, default=os.environ.get('MOYAI_RESTART_UNPUBLISHED_FROM'),
                        help='Allow the same owner to restart one checked but undelivered App issue from this exact previous session.')
    args = parser.parse_args()
    auto_merge = os.environ.get('MOYAI_AUTO_MERGE', '')
    if auto_merge not in ('', '0', '1') or (auto_merge == '1' and not (args.app and args.operator_issue)):
        parser.error('AUTO_MERGE=1 requires the GitHub App and one operator-approved issue')
    auto_merge = auto_merge == '1'
    if args.restart_unpublished_from and not (args.app and args.operator_issue):
        parser.error('restart requires the GitHub App scoped to one operator-approved issue')
    if args.profile == 'rust-retry-tests' and not args.operator_issue:
        parser.error('the tests-only publisher requires one operator-approved issue')
    os.chdir(ROOT)
    STATE.mkdir(exist_ok=True, parents=True, mode=0o700)
    exchange = STATE / 'exchange'
    exchange.mkdir(exist_ok=True)
    (exchange/'requests').mkdir(exist_ok=True)
    (exchange/'requests').chmod(0o777)  # The relay has write access only to this mailbox.
    (exchange/'replies').mkdir(exist_ok=True)

    def stop_publisher(signum, _frame):
        # Make can forward TERM after the process group already received it.
        # Let the first signal finish lease cleanup under the publisher lock.
        signal.signal(signum, signal.SIG_IGN)
        raise SystemExit(0)

    with (STATE/'publisher.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        signal.signal(signal.SIGTERM, stop_publisher)
        github = GitHub(args.operator_issue, installation=Installation() if args.app else None)
        client = MoyaiClient()
        if auto_merge:
            jobfile = STATE/f'app-{github.installation.app_id}'/f'issue-{args.operator_issue}.json'
            if jobfile.exists():
                prior = json.loads(jobfile.read_text())
                require(prior.get('auto_merge') and prior['binding']['verification_profile'] == args.profile,
                        'Auto-merge must be granted at original issue intake for this profile.')
                if prior.get('pr'):
                    client.login()
                    watch_verified_issue(github, client, jobfile)
                    return
        if args.operator_issue is not None:
            github.ticket(args.operator_issue)
        print('Preparing the pinned GitHub main snapshot and its local context index…', flush=True)
        manifest, binding = prepare(github.api('commits/main')['sha'], args.profile)
        try:
            subprocess.run([*COMPOSE, 'up', '--detach', '--no-deps', '--build', '--wait', 'moyai', 'issue-relay'],
                           cwd=ROOT, check=True)
            client.login()
            with live_lease(exchange) as lease:
                serve(Publisher(github, client, manifest, binding, auto_merge=auto_merge,
                                restart_from=str(args.restart_unpublished_from) if args.restart_unpublished_from else None),
                      exchange, lease)
                if auto_merge:
                    # The same turn may reconnect for the recorded reply while CI is running.
                    watch_verified_issue(github, client, jobfile)
        finally:
            subprocess.run([*COMPOSE, 'rm', '--stop', '--force', 'issue-relay'], cwd=ROOT, check=True)


if __name__ == '__main__':
    with contextlib.suppress(KeyboardInterrupt):
        main()
