"""Persistent knowledge scope registration; independent of gateway metadata."""
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from .db import search_chunks
from .embeddings import embed_texts


def register_store(settings, repository: str, branch: str) -> dict:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        return dict(conn.execute(
            'INSERT INTO workspace_vector_stores (id, repository, branch) VALUES (%s,%s,%s) '
            'ON CONFLICT (repository, branch) DO UPDATE SET repository=EXCLUDED.repository '
            'RETURNING id, repository, branch', ('vs_' + uuid4().hex, repository, branch)).fetchone())


def list_stores(settings) -> list[dict]:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        conn.execute("SET statement_timeout = '5s'")
        return [dict(r) for r in conn.execute(
            'SELECT id, repository, branch, extract(epoch from created_at)::bigint AS created_at '
            'FROM workspace_vector_stores ORDER BY id').fetchall()]


def scoped_search(settings, scope: dict, query: str, path: str | None, limit: int) -> list[dict]:
    vector = embed_texts([query], settings, timeout=30)[0]
    try:
        return search_chunks(settings, vector, repository=scope['repository'],
            branch=scope['branch'], exact_path=path, limit=limit)
    except psycopg.errors.QueryCanceled as exc:
        raise TimeoutError('Search deadline exceeded') from exc
