"""Persistent knowledge scope registration; independent of gateway metadata."""
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from .db import compatible_scope_exists, search_chunks
from .embeddings import embed_texts


def register_store(settings, repository: str, branch: str) -> dict:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        return dict(conn.execute(
            'INSERT INTO workspace_vector_stores (id, repository, branch) VALUES (%s,%s,%s) '
            'ON CONFLICT (repository, branch) WHERE generation_id IS NULL DO UPDATE SET repository=EXCLUDED.repository '
            'RETURNING id, repository, branch, snapshot_id, generation_id', ('vs_' + uuid4().hex, repository, branch)).fetchone())


def list_stores(settings) -> list[dict]:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        conn.execute("SET statement_timeout = '5s'")
        return [dict(r) for r in conn.execute(
            'SELECT s.id, s.repository, s.branch, s.snapshot_id, s.generation_id, '
            'g.embedding_fingerprint, extract(epoch from s.created_at)::bigint AS created_at '
            'FROM workspace_vector_stores s LEFT JOIN repository_generations g ON g.id=s.generation_id ORDER BY s.id').fetchall()]


def scoped_search(settings, scope: dict, query: str, path: str | None, limit: int) -> list[dict]:
    from .ingestion import embedding_fingerprint
    if not settings.embedding_artifact_id:
        raise ValueError('Query embedding identity is unavailable')
    fingerprint = embedding_fingerprint(settings, settings.embedding_artifact_id)
    if scope.get('generation_id') and fingerprint != scope.get('embedding_fingerprint'):
        raise ValueError('Pinned generation embedding identity differs from the active embedding model')
    if not compatible_scope_exists(settings, fingerprint, repository=scope['repository'],
            branch=scope['branch'], generation_id=scope.get('generation_id')):
        return []
    vector = embed_texts([query], settings, timeout=30)[0]
    try:
        return search_chunks(settings, vector, query=query, repository=scope['repository'],
            branch=scope['branch'], snapshot_id=scope.get('snapshot_id'),
            generation_id=scope.get('generation_id'), compatible_fingerprint=fingerprint, exact_path=path, limit=limit)
    except psycopg.errors.QueryCanceled as exc:
        raise TimeoutError('Search deadline exceeded') from exc
