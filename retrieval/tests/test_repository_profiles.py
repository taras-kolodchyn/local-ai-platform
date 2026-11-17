import hashlib
from pathlib import Path

import pytest

ORACLES = Path(__file__).resolve().parents[2] / 'integrations/moyai/oracles'


def data_files():
    return {name: (ORACLES / name).read_bytes() for name in ('Cargo.toml', 'Cargo.lock')} | {'src/lib.rs': b'pub fn baseline() {}'}


def validate(files):
    from local_ai_retrieval.profiles import validate_profile
    rows = [{'path': p, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} for p, raw in files.items()]
    return validate_profile({'verification_profile': 'rust-retry', 'files': rows}, lambda path: files[path])


def test_profile_accepts_actual_pinned_fixture_and_matches_guest_contract():
    from local_ai_retrieval.profiles import PROTECTED_HASHES
    for name, expected in PROTECTED_HASHES.items():
        assert hashlib.sha256((ORACLES / name).read_bytes()).hexdigest() == expected
    validate(data_files())


@pytest.mark.parametrize('case', ['missing_lock', 'changed_package', 'too_many', 'large_file', 'large_tree', 'deep_path', 'protected_path'])
def test_live_profile_rejects_unimportable_trees(case):
    files = data_files()
    if case == 'missing_lock':
        files.pop('Cargo.lock')
    elif case == 'changed_package':
        files['Cargo.toml'] += b'\n# drift'
    elif case == 'too_many':
        files.update({f'tests/file_{i}.rs': b'text' for i in range(510)})
    elif case == 'large_file':
        files['README.md'] = b'x' * (512 * 1024 + 1)
    elif case == 'large_tree':
        files.update({f'tests/file_{i}.rs': b'x' * (512 * 1024) for i in range(17)})
    elif case == 'deep_path':
        files['/'.join(['deep'] * 13) + '.rs'] = b'text'
    else:
        files['build.rs'] = b'fn main(){}'
    with pytest.raises(ValueError):
        validate(files)


@pytest.mark.parametrize('character', ['\n', '\t', '\x1f', '\x7f'])
def test_profile_rejects_controller_control_character_paths(character):
    files = data_files() | {f'notes{character}.md': b'ordinary text'}
    with pytest.raises(ValueError, match='path'):
        validate(files)


def test_profile_and_guest_encoder_agree_on_escaped_tree_wire_bound():
    import importlib.util
    import json
    path = ORACLES.parent / 'repository_guest.py'
    spec = importlib.util.spec_from_file_location('profile_guest_encoder', path)
    guest = importlib.util.module_from_spec(spec); spec.loader.exec_module(guest)
    # Short Unicode lines avoid the separate chunk size/line budget edge.
    raw = ('é' * 50 + '\n').encode() * 5000
    files = data_files() | {f'notes_{index}.md': raw for index in range(12)}
    rows = [{'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
             'text': data.decode()} for name, data in sorted(files.items())]
    reply = {'ok': True, 'files': rows, 'tree_hash': 'a' * 64,
             'protected_unchanged': True, 'snapshot_id': 'b' * 64}
    encoded = json.loads(guest.encode_response(reply))
    assert encoded['ok'] is False and encoded['error'] == 'response too large'
    with pytest.raises(ValueError, match='wire'):
        validate(files)


def test_profile_legal_long_filename_is_readable_by_actual_controller(tmp_path):
    import sys
    sys.path.insert(0, str(ORACLES.parent))
    from repository_backend import RepositoryBackend
    name = 'a' * 167 + '.md'
    files = data_files() | {name: b'ordinary source context'}
    validate(files)
    catalog = tmp_path / 'catalog'
    directory = catalog / 'snapshots' / ('a' * 64)
    source = directory / 'source'
    rows = []
    for path, raw in files.items():
        destination = source / path; destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        rows.append({'path': path, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    backend = RepositoryBackend(catalog=catalog)
    assert backend.originals({'files': rows}, directory) == files
