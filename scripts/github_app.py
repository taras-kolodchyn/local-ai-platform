"""Host-only GitHub App authentication for the repository publisher."""
import base64
from datetime import datetime
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

REPO = 'taras-kolodchyn/local-ai-platform'
API_VERSION = '2026-03-10'
PERMISSIONS = {'contents':'write', 'issues':'read', 'pull_requests':'write'}
APP_DIR = Path(__file__).resolve().parents[1] / '.local/github-issues/app'


class AppError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def request(path, token=None, body=None):
    if not path.startswith('/') or path.startswith('//'):
        raise AppError('Expected a GitHub REST path.')
    headers = {'Accept':'application/vnd.github+json', 'X-GitHub-Api-Version':API_VERSION,
               'User-Agent':'Moyai-local-issue-publisher'}
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
    def __init__(self, directory=APP_DIR):
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

    def refresh(self):
        jwt = make_jwt(str(self.app_id), self.key)
        repo_path = '/repos/' + REPO
        try:
            install = request(repo_path + '/installation', jwt)
        except HTTPError as error:
            error.close()
            if error.code == 404:
                raise AppError('Install the registered GitHub App on ' + REPO + ' first.') from None
            raise AppError(f'GitHub installation lookup returned HTTP {error.code}.') from None
        if (install['app_id'] != self.app_id or install['app_slug'] != self.slug or install.get('suspended_at')
                or install['account']['login'] != REPO.split('/')[0] or install['repository_selection'] != 'selected'):
            raise AppError('Install this app using Only select repositories and select local-ai-platform.')
        mint = '/app/installations/' + str(int(install['id'])) + '/access_tokens'
        # Inspect the actual installation selection using a metadata-only token.
        # A token already downscoped to this repo cannot establish that selection.
        metadata = request(mint, jwt, {'permissions':{'metadata':'read'}})
        selection = request('/installation/repositories?per_page=2', metadata['token'])
        if selection['total_count'] != 1 or [r['full_name'] for r in selection['repositories']] != [REPO]:
            raise AppError('The app installation must select only ' + REPO + '.')
        repo_id = selection['repositories'][0]['id']
        grant = request(mint, jwt, {'repository_ids':[repo_id], 'permissions':PERMISSIONS})
        if any(grant['permissions'].get(k) != v for k, v in PERMISSIONS.items()):
            raise AppError('GitHub did not grant the required issue, content and pull request permissions.')
        self.expires = datetime.fromisoformat(grant['expires_at'].replace('Z','+00:00')).timestamp()
        self.token = grant['token']

    def api(self, endpoint, body=None):
        if self.token is None or self.expires < time.time() + 90:
            self.refresh()
        return request('/repos/' + REPO + '/' + endpoint, self.token, body)
