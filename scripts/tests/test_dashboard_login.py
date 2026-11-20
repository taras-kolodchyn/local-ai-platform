"""Dashboard checks use shell-loaded .env credentials and real local HTTP sessions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[2]
ENV = """UI_USERNAME=fixture-admin
UI_PASSWORD='fixture-ui-$ with spaces'
LITELLM_MASTER_KEY=sk-fixture-master
GRAFANA_ADMIN_PASSWORD='fixture-grafana-$ with spaces'
"""


class DashboardLoginTests(unittest.TestCase):
    def run_check(self, env_text=ENV, *, lite_password='fixture-ui-$ with spaces',
                  lite_status=303, redirect='/ui?login=success', lite_cookie=True,
                  grafana_status=200, cookie=True, identity=None):
        events = []
        identity = identity if identity is not None else {'login': 'admin', 'isGrafanaAdmin': True}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                events.append(('POST', self.path))
                body = self.rfile.read(int(self.headers['Content-Length']))
                if self.path == '/litellm/login':
                    data = parse_qs(body.decode())
                    valid = data == {'username': ['fixture-admin'], 'password': [lite_password]}
                    self.send_response(lite_status if valid else 401)
                    if valid and lite_status == 303:
                        self.send_header('Location', redirect)
                        if lite_cookie:
                            self.send_header('Set-Cookie', 'token=fixture-session-token; Path=/; SameSite=Lax')
                else:
                    valid = json.loads(body) == {'user': 'admin', 'password': 'fixture-grafana-$ with spaces'}
                    self.send_response(grafana_status if valid else 401)
                    if valid and grafana_status == 200 and cookie:
                        self.send_header('Set-Cookie', 'grafana_session=fixture-session-token; Path=/; HttpOnly')
                self.end_headers()
                # A failure response may echo secrets; the checker must not print it.
                self.wfile.write(b'fixture-ui-$ with spaces fixture-grafana-$ with spaces fixture-session-token')

            def do_GET(self):
                events.append(('GET', self.path))
                authenticated = self.headers.get('Cookie') == 'grafana_session=fixture-session-token'
                self.send_response(200 if authenticated else 401)
                self.end_headers()
                self.wfile.write(json.dumps(identity).encode())

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    (root / 'scripts').mkdir()
                    shutil.copy(ROOT / 'scripts/lib.sh', root / 'scripts/lib.sh')
                    (root / '.env').write_text(env_text)
                    base = f'http://127.0.0.1:{server.server_port}'
                    (root / 'run.py').write_text(
                        'import importlib.util\n'
                        f'spec=importlib.util.spec_from_file_location("login_check", {str(ROOT / "scripts/check-dashboard-login.py")!r})\n'
                        'module=importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n'
                        f'raise SystemExit(module.main({base + "/litellm"!r}, {base + "/grafana"!r}))\n')
                    result = subprocess.run(
                        ['bash', '-c', 'source scripts/lib.sh; load_env; exec "$TEST_PYTHON" run.py'],
                        cwd=root, env=os.environ | {'TEST_PYTHON': sys.executable,
                            'HTTP_PROXY': 'http://invalid.invalid:1', 'NO_PROXY': ''},
                        capture_output=True, text=True, timeout=10)
                    output = result.stdout + result.stderr
                    for secret in ('fixture-ui-$ with spaces', 'fixture-grafana-$ with spaces',
                                   'sk-fixture-master', 'fixture-session-token'):
                        self.assertNotIn(secret, output)
                    return result, events
            finally:
                server.shutdown()
                thread.join()

    def test_env_credentials_and_authenticated_session_pass_without_following_ui_redirect(self):
        result, events = self.run_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('LiteLLM dashboard login passed', result.stdout)
        self.assertIn('Grafana dashboard login passed', result.stdout)
        self.assertEqual(events, [('POST', '/litellm/login'), ('POST', '/grafana/login'), ('GET', '/grafana/api/user')])

    def test_blank_ui_password_uses_master_key(self):
        result, _ = self.run_check(ENV.replace("UI_PASSWORD='fixture-ui-$ with spaces'", 'UI_PASSWORD='),
                                   lite_password='sk-fixture-master')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_grafana_rejection_fails_without_echoing_response(self):
        result, _ = self.run_check(grafana_status=401)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Grafana', result.stderr)
        self.assertIn('401', result.stderr)

    def test_grafana_needs_a_fresh_session_cookie(self):
        result, _ = self.run_check(cookie=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Grafana', result.stderr)

    def test_grafana_identity_must_be_the_configured_admin(self):
        for identity in ({'login': 'other', 'isGrafanaAdmin': True}, {'login': 'admin', 'isGrafanaAdmin': False}):
            with self.subTest(identity=identity):
                result, _ = self.run_check(identity=identity)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Grafana', result.stderr)

    def test_invalid_litellm_redirects_are_rejected_and_never_followed(self):
        for redirect in ('/unexpected?token=fixture-session-token', 'https://example.invalid/ui?token=fixture-session-token'):
            with self.subTest(redirect=redirect):
                result, events = self.run_check(redirect=redirect)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('LiteLLM', result.stderr)
                self.assertEqual(events, [('POST', '/litellm/login')])

    def test_litellm_login_page_is_not_successful_authentication(self):
        result, _ = self.run_check(lite_status=200)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('LiteLLM', result.stderr)

    def test_litellm_rejection_fails_without_echoing_response(self):
        result, events = self.run_check(lite_status=401)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('LiteLLM', result.stderr)
        self.assertIn('401', result.stderr)
        self.assertEqual(events, [('POST', '/litellm/login')])

    def test_litellm_needs_a_fresh_session_cookie(self):
        result, _ = self.run_check(lite_cookie=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('LiteLLM', result.stderr)

    def test_litellm_redirect_needs_a_success_marker(self):
        result, _ = self.run_check(redirect='/ui?login=failed')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('LiteLLM', result.stderr)

    def test_missing_password_fails_before_any_http_request(self):
        result, events = self.run_check(ENV.replace("GRAFANA_ADMIN_PASSWORD='fixture-grafana-$ with spaces'", 'GRAFANA_ADMIN_PASSWORD='))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('GRAFANA_ADMIN_PASSWORD', result.stderr)
        self.assertEqual(events, [])
