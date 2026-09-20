import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from local_ai_retrieval.vector_api import vector_routes


@pytest.fixture
def client(tmp_path):
    key = tmp_path / 'key'
    key.write_text('backend-test')
    stores = [{'id': 'vs_test', 'repository': 'fixture', 'branch': 'main'}]

    def search(scope, query, path, limit):
        assert scope['repository'] == 'fixture'
        assert scope['branch'] == 'main'
        if query == 'timeout':
            raise TimeoutError('private database details')
        if query == 'empty':
            return []
        return [{'id': 1, 'path': 'docs/policy.md', 'similarity': 0.9,
                 'repository': 'fixture', 'branch': 'main', 'commit_hash': 'abc',
                 'start_line': 3, 'end_line': 5, 'content': 'Fixture policy.'}]

    return TestClient(Starlette(routes=vector_routes(
        key, lambda: stores, search)))


AUTH = {'Authorization': 'Bearer backend-test'}


@pytest.mark.parametrize('route', ['/v1/vector_stores', '/v1/vector_stores/vs_test'])
def test_backend_metadata_requires_own_credentials(client, route):
    assert client.get(route).status_code == 401
    assert client.get(route, headers={'Authorization': 'Bearer ordinary-client'}).status_code == 401
    assert client.get(route, headers=AUTH).status_code == 200


def test_search_returns_citable_results(client):
    response = client.post('/v1/vector_stores/vs_test/search', headers=AUTH,
                           json={'query': 'policy', 'max_num_results': 2})
    assert response.status_code == 200
    item = response.json()['data'][0]
    assert item['file_id'] == 'chunk_1'
    assert item['content'] == [{'type': 'text', 'text': 'Fixture policy.'}]
    assert item['attributes']['start_line'] == 3
    assert item['score'] == 0.9


@pytest.mark.parametrize('body', [
    {'query': 'policy', 'filters': {'type': 'eq', 'key': 'repository', 'value': 'other'}},
    {'query': 'policy', 'filters': {'type': 'or', 'filters': []}},
    {'query': ['policy']}, {'query': 'x'}, {'query': 'x' * 4001},
    {'query': 'policy', 'max_num_results': 21},
    {'query': 'policy', 'api_base': 'http://elsewhere'},
    {'query': 'policy', 'rewrite_query': True},
])
def test_invalid_or_scope_widening_requests_fail(client, body):
    assert client.post('/v1/vector_stores/vs_test/search', headers=AUTH, json=body).status_code == 400


def test_unknown_store_and_malformed_body(client):
    assert client.post('/v1/vector_stores/missing/search', headers=AUTH,
                       json={'query': 'policy'}).status_code == 404
    assert client.post('/v1/vector_stores/vs_test/search', headers=AUTH, content='{').status_code == 400
    assert client.post('/v1/vector_stores/vs_test/search', headers=AUTH, content='x' * 65537).status_code == 413


def test_search_auth_and_timeout_redaction(client):
    url = '/v1/vector_stores/vs_test/search'
    assert client.post(url, json={'query': 'policy'}).status_code == 401
    response = client.post(url, headers=AUTH, json={'query': 'timeout'})
    assert response.status_code == 504
    assert 'private' not in response.text
    assert client.post(url, headers=AUTH, json={'query': 'empty'}).json()['data'] == []


def test_cursor_and_conflicting_filters(client):
    assert client.get('/v1/vector_stores?after=missing', headers=AUTH).status_code == 400
    assert client.get('/v1/vector_stores?limit=0', headers=AUTH).status_code == 400
    assert client.get('/v1/vector_stores?after=vs_test', headers=AUTH).json()['data'] == []
    body = {'query': 'policy', 'filters': {'type': 'and', 'filters': [
        {'type': 'eq', 'key': 'path', 'value': 'one'},
        {'type': 'eq', 'key': 'path', 'value': 'two'}]}}
    assert client.post('/v1/vector_stores/vs_test/search', headers=AUTH, json=body).status_code == 400


def test_gateway_null_optional_fields(client):
    body = {'query': 'policy', 'max_num_results': None, 'rewrite_query': None,
            'ranking_options': None, 'filters': None}
    assert client.post('/v1/vector_stores/vs_test/search', headers=AUTH, json=body).status_code == 200
