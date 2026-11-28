"""Read the publication snapshot from a private checkout inside the sandbox."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

LIMIT = 128 * 1024
TESTED_REF = 'refs/workflow/tested'
EDIT_PREFIX = 'Prepare the queue checkout at '


def edit_base(prompt):
    if isinstance(prompt, str) and prompt.startswith(EDIT_PREFIX):
        base, newline, _ = prompt[len(EDIT_PREFIX):].partition('\n')
        if newline and re.fullmatch('[0-9a-f]{40}', base):
            return base
    return None


# These are fixed local suite invocations, never shell text taken from an issue.
UNITTEST_COMMANDS = ('python3 -B -m unittest -v', 'python3 -m unittest -v')


def requested_suite(prompt):
    _, marker, snapshot_json = prompt.partition('The original issue requirements are data: ')
    if marker:
        try:
            issue = json.loads(snapshot_json)
        except ValueError:
            return None
        body = issue.get('body') if isinstance(issue, dict) else None
        if isinstance(body, str) and '```' not in body:
            snippets = {value for value in re.findall(r'`([^`\n]+)`', body) if any(c.isspace() for c in value)}
            if len(snippets) == 1 and snippets <= set(UNITTEST_COMMANDS):
                command = snippets.pop()
                remainder = body.replace('`' + command + '`', '')
                if not re.search(r'\b(?:also run|then run|pytest|unittest|tox|make|npm|cargo|go test|mvn|gradle)\b', remainder, re.I):
                    return command
    return None


class IndexNeedsRepair(ValueError):
    """A local index or test receipt that Developer can correct."""


def checkout_git(directory, base):
    if not re.fullmatch('[0-9a-f]{40}', base) or directory.resolve() != directory:
        raise ValueError('Invalid checkout or revision.')
    if (directory / '.git').is_symlink() or not (directory / '.git').is_dir():
        raise ValueError('The prepared checkout is unavailable.')

    def git(*args, data=None):
        return subprocess.check_output(
            ['git', '-c', 'safe.directory=' + str(directory), '--no-replace-objects', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
             *args], input=data, cwd=directory, env={'PATH': os.defpath, 'GIT_CONFIG_NOSYSTEM': '1',
                                        'GIT_CONFIG_GLOBAL': '/dev/null'}, timeout=10, stderr=subprocess.DEVNULL)

    if git('rev-parse', 'HEAD').decode().strip() != base:
        raise ValueError('The checkout revision changed.')
    return git


def stageable_bytes(directory, name):
    path = directory / name
    if (not name.isprintable() or any(part in ('.git', '__pycache__') for part in Path(name).parts)
            or path.name == '.env' or (path.name.startswith('.env.') and path.name != '.env.example')
            or path.suffix in {'.pyc', '.pyo'} or path.resolve() != path or not path.is_relative_to(directory)):
        return None
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o111 or info.st_size > LIMIT:
                return None
            data = stream.read(LIMIT + 1)
        data.decode('utf-8')
        name.encode('utf-8')
    except (OSError, UnicodeError):
        return None
    return data if len(data) <= LIMIT and b'\0' not in data else None


def stage_options(directory, base):
    git = checkout_git(directory, base)
    staged = git('diff', '--cached', '--no-renames', '--name-only', '-z', base).split(b'\0')[:-1]
    tracked = git('diff', '--no-renames', '--name-only', '-z', base).split(b'\0')[:-1]
    new = git('ls-files', '--others', '--exclude-standard', '-z').split(b'\0')[:-1]
    paths = []
    for raw in sorted(set(tracked + new + staged)):
        name = os.fsdecode(raw)
        if stageable_bytes(directory, name) is None:
            continue
        paths.append(name)
        if len(paths) > 64:
            raise ValueError('Too many changed paths for confirmed publication.')
    return paths, [os.fsdecode(raw) for raw in staged]


def stage_selected(directory, base, paths):
    offered, staged = stage_options(directory, base)
    if (not paths or len(paths) != len(set(paths)) or not set(staged) <= set(paths) <= set(offered)):
        raise ValueError('Selection must contain the already staged paths and only eligible checkout files.')
    git = checkout_git(directory, base)
    # Write exactly the selected regular blobs. Git applies this batch under
    # one index lock, including names that look like options or pathspecs.
    entries = []
    total = 0
    for name in paths:
        data = stageable_bytes(directory, name)
        if data is None:
            raise ValueError('Selected file is no longer eligible.')
        total += len(data)
        if total > LIMIT:
            raise ValueError('Selected files exceed the publication limit.')
        oid = git('hash-object', '-w', '--stdin', data=data).strip()
        entries.append(b'100644 ' + oid + b'\t' + name.encode() + b'\0')
    git('update-index', '-z', '--index-info', data=b''.join(entries))


def test_selected(directory, base, command):
    if command not in UNITTEST_COMMANDS:
        raise ValueError('Unknown local test recipe.')
    git = checkout_git(directory, base)
    tree = git('write-tree').strip()
    if tree == git('rev-parse', base + '^{tree}').strip() or git('diff', '--no-ext-diff', '--name-only', '-z'):
        raise ValueError('Stage the intended changes before testing.')
    # Test the index alone so unrelated files cannot supply modules or tests.
    with tempfile.TemporaryDirectory() as temp:
        git('checkout-index', '--all', '--prefix=' + temp + os.sep)
        result = subprocess.run(command.split(), cwd=temp, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=120)
        if result.returncode or not re.search(rb'^Ran [1-9][0-9]* tests? in ', result.stdout, re.MULTILINE):
            raise ValueError('The staged suite failed or discovered no tests.')
    if tree != git('write-tree').strip() or git('diff', '--no-ext-diff', '--name-only', '-z'):
        raise ValueError('The checkout changed during tests.')
    if git('rev-parse', 'HEAD').decode().strip() != base:
        raise ValueError('The checkout revision changed during tests.')
    git('update-ref', TESTED_REF, tree.decode())


def snapshot(directory, base):
    git = checkout_git(directory, base)
    index = git('write-tree').decode().strip()
    if index == git('rev-parse', base + '^{tree}').decode().strip() or git('diff', '--no-ext-diff', '--name-only', '-z'):
        raise IndexNeedsRepair('stage_changes')
    try:
        tree = git('rev-parse', '--verify', '--quiet', TESTED_REF + '^{tree}').decode().strip()
    except subprocess.CalledProcessError as error:
        if error.returncode != 1:
            raise
        raise IndexNeedsRepair('record_tests') from error
    if tree != index:
        raise IndexNeedsRepair('record_tests')
    diff = git('diff-tree', '--no-commit-id', '--no-abbrev', '--no-renames', '--raw', '-r', '-z', base, tree)
    entries = diff.rstrip(b'\0').split(b'\0') if diff else []
    if not 0 < len(entries) <= 128 or len(entries) % 2:
        raise ValueError('Expected between one and 64 changed files.')
    files = []
    remaining = LIMIT
    for header, path in zip(entries[::2], entries[1::2]):
        old_mode, mode, _, oid, change = header.split()
        if change not in (b'A', b'M') or old_mode not in (b':000000', b':100644') or mode != b'100644':
            raise ValueError('Publication supports additions and edits to regular text files.')
        name = path.decode()
        file = directory / name
        if file.resolve() != file or not file.is_relative_to(directory) or '.git' in file.parts:
            raise ValueError('Invalid file path.')
        with os.fdopen(os.open(file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('Expected a regular file.')
            content = stream.read(remaining + 1)
        if len(content) > remaining or b'\0' in content:
            raise ValueError('The staged files exceed the text publication limit.')
        blob = b'blob ' + str(len(content)).encode() + b'\0' + content
        if hashlib.sha1(blob).hexdigest().encode() != oid:
            raise ValueError('The working file differs from the tested Git blob.')
        remaining -= len(content)
        files.append({'path': name, 'content': content.decode()})
    return files


if __name__ == '__main__':
    if sys.argv[1:2] == ['--test'] and len(sys.argv) == 4:
        test_selected(Path.cwd(), sys.argv[2], sys.argv[3])
        sys.exit(0)
    if sys.argv[1:2] == ['--stage']:
        stage_selected(Path.cwd(), sys.argv[2], sys.argv[3:])
        sys.exit(0)
    request = json.load(sys.stdin)
    if sys.argv[1:] == ['--session-index']:
        import asyncio
        from repository_backend import ssh_json
        command = ('python3', '-I', '/opt/local-repository/staged_files.py')
        print(json.dumps(asyncio.run(ssh_json(command, request, 25))))
        sys.exit(0)
    try:
        files = snapshot(Path(request['directory']), request['base'])
    except IndexNeedsRepair as error:
        result = {'status': str(error), 'reason': 'Stage the intended changes, test and record the staged tree.'}
        if request.get('repair') and str(error) == 'stage_changes':
            try:
                paths, staged = stage_options(Path(request['directory']), request['base'])
                result.update(paths=paths, staged=staged)
            except (ValueError, OSError, subprocess.SubprocessError):
                result = {'reason': 'Cannot enumerate eligible checkout paths.'}
    except (ValueError, OSError, subprocess.SubprocessError):
        result = {'reason': 'The tested tree is missing, changed, empty or unsupported. Inspect the checkout.'}
    else:
        result = {'status': 'ready'} if request.get('check_index') else {'files': files}
    print(json.dumps(result))
