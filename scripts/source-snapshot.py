#!/usr/bin/env python3
"""Publish a bounded source-only copy, excluding private paths and credentials."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

EXTENSIONS = {'.rs', '.py', '.sh', '.md', '.toml', '.yaml', '.yml', '.json', '.sql',
              '.txt', '.js', '.ts', '.tsx', '.jsx', '.go', '.c', '.h', '.cpp', '.swift', '.html', '.css'}
DENIED = {'secrets', 'credentials', 'node_modules', 'target', 'vendor', 'build', 'dist', '__pycache__'}
PATTERN = re.compile(r'-----BEGIN .*PRIVATE KEY-----|(?:ghp_|github_pat_|sk-)[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16}|(?:api[_-]?key|client[_-]?secret|access[_-]?token)\s*[:=]\s*[\x22\x27]?[A-Za-z0-9_./+=-]{20,}', re.I)


def snapshot(root, destination):
    root = root.resolve()
    names = subprocess.run(['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
        '-c', 'core.hooksPath=/dev/null', '-C', str(root), 'ls-files', '-co',
        '--exclude-standard', '-z'], capture_output=True, check=True).stdout.split(b'\0')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        stage = Path(temp)
        for raw in sorted(set(names)):
            if not raw:
                continue
            relative = Path(os.fsdecode(raw))
            if relative.is_absolute() or any(p.startswith('.') or p.lower() in DENIED for p in relative.parts):
                continue
            if relative.stem.lower() in DENIED:
                continue
            if relative.suffix not in EXTENSIONS and relative.name not in {'Makefile', 'Dockerfile', 'Containerfile'}:
                continue
            source = root / relative
            if any(p.is_symlink() for p in [source, *source.parents]) or not source.is_file() or source.stat().st_size > 1_000_000:
                continue
            try:
                content = source.read_text()
            except (UnicodeError, OSError):
                continue
            if '\0' in content or PATTERN.search(content):
                continue
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        destination.mkdir(exist_ok=True)
        # This directory contains generated copies only; keep its mount inode stable.
        for child in destination.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        shutil.copytree(stage, destination, dirs_exist_ok=True)


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    snapshot(Path(os.environ.get('HERMES_WORKSPACE_PATH') or root), root / '.local/source-snapshot')
    print('Filtered source snapshot prepared')
