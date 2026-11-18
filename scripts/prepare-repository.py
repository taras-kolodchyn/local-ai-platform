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
# First parent of the merge that repaired the demo in PR #14.
SMOKE_FIXTURE_COMMIT = '553a11afb886c01cd6d0d07cce6963a10534b8b3'


def sibling(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare_smoke_fixture(catalog):
    subtree = 'examples/rust-service'
    env = {'PATH': os.environ['PATH'], 'GIT_ALLOW_PROTOCOL': 'file',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_NO_LAZY_FETCH': '1'}
    with tempfile.TemporaryDirectory(prefix='rust-demo-') as tmp:
        checkout = Path(tmp)
        git = ['git', '-C', str(checkout), '-c', 'core.hooksPath=/dev/null']
        subprocess.run([*git, 'init', '--quiet', '--template='], env=env, check=True, timeout=60)
        subprocess.run([*git, 'fetch', '--quiet', '--no-tags', '--depth=1', str(ROOT), SMOKE_FIXTURE_COMMIT],
                       env=env, check=True, timeout=60)
        subprocess.run([*git, 'checkout', '--quiet', '--detach', SMOKE_FIXTURE_COMMIT],
                       env=env, check=True, timeout=60)
        manifest = sibling('repository-snapshot').prepare_snapshot(
            checkout / subtree, catalog, 'rust-retry', repository_id='repo_smoke_rust_retry')
    if manifest['git'] != {'head': SMOKE_FIXTURE_COMMIT, 'branch': 'detached', 'subtree': subtree, 'dirty': False}:
        raise ValueError('Rust smoke snapshot does not match the pinned clean subtree')
    return manifest


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def gateway_model_info():
    key = (ROOT / '.local/litellm-master-key').read_text().strip()
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
    result = subprocess.run(['docker', 'compose', '--project-name', 'local-ai-platform',
        '--file', str(ROOT / 'compose.yaml'), '--env-file', str(ROOT / '.env'),
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
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--repo', type=Path)
    source.add_argument('--smoke-fixture', action='store_true', help='Use the pinned local Rust demo commit')
    parser.add_argument('--profile', default='rust-retry', choices=['rust-retry'])
    parser.add_argument('--catalog', default=ROOT / '.local/repositories', type=Path,
        help='Custom catalogs are supported only with --no-index')
    parser.add_argument('--unversioned', action='store_true')
    parser.add_argument('--no-index', action='store_true', help='Prepare bytes only, without publishing a catalog selection')
    args = parser.parse_args(argv)
    if args.smoke_fixture and args.unversioned:
        parser.error('--smoke-fixture requires Git provenance')
    try:
        catalog = args.catalog.resolve()
        if not args.no_index and catalog != (ROOT / '.local/repositories').resolve():
            print('Live registration requires the default catalog mounted by the platform; custom catalogs require --no-index', file=sys.stderr)
            return 1
        if args.smoke_fixture:
            manifest = prepare_smoke_fixture(catalog)
        else:
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
