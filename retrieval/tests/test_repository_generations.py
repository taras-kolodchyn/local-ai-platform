"""Generation contracts use disposable PostgreSQL; inference boundary alone is replaced."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest
import psycopg
from starlette.applications import Starlette
from starlette.testclient import TestClient
from local_ai_retrieval.vector_api import vector_routes

from local_ai_retrieval import ingestion, vector_registry
from local_ai_retrieval.db import search_chunks

VECTOR = [1.0] + [0.0] * 1023


def manifest(tmp_path, text):
    oracle = Path(__file__).resolve().parents[2] / 'integrations/moyai/oracles'
    files = {'src/lib.rs': text.encode()}
    files.update({name: (oracle / name).read_bytes() for name in ('Cargo.toml', 'Cargo.lock')})
    value = {'schema_version': 1, 'repository_id': 'repo_fixture', 'name': 'fixture',
             'git': {'branch': 'main', 'head': 'abc', 'dirty': True, 'subtree': ''},
             'scanner_version': 'repository-source-v1', 'verification_profile': 'rust-retry',
             'files': [{'path': p, 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()} for p, raw in sorted(files.items())]}
    value['snapshot_id'] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    root = tmp_path / 'catalog' / 'snapshots' / value['snapshot_id']
    source = root / 'source'
    (source / 'src').mkdir(parents=True)
    for name, raw in files.items():
        (source / name).write_bytes(raw)
    path = root / 'manifest.json'; path.write_text(json.dumps(value))
    return path


def rewrite_manifest(path, value):
    destination = path.parent.parent / value['snapshot_id']
    path.write_text(json.dumps(value))
    path.parent.rename(destination)
    return destination / 'manifest.json'


def index(path, settings, *, embedding_artifact_id, **kwargs):
    return ingestion.index_snapshot(path, replace(settings, embedding_artifact_id=embedding_artifact_id),
        embedding_artifact_id=embedding_artifact_id, catalog_root=path.parents[2], **kwargs)


def test_pinned_both_channels_and_failed_force_preserves_active(db_settings, tmp_path, monkeypatch):
    monkeypatch.setattr(ingestion, 'embed_texts', lambda *a, **k: [VECTOR for _ in a[0]])
    monkeypatch.setattr(vector_registry, 'embed_texts', lambda *a, **k: [VECTOR])
    one = index(manifest(tmp_path, 'old needle policy'), db_settings, embedding_artifact_id='sha256:model1')
    two_path = manifest(tmp_path, 'new needle policy')
    two = index(two_path, db_settings, embedding_artifact_id='sha256:model1')
    assert one['store_id'] != two['store_id']
    for mode in ('hybrid', 'semantic'):
        old = search_chunks(db_settings, VECTOR, query='needle', exact_path='src/lib.rs', query_mode=mode, generation_id=one['generation_id'])
        assert [r['content'] for r in old] == ['old needle policy']
        assert old[0]['snapshot_id'] == one['snapshot_id']
        current = search_chunks(db_settings, VECTOR, query='needle', exact_path='src/lib.rs', query_mode=mode)
        assert [r['content'] for r in current] == ['new needle policy']
    stores = vector_registry.list_stores(db_settings)
    old_scope = next(s for s in stores if s['id'] == one['store_id'])
    settings = replace(db_settings, embedding_artifact_id='sha256:model1')
    assert vector_registry.scoped_search(settings, old_scope, 'needle', 'src/lib.rs', 3)[0]['content'] == 'old needle policy'
    key = tmp_path / 'key'; key.write_text('private-test')
    api = Starlette(routes=vector_routes(key, lambda: vector_registry.list_stores(settings),
        lambda scope, query, path, limit: vector_registry.scoped_search(settings, scope, query, path, limit)))
    with TestClient(api) as client:
        response = client.post(f"/v1/vector_stores/{one['store_id']}/search",
            headers={'Authorization': 'Bearer private-test'}, json={'query': 'needle', 'filters': {'type': 'eq', 'key': 'path', 'value': 'src/lib.rs'}})
        assert response.status_code == 200
        attrs = response.json()['data'][0]['attributes']
        assert attrs['snapshot_id'] == one['snapshot_id']
        assert attrs['generation_id'] == one['generation_id']
        assert attrs['repository'] == one['repository_id']
        assert 'git' not in attrs
        assert attrs['dirty'] is True
        assert attrs['subtree'] == ''
        assert all(type(value) in (str, int, float, bool) for value in attrs.values())

    def fail(*a, **k):
        raise RuntimeError('embedding unavailable')
    monkeypatch.setattr(ingestion, 'embed_texts', fail)
    with pytest.raises(RuntimeError):
        index(two_path, db_settings, embedding_artifact_id='sha256:model2', force=True)
    assert search_chunks(db_settings, VECTOR, query='needle')[0]['generation_id'] == two['generation_id']


def test_model_identity_invalidates_generation_reuse(db_settings, tmp_path, monkeypatch):
    batches = []
    def embed(texts, *args, **kwargs):
        batches.append(texts)
        return [VECTOR for _ in texts]
    monkeypatch.setattr(ingestion, 'embed_texts', embed)
    path = manifest(tmp_path, 'needle source')
    first = index(path, db_settings, embedding_artifact_id='sha256:one')
    same = index(path, db_settings, embedding_artifact_id='sha256:one')
    assert same['generation_id'] == first['generation_id']
    assert len(batches) == 1
    changed = index(path, db_settings, embedding_artifact_id='sha256:two')
    assert changed['generation_id'] != first['generation_id']
    assert len(batches) == 2
    with pytest.raises(ValueError):
        index(path, db_settings, embedding_artifact_id='')


def test_pinned_scope_rejects_query_model_mismatch_before_inference(db_settings, tmp_path, monkeypatch):
    monkeypatch.setattr(ingestion, 'embed_texts', lambda *a, **k: [VECTOR for _ in a[0]])
    indexed = index(manifest(tmp_path, 'needle policy'), db_settings, embedding_artifact_id='sha256:original')
    scope = next(s for s in vector_registry.list_stores(db_settings) if s['id'] == indexed['store_id'])
    called = []
    monkeypatch.setattr(vector_registry, 'embed_texts', lambda *a, **k: called.append(1))
    for artifact in ('', 'sha256:different'):
        with pytest.raises(ValueError, match='embedding identity'):
            vector_registry.scoped_search(replace(db_settings, embedding_artifact_id=artifact), scope, 'needle', None, 3)
    assert called == []


def test_partial_database_publish_rolls_back_without_replacing_previous(db_settings, tmp_path, monkeypatch):
    monkeypatch.setattr(ingestion, 'embed_texts', lambda *a, **k: [VECTOR for _ in a[0]])
    first = index(manifest(tmp_path, 'old needle'), db_settings, embedding_artifact_id='sha256:model')
    pending = manifest(tmp_path, '\n'.join(f'line {i} needle' for i in range(201)))
    with psycopg.connect(db_settings.database_url) as connection:
        before = connection.execute('SELECT count(*) FROM source_chunks').fetchone()[0]
    monkeypatch.setattr(ingestion, 'embed_texts', lambda *a, **k: [VECTOR if i == 0 else VECTOR[:-1] for i, _ in enumerate(a[0])])
    with pytest.raises(psycopg.DataError):
        index(pending, db_settings, embedding_artifact_id='sha256:model')
    assert len(vector_registry.list_stores(db_settings)) == 1
    assert search_chunks(db_settings, VECTOR, query='needle')[0]['generation_id'] == first['generation_id']
    with psycopg.connect(db_settings.database_url) as connection:
        assert connection.execute('SELECT count(*) FROM repository_generations').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM source_chunks').fetchone()[0] == before


def test_embedding_fingerprint_covers_dimensions_and_chunker_source(db_settings, monkeypatch):
    fingerprint = ingestion.embedding_fingerprint(db_settings, 'sha256:model')
    assert ingestion.embedding_fingerprint(replace(db_settings, embedding_dimensions=512), 'sha256:model') != fingerprint
    original = ingestion.Path.read_bytes
    def changed(path):
        return b'different chunker implementation' if path.name == 'chunker.py' else original(path)
    monkeypatch.setattr(ingestion.Path, 'read_bytes', changed)
    assert ingestion.embedding_fingerprint(db_settings, 'sha256:model') != fingerprint


def test_manifest_tamper_fails_before_embedding_or_publication(db_settings, tmp_path, monkeypatch):
    path = manifest(tmp_path, 'needle source')
    (path.parent / 'source/src/lib.rs').write_text('tampered')
    calls = []
    monkeypatch.setattr(ingestion, 'embed_texts', lambda *a, **k: calls.append(1))
    with pytest.raises(ValueError, match='size changed|hash changed'):
        index(path, db_settings, embedding_artifact_id='sha256:model')
    assert calls == []
    assert vector_registry.list_stores(db_settings) == []


def test_indexing_rejects_deployment_identity_drift_before_reuse_or_inference(db_settings, tmp_path, monkeypatch):
    path = manifest(tmp_path, 'original source')
    monkeypatch.setattr(ingestion, 'embed_texts', lambda *a, **k: [VECTOR for _ in a[0]])
    first = ingestion.index_snapshot(path, replace(db_settings, embedding_artifact_id='sha256:serving'), embedding_artifact_id='sha256:serving', catalog_root=path.parents[2])
    calls = []
    monkeypatch.setattr(ingestion, 'embed_texts', lambda *a, **k: calls.append(1))
    with pytest.raises(ValueError, match='deployment identity'):
        ingestion.index_snapshot(path, replace(db_settings, embedding_artifact_id='sha256:serving'), embedding_artifact_id='sha256:disk-selection', catalog_root=path.parents[2])
    assert calls == []
    assert search_chunks(db_settings, VECTOR, query='source')[0]['generation_id'] == first['generation_id']


def test_unpinned_rest_and_legacy_store_exclude_other_embedding_spaces(db_settings, tmp_path, monkeypatch):
    import os
    os.environ.setdefault('DATABASE_URL', 'postgresql://unused:unused@127.0.0.1/unused')
    from local_ai_retrieval import service
    monkeypatch.setattr(ingestion, 'embed_texts', lambda *a, **k: [VECTOR for _ in a[0]])
    old = manifest(tmp_path, 'old embedding needle')
    one = index(old, db_settings, embedding_artifact_id='sha256:A')
    newer = manifest(tmp_path, 'current embedding needle')
    value = json.loads(newer.read_text()); value['repository_id'] = 'repo_second'
    value.pop('snapshot_id'); value['snapshot_id'] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    newer = rewrite_manifest(newer, value)
    index(newer, db_settings, embedding_artifact_id='sha256:B')
    settings = replace(db_settings, embedding_artifact_id='sha256:B')
    monkeypatch.setattr(service, 'SETTINGS', settings)
    calls = []
    def embed(*args, **kwargs):
        calls.append(1)
        return [VECTOR]
    monkeypatch.setattr(service, 'embed_texts', embed)
    monkeypatch.setattr(vector_registry, 'embed_texts', embed)
    for mode in ('hybrid', 'semantic'):
        direct = search_chunks(settings, VECTOR, query='needle', query_mode=mode)
        assert direct and {row['repository'] for row in direct} == {'repo_second'}
    rows = service.perform_search(service.SearchRequest(query='needle'))
    assert rows and {r['repository'] for r in rows} == {'repo_second'}
    store = vector_registry.register_store(settings, one['repository_id'], 'main')
    calls.clear()
    assert vector_registry.scoped_search(settings, store, 'needle', None, 3) == []
    assert calls == []


def test_unknown_legacy_provenance_has_no_inference_backed_results(db_settings, tmp_path, monkeypatch):
    import os
    os.environ.setdefault('DATABASE_URL', 'postgresql://unused:unused@127.0.0.1/unused')
    from local_ai_retrieval import service
    from local_ai_retrieval.db import vector_literal
    with psycopg.connect(db_settings.database_url) as connection:
        connection.execute("""INSERT INTO source_chunks (repository,branch,commit_hash,path,language,chunk_index,
            start_line,end_line,chunk_hash,content,embedding) VALUES
            ('unknown','main','abc','source.rs','rust',0,1,1,'hash','needle',%s::vector)""", (vector_literal(VECTOR),))
    settings = replace(db_settings, embedding_artifact_id='sha256:known')
    monkeypatch.setattr(service, 'SETTINGS', settings)
    calls = []
    monkeypatch.setattr(service, 'embed_texts', lambda *a, **k: calls.append(1))
    monkeypatch.setattr(vector_registry, 'embed_texts', lambda *a, **k: calls.append(1))
    assert service.perform_search(service.SearchRequest(query='needle', repository='unknown')) == []
    store = vector_registry.register_store(settings, 'unknown', 'main')
    assert vector_registry.scoped_search(settings, store, 'needle', None, 3) == []
    assert calls == []


def test_unimportable_profile_fails_before_inference_or_generation_selection(db_settings, tmp_path, monkeypatch):
    path = manifest(tmp_path, 'needle source')
    value = json.loads(path.read_text())
    value['files'] = [row for row in value['files'] if row['path'] != 'Cargo.lock']
    value.pop('snapshot_id'); value['snapshot_id'] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    path = rewrite_manifest(path, value)
    calls = []
    monkeypatch.setattr(ingestion, 'embed_texts', lambda *a, **k: calls.append(1))
    with pytest.raises(ValueError, match='requires pinned'):
        index(path, db_settings, embedding_artifact_id='sha256:model')
    assert calls == []
    assert vector_registry.list_stores(db_settings) == []


def test_snapshot_id_parent_symlink_is_rejected_before_inference(db_settings, tmp_path, monkeypatch):
    path = manifest(tmp_path, 'original snapshot source')
    alias = path.parent.parent / ('0' * 64)
    alias.symlink_to(path.parent, target_is_directory=True)
    calls = []
    monkeypatch.setattr(ingestion, 'embed_texts', lambda *a, **k: calls.append(1) or [VECTOR for _ in a[0]])
    with pytest.raises((OSError, ValueError)):
        index(alias / 'manifest.json', db_settings, embedding_artifact_id='sha256:model')
    assert calls == []
    assert vector_registry.list_stores(db_settings) == []


def test_manifest_id_must_match_selected_snapshot_directory(db_settings, tmp_path, monkeypatch):
    import shutil
    path = manifest(tmp_path, 'original valid source')
    substituted = path.parent.parent / ('1' * 64)
    shutil.copytree(path.parent, substituted)
    calls = []
    monkeypatch.setattr(ingestion, 'embed_texts', lambda *a, **k: calls.append(1) or [VECTOR for _ in a[0]])
    with pytest.raises(ValueError, match='selected snapshot'):
        index(substituted / 'manifest.json', db_settings, embedding_artifact_id='sha256:model')
    assert calls == []
    assert vector_registry.list_stores(db_settings) == []
