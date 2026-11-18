import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('workspace_provision', Path(__file__).parents[1] / 'provision-workspace.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ProvisioningTest(unittest.TestCase):
    def test_knowledge_refresh_preserves_unrelated_agent_grants_after_retirement(self):
        import json
        from unittest.mock import patch
        updates = []
        def request(method, route, body=None):
            if route == '/team/list':
                return [{'team_id': module.TEAM, 'object_permission': {
                    'agents': ['unrelated-agent'], 'vector_stores': ['retained-store'],
                    'mcp_servers': ['custom-readonly']}}]
            if route == '/team/update':
                updates.append(body['object_permission'])
            if route.startswith('/key/info?'):
                return {'info': {'key_alias': 'workspace-store-provisioning', 'team_id': module.TEAM}}
            return {}
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory)
            (local / 'workspace-registry.json').write_text(json.dumps({'routes': {}, 'stores': {}}))
            (local / 'workspace-owner-key').write_text('fixture-owner')
            with patch.object(module, 'LOCAL', local), patch.object(module, 'gateway', return_value=request):
                module.owner_request(request)
        self.assertEqual(updates[0]['agents'], ['unrelated-agent'])
        self.assertEqual(updates[0]['vector_stores'], ['retained-store'])
        self.assertIn('custom-readonly', updates[0]['mcp_servers'])

    def test_provisioning_recovers_a_missing_owner_key_after_volume_reset(self):
        from unittest.mock import Mock, patch

        def admin(method, route, body=None):
            if route == '/team/list':
                return []
            if route.startswith('/key/info?'):
                raise RuntimeError('Gateway GET /key/info returned 404')
            if route == '/key/generate':
                return {'key': 'replacement'}
            return {}

        with tempfile.TemporaryDirectory() as directory, patch.object(module, 'LOCAL', Path(directory)):
            key_path = Path(directory) / 'workspace-owner-key'
            key_path.write_text('stale')
            with patch.object(module, 'gateway', Mock()) as gateway:
                module.owner_request(admin)
            gateway.assert_called_once_with('replacement')
            self.assertEqual(key_path.read_text(), 'replacement')

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

    def test_unchanged_secret_keeps_inode_for_running_bind_mounts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'caller.sha256'
            module.private_write(path, 'fixture-verifier')
            inode = path.stat().st_ino
            path.chmod(0o644)
            module.private_write(path, 'fixture-verifier')
            self.assertEqual(path.stat().st_ino, inode)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_catalog_registers_pinned_sources_without_duplicates(self):
        records = []
        def request(method, route, body=None):
            self.assertEqual(route, '/claude-code/plugins')
            if method == 'GET':
                return {'plugins': records}
            records.append(body)
            return {'status': 'success'}
        manifest = {'source': 'https://github.com/obra/superpowers', 'commit': 'a' * 40,
                    'skills': [{'name': 'systematic-debugging', 'profiles': ['development']}]}
        module.provision_skill_catalog(request, manifest)
        module.provision_skill_catalog(request, manifest)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['source']['sha'], 'a' * 40)
        self.assertEqual(records[0]['source']['path'], 'skills/systematic-debugging')
        self.assertIn('adaptation', records[0]['description'])

    def test_moyai_catalog_matches_packaged_skills_and_preserves_existing_entries(self):
        root = module.ROOT / 'integrations/moyai/skills'
        manifest = json.loads((root / 'catalog.json').read_text())
        self.assertRegex(manifest['commit'], r'^[0-9a-f]{40}$')
        self.assertEqual(sorted(skill['name'] for skill in manifest['skills']),
                         sorted(path.parent.name for path in root.glob('*/SKILL.md')))
        records = [{'name': 'operator-skill', 'source': {'source': 'github', 'repo': 'example/other'}}]

        def request(method, route, body=None):
            self.assertEqual(route, '/claude-code/plugins')
            if method == 'GET':
                return {'plugins': records}
            self.assertEqual(method, 'POST')
            records.append(body)

        module.provision_skill_catalog(request, manifest, root)
        module.provision_skill_catalog(request, manifest, root)
        all_skills = manifest['skills']
        self.assertEqual(len({skill['name'] for skill in all_skills}), 25)
        self.assertEqual(len(records), len(all_skills) + 1)
        by_catalog_name = {row['name']: row for row in records[1:]}
        for skill in all_skills:
            row = by_catalog_name[skill.get('catalog_name', skill['name'])]
            self.assertEqual(row['source'], {'source': 'git-subdir', 'url': manifest['source'] + '.git',
                                             'path': 'skills/' + skill['name'], 'sha': manifest['commit']})
            self.assertEqual(row['author']['name'], manifest['author'])
            name = skill['name']
            header = (root / name / 'SKILL.md').read_text().split('---\n')[1].splitlines()
            self.assertIn('name: ' + name, header)
            expected_body = (root / name / 'SKILL.md').read_text().split('\n---\n', 1)[1].strip()
            self.assertEqual(row['description'], skill['description'] + '\n\n' + expected_body)
            self.assertIn(module.TAG, row['keywords'])
        records[1]['source'] = records[0]['source'].copy()
        with self.assertRaisesRegex(ValueError, records[1]['name']):
            module.provision_skill_catalog(request, manifest, root)
        self.assertEqual(records[1]['source'], records[0]['source'])

    def test_catalog_only_entry_can_share_an_upstream_name_with_another_publisher(self):
        existing = {'name': 'test-driven-development', 'source': {'repo': 'obra/superpowers'}}
        records = [existing.copy()]
        def request(method, route, body=None):
            if method == 'GET':
                return {'plugins': records}
            records.append(body)
        manifest = {'source': 'https://github.com/addyosmani/agent-skills', 'author': 'Addy Osmani',
                    'commit': 'b' * 40, 'skills': [], 'marketplace_only': [
                        {'name': 'test-driven-development', 'catalog_name': 'addyosmani-test-driven-development',
                         'description': 'Drive changes with a failing test.'}]}
        module.provision_skill_catalog(request, manifest)
        module.provision_skill_catalog(request, manifest)
        self.assertEqual([row['name'] for row in records], [existing['name'], 'addyosmani-test-driven-development'])
        self.assertEqual(records[0], existing)
        self.assertEqual(records[1]['source']['path'], 'skills/test-driven-development')
        self.assertIn('/skills/test-driven-development', records[1]['homepage'])
        self.assertNotIn('offline adaptation', records[1]['description'])

    def test_promoting_a_marketplace_skill_updates_only_managed_metadata_once(self):
        manifest = json.loads((module.ROOT / 'integrations/moyai/skills/catalog.json').read_text())
        skill = next(row for row in manifest['skills'] if row.get('catalog_name'))
        manifest['skills'] = [skill]
        old = {
            'name': skill['catalog_name'],
            'source': {'source': 'git-subdir', 'url': manifest['source'] + '.git',
                       'sha': manifest['commit'], 'path': 'skills/' + skill['name']},
            'enabled': False, 'version': '2.3.1', 'domain': 'Engineering', 'namespace': 'workflows',
            'keywords': ['local-workspace', 'upstream-workflow'],
            'description': skill['description'] + ' Catalog installation retrieves the pinned upstream skill.',
        }
        changes = []

        def request(method, route, body=None):
            if method == 'GET':
                return {'plugins': [old]}
            self.assertEqual((method, route), ('PUT', '/claude-code/plugins/' + skill['catalog_name']))
            self.assertEqual(set(body), {'source', 'version', 'domain', 'namespace', 'description', 'keywords'})
            for key in ('source', 'version', 'domain', 'namespace'):
                self.assertEqual(body[key], old[key])
            changes.append(body)
            old.update(body)

        module.provision_skill_catalog(request, manifest, module.ROOT / 'integrations/moyai/skills')
        module.provision_skill_catalog(request, manifest, module.ROOT / 'integrations/moyai/skills')
        self.assertEqual(len(changes), 1)
        self.assertTrue(old['description'].startswith(skill['description'] + '\n\n'))
        old['description'] = 'Changed by operator\n\nLocal instructions'
        module.provision_skill_catalog(request, manifest, module.ROOT / 'integrations/moyai/skills')
        self.assertEqual(len(changes), 1)
        self.assertFalse(old['enabled'])

    def test_gateway_exposes_only_tagged_text_and_rejects_ambiguous_native_ids(self):
        from skill_marketplace import read_marketplace
        row = {'name': 'addyosmani-test-driven-development', 'description': 'Summary\r\n\r\nA local body',
               'keywords': [module.TAG], 'source': {'url': 'https://example.invalid'}}
        self.assertEqual(read_marketplace({'plugins': [row, {'name': 'other'}]}),
                         {'test-driven-development': {'description': 'Summary', 'body': 'A local body'}})
        with self.assertRaises(ValueError):
            read_marketplace({'plugins': [row, {**row, 'name': 'test-driven-development'}]})
        with self.assertRaises(ValueError):
            read_marketplace({'plugins': [{**row, 'description': 'No instructions'}]})
