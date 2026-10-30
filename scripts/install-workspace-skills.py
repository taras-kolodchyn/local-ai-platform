#!/usr/bin/env python3
"""Install reviewed, hash-checked local skills without overwriting local edits."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def hashes(directory):
    result = {}
    for path in directory.rglob('*'):
        if path.is_symlink():
            raise ValueError('Symlinks are not allowed in skill bundles')
        if path.is_file():
            result[str(path.relative_to(directory))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def install_bundle(bundle: Path, home: Path) -> dict[str, str]:
    if bundle.is_symlink() or not bundle.is_dir() or not (bundle / 'SKILL.md').is_file():
        raise ValueError('Invalid bundle')
    expected = hashes(bundle)
    target = home / 'skills' / bundle.name
    if (home / 'skills').is_symlink() or target.is_symlink():
        raise ValueError('Symlink install destination')
    if target.exists():
        if hashes(target) != expected:
            return {'status': 'local_changes'}
        return {'status': 'unchanged'}
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='.install-', dir=target.parent))
    try:
        shutil.copytree(bundle, temp, dirs_exist_ok=True)
        os.replace(temp, target)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    return {'status': 'installed'}


def main():
    root = ROOT / 'execution/skills'
    manifest = json.loads((root / 'manifest.json').read_text())
    for entry in manifest['skills']:
        bundle = root / entry['name']
        if bundle.parent != root or hashes(bundle) != entry['files']:
            raise ValueError('Bundle integrity check failed')
        for profile in entry['profiles']:
            if profile not in ('development', 'review', 'research'):
                raise ValueError('Unknown profile')
            status = install_bundle(bundle, ROOT / '.local/profiles' / profile)['status']
            print(profile, entry['name'], status)


if __name__ == '__main__':
    main()
