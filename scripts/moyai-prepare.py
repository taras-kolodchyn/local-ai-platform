#!/usr/bin/env python3
"""Create private local Moyai credentials and SSH identities; never print values."""
import json
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import sys
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / '.local/moyai'
sys.path.insert(0, str(ROOT / 'integrations/moyai'))
from local_config import valid_scoped_key


class ProvisionError(Exception):
    pass


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


def request(key, route, body=None):
    req = Request('http://127.0.0.1:4000' + route,
                  data=None if body is None else json.dumps(body).encode(),
                  headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    with build_opener(ProxyHandler({})).open(req, timeout=30) as response:
        return json.load(response)


def new_key_payload():
    return {
        'key_alias': 'moyai-local-mvp-' + uuid.uuid4().hex, 'models': ['local-qwen'],
        'allowed_routes': ['openai_routes', '/model/info'],
        'rpm_limit': 60, 'max_parallel_requests': 2,
        'object_permission': {'mcp_servers': [], 'vector_stores': []},
    }


def main():
    os.umask(0o077)
    LOCAL.mkdir(parents=True, exist_ok=True, mode=0o700)
    volumes = subprocess.run(['docker', 'volume', 'ls', '--format', '{{.Name}}'],
                             check=True, capture_output=True, text=True).stdout.splitlines()
    password = bootstrap_password(LOCAL, retained_data='local-ai-moyai_moyai_data' in volumes)

    key_file = LOCAL / 'litellm-key'
    key = key_file.read_text().strip() if key_file.exists() else ''
    master = (ROOT / '.local/litellm-master-key').read_text().strip()
    valid = False
    if key and key != master:
        try:
            info = request(master, '/key/info?key=' + hashlib.sha256(key.encode()).hexdigest())['info']
            valid = valid_scoped_key(key, master, info)
        except (HTTPError, URLError, OSError):
            pass
    if not valid:
        response = request(master, '/key/generate', new_key_payload())
        key = response['key']
        private_write(key_file, key + '\n')
    changed = private_write(LOCAL / 'runtime-config.json', json.dumps({'password': password, 'api_key': key}) + '\n')

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
        main()
    except ProvisionError as error:
        raise SystemExit(str(error)) from None
    except (HTTPError, URLError, OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError):
        raise SystemExit('Moyai provisioning failed; check the local gateway and private configuration') from None
