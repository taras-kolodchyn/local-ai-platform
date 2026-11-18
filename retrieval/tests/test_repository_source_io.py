import os
from pathlib import Path
import signal

import pytest


def test_fifo_snapshot_read_is_bounded_and_rejected(tmp_path):
    from local_ai_retrieval.source_io import read_source
    os.mkfifo(tmp_path / 'source.rs')
    def timeout(*_):
        raise TimeoutError('special file read blocked')
    previous = signal.signal(signal.SIGALRM, timeout)
    signal.alarm(2)
    try:
        with pytest.raises(ValueError, match='regular'):
            read_source(tmp_path, Path('source.rs'))
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def test_snapshot_read_never_follows_directory_symlinks(tmp_path):
    from local_ai_retrieval.source_io import read_source
    outside = tmp_path / 'outside'; outside.mkdir()
    (outside / 'file.rs').write_text('private')
    root = tmp_path / 'root'; root.mkdir()
    (root / 'linked').symlink_to(outside, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        read_source(root, Path('linked/file.rs'))


def test_snapshot_reader_pins_selected_directory_across_parent_replacement(tmp_path):
    from local_ai_retrieval.source_io import SnapshotReader
    catalog = tmp_path / 'catalog'
    selected = catalog / 'snapshots' / ('a' * 64)
    (selected / 'source').mkdir(parents=True)
    (selected / 'manifest.json').write_bytes(b'original manifest')
    (selected / 'source/file.rs').write_bytes(b'original bytes')
    with SnapshotReader(catalog, 'a' * 64) as reader:
        assert reader.read_manifest() == b'original manifest'
        selected.rename(selected.with_name('retained'))
        (selected / 'source').mkdir(parents=True)
        (selected / 'manifest.json').write_bytes(b'substituted manifest')
        (selected / 'source/file.rs').write_bytes(b'substituted bytes')
        assert reader.read_source(Path('file.rs')) == b'original bytes'


def test_snapshot_reader_rejects_symlink_at_snapshots_parent(tmp_path):
    from local_ai_retrieval.source_io import SnapshotReader
    catalog = tmp_path / 'catalog'; catalog.mkdir()
    outside = tmp_path / 'outside'; (outside / ('b' * 64)).mkdir(parents=True)
    (catalog / 'snapshots').symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        with SnapshotReader(catalog, 'b' * 64):
            pass
