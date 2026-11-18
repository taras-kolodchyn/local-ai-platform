"""Pinned, pure-data import policy shared by host preparation and ingestion."""
import hashlib
import json
from pathlib import PurePosixPath

MAX_FILES = 512
MAX_FILE = 512 * 1024
MAX_TREE_BYTES = 8 * 1024 * 1024
MAX_TREE_WIRE_BYTES = 16 * 1024 * 1024
PROTECTED_HASHES = {'Cargo.toml': '5f341b2e590f8d49cdfc84ec843519510cdc3aa3c31ef88c2e2b7f9ed4c30736', 'Cargo.lock': '45f75a27125b22c5c6aa1a5fb54db7606648daa6b3fdf43bf9301a1be756ffb4'}


def safe_relative(value):
    if not isinstance(value, str) or not value or '\\' in value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError('Unsafe profile path')
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in ('.', '..') or part.startswith('.') for part in path.parts):
        raise ValueError('Unsafe profile path')
    if any(part in {'target', 'build.rs', 'rust-toolchain', 'rust-toolchain.toml', 'vendor'} for part in path.parts):
        raise ValueError('Protected build path')
    if len(path.parts) > 12 or len(value) > 240:
        raise ValueError('Profile path exceeds import bounds')
    return path


def validate_profile(manifest, read_source):
    if manifest.get('verification_profile') not in ('rust-retry', 'rust-retry-tests'):
        raise ValueError('Unsupported verification profile')
    rows = manifest.get('files')
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_FILES:
        raise ValueError('Profile file count exceeds import bounds')
    seen = set()
    total = 0
    # The fixed guest returns every file's text in ASCII-escaped JSON. Count its
    # actual entry representation, including names/hashes, before activation.
    envelope = {'ok': True, 'files': [], 'tree_hash': '0' * 64,
                'protected_unchanged': False, 'snapshot_id': '0' * 64}
    wire_bytes = len(json.dumps(envelope, separators=(',', ':'), ensure_ascii=True).encode())
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'path', 'sha256', 'size'}:
            raise ValueError('Invalid profile file record')
        name = safe_relative(row['path']).as_posix()
        if name in seen or type(row['size']) is not int or not 0 <= row['size'] <= MAX_FILE:
            raise ValueError('Profile file exceeds import bounds')
        seen.add(name)
        total += row['size']
        if total > MAX_TREE_BYTES:
            raise ValueError('Profile tree exceeds import bounds')
        raw = read_source(name)
        if not isinstance(raw, bytes) or len(raw) != row['size'] or b'\x00' in raw or hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise ValueError('Profile source hash or size changed')
        text = raw.decode('utf-8')
        entry = {'path': name, 'sha256': row['sha256'], 'size': len(raw), 'text': text}
        wire_bytes += len(json.dumps(entry, separators=(',', ':'), ensure_ascii=True).encode()) + (1 if len(seen) > 1 else 0)
        if wire_bytes > MAX_TREE_WIRE_BYTES:
            raise ValueError('Profile tree exceeds escaped response wire bounds')
        if name in PROTECTED_HASHES and row['sha256'] != PROTECTED_HASHES[name]:
            raise ValueError('Unsupported protected package or dependencies')
    if not {'Cargo.toml', 'Cargo.lock', 'src/lib.rs'} <= seen:
        raise ValueError('Profile requires pinned Cargo.toml, Cargo.lock and src/lib.rs')
