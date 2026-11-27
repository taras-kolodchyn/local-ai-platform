"""Prepare a verified, offline checkout for a new private workspace."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from uuid import UUID


REPOSITORY = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+')
COMMIT = re.compile(r'[0-9a-f]{40}')
WORKSPACE = re.compile(r'[0-9a-f]{64}')
GIT_HOST = [
    '-c', 'http.userAgent=Repository Checkout',
    '-c', 'http.followRedirects=false',
    '-c', 'credential.https://github.com.helper=!gh auth git-credential',
]
GIT_ISOLATED = {'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_COUNT': '0'}


class CheckoutError(ValueError):
    pass


def git(*args, directory=None, host=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env['GIT_TERMINAL_PROMPT'] = '0'
    env.update(GIT_ISOLATED)
    # The GitHub CLI credential helper is called only on the Mac.
    return subprocess.check_output(
        ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
         *(GIT_HOST if host else ()), *args],
        cwd=directory, env=env, stderr=subprocess.PIPE, timeout=120,
    )


def source_url(repository):
    name = repository.removesuffix('.git')
    if not REPOSITORY.fullmatch(name) or name.split('/')[1] in {'.', '..'}:
        raise CheckoutError('Use REPO=owner/project for the GitHub repository.')
    return 'https://github.com/' + name + '.git'


def check_tree(repository, commit):
    entries = git('ls-tree', '--full-tree', '-rz', commit, directory=repository).split(b'\0')
    if any(not entry.startswith((b'100644 blob ', b'100755 blob ')) for entry in entries if entry):
        raise CheckoutError('Checkout requires regular files. Git links and symbolic links are unsupported.')


def branch_bundle(source, branch, destination, expected=None):
    ref = 'refs/heads/' + branch
    git('check-ref-format', ref)
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        git('init', '--bare', '--quiet', directory)
        git('fetch', '--quiet', '--no-tags', '--depth=1', '--', source, ref, directory=directory, host=True)
        commit = git('rev-parse', 'FETCH_HEAD^{commit}', directory=directory).decode().strip()
        remote = git('ls-remote', '--exit-code', '--refs', '--', source, ref, host=True).decode().splitlines()
        if not COMMIT.fullmatch(commit) or remote != [f'{commit}\t{ref}'] or expected not in {None, commit}:
            raise CheckoutError('The fetched commit differs from the requested or current branch tip.')
        check_tree(directory, commit)
        git('update-ref', '--no-deref', 'HEAD', commit, directory=directory)
        git('bundle', 'create', str(destination), 'HEAD', directory=directory)
    return commit


def workspace_key(database, session, connection):
    import sqlite3

    from local_config import workspace_directory
    from moyai.contracts import Grant, RunScope

    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute(
            'SELECT s.owner_id, c.revision FROM moyai_sessions s '
            'JOIN moyai_connections c ON c.owner_id=s.owner_id '
            'WHERE s.id=? AND c.id=? AND c.kind=? AND c.enabled AND NOT c.shared '
            'AND s.visibility=? AND s.surface=? AND NOT s.archived AND NOT s.deleted '
            'AND s.active_turn IS NULL AND s.workspace IS NULL '
            'AND NOT EXISTS (SELECT 1 FROM moyai_turns t WHERE t.session_id=s.id) '
            'AND NOT EXISTS (SELECT 1 FROM moyai_setting_automations a WHERE a.session_id=s.id '
            "AND NOT a.paused AND a.state<>'deleted')",
            (session, connection, 'github', 'private', 'web'),
        ).fetchone()
    if row is None:
        raise CheckoutError('Choose a new private chat with no enabled schedule and its own GitHub connection.')
    owner, revision = row
    scope = RunScope(session, '', owner, '', grants=[Grant(connection, 'github', revision, '')])
    return Path(workspace_directory(session, scope.workspace_key)).name


def clean_at(directory, commit):
    return (not (directory / '.git').is_symlink() and (directory / '.git').is_dir()
            and git('rev-parse', 'HEAD', directory=directory).decode().strip() == commit
            and not git('status', '--porcelain', '--untracked-files=all', '--ignored=matching', directory=directory)
            and not git('remote', directory=directory))


def install_bundle(root, key, commit, source):
    if not WORKSPACE.fullmatch(key) or not COMMIT.fullmatch(commit):
        raise CheckoutError('Invalid workspace or commit identifier.')
    directory = root / key
    if directory.is_symlink() or directory.exists():
        if not directory.is_symlink() and directory.is_dir() and clean_at(directory, commit):
            return
        raise CheckoutError('Workspace already exists. Use a new chat to preserve its files.')
    with tempfile.TemporaryDirectory(dir=root, prefix='.checkout-') as staging:
        bundle = Path(staging) / 'source.bundle'
        checkout = Path(staging) / 'checkout'
        with bundle.open('xb') as output:
            shutil.copyfileobj(source, output)
        git('clone', '--quiet', '--no-local', '--no-tags', '--no-checkout',
            str(bundle), str(checkout))
        check_tree(checkout, commit)
        # Recreate the shallow boundary because the bundle carries the tip
        # and tree of the depth-one fetch.
        (checkout / '.git' / 'shallow').write_text(commit + '\n')
        git('checkout', '--quiet', '--detach', commit, directory=checkout)
        git('remote', 'remove', 'origin', directory=checkout)
        git('config', 'user.name', 'Repository Development', directory=checkout)
        git('config', 'user.email', 'repository-development@localhost', directory=checkout)
        if not clean_at(checkout, commit):
            raise CheckoutError('The checkout does not match the verified commit.')
        checkout.chmod(0o700)
        if directory.exists() or directory.is_symlink():
            raise CheckoutError('Workspace appeared while preparing the checkout.')
        checkout.rename(directory)


def run_checkout(repository, branch, session, connection, expected):
    root = Path(__file__).resolve().parents[1]
    compose = ['docker', 'compose', '--project-name', 'local-ai-moyai', '-f', str(root / 'compose.moyai.yaml')]
    source = source_url(repository)
    try:
        ids = [str(UUID(value)) for value in (session, connection)]
    except ValueError:
        raise CheckoutError('SESSION and CONNECTION must be UUIDs.') from None
    if expected is not None and not COMMIT.fullmatch(expected):
        raise CheckoutError('SHA must be the full lowercase GitHub commit hash.')
    code = Path(__file__).read_bytes()

    def address():
        command = [*compose, 'exec', '-T', '--user', 'workspace', 'moyai', 'python', '-', '_scope', *ids]
        try:
            result = subprocess.check_output(command, input=code, stderr=subprocess.PIPE, timeout=30).decode().strip()
        except subprocess.CalledProcessError:
            raise CheckoutError('Use a new private chat and its enabled GitHub connection before enabling the schedule.') from None
        if not WORKSPACE.fullmatch(result):
            raise CheckoutError('The controller returned an invalid workspace identifier.')
        return result

    workspace = address()
    with tempfile.TemporaryDirectory() as staging:
        bundle = Path(staging) / 'source.bundle'
        commit = branch_bundle(source, branch, bundle, expected)
        if address() != workspace:
            raise CheckoutError('The connection revision changed during preparation.')
        command = [*compose, 'exec', '-T', '--user', '10001:10001', 'moyai-sandbox',
                   'python', '-c', code.decode(), '_install', workspace, commit]
        with bundle.open('rb') as data:
            subprocess.run(command, stdin=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=180, check=True)
    return commit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default=os.getenv('CHECKOUT_REPO'))
    parser.add_argument('--branch', default=os.getenv('CHECKOUT_BRANCH', 'main'))
    parser.add_argument('--session', default=os.getenv('CHECKOUT_SESSION'))
    parser.add_argument('--connection', default=os.getenv('CHECKOUT_CONNECTION'))
    parser.add_argument('--sha', default=os.getenv('CHECKOUT_SHA') or None)
    args = parser.parse_args()
    if not all((args.repo, args.branch, args.session, args.connection)):
        parser.error('Supply REPO, BRANCH, SESSION and CONNECTION.')
    try:
        commit = run_checkout(args.repo, args.branch, args.session, args.connection, args.sha)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        detail = str(error) if isinstance(error, CheckoutError) else type(error).__name__
        parser.exit(1, f'Checkout failed: {detail}\n')
    print(json.dumps({'repository': args.repo, 'branch': args.branch, 'commit': commit}))


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '_scope':
        print(workspace_key(Path(os.environ['DATA_DIR']) / 'moyai.db', *sys.argv[2:]))
    elif len(sys.argv) == 4 and sys.argv[1] == '_install':
        install_bundle(Path('/workspace'), *sys.argv[2:], sys.stdin.buffer)
    else:
        main()
