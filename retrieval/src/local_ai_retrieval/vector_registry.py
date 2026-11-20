"""Persistent knowledge scope registration; independent of gateway metadata."""
from dataclasses import replace
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from .db import compatible_scope_exists, search_chunks
from .embeddings import embed_texts
from .moyai_search import CANDIDATES, CONTEXT_CHARS, file_context, mentioned_paths, pack_chunks, select_chunks
from .scanner import MAX_FILE_BYTES


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


def scoped_search(settings, scope: dict, query: str, path: str | None, limit: int, *,
                  auto_rank=False, focus_mentions=True) -> list[dict]:
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
        # The public result limit and source budget apply after selection.
        candidate_settings = (replace(settings, max_limit=CANDIDATES,
            max_context_chars=CANDIDATES * MAX_FILE_BYTES) if auto_rank else settings)

        def fetch(exact_path, count):
            return search_chunks(candidate_settings, vector, query=query, repository=scope['repository'],
                branch=scope['branch'], snapshot_id=scope.get('snapshot_id'),
                generation_id=scope.get('generation_id'), compatible_fingerprint=fingerprint,
                exact_path=exact_path, limit=count)

        rows = fetch(path, CANDIDATES if auto_rank else limit)
        if not auto_rank:
            return rows
        limit = min(limit, settings.max_limit)
        budget = min(CONTEXT_CHARS, settings.max_context_chars)
        mentions = mentioned_paths(query) if focus_mentions and path is None else []
        if 0 < len(mentions) <= min(4, limit):
            groups = []
            for name in mentions:
                group = [row for row in rows if row['path'] == name] or fetch(name, 2)
                if not group:
                    break
                groups.append(group)
            else:
                return pack_chunks(file_context(groups, rows, limit), budget, preserve_leader=False)
        selected = select_chunks(settings, query, rows, limit)
        return pack_chunks(selected, budget)
    except psycopg.errors.QueryCanceled as exc:
        raise TimeoutError('Search deadline exceeded') from exc
