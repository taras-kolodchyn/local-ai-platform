#!/usr/bin/env python3
"""Idempotent local gateway provisioning. Never prints credential values."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local'
TEAM = 'workspace-knowledge'


def private_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def gateway(key):
    def request(method, route, body=None):
        req = urllib.request.Request('http://127.0.0.1:4000' + route,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                     'User-Agent': 'Platform Provisioning'}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'Gateway {method} {route.split("?")[0]} returned {exc.code}') from None
    return request


def provision_vectors(request, stores, backend_key):
    existing = {r['vector_store_id']: r for r in request('GET', '/vector_store/list')['data']}
    result = {}
    for store in stores:
        sid = store['id']
        if sid not in existing:
            request('POST', '/vector_store/new', {
                'vector_store_id': sid, 'custom_llm_provider': 'pg_vector',
                'vector_store_name': store['repository'] + ' / ' + store['branch'],
                'vector_store_description': 'Local indexed source knowledge',
                'litellm_params': {'api_base': 'http://retrieval:8000', 'api_key': backend_key}})
        result[store['repository'] + ':' + store['branch']] = sid
    return result


def owner_request(admin):
    teams = admin('GET', '/team/list')
    if isinstance(teams, dict):
        teams = teams.get('teams', teams.get('data', []))
    if not any(t.get('team_id') == TEAM for t in teams):
        admin('POST', '/team/new', {'team_id': TEAM, 'team_alias': 'Workspace knowledge',
                                 'models': ['local-qwen', 'local-embeddings']})
    user_id = 'workspace-provisioning'
    try:
        admin('GET', '/user/info?user_id=' + user_id)
    except RuntimeError:
        admin('POST', '/user/new', {'user_id': user_id, 'user_role': 'proxy_admin', 'auto_create_key': False})
    key_path = LOCAL / 'workspace-owner-key'
    if key_path.exists():
        admin('POST', '/key/update', {'key': key_path.read_text().strip(), 'user_id': user_id})
        request = gateway(key_path.read_text().strip())
        request('GET', '/v1/models')
        return request
    value = admin('POST', '/key/generate', {'team_id': TEAM, 'user_id': user_id,
        'key_alias': 'workspace-store-provisioning', 'models': ['local-qwen', 'local-embeddings']})
    private_write(key_path, value['key'])
    return gateway(value['key'])


def vectors():
    admin = gateway((LOCAL / 'litellm-master-key').read_text().strip())
    owner = owner_request(admin)
    code = '''import json, psycopg
from local_ai_retrieval.config import Settings
from local_ai_retrieval.vector_registry import register_store
s=Settings.from_env()
with psycopg.connect(s.database_url) as c:
 rows=c.execute("SELECT DISTINCT repository, branch FROM source_chunks ORDER BY repository, branch").fetchall()
print(json.dumps([register_store(s,*row) for row in rows]))
'''
    run = subprocess.run(['docker', 'compose', 'exec', '-T', 'retrieval', 'python', '-c', code],
                         cwd=ROOT, capture_output=True, text=True, check=True)
    mapping = provision_vectors(owner, json.loads(run.stdout), (LOCAL / 'vector-backend-key').read_text().strip())
    private_write(LOCAL / 'workspace-registry.json', json.dumps({'stores': mapping}, indent=2) + '\n')
    print(f'Registered {len(mapping)} knowledge stores')


if __name__ == '__main__':
    if sys.argv[1:] == ['vectors']:
        vectors()
    else:
        raise SystemExit('Usage: provision-workspace.py vectors')
