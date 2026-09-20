import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('workspace_provision', Path(__file__).parents[1] / 'provision-workspace.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ProvisioningTest(unittest.TestCase):
    def test_reconcile_twice_keeps_ids_and_existing_records(self):
        records = []
        def request(method, route, body=None):
            if method == 'GET':
                return {'data': records}
            self.assertEqual(route, '/vector_store/new')
            self.assertEqual(body['custom_llm_provider'], 'pg_vector')
            records.append(body)
            return {'status': 'success'}
        stores = [{'id': 'vs_fixture', 'repository': 'fixture', 'branch': 'main'}]
        first = module.provision_vectors(request, stores, 'test-backend')
        second = module.provision_vectors(request, stores, 'test-backend')
        self.assertEqual(first, second)
        self.assertEqual(len(records), 1)
        self.assertEqual(first, {'fixture:main': 'vs_fixture'})

    def test_secret_write_has_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'key'
            module.private_write(path, 'secret')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_text(), 'secret')

    def test_existing_profile_keys_receive_new_store_permissions(self):
        import json
        from unittest.mock import patch
        calls = []
        def request(method, route, body=None):
            calls.append((route, body))
            return {'key': 'fixture-key'}
        with tempfile.TemporaryDirectory() as d:
            local = Path(d)
            (local / 'litellm-master-key').write_text('fixture-master')
            registry = local / 'workspace-registry.json'
            registry.write_text(json.dumps({'stores': {'first': 'vs_first'}}))
            with patch.object(module, 'LOCAL', local), patch.object(module, 'gateway', return_value=request), \
                 patch.object(module, 'owner_request'), patch.object(module.subprocess, 'run') as run:
                run.return_value.returncode = 0
                run.return_value.stdout = ''
                module.profiles()
                calls.clear()
                registry.write_text(json.dumps({'stores': {'first': 'vs_first', 'second': 'vs_second'}}))
                module.profiles()
            updates = [body for route, body in calls if route == '/key/update']
            self.assertEqual(len(updates), 3)
            self.assertFalse(any(route == '/key/generate' for route, _ in calls))
            for update in updates:
                self.assertEqual(update['object_permission']['vector_stores'], ['vs_first', 'vs_second'])
                self.assertEqual([s['index_name'] for s in update['metadata']['allowed_vector_store_indexes']],
                                 ['vs_first', 'vs_second'])
            for profile in ('development', 'review', 'research'):
                self.assertFalse((local / 'profiles' / profile / 'caller.key').exists())
                self.assertEqual(len((local / 'profiles' / profile / 'caller.sha256').read_text()), 64)
