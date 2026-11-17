"""Descriptor-anchored, bounded regular-file reads under a trusted catalog."""
from contextlib import ExitStack
import os
from pathlib import Path
import re
import stat


def _read_relative(anchor: int, relative: Path, max_bytes: int) -> bytes:
    if relative.is_absolute() or not relative.parts or any(part in ('.', '..') for part in relative.parts):
        raise ValueError('Unsafe snapshot source path')
    directory = os.dup(anchor)
    try:
        for component in relative.parts[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError('Snapshot source is not a regular file')
            if info.st_size > max_bytes:
                raise ValueError('Snapshot source exceeds file bounds')
            raw = stream.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise ValueError('Snapshot source exceeds file bounds')
            return raw
    finally:
        os.close(directory)


def read_source(root: Path, relative: Path, max_bytes=512 * 1024) -> bytes:
    """Read below an explicit trusted anchor; never trust a nested root pathname."""
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return _read_relative(descriptor, relative, max_bytes)
    finally:
        os.close(descriptor)


class SnapshotReader:
    """Keep one catalog -> snapshots -> selected ID authority for all reads."""
    def __init__(self, catalog: Path, snapshot_id: str):
        if not re.fullmatch(r'[0-9a-f]{64}', snapshot_id):
            raise ValueError('Invalid selected snapshot ID')
        self.catalog = Path(catalog)
        self.snapshot_id = snapshot_id
        self.stack = ExitStack()

    def __enter__(self):
        try:
            descriptor = os.open(self.catalog, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self.stack.callback(os.close, descriptor)
            for component in ('snapshots', self.snapshot_id):
                descriptor = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                self.stack.callback(os.close, descriptor)
            self.descriptor = descriptor
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *exception):
        return self.stack.__exit__(*exception)

    def read_manifest(self):
        return _read_relative(self.descriptor, Path('manifest.json'), 1024 * 1024)

    def read_source(self, relative: Path):
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe snapshot source path')
        return _read_relative(self.descriptor, Path('source') / relative, 512 * 1024)
