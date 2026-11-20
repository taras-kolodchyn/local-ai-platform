#!/usr/bin/env python3
"""Create private local Moyai credentials and SSH identities; never print values."""
import argparse
import json
import hashlib
import os
from pathlib import Path
import runpy
import secrets
import subprocess
import sys
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local/moyai'
sys.path.insert(0, str(ROOT / 'integrations/moyai'))
from local_config import INFERENCE_ROUTES, valid_scoped_key

MAX_PARALLEL_REQUESTS = 10


class ProvisionError(Exception):
    pass


def master_key():
    return subprocess.check_output(['bash', str(ROOT / 'scripts/read-admin-key.sh')], text=True).strip()


def private_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_file() and path.read_text() == value:
        path.chmod(0o600)
        return False
    path.write_text(value)
    path.chmod(0o600)
    return True


def bootstrap_password(local, *, retained_data):
    password_file = local / 'admin-password'
    config_file = local / 'runtime-config.json'
    previous = json.loads(config_file.read_text()).get('password') if config_file.exists() else None
    if password_file.exists():
        password = password_file.read_text().strip()
        if previous and password != previous:
            raise ProvisionError('admin-password is bootstrap-only; restore the saved file instead of resetting an existing account this way')
    elif previous:
        password = previous
        private_write(password_file, password + '\n')
    elif retained_data:
        raise ProvisionError('Retained Moyai data exists; restore admin-password or runtime-config.json from backup before starting')
    else:
        password = secrets.token_urlsafe(24)
        private_write(password_file, password + '\n')
    if len(password) < 12:
        raise ProvisionError('Moyai admin-password must contain at least 12 characters')
    return password


def request(key, route, body=None, *, method=None):
    req = Request('http://127.0.0.1:4000' + route,
                  data=None if body is None else json.dumps(body).encode(),
                  headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
                  method=method)
    with build_opener(ProxyHandler({})).open(req, timeout=30) as response:
        return json.load(response)


def check_ui(key):
    if request(key, '/get/ui_settings')['values'].get('moyai_url') != 'http://127.0.0.1:8787':
        raise ProvisionError('LiteLLM Moyai UI link is not local; run make up to reconnect it')


def connect_ui(key):
    if request(key, '/get/ui_settings')['values'].get('moyai_url') != 'http://127.0.0.1:8787':
        request(key, '/update/ui_settings', {'moyai_url': 'http://127.0.0.1:8787'}, method='PATCH')
    check_ui(key)


def new_key_payload():
    return {
        'key_alias': 'moyai-local-mvp-' + uuid.uuid4().hex, 'models': ['local-qwen'],
        'allowed_routes': list(INFERENCE_ROUTES),
        'rpm_limit': 60, 'max_parallel_requests': MAX_PARALLEL_REQUESTS,
        'object_permission': {'mcp_servers': [], 'vector_stores': []},
    }


def prepare_key(master, key):
    if not key or key == master:
        return request(master, '/key/generate', new_key_payload())['key']

    info_route = '/key/info?key=' + hashlib.sha256(key.encode()).hexdigest()
    try:
        info = request(master, info_route)['info']
    except (HTTPError, URLError, OSError):
        info = {}

    changes = {}
    if set(info.get('allowed_routes') or []) == {'openai_routes', '/model/info'}:
        changes['allowed_routes'] = list(INFERENCE_ROUTES)
    if not valid_scoped_key(key, master, info | changes):
        return request(master, '/key/generate', new_key_payload())['key']

    if info.get('max_parallel_requests') != MAX_PARALLEL_REQUESTS:
        changes['max_parallel_requests'] = MAX_PARALLEL_REQUESTS
    if changes:
        request(master, '/key/update', {'key': key, **changes})
        updated = request(master, info_route)['info']
        if not valid_scoped_key(key, master, updated) or updated.get('max_parallel_requests') != MAX_PARALLEL_REQUESTS:
            raise ProvisionError('LiteLLM did not apply the expected Moyai key settings')
    return key


def main():
    os.umask(0o077)
    LOCAL.mkdir(parents=True, exist_ok=True, mode=0o700)
    volumes = subprocess.run(['docker', 'volume', 'ls', '--format', '{{.Name}}'],
                             check=True, capture_output=True, text=True).stdout.splitlines()
    password = bootstrap_password(LOCAL, retained_data='local-ai-moyai_moyai_data' in volumes)

    key_file = LOCAL / 'litellm-key'
    key = key_file.read_text().strip() if key_file.exists() else ''
    master = master_key()
    key = prepare_key(master,key)
    private_write(key_file, key + '\n')
    context = runpy.run_path(str(ROOT / 'scripts/provision-repository-context.py'))
    context_token = context['provision_context'](
        lambda method, route, body=None: request(master, route, body, method=method), ROOT)
    changed = private_write(LOCAL / 'runtime-config.json', json.dumps({
        'password': password, 'api_key': key, 'repository_context_token': context_token}) + '\n')

    ssh = LOCAL / 'ssh'
    ssh.mkdir(exist_ok=True, mode=0o700)
    for name in ('id_ed25519', 'host_ed25519'):
        path = ssh / name
        if not path.exists():
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(path)], check=True)
        path.chmod(0o600)
        path.with_suffix('.pub').chmod(0o644)
    host_key = (ssh / 'host_ed25519.pub').read_text().split()[:2]
    private_write(ssh / 'known_hosts', 'moyai-sandbox ' + ' '.join(host_key) + '\n')
    print('changed' if changed else 'unchanged')


if __name__ == '__main__':
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--ui', choices=('connect', 'check'), help='Configure or verify the local LiteLLM UI link')
        args = parser.parse_args()
        if args.ui:
            master = master_key()
            (connect_ui if args.ui == 'connect' else check_ui)(master)
            print('LiteLLM Moyai UI link verified: http://127.0.0.1:8787')
        else:
            main()
    except ProvisionError as error:
        raise SystemExit(str(error)) from None
    except (HTTPError, URLError, OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError):
        raise SystemExit('Moyai provisioning failed; check the local gateway and private configuration') from None
