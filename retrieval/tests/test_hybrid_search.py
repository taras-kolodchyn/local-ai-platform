"""Hybrid ranking/scope contracts against PostgreSQL 17 / pgvector 0.8.7."""
from dataclasses import replace
import importlib.util
import json
import os

import psycopg
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

os.environ.setdefault('DATABASE_URL', 'postgresql://unused:unused@127.0.0.1/unused')

from local_ai_retrieval import service, vector_registry
from local_ai_retrieval.db import migrate, search_chunks, vector_literal
from local_ai_retrieval.vector_api import vector_routes

QUERY_VECTOR = [1.0] + [0.0] * 1023
OPPOSITE_VECTOR = [-1.0] + [0.0] * 1023


def add_chunk(settings, *, path='src/policy.py', symbol='policy', content='policy text',
              embedding=None, repository='fixture', branch='main', chunk_index=0):
    with psycopg.connect(settings.database_url) as connection:
        return connection.execute(
            '''INSERT INTO source_chunks
               (repository, branch, commit_hash, path, language, symbol_name,
                chunk_index, start_line, end_line, chunk_hash, content, embedding)
               VALUES (%s,%s,'abc',%s,'python',%s,%s,3,5,'hash',%s,%s::vector)
               RETURNING id''',
            (repository, branch, path, symbol, chunk_index, content,
             vector_literal(QUERY_VECTOR if embedding is None else embedding)),
        ).fetchone()[0]


def add_semantic_distractors(settings):
    # More than the bounded candidate pool for limit=2; target cosine=-1 is excluded.
    return [add_chunk(settings, path=f'src/near_{i:02}.py', symbol='unrelated',
                      content='semantically near but no keyword match') for i in range(40)]


@pytest.mark.parametrize('query,path,symbol,content', [
    ('resolveAccessToken', 'src/auth.py', 'resolveAccessToken', 'return token'),
    ('src/special/retry_policy.py', 'src/special/retry_policy.py', None, 'return retry'),
    ('retry policy', 'src/special/retry_policy.py', None, 'return value'),
    ('ECONNRESET', 'src/socket.py', None, 'raise ECONNRESET'),
])
def test_keyword_hit_outside_vector_candidates_is_retrieved(db_settings, query, path, symbol, content):
    add_semantic_distractors(db_settings)
    target = add_chunk(db_settings, path=path, symbol=symbol, content=content,
                       embedding=OPPOSITE_VECTOR)
    rows = search_chunks(db_settings, QUERY_VECTOR, query=query, limit=2)
    assert rows[0]['id'] == target
    assert rows[0]['similarity'] == pytest.approx(-1)
    assert rows[0]['relevance'] == pytest.approx(1 / 61)
    assert len(rows) == 2
    baseline = search_chunks(db_settings, QUERY_VECTOR, query=query,
                             query_mode='semantic', limit=2)
    assert target not in [row['id'] for row in baseline]


def test_rank_fusion_deduplicates_and_preserves_semantic_only_hits(db_settings):
    semantic = add_chunk(db_settings, path='a.py', symbol=None, content='unrelated')
    shared = add_chunk(db_settings, path='b.py', symbol='needle', content='needle')
    lexical = add_chunk(db_settings, path='c.py', symbol='needle', content='needle',
                        embedding=OPPOSITE_VECTOR)
    rows = search_chunks(db_settings, QUERY_VECTOR, query='needle', limit=3)
    assert [row['id'] for row in rows] == [shared, lexical, semantic]
    assert len({row['id'] for row in rows}) == 3
    assert rows[0]['relevance'] == pytest.approx(1 / 62 + 1 / 61)
    assert rows[-1]['relevance'] == pytest.approx(1 / 61)
    assert rows[-1]['similarity'] == pytest.approx(1)


def test_equal_scores_are_repeatable_and_have_stable_chunk_id_ties(db_settings):
    ids = [add_chunk(db_settings, path='src/ties.py', symbol='needle', content='needle',
                     chunk_index=i) for i in range(8)]
    for _ in range(3):
        rows = search_chunks(db_settings, QUERY_VECTOR, query='needle', limit=4)
        assert [row['id'] for row in rows] == ids[:4]


def test_semantic_index_pool_is_bounded_before_tie_sorting(db_settings, monkeypatch):
    # Inspect the actual production statement, not a second hand-written query.
    # Disabling seqscan checks HNSW eligibility independently of table size.
    plans = []

    class ExplainConnection(psycopg.Connection):
        def execute(self, query, params=None, **kwargs):
            if isinstance(query, str) and query.lstrip().startswith('WITH search_input AS'):
                super().execute('SET LOCAL enable_seqscan=off')
                plans.extend(super().execute('EXPLAIN (FORMAT JSON) ' + query, params)
                             .fetchone()['QUERY PLAN'])
            return super().execute(query, params, **kwargs)

    monkeypatch.setattr(psycopg, 'connect', ExplainConnection.connect)
    assert search_chunks(db_settings, QUERY_VECTOR, query='needle', limit=2) == []

    def nodes(node):
        yield node
        for child in node.get('Plans', []):
            yield from nodes(child)

    pool = next(node for node in nodes(plans[0]['Plan'])
                if node.get('Subplan Name') == 'CTE semantic_candidates')
    assert pool['Node Type'] == 'Limit'
    assert any(node.get('Index Name') == 'source_chunks_hnsw_cosine' for node in nodes(pool))
    # A sort below this LIMIT can consume an entire equal-distance group before
    # limiting it. Rank/tie sorting must operate only on the materialized pool.
    assert not any(node['Node Type'] in {'Sort', 'Incremental Sort', 'WindowAgg'}
                   for node in nodes(pool))


@pytest.mark.parametrize('filters,expected_path', [
    ({'repository': 'fixture', 'branch': 'main'}, 'wanted/file.py'),
    ({'repository': 'fixture', 'branch': 'main', 'path_prefix': 'wanted/'}, 'wanted/file.py'),
    ({'repository': 'fixture', 'branch': 'main', 'exact_path': 'wanted/file.py'}, 'wanted/file.py'),
    ({'repository': 'fixture', 'branch': 'main', 'path_prefix': 'literal_%\\'}, 'literal_%\\file.py'),
])
def test_both_candidate_paths_obey_all_scopes(db_settings, filters, expected_path):
    # High-scoring rows outside any scope must never consume or widen candidates.
    add_chunk(db_settings, repository='other', path=expected_path, symbol='needle')
    add_chunk(db_settings, branch='other', path=expected_path, symbol='needle')
    if 'path_prefix' in filters or 'exact_path' in filters:
        add_chunk(db_settings, path='not_wanted/file.py', symbol='needle')
        add_chunk(db_settings, path='literal_ABfile.py', symbol='needle')
    wanted = add_chunk(db_settings, path=expected_path, symbol='needle', embedding=OPPOSITE_VECTOR)
    rows = search_chunks(db_settings, QUERY_VECTOR, query='needle', **filters)
    assert [row['id'] for row in rows] == [wanted]
    assert (rows[0]['repository'], rows[0]['branch'], rows[0]['path']) == (
        'fixture', 'main', expected_path)


def test_conflicting_exact_and_prefix_scopes_return_no_rows(db_settings):
    add_chunk(db_settings, path='wanted/file.py', symbol='needle')
    assert search_chunks(db_settings, QUERY_VECTOR, query='needle',
                         exact_path='wanted/file.py', path_prefix='other/') == []


@pytest.mark.parametrize('query', ['', '   ', '" & | ! :* ( ) <->', "'); DROP TABLE source_chunks; --"])
def test_empty_or_untrusted_keywords_are_plain_text_not_query_syntax(db_settings, query):
    target = add_chunk(db_settings, content='boring source text')
    rows = search_chunks(db_settings, QUERY_VECTOR, query=query)
    assert [row['id'] for row in rows] == [target]
    with psycopg.connect(db_settings.database_url) as connection:
        assert connection.execute('SELECT count(*) FROM source_chunks').fetchone()[0] == 1


def test_all_paths_preserve_result_and_context_limits_and_citations(db_settings):
    settings = replace(db_settings, max_limit=2, max_context_chars=300)
    add_chunk(settings, symbol='needle', content='needle ' + 'x' * 500)
    add_chunk(settings, path='second.py', symbol='needle', content='second')
    rows = search_chunks(settings, QUERY_VECTOR, query='needle', limit=100, max_chars=10000)
    assert len(rows) == 1
    assert len(rows[0]['content']) == 300
    assert {key: rows[0][key] for key in ('repository', 'branch', 'commit_hash', 'path',
                                        'start_line', 'end_line')} == {
        'repository': 'fixture', 'branch': 'main', 'commit_hash': 'abc',
        'path': 'src/policy.py', 'start_line': 3, 'end_line': 5,
    }
    rows = search_chunks(settings, QUERY_VECTOR, query='needle', limit=1, max_chars=20)
    assert len(rows) == 1 and len(rows[0]['content']) == 20
    with pytest.raises(ValueError):
        search_chunks(settings, QUERY_VECTOR, query='needle', limit=-1)
    with pytest.raises(ValueError):
        search_chunks(settings, QUERY_VECTOR, query='needle', max_chars=-1)


def test_migration_indexes_existing_chunks_and_tracks_content_updates(db_settings):
    with psycopg.connect(db_settings.database_url) as connection:
        connection.execute('DROP INDEX IF EXISTS source_chunks_search_vector_gin')
        connection.execute('ALTER TABLE source_chunks DROP COLUMN IF EXISTS search_vector')
    add_semantic_distractors(db_settings)
    target = add_chunk(db_settings, path='migrated.py', symbol=None, content='original_marker',
                       embedding=OPPOSITE_VECTOR)
    migrate(db_settings)
    migrate(db_settings)
    assert search_chunks(db_settings, QUERY_VECTOR, query='original_marker', limit=1)[0]['id'] == target
    with psycopg.connect(db_settings.database_url) as connection:
        connection.execute('UPDATE source_chunks SET content=%s WHERE id=%s', ('replacement_marker', target))
    assert search_chunks(db_settings, QUERY_VECTOR, query='replacement_marker', limit=1)[0]['id'] == target
    assert search_chunks(db_settings, QUERY_VECTOR, query='original_marker', limit=1)[0]['id'] != target


def test_http_mcp_and_scoped_vector_store_use_the_real_hybrid_search(db_settings, monkeypatch, tmp_path):
    # The SDK's session manager can start only once. Load a separate service
    # instance so this transport test cannot consume another test's lifespan.
    spec = importlib.util.spec_from_file_location('local_ai_retrieval.hybrid_service', service.__file__)
    transport = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport)
    add_semantic_distractors(db_settings)
    target = add_chunk(db_settings, path='auth.py', symbol='resolveAccessToken',
                       content='return token', embedding=OPPOSITE_VECTOR)
    key = tmp_path / 'key'
    key.write_text('backend-test')
    settings = replace(db_settings, vector_key_file=key, embedding_artifact_id='sha256:fixture')
    from local_ai_retrieval.ingestion import embedding_fingerprint
    with psycopg.connect(settings.database_url) as connection:
        connection.execute("UPDATE source_chunks SET metadata=jsonb_build_object('embedding_fingerprint', %s::text)",
            (embedding_fingerprint(settings, settings.embedding_artifact_id),))
    monkeypatch.setattr(transport, 'SETTINGS', settings)
    # Embedding inference is the only external boundary replaced; SQL/search stay real.
    monkeypatch.setattr(transport, 'embed_texts', lambda *_: [QUERY_VECTOR])
    monkeypatch.setattr(vector_registry, 'embed_texts', lambda *_, **__: [QUERY_VECTOR])
    store = vector_registry.register_store(settings, 'fixture', 'main')
    with TestClient(transport.app, base_url='http://127.0.0.1:8000') as client:
        body = {'query': 'resolveAccessToken', 'repository': 'fixture', 'branch': 'main', 'limit': 2}
        response = client.post('/search', json=body)
        assert response.status_code == 200
        assert response.json()['results'][0]['id'] == target
        response = client.post('/mcp/', headers={
            'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-11-25',
        }, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                 'params': {'name': 'search_code', 'arguments': body}})
        assert response.status_code == 200
        assert response.json()['result']['isError'] is False
        assert json.loads(response.json()['result']['structuredContent']['result'])[0]['id'] == target
    rows = vector_registry.scoped_search(settings, store, 'resolveAccessToken', 'auth.py', 2)
    assert [row['id'] for row in rows] == [target]
    api = Starlette(routes=vector_routes(
        key, lambda: vector_registry.list_stores(settings),
        lambda scope, query, path, limit: vector_registry.scoped_search(settings, scope, query, path, limit),
    ))
    with TestClient(api) as client:
        response = client.post(f'/v1/vector_stores/{store["id"]}/search',
            headers={'Authorization': 'Bearer backend-test'},
            json={'query': 'resolveAccessToken', 'max_num_results': 2,
                  'ranking_options': {'score_threshold': 0.49}})
        assert response.status_code == 200
        item = response.json()['data'][0]
        assert item['file_id'] == f'chunk_{target}'
        assert item['score'] == pytest.approx(0.5)
        assert item['attributes']['cosine_similarity'] == pytest.approx(-1)
