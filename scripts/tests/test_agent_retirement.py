"""Retiring legacy routes must preserve user data and unrelated gateway objects."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]


class AgentRetirementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.local = Path(temporary.name)
        self.routes = {name: 'route-' + name for name in ('development', 'review', 'research')}
        self.agents = [{'agent_id': value, 'agent_name': 'workspace-' + name,
                        'agent_card_params': {'url': f'http://workspace-{name}:8100/profiles/{name}'}}
                       for name, value in self.routes.items()]
        self.agents.append({'agent_id': 'unrelated', 'agent_name': 'external-agent',
                            'agent_card_params': {'url': 'https://example.invalid/agent'}})
        self.permissions = {'agents': [*self.routes.values(), 'unrelated'],
                            'mcp_servers': ['local_retrieval'], 'vector_stores': ['vs_retained']}
        self.team = {'team_id': 'workspace-knowledge', 'object_permission': copy.deepcopy(self.permissions)}
        self.keys = {}
        for name, route in self.routes.items():
            home = self.local / 'profiles' / name
            home.mkdir(parents=True)
            (home / 'gateway.key').write_text('fixture-' + name)
            (home / 'retained-memory').write_text('retain-' + name)
            self.keys['fixture-' + name] = {'agent_id': route, 'key_alias': 'workspace-' + name,
                'team_id': 'workspace-knowledge',
                'object_permission': copy.deepcopy(self.permissions)}
        (self.local / 'workspace-client-key').write_text('fixture-console')
        self.keys['fixture-console'] = {'key_alias': 'workspace-console', 'team_id': 'workspace-knowledge',
                                       'object_permission': copy.deepcopy(self.permissions)}
        self.registry = self.local / 'workspace-registry.json'
        self.registry.write_text(json.dumps({'routes': self.routes, 'stores': {'fixture:main': 'vs_retained'}}))
        self.deleted = []
        self.fail_delete = False
        self.revoked = []

    def module(self):
        spec = importlib.util.spec_from_file_location('agent_retirement', ROOT / 'scripts/reconcile-agents.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def request(self, method, route, body=None):
        if method == 'GET' and route == '/v1/agents':
            return copy.deepcopy(self.agents)
        if method == 'GET' and route == '/team/list':
            return [copy.deepcopy(self.team)]
        if method == 'GET' and route.startswith('/key/info?key='):
            digest = route.split('=', 1)[1]
            return {'info': copy.deepcopy(next(value for key, value in self.keys.items()
                            if hashlib.sha256(key.encode()).hexdigest() == digest))}
        if method == 'GET' and route.startswith('/key/list?'):
            filters = parse_qs(urlsplit(route).query)
            return {'keys': [copy.deepcopy(info) | {'token': hashlib.sha256(key.encode()).hexdigest()}
                             for key, info in self.keys.items()
                             if info.get('key_alias') == filters['key_alias'][0]
                             and info.get('team_id') == filters['team_id'][0]], 'total_pages': 1}
        if method == 'POST' and route == '/team/update':
            self.team['object_permission'] = copy.deepcopy(body['object_permission'])
            return {}
        if method == 'POST' and route == '/key/update':
            key = self.resolve_key(body['key'])
            self.keys[key].update({k: copy.deepcopy(v) for k, v in body.items() if k != 'key'})
            return {}
        if method == 'POST' and route == '/key/delete':
            self.revoked.extend(self.resolve_key(key) for key in body['keys'])
            return {}
        if method == 'DELETE' and route.startswith('/v1/agents/'):
            if self.fail_delete:
                raise RuntimeError('controlled delete failure')
            identifier = route.rsplit('/', 1)[1]
            self.deleted.append(identifier)
            self.agents = [agent for agent in self.agents if agent['agent_id'] != identifier]
            return {}
        raise AssertionError(f'Unexpected request: {method} {route}')

    def resolve_key(self, value):
        return next(key for key in self.keys if value in (key, hashlib.sha256(key.encode()).hexdigest()))

    def test_retires_owned_routes_and_grants_but_preserves_other_agents_and_data(self):
        module = self.module()
        removed = module.reconcile(self.request, self.local)
        self.assertEqual(set(removed), set(self.routes.values()))
        self.assertEqual([agent['agent_id'] for agent in self.agents], ['unrelated'])
        registry = json.loads(self.registry.read_text())
        self.assertEqual(registry, {'routes': {}, 'stores': {'fixture:main': 'vs_retained'}})
        for name in self.routes:
            self.assertEqual((self.local / 'profiles' / name / 'retained-memory').read_text(), 'retain-' + name)
            self.assertEqual(self.keys['fixture-' + name]['agent_id'], None)
        for value in [self.team, *self.keys.values()]:
            self.assertEqual(value['object_permission']['agents'], ['unrelated'])
            self.assertEqual(value['object_permission']['vector_stores'], ['vs_retained'])
        self.assertEqual(module.reconcile(self.request, self.local), [])
        self.assertEqual(len(self.deleted), 3)

    def test_matching_name_on_an_unrelated_backend_is_not_owned(self):
        self.agents[0]['agent_id'] = 'different-route'
        self.agents[0]['agent_card_params']['url'] = 'https://example.invalid/agent'
        self.module().reconcile(self.request, self.local)
        self.assertIn('different-route', [agent['agent_id'] for agent in self.agents])
        self.assertNotIn('different-route', self.deleted)

    def test_lost_registry_can_still_reconcile_verified_local_backend_cards(self):
        self.registry.write_text(json.dumps({'stores': {'fixture:main': 'vs_retained'}}))
        self.module().reconcile(self.request, self.local)
        self.assertEqual(set(self.deleted), set(self.routes.values()))

    def test_delete_failure_is_not_reported_as_success_and_retains_recovery_registry(self):
        self.fail_delete = True
        with self.assertRaises(RuntimeError):
            self.module().reconcile(self.request, self.local)
        self.assertEqual(json.loads(self.registry.read_text())['routes'], self.routes)
        self.fail_delete = False
        self.module().reconcile(self.request, self.local)
        self.assertEqual(len(self.deleted), 3)

    def test_check_detects_legacy_routes_without_modifying_any_gateway_object(self):
        before = copy.deepcopy((self.agents, self.team, self.keys))
        with self.assertRaises(RuntimeError):
            self.module().check(self.request, self.local)
        self.assertEqual((self.agents, self.team, self.keys), before)
        self.module().reconcile(self.request, self.local)
        self.module().check(self.request, self.local)

    def test_explicit_purge_deletes_only_retired_profile_data_and_keys(self):
        calls = []
        def docker(*args):
            calls.append(args)
            if args[:2] == ('volume', 'ls'):
                return '\n'.join(['workspace_' + name + '_state' for name in self.routes])
            return ''
        for name in self.routes:
            self.keys['fixture-' + name]['key_alias'] = 'workspace-' + name
        self.keys['fixture-console']['key_alias'] = 'workspace-console'
        moyai = self.local / 'moyai'
        moyai.mkdir()
        (moyai / 'retained').write_text('moyai-data')
        (self.local / 'memory-passwords.json').write_text('legacy-passwords')
        module = self.module()
        module.purge(self.request, self.local, docker=docker)
        self.assertEqual(set(self.revoked), {'fixture-' + name for name in self.routes} | {'fixture-console'})
        self.assertTrue(all(not (self.local / 'profiles' / name).exists() for name in self.routes))
        self.assertFalse((self.local / 'memory-passwords.json').exists())
        self.assertEqual((moyai / 'retained').read_text(), 'moyai-data')
        self.assertEqual(json.loads(self.registry.read_text())['stores'], {'fixture:main': 'vs_retained'})
        removals = [args for args in calls if args[:2] == ('volume', 'rm')]
        self.assertEqual(set(removals[0][2:]), {'workspace_' + name + '_state' for name in self.routes})
        sql = next(args for args in calls if 'psql' in args)
        self.assertIn('DROP DATABASE IF EXISTS workspace_memory WITH (FORCE)', sql)
        self.assertFalse(any('moyai' in str(args) for args in calls))

    def test_purge_refuses_to_revoke_a_foreign_key_stored_in_a_legacy_path(self):
        self.keys['fixture-development']['key_alias'] = 'unrelated-owner'
        with self.assertRaises(RuntimeError):
            self.module().purge(self.request, self.local, docker=lambda *args: '')
        self.assertEqual(self.revoked, [])
        self.assertTrue((self.local / 'profiles' / 'development' / 'retained-memory').exists())

    def test_purge_finds_dedicated_keys_even_after_a_local_key_file_is_lost(self):
        (self.local / 'profiles/development/gateway.key').unlink()
        self.module().purge(self.request, self.local, docker=lambda *args: '')
        self.assertIn('fixture-development', self.revoked)

    def test_lost_registry_and_deleted_agents_do_not_leave_key_associations_or_grants(self):
        self.registry.unlink()
        self.agents = [agent for agent in self.agents if agent['agent_id'] == 'unrelated']
        self.module().reconcile(self.request, self.local)
        for name in self.routes:
            self.assertIsNone(self.keys['fixture-' + name]['agent_id'])
        self.assertEqual(self.team['object_permission']['agents'], ['unrelated'])

    def test_same_key_alias_in_another_team_is_not_reconciled_or_revoked(self):
        self.keys['foreign'] = {'key_alias': 'workspace-development', 'team_id': 'foreign-team',
                                'agent_id': 'foreign-agent', 'object_permission': {'agents': ['foreign-agent']}}
        expected = copy.deepcopy(self.keys['foreign'])
        self.module().purge(self.request, self.local, docker=lambda *args: '')
        self.assertNotIn('foreign', self.revoked)
        self.assertEqual(self.keys['foreign'], expected)
