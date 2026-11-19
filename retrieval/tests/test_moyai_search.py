from dataclasses import replace
from types import SimpleNamespace

import httpx

from local_ai_retrieval import moyai_search as search
from local_ai_retrieval import vector_registry


def row(i, path, score, text='source'):
    return {'id': i, 'path': path, 'similarity': score, 'content': text}


def test_confident_hybrid_admits_third_chunk_only_with_support():
    rows = [
        row(1, 'tests/test_tmpdir.py', .99), row(2, 'tmpdir.py', .95),
        row(3, 'docs/note.md', .90), row(4, 'tmpdir.py', .89),
        row(5, 'other.py', .71), row(6, 'tmpdir.py', .68),
        *[row(i, f'file{i}.py', .6) for i in range(7, 12)]]
    chosen = search.select_chunks(None, 'cleanup', rows, 6)
    assert [r['id'] for r in chosen] == [2, 4, 5, 6, 7, 8]
    rows[5], rows[6] = rows[6], rows[5]
    assert [r['id'] for r in search.select_chunks(None, 'cleanup', rows, 6)] == [2, 4, 5, 7, 8, 9]


def test_flat_scores_rank_windows_and_keep_source_chunks(monkeypatch):
    rows = [row(i, 'a.py' if i < 3 else f'f{i}.py', .8, 'x' * 3000) for i in range(8)]
    used = []
    def rank(settings, query, code):
        used.extend(code)
        return list(reversed(code))
    monkeypatch.setattr(search, 'rank_windows', rank)
    assert [r['id'] for r in search.select_chunks(None, 'query', rows, 4)] == [7, 6, 5, 4]
    assert used == rows


def test_window_ranking_uses_best_window_via_gateway(monkeypatch):
    import json

    rows = [row(1, 'a.py', .7, 'a' * 1300 + 'b' * 300 + 'c' * 250), row(2, 'b.py', .7)]
    def respond(request):
        assert request.url == 'http://gateway/v1/rerank'
        assert request.headers['Authorization'] == 'Bearer scoped-key'
        body = json.loads(request.content)
        assert body['model'] == 'local-reranker' and body['query'] == 'find code'
        assert body['documents'] == [
            'a.py\n' + 'a' * 1300 + 'b' * 300,
            'a.py\n' + 'b' * 300 + 'c' * 250,
            'b.py\nsource',
        ]
        return httpx.Response(200, json={'results': [{'index': i} for i in (1, 2, 0)]})
    client = httpx.Client(transport=httpx.MockTransport(respond))
    monkeypatch.setattr(search.httpx, 'Client', lambda **kwargs: client)
    settings = SimpleNamespace(litellm_base_url='http://gateway/v1', api_key=lambda: 'scoped-key')
    assert search.select_chunks(settings, 'find code', rows, 2) == rows


def test_sparse_hybrid_keeps_order_when_third_chunk_already_selected():
    rows = [row(1, 'a.py', .9), row(2, 'a.py', .8), row(3, 'a.py', .7),
            row(4, 'docs/api.md', .6)]
    assert [r['id'] for r in search.select_chunks(None, 'query', rows, 6)] == [1, 2, 4, 3]


def test_full_leader_fair_shares_and_complete_tail():
    rows = [row(i, f'{i}.py', .8, 'source line\n' * 600) for i in range(6)]
    rows[1]['content'] = 'short'
    rows[4]['content'] += 'dependency changed'
    packed = search.pack_chunks(rows, 24000)
    assert [r['id'] for r in packed] == list(range(6))
    assert packed[0]['content'] == rows[0]['content']
    assert not packed[0]['truncated'] and not packed[1]['truncated']
    assert packed[4]['truncated'] and search.GAP in packed[4]['content']
    assert packed[4]['content'].endswith('dependency changed')
    assert sum(len(r['content']) for r in packed) <= 24000
    assert search.pack_chunks([], 24000) == []
    assert sum(len(r['content']) for r in search.pack_chunks(rows, 1)) <= 1


def test_scoped_auto_fetches_48_before_packing(db_settings, monkeypatch):
    calls = []
    def fetch(settings, vector, **kwargs):
        calls.append((settings, kwargs))
        return [row(i, 'implementation.py', .9, 'line\n' * 1600) for i in range(48)]
    monkeypatch.setattr(vector_registry, 'search_chunks', fetch)
    monkeypatch.setattr(vector_registry, 'compatible_scope_exists', lambda *a, **kw: True)
    monkeypatch.setattr(vector_registry, 'embed_texts', lambda *a, **kw: [[0.]])
    from local_ai_retrieval import ingestion
    monkeypatch.setattr(ingestion, 'embedding_fingerprint', lambda *a: 'fp')
    scope = {'repository':'r','branch':'main','snapshot_id':'s','generation_id':'g','embedding_fingerprint':'fp'}
    rows = vector_registry.scoped_search(replace(db_settings,embedding_artifact_id='test'), scope, 'term', 'implementation.py', 6, auto_rank=True)
    assert len(rows) == 6 and sum(len(r['content']) for r in rows) <= 24000
    settings, opts = calls.pop()
    assert settings.max_limit == opts['limit'] == 48
    assert opts['exact_path'] == 'implementation.py' and opts['generation_id'] == 'g' and opts['snapshot_id'] == 's'
