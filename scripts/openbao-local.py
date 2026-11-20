"""Manage the single-node Compose OpenBao on an operator's Mac."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

STATE = Path('.local/openbao-local')
TLS = STATE / 'tls'
AUTH = STATE / 'auth'
OPERATOR = STATE / 'operator'
PENDING = STATE / 'bootstrap.json'
INSTANCE = STATE / 'instance'
KEYCHAIN = 'local-ai-platform.openbao.unseal'
KV = 'secret/data/litellm/UI_PASSWORD'


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def save(path, contents):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as staging:
        staging.write(contents.encode())
        staging.flush()
        os.fsync(staging.fileno())
    os.replace(staging.name, path)


def prepare_tls():
    if TLS.exists():
        return
    with tempfile.TemporaryDirectory(dir=STATE) as folder:
        pki = Path(folder)
        published = pki / 'tls'
        published.mkdir()
        config = pki / 'openssl.cnf'
        config.write_text("""[req]
distinguished_name = dn
[dn]
[ca]
basicConstraints = critical,CA:TRUE,pathlen:0
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
[server]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:openbao,IP:127.0.0.1
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
""")
        commands = (
            ['req', '-new', '-x509', '-newkey', 'rsa:3072', '-nodes', '-days', '3650',
             '-subj', '/CN=Local AI OpenBao CA', '-keyout', 'ca.key', '-out', 'tls/ca.pem',
             '-config', 'openssl.cnf', '-extensions', 'ca'],
            ['req', '-new', '-newkey', 'rsa:3072', '-nodes', '-subj', '/CN=openbao',
             '-keyout', 'tls/server.key', '-out', 'server.csr', '-config', 'openssl.cnf'],
            ['x509', '-req', '-in', 'server.csr', '-CA', 'tls/ca.pem', '-CAkey', 'ca.key',
             '-set_serial', str(secrets.randbits(120) or 1), '-days', '825', '-out', 'tls/server.pem',
             '-extfile', 'openssl.cnf', '-extensions', 'server'],
        )
        for args in commands:
            subprocess.run(['openssl', *args], cwd=pki, capture_output=True, check=True)
        published.rename(TLS)


def api_client():
    context = ssl.create_default_context(cafile=str(TLS / 'ca.pem'))
    return build_opener(ProxyHandler({}), NoRedirects(), HTTPSHandler(context=context))


def api(client, path, body=None, token=None, method=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-Vault-Token'] = token
    request = Request('https://127.0.0.1:8200/v1/' + path,
                      data=None if body is None else json.dumps(body).encode(),
                      headers=headers, method=method)
    with client.open(request, timeout=10) as response:
        data = response.read()
        return json.loads(data) if data else {}


def keychain_share(share=None):
    account = INSTANCE.read_text().strip()
    if share:
        # The interactive CLI accepts this generated base64 value over a pipe.
        # Read it back before discarding the bootstrap response.
        if not all(c.isalnum() or c in '+/=' for c in share):
            raise ValueError('Unexpected unseal share encoding')
        command = f'add-generic-password -U -s {KEYCHAIN} -a {account} -w {share}\n'
        subprocess.run(['/usr/bin/security', '-i'], input=command, text=True,
                       capture_output=True, check=True)
    result = subprocess.run(['/usr/bin/security', 'find-generic-password', '-s', KEYCHAIN,
                             '-a', account, '-w'], capture_output=True, text=True, check=True)
    stored = result.stdout.strip()
    if not stored or (share and stored != share):
        raise RuntimeError('Could not verify the unseal share in macOS Keychain')
    return stored


def login(client, directory):
    result = api(client, 'auth/approle/login', {
        'role_id': (directory / 'role-id').read_text(),
        'secret_id': (directory / 'secret-id').read_text(),
    })
    return result['auth']['client_token']


def configure_role(client, root_token, directory, name, capabilities):
    policy = f'path "{KV}" {{ capabilities = {json.dumps(capabilities)} }}'
    api(client, 'sys/policies/acl/' + name, {'policy': policy}, root_token)
    role = 'auth/approle/role/' + name
    api(client, role, {'token_policies': [name], 'token_ttl': '20m', 'token_max_ttl': '1h'},
        root_token)
    directory.mkdir(exist_ok=True)
    if not (directory / 'secret-id').exists():
        save(directory / 'role-id', api(client, role + '/role-id', token=root_token)['data']['role_id'])
        save(directory / 'secret-id', api(client, role + '/secret-id', {}, root_token)['data']['secret_id'])


def provision(client, bootstrap):
    token = bootstrap['root_token']
    # Restartable when interrupted after a mount or a credential was created.
    if 'secret/' not in api(client, 'sys/mounts', token=token)['data']:
        api(client, 'sys/mounts/secret', {'type': 'kv', 'options': {'version': '2'}}, token)
    if 'approle/' not in api(client, 'sys/auth', token=token)['data']:
        api(client, 'sys/auth/approle', {'type': 'approle'}, token)
    configure_role(client, token, AUTH, 'litellm-ui', ['read'])
    configure_role(client, token, OPERATOR, 'litellm-ui-rotate', ['read', 'create', 'update'])
    try:
        api(client, KV, token=token)
    except HTTPError as error:
        if error.code != 404:
            raise
        write_password(client, token, 0)
    save(AUTH / 'ca.pem', (TLS / 'ca.pem').read_text())


def write_password(client, token, version):
    result = api(client, KV, {'options': {'cas': version},
                              'data': {'key': secrets.token_urlsafe(32)}}, token)
    return result['data']['version']


def start():
    if not INSTANCE.exists():
        save(INSTANCE, str(uuid4()))
    (STATE / 'data').mkdir(exist_ok=True)
    prepare_tls()
    client = api_client()
    subprocess.run(['docker', 'compose', 'up', '-d', '--no-deps', 'openbao'], check=True)
    deadline = time.monotonic() + 45
    while True:
        try:
            status = api(client, 'sys/seal-status')
            break
        except (URLError, OSError):
            if time.monotonic() >= deadline:
                raise RuntimeError('OpenBao TLS endpoint did not become ready') from None
            time.sleep(1)

    if not status['initialized']:
        if PENDING.exists() or AUTH.exists() or OPERATOR.exists():
            raise RuntimeError('OpenBao data is missing. Restore the original Raft data before starting.')
        initial = api(client, 'sys/init', {'secret_shares': 1, 'secret_threshold': 1}, method='PUT')
        save(PENDING, json.dumps(initial))
    bootstrap = json.loads(PENDING.read_text()) if PENDING.exists() else None
    if bootstrap:
        share = keychain_share(bootstrap['keys_base64'][0])
    if status['sealed']:
        state = api(client, 'sys/unseal', {'key': share if bootstrap else keychain_share()}, method='PUT')
        if state['sealed']:
            raise RuntimeError('OpenBao stayed sealed')
    if bootstrap:
        token = bootstrap['root_token']
        try:
            api(client, 'auth/token/lookup-self', token=token)
        except HTTPError as error:
            # A crash after revocation can leave the private bootstrap file in place.
            if error.code != 403:
                raise
        else:
            provision(client, bootstrap)
            api(client, 'auth/token/revoke-self', {}, token)
        for directory in (AUTH, OPERATOR):
            api(client, KV, token=login(client, directory))
        PENDING.unlink()
    elif not (AUTH / 'secret-id').is_file() or not (OPERATOR / 'secret-id').is_file():
        raise RuntimeError('Local AppRole files are missing. Restore the protected operator backup.')
    print('Local OpenBao is initialized and unsealed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'rotate', 'copy-password'))
    action = parser.parse_args().action
    if os.environ.get('LITELLM_UI_PASSWORD_SOURCE') != 'openbao-local' or sys.platform != 'darwin':
        sys.exit('Set LITELLM_UI_PASSWORD_SOURCE=openbao-local on the Mac first.')
    os.umask(0o077)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / 'operation.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if action == 'start':
            start()
            return
        client = api_client()
        token = login(client, OPERATOR if action == 'rotate' else AUTH)
        current = api(client, KV, token=token)['data']
        if action == 'rotate':
            version = write_password(client, token, current['metadata']['version'])
            print(f'OpenBao stored dashboard password version {version}.')
        else:
            subprocess.run(['/usr/bin/pbcopy'], input=current['data']['key'].encode(), check=True)
            print('LiteLLM dashboard password copied to the macOS clipboard.')


if __name__ == '__main__':
    try:
        main()
    except HTTPError as error:
        sys.exit(f'OpenBao returned HTTP {error.code}.')
    except subprocess.CalledProcessError as error:
        sys.exit(f'{Path(error.cmd[0]).name} exited with status {error.returncode}.')
    except (KeyError, OSError, RuntimeError, ValueError) as error:
        sys.exit(f'OpenBao setup failed: {error}')
