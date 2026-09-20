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
