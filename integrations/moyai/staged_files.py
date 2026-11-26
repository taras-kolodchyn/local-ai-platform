"""Read the publication snapshot from a private checkout inside the sandbox."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

LIMIT = 128 * 1024
TESTED_REF = 'refs/workflow/tested'


def snapshot(directory, base):
    if not re.fullmatch('[0-9a-f]{40}', base) or directory.resolve() != directory:
        raise ValueError('Invalid checkout or revision.')
    if (directory / '.git').is_symlink() or not (directory / '.git').is_dir():
        raise ValueError('The prepared checkout is unavailable.')

    def git(*args):
        return subprocess.check_output(
            ['git', '--no-replace-objects', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
             *args], cwd=directory, env={'PATH': os.defpath, 'GIT_CONFIG_NOSYSTEM': '1',
                                        'GIT_CONFIG_GLOBAL': '/dev/null'}, timeout=10, stderr=subprocess.DEVNULL)

    if git('rev-parse', 'HEAD').decode().strip() != base:
        raise ValueError('The checkout revision changed.')
    tree = git('rev-parse', '--verify', TESTED_REF + '^{tree}').decode().strip()
    if tree != git('write-tree').decode().strip() or git('diff-files', '--name-only', '-z'):
        raise ValueError('The staged or working tree changed after testing.')
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
    request = json.load(sys.stdin)
    try:
        files = snapshot(Path(request['directory']), request['base'])
    except (ValueError, OSError, subprocess.SubprocessError):
        result = {'reason': 'The tested tree is missing, changed, empty or unsupported. Inspect the checkout.'}
    else:
        result = {'files': files}
    print(json.dumps(result))
