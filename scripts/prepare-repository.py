#!/usr/bin/env python3
"""Operator-only source preparation; registered tasks never receive host paths."""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]


def sibling(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare_smoke_source(repo, catalog):
    return sibling('repository-snapshot').prepare_snapshot(repo, catalog, 'rust-retry', require_clean=True)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def gateway_model_info():
    key = subprocess.check_output(['bash', str(ROOT / 'scripts/read-admin-key.sh')], text=True).strip()
    opener = build_opener(ProxyHandler({}), NoRedirects())
    request = Request('http://127.0.0.1:4000/model/info',
        headers={'Authorization': 'Bearer ' + key})
    with opener.open(request, timeout=30) as response:
        return json.load(response)


def embedding_identity():
    configuration = sibling('model-config').resolve(ROOT)
    sibling('check-model-bindings').verify_bindings(configuration, gateway_model_info())
    model = next(m for m in configuration['models'] if m['alias'] == 'local-embeddings')
    result = subprocess.run(['docker', 'model', 'inspect', model['reference']],
        check=True, capture_output=True, text=True, timeout=30)
    artifact = json.loads(result.stdout).get('id', '')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', artifact):
        raise ValueError('Embedding model inspection did not provide an immutable artifact digest')
    return artifact


def validate_snapshot_profile(catalog, manifest):
    path = ROOT / 'retrieval/src/local_ai_retrieval/profiles.py'
    spec = importlib.util.spec_from_file_location('repository_profile', path)
    profile = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(profile)
    source = catalog / 'snapshots' / manifest['snapshot_id'] / 'source'
    reader = sibling('repository-snapshot').read_source
    profile.validate_profile(manifest, lambda name: reader(source, Path(name)))


def index_prepared(catalog, manifest, artifact=None):
    # Serialize the entire successful DB activation -> gateway -> current catalog
    # sequence for a logical repository; catalog publication uses a separate lock.
    with (catalog / ('.registration-' + manifest['repository_id'] + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        validate_snapshot_profile(catalog, manifest)
        return _index_prepared_locked(catalog, manifest, artifact or embedding_identity())


def _index_prepared_locked(catalog, manifest, artifact):
    snapshot = catalog / 'snapshots' / manifest['snapshot_id']
    environment = {k: v for k, v in os.environ.items() if not k.startswith('COMPOSE_')}
    environment['INDEX_REPO_PATH'] = str(snapshot / 'source')
    # Never inherit an operator's alternate Compose file/project or mount the host checkout.
    result = subprocess.run(['bash', str(ROOT / 'scripts/compose.sh'),
        '--profile', 'tools', 'run', '--rm', '--no-deps', '--volume',
        str(catalog) + ':/run/local-repositories:ro', 'ingestion', 'index-snapshot',
        f"/run/local-repositories/snapshots/{manifest['snapshot_id']}/manifest.json",
        '--embedding-artifact-id', artifact], cwd=ROOT, env=environment,
        check=True, capture_output=True, text=True, timeout=1800)
    indexed = json.loads(result.stdout)
    def provision(row):
        pending = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', prefix='.registration-', suffix='.json', dir=catalog, delete=False) as stream:
                pending = Path(stream.name)
                json.dump(row, stream)
            subprocess.run([sys.executable, str(ROOT / 'scripts/provision-repository-context.py'),
                '--registration', str(pending)], cwd=ROOT, env=environment, check=True,
                capture_output=True, text=True, timeout=120)
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)
    return sibling('repository-snapshot').register_catalog(catalog, manifest, indexed, before_publish=provision)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True,
                        help='Local source directory for the selected verification profile')
    parser.add_argument('--profile', default='rust-retry', choices=('rust-retry', 'rust-retry-tests'))
    parser.add_argument('--catalog', default=ROOT / '.local/repositories', type=Path,
        help='Custom catalogs are supported only with --no-index')
    parser.add_argument('--unversioned', action='store_true')
    parser.add_argument('--no-index', action='store_true', help='Prepare bytes only, without publishing a catalog selection')
    args = parser.parse_args(argv)
    try:
        catalog = args.catalog.resolve()
        if not args.no_index and catalog != (ROOT / '.local/repositories').resolve():
            print('Live registration requires the default catalog mounted by the platform; custom catalogs require --no-index', file=sys.stderr)
            return 1
        manifest = sibling('repository-snapshot').prepare_snapshot(args.repo, catalog,
            args.profile, unversioned=args.unversioned)
        row = manifest if args.no_index else index_prepared(catalog, manifest)
        print(json.dumps(row, sort_keys=True))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # Do not print captured backend output (it may contain source or credentials).
        print(f'Repository preparation failed: {type(exc).__name__}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
