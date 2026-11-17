#!/usr/bin/env python3
"""Stage filtered bytes and publish immutable repository snapshots (never host paths)."""
from __future__ import annotations

import fcntl
import errno
import re
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import stat
import tempfile

_spec = importlib.util.spec_from_file_location('source_guards', Path(__file__).with_name('source-snapshot.py'))
_guards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_guards)
SCANNER_VERSION = 'repository-source-v1'
MAX_FILES = 5000
MAX_BYTES = 100_000_000


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def git(root, *arguments, input_bytes=None):
    try:
        return subprocess.run(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
            '-c', 'core.hooksPath=/dev/null', '-C', str(root), *arguments],
            capture_output=True, check=True, timeout=15, input=input_bytes).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError('Git source inspection failed; plain directories require --unversioned') from exc


def reject_git_execution(root):
    # Reading effective configuration and attributes is pure metadata inspection;
    # do this before any command that compares working bytes with the index.
    if any(row.startswith(b'160000 ') for row in git(root, 'ls-files', '--stage', '-z').split(b'\0')):
        raise ValueError('Selected source contains a submodule; select its explicit checkout separately')
    configuration = git(root, 'config', '--null', '--list')
    external = set()
    for record in configuration.split(b'\0'):
        key, _, value = record.partition(b'\n')
        match = re.fullmatch(rb'filter\.(.*)\.(?:clean|process|smudge)', key, re.I)
        if match and value.strip():
            external.add(match[1])
    if external:
        tracked = git(root, 'ls-files', '-z')
        attributes = git(root, 'check-attr', '-z', '--stdin', 'filter', input_bytes=tracked).split(b'\0')
        if any(value in external for value in attributes[2::3]):
            raise ValueError('Configured external Git filter is unsupported for source inspection')


def git_blob_dirty(root):
    """Compare index/HEAD metadata and raw bytes; never run conversion drivers.

    Unlike status, ls-tree/ls-files do not compare working bytes through Git's
    mutable filter configuration. Raw line-ending differences are conservative
    dirty provenance, even when a configured built-in normalization hides them.
    """
    index = {}
    for entry in git(root, 'ls-files', '--stage', '-z').split(b'\0'):
        if not entry:
            continue
        header, name = entry.split(b'\t', 1)
        mode, oid, stage = header.split()
        if mode == b'160000':
            raise ValueError('Selected source contains a submodule')
        if stage != b'0':
            return True
        index[name] = (mode, oid)
    head = {}
    for entry in git(root, 'ls-tree', '-r', '-z', 'HEAD', '--', '.').split(b'\0'):
        if entry:
            header, name = entry.split(b'\t', 1)
            mode, kind, oid = header.split()
            head[name] = (mode, oid)
    if index != head or git(root, 'ls-files', '--others', '--exclude-standard', '-z'):
        return True
    algorithm = git(root, 'rev-parse', '--show-object-format').strip().decode('ascii')
    if algorithm not in {'sha1', 'sha256'}:
        raise ValueError('Unsupported Git object format')
    track_mode = git(root, 'config', '--type=bool', '--default=true', '--get', 'core.filemode').strip() == b'true'
    for name, (mode, oid) in index.items():
        if not working_blob_matches(root, Path(os.fsdecode(name)), mode, oid, algorithm, track_mode):
            return True
    return False


def working_blob_matches(root, relative, index_mode, expected_oid, algorithm, track_mode):
    """Inspect actual type/mode, then stream a regular blob without Git filters."""
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in relative.parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        info = os.stat(relative.name, dir_fd=directory, follow_symlinks=False)
        if index_mode == b'120000':
            if not stat.S_ISLNK(info.st_mode):
                return False
            raw = os.fsencode(os.readlink(relative.name, dir_fd=directory))
            digest = hashlib.new(algorithm, b'blob ' + str(len(raw)).encode() + b'\0' + raw)
        else:
            if not stat.S_ISREG(info.st_mode):
                return False
            if track_mode and bool(info.st_mode & stat.S_IXUSR) != (index_mode == b'100755'):
                return False
            descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(descriptor, 'rb') as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    return False
                digest = hashlib.new(algorithm, b'blob ' + str(opened.st_size).encode() + b'\0')
                remaining = opened.st_size
                while remaining:
                    block = stream.read(min(64 * 1024, remaining))
                    if not block:
                        return False
                    digest.update(block)
                    remaining -= len(block)
                if stream.read(1):
                    return False
                after = os.fstat(stream.fileno())
                if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns):
                    return False
        current = os.stat(relative.name, dir_fd=directory, follow_symlinks=False)
        if (current.st_dev, current.st_ino, current.st_mode, current.st_mtime_ns, current.st_ctime_ns) != (info.st_dev, info.st_ino, info.st_mode, info.st_mtime_ns, info.st_ctime_ns):
            return False
        return digest.hexdigest().encode() == expected_oid
    except OSError as exc:
        if exc.errno in {errno.ENOENT, errno.ENOTDIR, errno.EINVAL, errno.ELOOP}:
            return False  # Genuine missing/type-replaced entries are dirty.
        raise
    finally:
        os.close(directory)


def walk_source_paths(root):
    """Enumerate explicitly; unreadable directories cannot become omissions."""
    def fail(error):
        raise error
    paths = []
    for base, directories, names in os.walk(root, topdown=True, followlinks=False, onerror=fail):
        directories[:] = [name for name in directories if not name.startswith('.') and
            name.lower() not in (_guards.DENIED | {'secret', 'credential', 'coverage'})]
        paths.extend((Path(base) / name).relative_to(root).as_posix() for name in names)
    return paths


def read_source(root, relative):
    """Traverse descriptor-relative components without following symlinks or FIFOs."""
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in relative.parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            return stream.read(1_000_001)
    finally:
        os.close(directory)


def stage_sources(root, stage, paths):
    """Copy accepted bytes first; hash only this private staged tree."""
    total = count = 0
    for name in sorted(set(paths)):
        relative = Path(name)
        if relative.is_absolute() or not relative.parts or any(
            p in {'.', '..'} or p.startswith('.') or p.lower() in (_guards.DENIED | {'secret', 'credential', 'coverage'}) for p in relative.parts
        ) or relative.stem.lower() in (_guards.DENIED | {'secret', 'credential'}):
            continue
        if relative.suffix not in _guards.EXTENSIONS and relative.name not in {
            'Cargo.lock', 'Makefile', 'Dockerfile', 'Containerfile'
        }:
            continue
        source = root / relative
        if any(p.is_symlink() for p in [source, *source.parents]):
            continue
        try:
            raw = read_source(root, relative)
            if raw is None or len(raw) > 1_000_000 or b'\0' in raw:
                continue
            text = raw.decode('utf-8')
            if _guards.PATTERN.search(text):
                continue
        except (FileNotFoundError, UnicodeError):
            # Deleted tracked files and policy-excluded binary text are omitted.
            continue
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                continue  # A component became a symlink after enumeration.
            raise
        count += 1
        total += len(raw)
        if count > MAX_FILES or total > MAX_BYTES:
            raise ValueError('Repository snapshot exceeds source limits')
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


def prepare_snapshot(repo: Path, catalog: Path, verification_profile: str,
                     *, unversioned=False, repository_id=None):
    root = Path(repo).resolve(strict=True)
    if not root.is_dir() or verification_profile != 'rust-retry':
        raise ValueError('Expected a source directory and supported verification profile')
    if unversioned:
        identity = str(root)
        metadata = {'branch': 'unversioned', 'head': 'unversioned', 'dirty': True, 'subtree': ''}
        paths = walk_source_paths(root)
    else:
        reject_git_execution(root)
        top = Path(os.fsdecode(git(root, 'rev-parse', '--show-toplevel')).strip()).resolve()
        subtree = root.relative_to(top).as_posix()
        if subtree == '.':
            subtree = ''
        common = os.fsdecode(git(root, 'rev-parse', '--git-common-dir')).strip()
        identity = str((root / common).resolve()) + '\0' + subtree
        metadata = {'branch': os.fsdecode(git(root, 'branch', '--show-current')).strip() or 'detached',
            'head': os.fsdecode(git(root, 'rev-parse', 'HEAD')).strip(),
            'dirty': git_blob_dirty(root),
            'subtree': subtree}
        paths = [os.fsdecode(p) for p in git(root, 'ls-files', '-co', '--exclude-standard', '-z').split(b'\0') if p]
    repo_id = repository_id or 'repo_' + hashlib.sha256(identity.encode()).hexdigest()[:24]
    if not repo_id.startswith('repo_') or not repo_id.replace('_', '').isalnum():
        raise ValueError('Invalid repository ID')
    catalog = Path(catalog)
    snapshots = catalog / 'snapshots'
    snapshots.mkdir(parents=True, exist_ok=True)
    catalog.chmod(0o755)
    snapshots.chmod(0o755)
    with tempfile.TemporaryDirectory(prefix='.stage-', dir=snapshots) as temp:
        staging = Path(temp)
        source = staging / 'source'
        source.mkdir()
        stage_sources(root, source, paths)
        files = [{'path': p.relative_to(source).as_posix(),
                  'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'size': p.stat().st_size}
                 for p in [source / name for name in sorted(walk_source_paths(source))]]
        manifest = {'schema_version': 1, 'repository_id': repo_id, 'name': root.name,
            'git': metadata, 'files': files, 'scanner_version': SCANNER_VERSION,
            'verification_profile': verification_profile}
        manifest['snapshot_id'] = hashlib.sha256(canonical(manifest)).hexdigest()
        (staging / 'manifest.json').write_bytes(canonical(manifest) + b'\n')
        # Read-only container consumers use a nonroot UID distinct from the host.
        def fail(error):
            raise error
        for base, directories, names in os.walk(staging, onerror=fail):
            Path(base).chmod(0o755)
            for name in names:
                (Path(base) / name).chmod(0o644)
        with (catalog / '.publish.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            destination = snapshots / manifest['snapshot_id']
            if destination.exists():
                if (destination / 'manifest.json').read_bytes() != (staging / 'manifest.json').read_bytes():
                    raise ValueError('Snapshot identity collision')
                for entry in files:
                    published = read_source(destination / 'source', Path(entry['path']))
                    if published is None or hashlib.sha256(published).hexdigest() != entry['sha256']:
                        raise ValueError('Published snapshot bytes changed')
            else:
                os.rename(staging, destination)
                # TemporaryDirectory cleanup tolerates the moved directory.
    return manifest


def register_catalog(catalog: Path, manifest: dict, indexed: dict, *, before_publish=None):
    """Publish current selection only after a complete DB generation exists."""
    for field in ('repository_id', 'snapshot_id'):
        if indexed.get(field) != manifest[field]:
            raise ValueError('Indexed scope does not match the prepared snapshot')
    if not indexed.get('store_id') or not indexed.get('generation_id'):
        raise ValueError('Indexed store/generation scope is missing')
    catalog = Path(catalog)
    with (catalog / '.publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        target = catalog / 'catalog.json'
        data = json.loads(target.read_text()) if target.exists() else {'schema_version': 1, 'repositories': [], 'indexed_generations': []}
        row = {k: manifest[k] for k in ('repository_id', 'name', 'snapshot_id', 'verification_profile')}
        row.update({k: indexed[k] for k in ('store_id', 'generation_id')})
        row['manifest'] = f"snapshots/{manifest['snapshot_id']}/manifest.json"
        history = data.setdefault('indexed_generations', [])
        previous = next((r for r in history if r['generation_id'] == row['generation_id']), None)
        if previous is not None and previous != row:
            raise ValueError('Immutable indexed generation record conflicts')
        if previous is None:
            history.append(row)
        if before_publish is not None:
            before_publish(row)
        data['repositories'] = sorted([r for r in data['repositories'] if r['repository_id'] != row['repository_id']] + [row], key=lambda r: r['repository_id'])
        with tempfile.NamedTemporaryFile(dir=catalog, delete=False) as stream:
            temp = Path(stream.name)
            os.fchmod(stream.fileno(), 0o644)
            stream.write(canonical(data) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, target)
    return row
