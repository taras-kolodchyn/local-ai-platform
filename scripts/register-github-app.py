#!/usr/bin/env python3
"""Run the one-time, loopback-only GitHub manifest setup."""
import hmac
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from github_app import APP_DIR, PERMISSIONS, REPO, request


def manifest(origin):
    return {'name':'moyai-local-' + secrets.token_hex(4), 'url':'https://github.com/' + REPO,
            'description':'Local Moyai publisher for verified Rust issue fixes.',
            'public':False, 'request_oauth_on_install':False, 'hook_attributes':{'active':False, 'url':'https://github.com/'+REPO},
            'default_events':[], 'default_permissions':PERMISSIONS, 'redirect_url':origin+'/callback'}


def save_registration(row, destination):
    if (row['owner']['login'] != REPO.split('/')[0] or type(row['id']) is not int
            or not re.fullmatch('[a-z0-9-]+',row['slug']) or not row.get('pem')):
        raise ValueError('Unexpected GitHub App owner or identity.')
    if destination.exists():
        raise FileExistsError('An app registration is already stored.')
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        staging = Path(temporary)/'app'
        staging.mkdir(mode=0o700)
        with open(staging/'private-key.pem','x',opener=lambda p, f: os.open(p,f,0o600)) as f:
            f.write(row['pem'])
        (staging/'app.json').write_text(json.dumps({'id':row['id'], 'slug':row['slug']})+'\n')
        staging.rename(destination)


def install_url(slug):
    return 'https://github.com/apps/' + slug + '/installations/new'


def main(*, port=0, state=None, config=None):
    if APP_DIR.exists():
        print('Install the registered app: ' + install_url(json.loads((APP_DIR/'app.json').read_text())['slug']))
        return
    APP_DIR.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    state = state or secrets.token_urlsafe(32)

    class Registration(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # The callback query carries a single-use code.

        def page(self, code, body):
            raw = ('<!doctype html><html lang="uk"><meta charset="utf-8"><title>Moyai · GitHub App</title>'
                   '<main style="font:18px/1.5 system-ui;max-width:48rem;margin:4rem auto">'+body+'</main></html>').encode()
            self.send_response(code)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'none'; style-src 'unsafe-inline'; form-action https://github.com; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.headers.get('Host') != authority:
                return self.page(400,'Unexpected host.')
            url = urlsplit(self.path)
            if url.path == '/' and not url.query:
                value = escape(json.dumps(config), quote=True)
                return self.page(200, '<h1>Приватна GitHub App для Moyai</h1><p>Увійдіть у GitHub як '
                    'taras-kolodchyn. Після реєстрації виберіть <b>Only select repositories → local-ai-platform</b>.</p>'
                    '<p>Issues — читання. Contents і Pull requests — читання та запис. Ключ збережеться на цьому Mac.</p>'
                    f'<form action="https://github.com/settings/apps/new?state={state}" method="post">'
                    f'<input type="hidden" name="manifest" value="{value}"><button>Перейти до реєстрації в GitHub</button></form>')
            if url.path != '/callback':
                return self.page(404, 'Unknown path.')
            query = parse_qs(url.query)
            if (len(query.get('state', [])) != 1 or not hmac.compare_digest(query['state'][0].encode(), state.encode())
                    or len(query.get('code', [])) != 1 or not re.fullmatch('[A-Za-z0-9_-]{1,200}',query['code'][0])):
                return self.page(400, 'Invalid registration callback.')
            try:
                row = request('/app-manifests/'+query['code'][0]+'/conversions', body={})
                save_registration(row, APP_DIR)
            except (ValueError, KeyError, OSError, HTTPError, URLError):
                return self.page(400, 'Registration did not complete. Check the GitHub account and local app directory.')
            self.server.complete = True
            self.page(200, '<h1>App зареєстрована</h1><p>Встановіть її лише для local-ai-platform.</p>'
                           f'<a href="{install_url(row["slug"])}">Встановити GitHub App</a>')

    with HTTPServer(('127.0.0.1', port), Registration) as server:
        authority = '127.0.0.1:'+str(server.server_port)
        origin = 'http://'+authority
        config = config or manifest(origin)
        print('Open ' + origin + '/ in the browser signed in as ' + REPO.split('/')[0], flush=True)
        server.complete = False
        server.timeout = 1
        deadline = time.monotonic()+3600
        while not server.complete and time.monotonic() < deadline:
            server.handle_request()
        if not server.complete:
            raise SystemExit('Registration link expired. Run make github-app-setup for a fresh link.')


if __name__ == '__main__':
    main()
