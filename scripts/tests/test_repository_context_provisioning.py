"""Source access credentials are separate, least-privilege and reconcilable."""
import hashlib
import fcntl
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[2]


class ContextProvisioningTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('context_provision',
            ROOT / 'scripts/provision-repository-context.py')
        self.module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(self.module)
        except FileNotFoundError:
            self.fail('Repository context provisioning is missing')
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.local = self.root / '.local'; self.local.mkdir()
        (self.local / 'vector-backend-key').write_text('fixture-backend')
        snapshots = self.local / 'repositories/snapshots'; snapshots.mkdir(parents=True)
        manifest = {'schema_version': 1, 'repository_id': 'repo_fixture',
            'name': 'fixture', 'snapshot_id': 'a' * 64,
            'git': {'branch': 'main', 'head': 'abc', 'dirty': True, 'subtree': ''}}
        snapshot = snapshots / manifest['snapshot_id']; snapshot.mkdir()
        (snapshot / 'manifest.json').write_text(json.dumps(manifest))
        self.row = {'repository_id': 'repo_fixture', 'name': 'fixture',
            'snapshot_id': 'a' * 64, 'generation_id': 'gen_fixture',
            'verification_profile': 'rust-retry', 'store_id': 'vs_fixture',
            'manifest': 'snapshots/' + 'a' * 64 + '/manifest.json'}
        (self.local / 'repositories/catalog.json').write_text(json.dumps({
            'schema_version': 1, 'repositories': [], 'indexed_generations': []}))
        self.keys = {}; self.stores = {}; self.users = {}; self.calls = []

    def admin(self, method, route, body=None):
        self.calls.append((method, route, body))
        if route == '/vector_store/list':
            return {'data': list(self.stores.values())}
        if route == '/vector_store/new':
            self.stores[body['vector_store_id']] = body
            return body
        if route.startswith('/user/info?'):
            if not self.users:
                raise RuntimeError('Gateway returned 404')
            return self.users
        if route == '/user/new':
            self.users.update(body); return body
        if route.startswith('/key/info?'):
            supplied = route.partition('key=')[2]
            for key, info in self.keys.items():
                if hashlib.sha256(key.encode()).hexdigest() == supplied:
                    return {'info': info}
            raise RuntimeError('Gateway returned 404')
        if route == '/key/generate':
            key = 'fixture-context-' + str(len(self.keys))
            self.keys[key] = body | {'status': 'active', 'blocked': False}
            return {'key': key}
        if route == '/key/update':
            self.keys[body['key']].update({k: v for k, v in body.items() if k != 'key'})
            return {}
        raise AssertionError((method, route))

    def test_registration_creates_scoped_nonadmin_key_and_preserves_token(self):
        token = self.module.provision_context(self.admin, self.root, self.row)
        key_file = self.local / 'moyai/context-gateway-key'
        key = key_file.read_text().strip()
        info = self.keys[key]
        self.assertEqual(info['models'], ['local-embeddings'])
        self.assertEqual(info['allowed_routes'], ['/v1/vector_stores/vs_fixture/search'])
        self.assertEqual(info['object_permission'], {'vector_stores': ['vs_fixture'],
                                                    'mcp_servers': [], 'agents': []})
        self.assertEqual(self.users['user_role'], 'internal_user')
        self.assertEqual(self.stores['vs_fixture']['litellm_params']['api_base'], 'http://retrieval:8000')
        secret_files = (key_file, self.local / 'moyai/context-token')
        for path in secret_files:
            path.chmod(0o600)
        again = self.module.provision_context(self.admin, self.root, self.row)
        self.assertEqual(again, token)
        self.assertEqual(len(self.keys), 1)
        for path in secret_files:
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    def test_full_catalog_allows_reconciliation_of_an_existing_generation(self):
        rows = [self.row | {'generation_id': f'gen_{i}'} for i in range(1000)]
        (self.local / 'repositories/catalog.json').write_text(json.dumps({
            'schema_version': 1, 'repositories': [], 'indexed_generations': rows}))
        self.assertEqual(len(self.module.registrations(self.root, rows[-1])), 1000)
        with self.assertRaisesRegex(ValueError, 'catalog limit'):
            self.module.registrations(self.root, self.row)

    def test_native_http_404_creates_missing_owner_and_replaces_missing_key(self):
        home = self.local / 'moyai'; home.mkdir()
        (home/'context-gateway-key').write_text('missing-key')
        def http_admin(method,route,body=None):
            if route.startswith(('/user/info?', '/key/info?')):
                raise HTTPError('http://127.0.0.1:4000'+route,404,'Not Found',{},None)
            return self.admin(method,route,body)
        self.module.provision_context(http_admin,self.root,self.row)
        self.assertEqual(self.users['user_role'],'internal_user')
        self.assertNotEqual((home/'context-gateway-key').read_text().strip(),'missing-key')

    def test_foreign_key_file_is_replaced_without_adopting_its_authority(self):
        home = self.local / 'moyai'; home.mkdir()
        (home / 'context-gateway-key').write_text('foreign-fixture')
        self.keys['foreign-fixture'] = {'key_alias': 'foreign', 'models': ['local-qwen'],
            'status': 'active', 'object_permission': {'vector_stores': ['foreign_store']}}
        self.module.provision_context(self.admin, self.root, self.row)
        self.assertEqual(self.keys['foreign-fixture']['object_permission']['vector_stores'], ['foreign_store'])
        generated = self.keys[(home / 'context-gateway-key').read_text().strip()]
        self.assertEqual(generated['object_permission']['vector_stores'], ['vs_fixture'])

    def test_registration_manifest_escape_is_rejected_before_gateway_call(self):
        self.row['manifest'] = '../private/manifest.json'
        with self.assertRaises(ValueError):
            self.module.provision_context(self.admin, self.root, self.row)
        self.assertEqual(self.calls, [])

    def test_empty_catalog_never_generates_an_unrestricted_empty_route_list(self):
        self.module.provision_context(self.admin,self.root)
        key = (self.local/'moyai/context-gateway-key').read_text().strip()
        self.assertEqual(self.keys[key]['allowed_routes'], ['/__local_repository_context_unregistered__'])
        self.assertEqual(self.keys[key]['object_permission']['vector_stores'], [])

    def test_startup_waits_for_pending_catalog_publication(self):
        directory = self.local / 'repositories'
        entered = threading.Event()
        def startup():
            entered.set()
            self.module.provision_context(self.admin, self.root)
        with ThreadPoolExecutor(max_workers=1) as executor:
            with (directory / '.publish.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                future = executor.submit(startup)
                self.assertTrue(entered.wait(3))
                with self.assertRaises(TimeoutError):
                    future.result(timeout=0.2)
                (directory / 'catalog.json').write_text(json.dumps({'schema_version': 1,
                    'repositories': [self.row], 'indexed_generations': [self.row]}))
            future.result(timeout=3)
        key = (self.local / 'moyai/context-gateway-key').read_text().strip()
        self.assertEqual(self.keys[key]['allowed_routes'], ['/v1/vector_stores/vs_fixture/search'])
