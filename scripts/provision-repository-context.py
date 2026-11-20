#!/usr/bin/env python3
"""Register immutable stores and a read-only gateway credential; never print keys."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[1]
UNREGISTERED_ROUTE = '/__local_repository_context_unregistered__'
USER = 'local-repository-context'
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,199}\Z')


def missing_resource(error):
    return error.code == 404 if isinstance(error, HTTPError) else str(error) == 'Gateway returned 404'


def private_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_file() or path.read_text() != value:
        path.write_text(value)
    path.chmod(0o600)


def registrations(root, candidate=None):
    directory = root / '.local/repositories'
    path = directory / 'catalog.json'
    catalog = json.loads(path.read_text()) if path.exists() else {
        'schema_version': 1, 'repositories': [], 'indexed_generations': []}
    if catalog.get('schema_version') != 1:
        raise ValueError('Unsupported repository catalog')
    rows = list(catalog.get('indexed_generations', []))
    if candidate is not None and candidate not in rows:
        rows.append(candidate)
    if len(rows) > 1000:
        raise ValueError('Repository catalog limit exceeded')
    result = {}
    for row in rows:
        for key in ('repository_id', 'snapshot_id', 'generation_id', 'store_id'):
            if not isinstance(row.get(key), str) or not IDENTIFIER.fullmatch(row[key]):
                raise ValueError('Invalid repository registration')
        relative = 'snapshots/' + row['snapshot_id'] + '/manifest.json'
        if row.get('manifest') != relative:
            raise ValueError('Invalid manifest path')
        manifest_path = directory / relative
        if not manifest_path.resolve().is_relative_to(directory.resolve()):
            raise ValueError('Manifest escapes repository catalog')
        manifest = json.loads(manifest_path.read_text())
        if (manifest.get('repository_id'), manifest.get('snapshot_id')) != (
                row['repository_id'], row['snapshot_id']):
            raise ValueError('Manifest identity differs from registration')
        enriched = row | {'branch': manifest['git']['branch']}
        previous = result.get(row['generation_id'])
        if previous is not None and previous != enriched:
            raise ValueError('Immutable generation registration changed')
        result[row['generation_id']] = enriched
    return list(result.values())


def provision_context(admin, root=ROOT, registration=None):
    root = Path(root)
    directory = root / '.local/repositories'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.publish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _provision_context_locked(admin, root, registration)


def _provision_context_locked(admin, root, registration):
    rows = registrations(root, registration)
    stores = sorted({row['store_id'] for row in rows})
    local = root / '.local/moyai'
    token_file = local / 'context-token'
    token = token_file.read_text().strip() if token_file.exists() else secrets.token_urlsafe(32)
    if len(token) < 32:
        raise ValueError('Repository context token is invalid')
    private_write(token_file, token + '\n')
    existing = {item['vector_store_id']: item for item in admin('GET', '/vector_store/list')['data']}
    backend_key = (root / '.local/vector-backend-key').read_text().strip()
    for row in rows:
        if row['store_id'] not in existing:
            admin('POST', '/vector_store/new', {'vector_store_id': row['store_id'],
                'custom_llm_provider': 'pg_vector',
                'vector_store_name': row['name'] + ' / ' + row['snapshot_id'][:12],
                'vector_store_description': 'Immutable local repository source snapshot',
                'litellm_params': {'api_base': 'http://retrieval:8000', 'api_key': backend_key}})
        else:
            provider = existing[row['store_id']].get('custom_llm_provider')
            if provider is not None and provider != 'pg_vector':
                raise ValueError('Repository store has an unexpected provider')
    try:
        user = admin('GET', '/user/info?user_id=' + USER)
        info = user.get('user_info', user)
        if info.get('user_role') not in (None, 'internal_user'):
            raise ValueError('Repository context owner has unexpected authority')
    except (HTTPError, RuntimeError) as exc:
        if not missing_resource(exc):
            raise
        admin('POST', '/user/new', {'user_id': USER, 'user_role': 'internal_user',
                                  'auto_create_key': False})
    key_file = local / 'context-gateway-key'
    key = key_file.read_text().strip() if key_file.exists() else ''
    info = {}
    if key:
        try:
            info = admin('GET', '/key/info?key=' + hashlib.sha256(key.encode()).hexdigest())['info']
        except (HTTPError, RuntimeError) as exc:
            if not missing_resource(exc):
                raise
    payload = {'user_id': USER, 'models': ['local-embeddings'],
        'allowed_routes': ['/v1/vector_stores/' + store + '/search' for store in stores] or [UNREGISTERED_ROUTE],
        'rpm_limit': 120, 'max_parallel_requests': 2,
        'object_permission': {'vector_stores': stores, 'mcp_servers': [], 'agents': []}}
    if (not re.fullmatch(r'repository-context-[0-9a-f]{32}', info.get('key_alias', ''))
            or info.get('blocked') or info.get('status') not in (None, 'active')
            or info.get('user_id') != USER):
        key = admin('POST', '/key/generate', payload | {
            'key_alias': 'repository-context-' + uuid.uuid4().hex})['key']
    else:
        admin('POST', '/key/update', payload | {'key': key})
    private_write(key_file, key + '\n')
    return token


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registration', type=Path)
    args = parser.parse_args()
    master = subprocess.check_output(['bash', str(ROOT / 'scripts/read-admin-key.sh')], text=True).strip()
    opener = build_opener(ProxyHandler({}), NoRedirects())

    def admin(method, route, body=None):
        request = Request('http://127.0.0.1:4000' + route,
            data=None if body is None else json.dumps(body).encode(), method=method,
            headers={'Authorization': 'Bearer ' + master, 'Content-Type': 'application/json'})
        try:
            with opener.open(request, timeout=60) as response:
                return json.load(response)
        except HTTPError as exc:
            raise RuntimeError('Gateway returned ' + str(exc.code)) from None

    if args.registration:
        # register_catalog holds .publish.lock through this callback and its atomic rename.
        _provision_context_locked(admin, ROOT, json.loads(args.registration.read_text()))
    else:
        provision_context(admin, ROOT)
    print('Repository context stores and restricted credential reconciled')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print('Repository context provisioning failed: ' + type(exc).__name__, file=sys.stderr)
        sys.exit(1)
