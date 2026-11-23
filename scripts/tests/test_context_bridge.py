"""The internal context bridge never broadens an immutable source binding."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

import httpx
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


class ContextBridgeTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('local_context_bridge',
            ROOT / 'integrations/moyai/context_bridge.py')
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        try:
            spec.loader.exec_module(self.module)
        except FileNotFoundError:
            self.fail('The scoped repository context bridge is missing')
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.catalog = self.root / 'catalog.json'
        self.row = {'repository_id': 'repo_test', 'name': 'fixture',
                    'snapshot_id': 'a' * 64, 'generation_id': 'gen_old',
                    'store_id': 'vs_old', 'verification_profile': 'rust-retry',
                    'manifest': 'snapshots/' + 'a' * 64 + '/manifest.json'}
        latest = self.row | {'generation_id': 'gen_new', 'store_id': 'vs_new'}
        self.catalog.write_text(json.dumps({'schema_version': 1,
            'repositories': [latest], 'indexed_generations': [self.row, latest]}))
        token = self.root / 'bridge-token'; token.write_text('bridge-test-token')
        key = self.root / 'gateway-key'; key.write_text('gateway-test-token')
        self.config = self.module.BridgeConfig(self.catalog, token, key,
                                               'http://litellm:4000')
        self.requests = []
        self.payload = {'data': [{'file_id': 'chunk_7', 'filename': 'docs/policy.md',
            'content': [{'type': 'text', 'text': 'Retry ceiling: 30000 milliseconds.'}],
            'attributes': {'repository': 'repo_test', 'snapshot_id': 'a' * 64,
                'generation_id': 'gen_old', 'branch': 'main', 'commit_hash': 'abc',
                'start_line': 1, 'end_line': 3, 'chunk_id': 7,
                'dirty': True, 'subtree': 'service'}}]}

    def bridge(self, response=None):
        def handle(request):
            self.requests.append(request)
            return response or httpx.Response(200, json=self.payload)
        return self.module.ContextBridge(self.config, transport=httpx.MockTransport(handle))

    def test_old_pinned_generation_survives_latest_catalog_update(self):
        result = self.bridge().search('repo_test', 'a' * 64, 'gen_old', 'retry ceiling', 3)
        self.assertEqual(result['results'][0]['path'], 'docs/policy.md')
        self.assertEqual(result['results'][0]['start_line'], 1)
        self.assertEqual(result['generation_id'], 'gen_old')
        self.assertIs(result['results'][0]['dirty'], True)
        self.assertEqual(result['results'][0]['subtree'], 'service')
        self.assertEqual(result['results'][0]['chunk_id'], '7')
        self.assertEqual(self.requests[0].url.path, '/v1/vector_stores/vs_old/search')
        self.assertEqual(json.loads(self.requests[0].content)['query'], 'retry ceiling')
        self.assertEqual(json.loads(self.requests[0].content)['ranking_options'], {'ranker': 'auto'})

    def test_foreign_repository_snapshot_or_generation_never_reaches_gateway(self):
        bridge = self.bridge()
        for values in [('repo_other', 'a' * 64, 'gen_old'),
                       ('repo_test', 'b' * 64, 'gen_old'),
                       ('repo_test', 'a' * 64, 'gen_other')]:
            with self.subTest(values=values), self.assertRaises(PermissionError):
                bridge.search(*values, 'retry ceiling', 3)
        self.assertEqual(self.requests, [])

    def test_branch_search_requires_session_repository_target_and_indexed_sha(self):
        scope = 'connected/github/example/project'
        grant = {'scope': scope, 'branch': 'main', 'indexed': 'a' * 40, 'store': 'vs_branch',
                 'sessions': ['developer'], 'targets': {'default': True, 'pulls': [7]}}
        path = self.root / 'branch-search.json'
        path.write_text(json.dumps([grant]))
        url = 'https://github.com/example/project/pull/7'
        bridge = self.bridge()
        for user, target in [('other', url), ('developer', url[:-1] + '8'),
                             ('developer', url.replace('example', 'foreign'))]:
            with self.assertRaises(PermissionError):
                bridge.search_branch(user, target, 'policy', 1, None)
        attrs = self.payload['data'][0]['attributes']
        attrs.update(repository=scope, snapshot_id='legacy', generation_id='legacy')
        with self.assertRaises(RuntimeError):
            bridge.search_branch('developer', url, 'policy', 1, None)
        attrs['commit_hash'] = grant['indexed']
        self.assertEqual(bridge.search_branch('developer', url, 'policy', 1, None)['count'], 1)
        path.write_text('[]')
        with self.assertRaises(PermissionError):
            bridge.search_branch('developer', url, 'policy', 1, None)

    def test_exact_path_filters_the_pinned_store_and_rejects_other_results(self):
        bridge = self.bridge()
        scope = ('repo_test', 'a' * 64, 'gen_old', 'retry ceiling', 6)
        rows = bridge.search(*scope, exact_path='docs/policy.md')['results']
        self.assertEqual([row['path'] for row in rows], ['docs/policy.md'])
        self.assertEqual(self.requests[-1].url.path, '/v1/vector_stores/vs_old/search')
        self.assertEqual(json.loads(self.requests[-1].content)['filters'],
                         {'type':'eq', 'key':'path', 'value':'docs/policy.md'})
        with self.assertRaises(RuntimeError):
            bridge.search(*scope, exact_path='src/lib.rs')

    def test_gateway_result_with_other_generation_is_rejected(self):
        self.payload['data'][0]['attributes']['generation_id'] = 'gen_other'
        with self.assertRaises(RuntimeError):
            self.bridge().search('repo_test', 'a' * 64, 'gen_old', 'retry ceiling', 3)

    def test_invalid_query_limits_and_gateway_redirect_are_rejected(self):
        bridge = self.bridge()
        for query, limit in [('', 3), ('x' * 4001, 3), ('retry', 0), ('retry', True)]:
            with self.subTest(query=query[:10], limit=limit), self.assertRaises(ValueError):
                bridge.search('repo_test', 'a' * 64, 'gen_old', query, limit)
        self.assertEqual(self.requests, [])
        with self.assertRaises(RuntimeError):
            self.bridge(httpx.Response(302, headers={'location': 'http://example.invalid'})).search(
                'repo_test', 'a' * 64, 'gen_old', 'retry ceiling', 3)
        self.assertEqual(len(self.requests), 1)

    def test_context_content_budget_and_upstream_size_are_enforced(self):
        self.payload['data'][0]['content'][0]['text'] = 'x' * 30000
        result = self.bridge().search('repo_test', 'a' * 64, 'gen_old', 'retry ceiling', 3)
        self.assertEqual(len(result['results'][0]['content']), 24000)
        self.assertTrue(result['results'][0]['truncated'])
        with self.assertRaises(RuntimeError):
            self.bridge(httpx.Response(200, content=b'x' * 1_048_577)).search(
                'repo_test', 'a' * 64, 'gen_old', 'retry ceiling', 3)

    def test_mcp_requires_its_own_bearer_and_declares_read_only_tools(self):
        with TestClient(self.module.create_app(self.bridge()), base_url='http://127.0.0.1:8000') as client:
            payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}}
            self.assertEqual(client.post('/mcp/', json=payload).status_code, 401)
            response = client.post('/mcp/', json=payload, headers={
                'Authorization': 'Bearer bridge-test-token',
                'Accept': 'application/json, text/event-stream'})
            self.assertEqual(response.status_code, 200, response.text)
            tools = response.json()['result']['tools']
            self.assertEqual({tool['name'] for tool in tools}, {'search_repository', 'repository_status', 'search_connected_branch'})
            self.assertTrue(all(tool['annotations']['readOnlyHint'] for tool in tools))

    def test_mcp_executes_scoped_search_and_exports_metadata_metrics(self):
        with TestClient(self.module.create_app(self.bridge()), base_url='http://127.0.0.1:8000') as client:
            response = client.post('/mcp/', headers={
                'Authorization': 'Bearer bridge-test-token',
                'Accept': 'application/json, text/event-stream'}, json={
                    'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
                        'name': 'search_repository', 'arguments': {
                            'repository_id': 'repo_test', 'snapshot_id': 'a' * 64,
                            'generation_id': 'gen_old', 'query': 'retry ceiling', 'limit': 3}}})
            self.assertEqual(response.status_code, 200)
            result = response.json()['result']
            self.assertFalse(result.get('isError', False), result)
            self.assertEqual(result['structuredContent']['count'], 1)
            metrics = client.get('/metrics').text
            self.assertIn('local_repository_context_requests_total', metrics)
            self.assertNotIn('repo_test', metrics)
            self.assertNotIn('retry ceiling', metrics)
