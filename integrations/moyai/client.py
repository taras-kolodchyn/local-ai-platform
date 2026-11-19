"""Authenticated localhost-only API client for local acceptance checks."""
from http.cookiejar import CookieJar
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class MoyaiUnavailable(ConnectionError):
    """The localhost controller connection can be retried after a restart."""


class MoyaiClient:
    def __init__(self):
        self.opener = build_opener(ProxyHandler({}), NoRedirects(), HTTPCookieProcessor(CookieJar()))
        self.csrf = ''

    def request(self, path, body=None, *, raw=False):
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('Expected a local API path')
        request = Request('http://127.0.0.1:8787' + path,
                          data=None if body is None else json.dumps(body).encode(),
                          headers={'Content-Type': 'application/json', 'x-moyai-csrf': self.csrf})
        try:
            with self.opener.open(request, timeout=15) as response:
                value = response.read(24 * 1024 * 1024)
        except HTTPError as error:
            error.close()
            if error.code in (502, 503, 504):
                raise MoyaiUnavailable(f'Moyai API unavailable (HTTP {error.code})') from error
            raise RuntimeError(f'Moyai API request failed (HTTP {error.code})') from None
        except (URLError, ConnectionError, TimeoutError) as error:
            raise MoyaiUnavailable('Moyai API connection interrupted') from error
        return value if raw else json.loads(value)

    def login(self):
        password = Path('.local/moyai/admin-password').read_text().strip()
        self.csrf = self.request('/api/login', {'username': 'admin', 'password': password})['csrf']

    def session(self, title, *, model='local-qwen'):
        return self.request('/api/sessions', {
            'title': title, 'visibility': 'private', 'model': model,
        })['id']

    def turn(self, session, prompt, *, timeout=300):
        turn = self.request(f'/api/sessions/{session}/messages', {
            'prompt': prompt, 'request_key': str(uuid.uuid4()),
        })['id']
        Path('.local/moyai/last-session.json').write_text(json.dumps({'session': session, 'turn': turn}) + '\n')
        print(f'Moyai turn submitted: {turn}', flush=True)
        deadline = time.monotonic() + timeout
        previous = None
        while time.monotonic() < deadline:
            detail = self.request(f'/api/sessions/{session}?include_messages=true')
            state = next((row for row in detail['turns'] if row['id'] == turn), None)
            if state:
                status = state['status']
                if status != previous:
                    print(f'  Turn status: {status}', flush=True)
                    previous = status
                if status == 'completed':
                    return turn, detail
                if status in {'failed', 'waiting', 'stopped'}:
                    raise RuntimeError(f'Moyai turn ended with status {status}; inspect its local session')
            time.sleep(1)
        self.request(f'/api/sessions/{session}/stop', {})
        raise RuntimeError('Moyai acceptance turn exceeded its time budget and was stopped')
