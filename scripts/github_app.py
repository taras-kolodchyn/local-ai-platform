"""Host-only GitHub App authentication for the repository publisher."""
import base64
from datetime import datetime
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'integrations/moyai'))
from github_issue import selected_target

API_VERSION = '2026-03-10'
PERMISSIONS = {'contents':'write', 'issues':'read', 'pull_requests':'write'}


def credentials_directory():
    return ROOT / '.local/github-issues/targets' / selected_target().repository


class AppError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def request(path, token=None, body=None):
    if not path.startswith('/') or path.startswith('//'):
        raise AppError('Expected a GitHub REST path.')
    headers = {'Accept':'application/vnd.github+json', 'X-GitHub-Api-Version':API_VERSION,
               'User-Agent':'Repository Access'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = None if body is None else json.dumps(body).encode()
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = Request('https://api.github.com' + path, headers=headers, data=data)
    with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=25) as response:
        return json.load(response)


def check_key(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise AppError('The private key must be an owner-only regular file.')


def encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=')


def make_jwt(issuer, key):
    check_key(key)
    now = int(time.time())
    claims = json.dumps({'iss':issuer, 'iat':now - 60, 'exp':now + 540}).encode()
    payload = encode(b'{"alg":"RS256","typ":"JWT"}') + b'.' + encode(claims)
    result = subprocess.run(['openssl','dgst','-sha256','-sign',str(key),'-passin','pass:'],
                            input=payload, capture_output=True, check=True, timeout=10)
    return (payload + b'.' + encode(result.stdout)).decode('ascii')


class Installation:
    def __init__(self, directory=None):
        self.repository = selected_target().repository
        if directory is None:
            directory = credentials_directory() / 'app'
        try:
            config = json.loads((directory/'app.json').read_text())
        except FileNotFoundError as exc:
            raise AppError('Register and install the private app with make github-app-setup first.') from exc
        self.app_id = config['id']
        self.slug = config['slug']
        if type(self.app_id) is not int or self.app_id < 1 or not re.fullmatch('[a-z0-9-]+', self.slug):
            raise AppError('Invalid app registration.')
        self.key = directory/'private-key.pem'
        self.bot = self.slug + '[bot]'
        self.token = None
        self.expires = 0

    def refresh(self, *, reader=False):
        jwt = make_jwt(str(self.app_id), self.key)
        repo_path = '/repos/' + self.repository
        try:
            install = request(repo_path + '/installation', jwt)
        except HTTPError as error:
            error.close()
            if error.code == 404:
                raise AppError('Install the registered GitHub App on ' + self.repository + ' first.') from None
            raise AppError(f'GitHub installation lookup returned HTTP {error.code}.') from None
        if (install['app_id'] != self.app_id or install['app_slug'] != self.slug or install.get('suspended_at')
                or install['account']['login'] != self.repository.split('/')[0] or install['repository_selection'] != 'selected'):
            raise AppError('Install this app using Only select repositories and select ' + self.repository + '.')
        mint = '/app/installations/' + str(int(install['id'])) + '/access_tokens'
        # Inspect the actual installation selection using a metadata-only token.
        # A token already downscoped to this repo cannot establish that selection.
        metadata = request(mint, jwt, {'permissions':{'metadata':'read'}})
        selection = request('/installation/repositories?per_page=2', metadata['token'])
        if selection['total_count'] != 1 or [r['full_name'] for r in selection['repositories']] != [self.repository]:
            raise AppError('The app installation must select only ' + self.repository + '.')
        repo_id = selection['repositories'][0]['id']
        permissions = dict.fromkeys(PERMISSIONS, 'read') if reader else PERMISSIONS
        grant = request(mint, jwt, {'repository_ids':[repo_id], 'permissions':permissions})
        if any(grant['permissions'].get(k) != v for k, v in permissions.items()):
            raise AppError('GitHub did not grant the required issue, content and pull request permissions.')
        if reader:
            if grant['permissions'] != {**permissions, 'metadata':'read'}:
                raise AppError('Unexpected installation token permissions.')
            scoped = request('/installation/repositories?per_page=2', grant['token'])
            if scoped['total_count'] != 1 or [r['id'] for r in scoped['repositories']] != [repo_id]:
                raise AppError('Unexpected installation token repository selection.')
            self.authority = {
                'app': self.app_id, 'installation': install['id'], 'owner': install['account']['id'],
                'repository': repo_id, 'permissions': grant['permissions'],
                'installation_permissions': install['permissions'],
            }
        self.expires = datetime.fromisoformat(grant['expires_at'].replace('Z','+00:00')).timestamp()
        self.token = grant['token']

    def api(self, endpoint, body=None):
        return request('/repos/' + self.repository + '/' + endpoint, self.access_token(), body)

    def access_token(self):
        if self.token is None or self.expires < time.time() + 90:
            self.refresh()
        return self.token

    def graphql(self, query, variables):
        result = request('/graphql', self.access_token(), {'query':query, 'variables':variables})
        if result.get('errors') or not isinstance(result.get('data'), dict):
            raise AppError('GitHub declined the requested PR transition. Check repository rules and the PR state.')
        return result['data']
