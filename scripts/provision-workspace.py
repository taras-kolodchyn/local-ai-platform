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
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def gateway(key, execution=False):
    def request(method, route, body=None):
        req = urllib.request.Request('http://127.0.0.1:4000' + route,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                     'User-Agent': 'Platform Provisioning', **({'X-Workspace-Key': key} if execution else {})}, method=method)
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
    for entry in manifest['skills']:
        name = entry['name']
        source = {'source': 'git-subdir', 'url': manifest['source'] + '.git',
                  'path': 'skills/' + name, 'sha': manifest['commit']}
        if name in existing:
            if existing[name]['source'] != source:
                raise ValueError('Existing skill source differs; review catalog entry: ' + name)
            continue
        request('POST', '/claude-code/plugins', {
            'name': name, 'source': source, 'version': '1.0.0',
            'description': 'Pinned upstream workflow. Reviewed local adaptation installed for: '
                           + ', '.join(entry['profiles']) + '. Catalog installation retrieves upstream content.',
            'author': {'name': 'Jesse Vincent'}, 'category': 'Development',
            'homepage': manifest['source'] + '/tree/' + manifest['commit'] + '/skills/' + name,
            'keywords': ['local-workspace', 'reviewed-workflow']})


def skills():
    manifest = json.loads((ROOT / 'execution/skills/manifest.json').read_text())
    admin = gateway((LOCAL / 'litellm-master-key').read_text().strip())
    provision_skill_catalog(admin, manifest)
    print('Three workflow sources registered in dashboard catalog')


def owner_request(admin):
    teams = admin('GET', '/team/list')
    if isinstance(teams, dict):
        teams = teams.get('teams', teams.get('data', []))
    if not any(t.get('team_id') == TEAM for t in teams):
        admin('POST', '/team/new', {'team_id': TEAM, 'team_alias': 'Workspace knowledge',
                                 'models': ['local-qwen', 'local-embeddings']})
    registry_path = LOCAL / 'workspace-registry.json'
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    admin('POST', '/team/update', {'team_id': TEAM, 'object_permission': {
        'mcp_servers': ['local_retrieval', 'local_tools'],
        'agents': list(registry.get('routes', {}).values())}})
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
    registry_path = LOCAL / 'workspace-registry.json'
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    registry['stores'] = mapping
    private_write(registry_path, json.dumps(registry, indent=2) + '\n')
    print(f'Registered {len(mapping)} knowledge stores')


def profiles():
    import secrets
    sys.path.insert(0, str(ROOT / 'execution/src'))
    from workspace_runtime.profiles import PROFILES, build_profile_config
    admin = gateway((LOCAL / 'litellm-master-key').read_text().strip())
    owner_request(admin)
    passwords_path = LOCAL / 'memory-passwords.json'
    if not passwords_path.exists():
        private_write(passwords_path, json.dumps({p: secrets.token_hex(24) for p in PROFILES}))
    passwords = json.loads(passwords_path.read_text())
    run = subprocess.run(['docker', 'compose', 'exec', '-T', 'retrieval', 'python', '-c',
        (ROOT / 'postgres/provision-memory.py').read_text()], input=json.dumps(passwords),
        text=True, capture_output=True, cwd=ROOT)
    if run.returncode:
        raise RuntimeError('Memory provisioning failed; check database availability and grants')
    print(run.stdout.strip())
    stores = list(json.loads((LOCAL / 'workspace-registry.json').read_text())['stores'].values())
    for profile in PROFILES:
        home = LOCAL / 'profiles' / profile
        home.mkdir(parents=True, exist_ok=True)
        home.chmod(0o700)
        key_file = home / 'gateway.key'
        if not key_file.exists():
            response = admin('POST', '/key/generate', {
                'team_id': TEAM, 'key_alias': 'workspace-' + profile,
                'models': ['local-qwen', 'local-embeddings'], 'rpm_limit': 120,
                'object_permission': {'vector_stores': stores,
                    'mcp_servers': ['local_retrieval', 'local_tools'],
                    'mcp_tool_permissions': {
                        'local_retrieval': ['search_code', 'get_chunk'],
                        'local_tools': ['list_files', 'read_text_file', 'git_status', 'git_log', 'postgres_select']}},
                'metadata': {'allowed_vector_store_indexes': [
                    {'index_name': sid, 'index_permissions': ['read']} for sid in stores]}})
            private_write(key_file, response['key'])
        key = key_file.read_text().strip()
        admin('POST', '/key/update', {'key': key,
            'object_permission': {'vector_stores': stores,
                'mcp_servers': ['local_retrieval', 'local_tools'],
                'mcp_tool_permissions': {
                    'local_retrieval': ['search_code', 'get_chunk'],
                    'local_tools': ['list_files', 'read_text_file', 'git_status', 'git_log', 'postgres_select']}},
            'metadata': {'allowed_vector_store_indexes': [
                {'index_name': sid, 'index_permissions': ['read']} for sid in stores]}})
        gateway(key)('GET', '/v1/models')
        config = build_profile_config(profile, {'gateway_key': key, 'database_password': passwords[profile]})
        for name, data in [('config.yaml', config['config']), ('mem0.json', config['mem0'])]:
            target = home / name
            if not target.exists():
                private_write(target, json.dumps(data, indent=2) + '\n')
        if not (home / 'backend.key').exists():
            private_write(home / 'backend.key', secrets.token_hex(32))
        (home / 'scratch').mkdir(exist_ok=True)
        private_write(home / '.env', 'LITELLM_API_KEY=' + key + '\n')
    (LOCAL / 'execution').mkdir(exist_ok=True)
    private_write(LOCAL / 'workspace-identity.env',
                  f'WORKSPACE_UID={os.getuid()}\nWORKSPACE_GID={os.getgid()}\n')
    console = LOCAL / 'workspace-client-key'
    if not console.exists():
        created = admin('POST', '/key/generate', {'team_id': TEAM, 'key_alias': 'workspace-console',
                         'models': ['local-qwen', 'local-embeddings']})
        private_write(console, created['key'])
    for profile in PROFILES:
        private_write(LOCAL / 'profiles' / profile / 'caller.sha256', hashlib.sha256(console.read_text().strip().encode()).hexdigest())
    for profile in PROFILES:
        home = LOCAL / 'profiles' / profile
        (home / 'caller.key').unlink(missing_ok=True)
        script = ('if [ ! -f /opt/data/.initialized ]; then cp -a /seed/. /opt/data/; '
                  'touch /opt/data/.initialized; fi; rm -f /opt/data/caller.key; chown -R '
                  + str(os.getuid()) + ':' + str(os.getgid()) + ' /opt/data')
        subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', '/bin/sh',
            '-v', 'workspace_' + profile + '_state:/opt/data',
            '-v', str(home.resolve()) + ':/seed:ro', 'workspace-runtime:0.1.0', '-ec', script], check=True)
    print('Three isolated profiles configured')


def routes():
    sys.path.insert(0, str(ROOT / 'execution/src'))
    from workspace_runtime.protocol import card
    admin = gateway((LOCAL / 'litellm-master-key').read_text().strip())
    existing = admin('GET', '/v1/agents')
    if isinstance(existing, dict):
        existing = existing['agents']
    mapping = {r['agent_name']: r['agent_id'] for r in existing}
    registered = {}
    for profile in ('development', 'review', 'research'):
        name = 'workspace-' + profile
        if name not in mapping:
            response = admin('POST', '/v1/agents', {
                'agent_name': name, 'agent_card_params': card(profile),
                'litellm_params': {'make_public': False},
                'static_headers': {'Authorization': 'Bearer ' +
                    (LOCAL / 'profiles' / profile / 'backend.key').read_text().strip()}})
            mapping[name] = response['agent_id']
        registered[profile] = mapping[name]
    registry_path = LOCAL / 'workspace-registry.json'
    registry = json.loads(registry_path.read_text())
    registry['routes'] = registered
    private_write(registry_path, json.dumps(registry, indent=2) + '\n')
    owner_request(admin)
    client_file = LOCAL / 'workspace-client-key'
    if not client_file.exists():
        value = admin('POST', '/key/generate', {'team_id': TEAM, 'key_alias': 'workspace-console',
            'models': ['local-qwen', 'local-embeddings'],
            'object_permission': {'agents': list(registered.values()), 'vector_stores': list(registry['stores'].values())}})
        private_write(client_file, value['key'])
    admin('POST', '/key/update', {'key': client_file.read_text().strip(),
        'object_permission': {'agents': list(registered.values()), 'vector_stores': list(registry['stores'].values())},
        'metadata': {'allowed_vector_store_indexes': [
            {'index_name': sid, 'index_permissions': ['read']} for sid in registry['stores'].values()]}})
    for profile, route_id in registered.items():
        admin('POST', '/key/update', {
            'key': (LOCAL / 'profiles' / profile / 'gateway.key').read_text().strip(),
            'agent_id': route_id})
        private_write(LOCAL / 'profiles' / profile / 'caller.sha256', hashlib.sha256(client_file.read_text().strip().encode()).hexdigest())
        admin('PATCH', '/v1/agents/' + route_id, {'extra_headers': ['X-Workspace-Key']})
    print('Three execution routes registered')


if __name__ == '__main__':
    modes = {'vectors': vectors, 'profiles': profiles, 'routes': routes, 'skills': skills}
    if len(sys.argv) != 2 or sys.argv[1] not in modes:
        raise SystemExit('Usage: provision-workspace.py vectors|profiles|routes|skills')
    modes[sys.argv[1]]()
