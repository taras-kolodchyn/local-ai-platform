"""Local Moyai boundaries: chat identity and private secret configuration."""
import importlib.util
import asyncio
import copy
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'integrations/moyai/local_config.py'


class MoyaiConfigTests(unittest.TestCase):
    def config(self):
        self.assertTrue(CONFIG.is_file(), 'Local Moyai configuration helper is missing')
        spec = importlib.util.spec_from_file_location('moyai_local_config', CONFIG)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_different_chats_and_authorities_get_different_directories(self):
        directory = self.config().workspace_directory
        first = directory('session-a', 'authority-a')
        self.assertEqual(first, directory('session-a', 'authority-a'))
        self.assertNotEqual(first, directory('session-b', 'authority-a'))
        self.assertNotEqual(first, directory('session-a', 'authority-b'))
        self.assertRegex(first, r'^/workspace/[0-9a-f]{64}$')

    def test_untrusted_identifiers_cannot_escape_workspace(self):
        directory = self.config().workspace_directory
        for session, authority in [('../outside', '/etc'), ('/tmp/other', 'a:b')]:
            with self.subTest(session=session):
                self.assertRegex(directory(session, authority), r'^/workspace/[0-9a-f]{64}$')
        for session, authority in [('', 'scope'), ('session', '')]:
            with self.subTest(session=session):
                with self.assertRaises(ValueError):
                    directory(session, authority)

    def test_config_rejects_short_admin_password_or_missing_gateway_key(self):
        validate = self.config().validate_secrets
        for secrets in ({'password': 'short', 'api_key': 'fixture'},
                        {'password': 'fixture-long-password', 'api_key': ''}):
            with self.subTest(secrets=secrets):
                with self.assertRaises(ValueError) as raised:
                    validate(secrets)
                self.assertNotIn('fixture-long-password', str(raised.exception))
        self.assertIsNone(validate({'password': 'fixture-long-password', 'api_key': 'fixture'}))

    def test_stored_key_must_be_a_dedicated_restricted_virtual_key(self):
        config = self.config()
        self.assertTrue(hasattr(config, 'valid_scoped_key'), 'Stored key scope verification is missing')
        valid = config.valid_scoped_key
        info = {'key_alias': 'moyai-local-mvp', 'models': ['local-qwen'],
                'allowed_routes': ['/v1/responses', '/model/info'], 'status': 'active',
                'object_permission': {'object_permission_id': 'fixture-id', 'mcp_servers': [], 'agents': []}}
        self.assertTrue(valid('fixture-virtual', 'fixture-master', info))
        self.assertFalse(valid('fixture-master', 'fixture-master', info))
        changes = ({'models': ['local-qwen', 'other']}, {'key_alias': 'general-client'},
                   {'allowed_routes': ['openai_routes', '/model/info']},
                   {'allowed_routes': ['openai_routes', 'management_routes']}, {'status': 'revoked'},
                   {'team_id': 'inherited-team'}, {'object_permission': None},
                   {'object_permission': {'mcp_servers': ['external-server']}},
                   {'object_permission': {'unknown_future_grant': ['external-resource']}})
        for change in changes:
            with self.subTest(change=change):
                self.assertFalse(valid('fixture-virtual', 'fixture-master', copy.deepcopy(info) | change))

    def test_pytest_evidence_requires_the_canonical_runner_and_native_exit_status(self):
        config = self.acceptance()
        self.assertTrue(hasattr(config, 'valid_pytest_action'), 'Native pytest receipt validation is missing')
        check = config.valid_pytest_action
        good = {'input': '{"command":"python -m pytest -q","mode":"foreground"}',
                'output': '... [100%]\n3 passed in 0.1s\n{"pid":123,"exit_code":0}'}
        self.assertTrue(check(good))
        for change in ({'input': '{"command":"echo pytest; echo 3 passed"}'},
                       {'input': '{"command":"python -m pytest -q; true"}'},
                       {'output': '3 passed\n{"exit_code":1}'},
                       {'output': '3 passed\necho "exit_code": 0'},
                       {'output': '3 passed\n{"exit_code":false}'}):
            with self.subTest(change=change):
                self.assertFalse(check(good | change))

    def test_independent_observer_checks_persistence_and_other_directory_contents(self):
        config = self.acceptance()
        self.assertTrue(hasattr(config, 'valid_workspace_states'), 'Trusted workspace observation checks are missing')
        check = config.valid_workspace_states
        hashes = {'calc.py': 'fixture-program-hash', 'test_calc.py': 'fixture-tests-hash'}
        first = {'directory': '/workspace/first', 'uid': 10001, 'files': hashes}
        other = {'directory': '/workspace/other', 'uid': 10001,
                 'files': {'calc.py': None, 'test_calc.py': None}}
        self.assertTrue(check(first, copy.deepcopy(first), other, hashes))
        for continued, second in [(first | {'files': {}}, other), (first, other | {'directory': '/workspace/first'}),
                                  (first, other | {'files': hashes}), (first, other | {'uid': 0})]:
            with self.subTest(continued=continued, second=second):
                self.assertFalse(check(first, continued, second, hashes))

    def acceptance(self):
        path = ROOT / 'integrations/moyai/acceptance.py'
        self.assertTrue(path.is_file(), 'Independent acceptance validators are missing')
        spec = importlib.util.spec_from_file_location('moyai_acceptance', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class MoyaiRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_upstream_preserves_request_and_response_bytes(self):
        path = ROOT / 'integrations/moyai/relay.py'
        self.assertTrue(path.is_file(), 'Local Moyai fixed TCP relay is missing')
        spec = importlib.util.spec_from_file_location('moyai_relay', path)
        relay = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(relay)
        received = []

        async def upstream(reader, writer):
            received.append(await reader.readexactly(7))
            writer.write(b'RESPONSE')
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async with await asyncio.start_server(upstream, '127.0.0.1', 0) as target:
            port = target.sockets[0].getsockname()[1]
            async with await relay.listen('127.0.0.1', 0, '127.0.0.1', port) as proxy:
                reader, writer = await asyncio.open_connection('127.0.0.1', proxy.sockets[0].getsockname()[1])
                writer.write(b'REQUEST')
                await writer.drain()
                self.assertEqual(await asyncio.wait_for(reader.readexactly(8), timeout=3), b'RESPONSE')
                writer.close()
                await writer.wait_closed()
        self.assertEqual(received, [b'REQUEST'])
