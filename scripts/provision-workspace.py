#!/usr/bin/env python3
"""Idempotent local gateway provisioning. Never prints credential values."""
import hashlib
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
    # Replacing an unchanged single-file bind mount invalidates its backing
    # inode in running Docker Desktop containers. Preserve it on reconciliation.
    if not path.is_symlink() and path.is_file() and path.read_text() == content:
        path.chmod(0o600)
        return
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
            with urllib.request.urlopen(req, timeout=65) as response:
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


def provision_skill_catalog(request, manifest):
    """Register upstream discovery references; runtime adaptations stay local."""
    existing = {item['name']: item for item in request('GET', '/claude-code/plugins')['plugins']}
    entries = manifest['skills'] + manifest.get('marketplace_only', [])
    bundled = {entry['name'] for entry in manifest['skills']}
    for entry in entries:
        upstream_name = entry['name']
        name = entry.get('catalog_name', upstream_name)
        path = 'skills/' + upstream_name
        source = {'source': 'git-subdir', 'url': manifest['source'] + '.git',
                  'path': path, 'sha': manifest['commit']}
        if upstream_name not in bundled:
            description = entry['description'] + ' Catalog installation retrieves the pinned upstream skill.'
        elif 'description' in entry:
            description = entry['description'] + ' Moyai includes an offline adaptation.'
        else:
            description = ('Pinned upstream workflow. Reviewed local adaptation installed for: '
                           + ', '.join(entry['profiles']) + '. Catalog installation retrieves upstream content.')
        keywords = ['local-workspace', 'reviewed-workflow' if upstream_name in bundled else 'upstream-workflow']
        if name in existing:
            current = existing[name]
            if current['source'] != source:
                raise ValueError('Existing skill source differs; review catalog entry: ' + name)
            # Promote only entries created by the previous marketplace-only provisioner.
            if (upstream_name in bundled and current.get('keywords') == ['local-workspace', 'upstream-workflow']
                    and (current.get('description') or '').endswith(' Catalog installation retrieves the pinned upstream skill.')):
                # LiteLLM PUT replaces the entire editable record. Keep the
                # operator's metadata, including version and installation settings.
                fields = ('source', 'version', 'author', 'homepage', 'category', 'domain', 'namespace',
                          'installation_preference')
                replacement = {key: current[key] for key in fields if key in current}
                request('PUT', '/claude-code/plugins/' + name, {**replacement, 'description': description, 'keywords': keywords})
            continue
        request('POST', '/claude-code/plugins', {
            'name': name, 'source': source, 'version': '1.0.0',
            'description': description,
            'author': {'name': manifest.get('author', 'Jesse Vincent')}, 'category': 'Development',
            'homepage': manifest['source'] + '/tree/' + manifest['commit'] + '/' + path,
            'keywords': keywords})
    return len(entries)


def skills(manifest_path='execution/skills/manifest.json'):
    manifest = json.loads((ROOT / manifest_path).read_text())
    admin = gateway((LOCAL / 'litellm-master-key').read_text().strip())
    count = provision_skill_catalog(admin, manifest)
    print(f"{count} workflow sources registered in the LiteLLM Skills catalog")


def owner_request(admin):
    teams = admin('GET', '/team/list')
    if isinstance(teams, dict):
        teams = teams.get('teams', teams.get('data', []))
    if not any(t.get('team_id') == TEAM for t in teams):
        admin('POST', '/team/new', {'team_id': TEAM, 'team_alias': 'Workspace knowledge',
                                 'models': ['local-qwen', 'local-embeddings']})
    existing_team = next((team for team in teams if team.get('team_id') == TEAM), {})
    permissions = {key: value for key, value in (existing_team.get('object_permission') or {}).items()
                   if key != 'object_permission_id'}
    permissions['mcp_servers'] = list(dict.fromkeys(
        (permissions.get('mcp_servers') or []) + ['local_retrieval', 'local_tools']))
    admin('POST', '/team/update', {'team_id': TEAM, 'object_permission': permissions})
    user_id = 'workspace-provisioning'
    try:
        admin('GET', '/user/info?user_id=' + user_id)
    except RuntimeError:
        admin('POST', '/user/new', {'user_id': user_id, 'user_role': 'proxy_admin', 'auto_create_key': False})
    key_path = LOCAL / 'workspace-owner-key'
    if key_path.exists():
        key = key_path.read_text().strip()
        try:
            info = admin('GET', '/key/info?key=' + hashlib.sha256(key.encode()).hexdigest())['info']
        except RuntimeError as exc:
            if not str(exc).endswith('returned 404'):
                raise
        else:
            if info.get('key_alias') != 'workspace-store-provisioning' or info.get('team_id') != TEAM:
                raise ValueError('Stored knowledge provisioning key has an unexpected owner')
            admin('POST', '/key/update', {'key': key, 'user_id': user_id})
            request = gateway(key)
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
    registry_path = LOCAL / 'workspace-registry.json'
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    registry['stores'] = mapping
    private_write(registry_path, json.dumps(registry, indent=2) + '\n')
    print(f'Registered {len(mapping)} knowledge stores')


if __name__ == '__main__':
    modes = {'vectors': vectors, 'skills': skills,
             'moyai-skills': lambda: skills('integrations/moyai/skills/catalog.json')}
    if len(sys.argv) != 2 or sys.argv[1] not in modes:
        raise SystemExit('Usage: provision-workspace.py vectors|skills|moyai-skills')
    modes[sys.argv[1]]()
