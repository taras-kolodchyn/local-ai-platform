from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row

from .config import Settings

RRF_K = 60


@dataclass(frozen=True)
class ChunkRecord:
    repository: str
    branch: str
    commit_hash: str
    path: str
    language: str
    symbol_name: str | None
    chunk_index: int
    start_line: int
    end_line: int
    chunk_hash: str
    content: str
    metadata: dict[str, Any]
    embedding: list[float]


def vector_literal(values: Iterable[float]) -> str:
    return "[" + ",".join(format(float(value), ".9g") for value in values) + "]"


def migrate(settings: Settings) -> None:
    sql_path = Path("/app/migrations/001_knowledge.sql")
    if not sql_path.exists():
        sql_path = Path(__file__).resolve().parents[2] / "migrations" / "001_knowledge.sql"
    sql = sql_path.read_text(encoding="utf-8")
    with psycopg.connect(settings.database_url, autocommit=True) as connection:
        connection.execute(sql)
        connection.execute((sql_path.parent / "002_vector_registry.sql").read_text())
        connection.execute((sql_path.parent / "003_hybrid_search.sql").read_text())
        connection.execute((sql_path.parent / "004_repository_snapshots.sql").read_text())


def existing_hashes(settings: Settings, repository: str, branch: str, fingerprint: str | None = None) -> dict[tuple[str, int], str]:
    if not fingerprint:
        return {}
    with psycopg.connect(settings.database_url) as connection:
        rows = connection.execute(
            "SELECT path, chunk_index, chunk_hash FROM source_chunks WHERE repository = %s AND branch = %s AND generation_id = 'legacy' AND metadata->>'embedding_fingerprint' = %s",
            (repository, branch, fingerprint),
        ).fetchall()
    return {(row[0], row[1]): row[2] for row in rows}


def create_ingestion_run(
    settings: Settings, repository: str, branch: str, commit_hash: str
) -> int:
    with psycopg.connect(settings.database_url) as connection:
        row = connection.execute(
            """
            INSERT INTO ingestion_runs (repository, branch, commit_hash)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (repository, branch, commit_hash),
        ).fetchone()
        connection.commit()
    return int(row[0])


def finish_ingestion_run(settings: Settings, run_id: int, *, status: str, **stats: Any) -> None:
    allowed = {
        "files_seen", "files_indexed", "chunks_embedded", "chunks_reused",
        "chunks_deleted", "skipped_sensitive", "duration_ms", "error_class",
    }
    fields = {key: value for key, value in stats.items() if key in allowed}
    assignments = ["completed_at = now()", "status = %s"]
    values: list[Any] = [status]
    for key, value in fields.items():
        assignments.append(f"{key} = %s")
        values.append(value)
    values.append(run_id)
    with psycopg.connect(settings.database_url) as connection:
        connection.execute(
            f"UPDATE ingestion_runs SET {', '.join(assignments)} WHERE id = %s",
            values,
        )
        connection.commit()


def apply_snapshot(
    settings: Settings,
    records: list[ChunkRecord],
    live_keys: list[tuple[str, int]],
    *,
    repository: str,
    branch: str,
    commit_hash: str,
) -> int:
    with psycopg.connect(settings.database_url) as connection:
        with connection.transaction():
            for record in records:
                connection.execute(
                    """
                    INSERT INTO source_chunks (
                        repository, branch, commit_hash, path, language, symbol_name,
                        chunk_index, start_line, end_line, chunk_hash, content, metadata, embedding
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::vector
                    )
                    ON CONFLICT (repository, branch, generation_id, path, chunk_index) DO UPDATE SET
                        commit_hash = EXCLUDED.commit_hash,
                        language = EXCLUDED.language,
                        symbol_name = EXCLUDED.symbol_name,
                        start_line = EXCLUDED.start_line,
                        end_line = EXCLUDED.end_line,
                        chunk_hash = EXCLUDED.chunk_hash,
                        content = EXCLUDED.content,
                        metadata = EXCLUDED.metadata,
                        embedding = EXCLUDED.embedding,
                        indexed_at = now()
                    """,
                    (
                        record.repository, record.branch, record.commit_hash, record.path,
                        record.language, record.symbol_name, record.chunk_index, record.start_line,
                        record.end_line, record.chunk_hash, record.content,
                        json.dumps(record.metadata), vector_literal(record.embedding),
                    ),
                )

            connection.execute(
                "CREATE TEMP TABLE live_chunk_keys (path text, chunk_index integer, PRIMARY KEY(path, chunk_index)) ON COMMIT DROP"
            )
            if live_keys:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        "INSERT INTO live_chunk_keys (path, chunk_index) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        live_keys,
                    )

            deleted = connection.execute(
                """
                DELETE FROM source_chunks AS chunks
                WHERE repository = %s AND branch = %s AND generation_id = 'legacy'
                  AND NOT EXISTS (
                    SELECT 1 FROM live_chunk_keys AS live
                    WHERE live.path = chunks.path AND live.chunk_index = chunks.chunk_index
                  )
                """,
                (repository, branch),
            ).rowcount
            connection.execute(
                "UPDATE source_chunks SET commit_hash = %s WHERE repository = %s AND branch = %s AND generation_id = 'legacy'",
                (commit_hash, repository, branch),
            )
    return int(deleted or 0)


def delete_snapshot(settings: Settings, repository: str, branch: str) -> int:
    with psycopg.connect(settings.database_url) as connection:
        deleted = connection.execute(
            "DELETE FROM source_chunks WHERE repository = %s AND branch = %s AND generation_id = 'legacy'",
            (repository, branch),
        ).rowcount
        connection.commit()
    return int(deleted or 0)


def _chunk_scope(
    fingerprint: str | None, generation_id: str | None = None,
) -> tuple[list[str], list[str]]:
    if generation_id:
        filters = ["generation_id = %s"]
        values = [generation_id]
    else:
        filters = ["""(EXISTS (SELECT 1 FROM repository_generations g
            WHERE g.id = source_chunks.generation_id AND g.active)
            OR (generation_id = 'legacy' AND NOT EXISTS
                (SELECT 1 FROM repository_generations g WHERE g.repository = source_chunks.repository
                 AND g.branch = source_chunks.branch AND g.active)))"""]
        values = []
    # Offline SQL diagnostics can search without a configured embedding identity.
    if fingerprint is not None:
        filters.append("metadata->>'embedding_fingerprint' = %s")
        values.append(fingerprint)
    return filters, values


def search_chunks(
    settings: Settings,
    embedding: list[float],
    *,
    query: str = "",
    query_mode: str = "hybrid",
    repository: str | None = None,
    branch: str | None = None,
    snapshot_id: str | None = None,
    generation_id: str | None = None,
    compatible_fingerprint: str | None = None,
    path_prefix: str | None = None,
    exact_path: str | None = None,
    limit: int | None = None,
    max_chars: int | None = None,
) -> list[dict[str, Any]]:
    """Fuse scoped, bounded cosine/keyword candidates; content is untrusted data.

    ``similarity`` stays raw cosine similarity [-1, 1]. ``relevance`` is reciprocal
    rank fusion, at most 2 / (RRF_K + 1), and is not a cosine similarity. The
    semantic-only mode is a diagnostic baseline, using the same scopes/bounds.
    PostgreSQL's plain-text query constructor treats keyword input as data.
    """
    if compatible_fingerprint is None and settings.embedding_artifact_id:
        from .ingestion import embedding_fingerprint
        compatible_fingerprint = embedding_fingerprint(settings, settings.embedding_artifact_id)
    if query_mode not in {"hybrid", "semantic"}:
        raise ValueError("Unsupported retrieval query mode")
    if not isinstance(query, str) or len(query) > 4000:
        raise ValueError("Query must be at most 4000 characters")
    if limit is not None and limit < 1:
        raise ValueError("Result limit must be positive")
    if max_chars is not None and max_chars < 1:
        raise ValueError("Context limit must be positive")
    bounded_limit = min(limit or settings.default_limit, settings.max_limit)
    bounded_chars = min(max_chars or settings.max_context_chars, settings.max_context_chars)
    candidate_limit = max(20, bounded_limit * 4)
    filters, scope_values = _chunk_scope(compatible_fingerprint, generation_id)
    values: list[Any] = [vector_literal(embedding), query, *scope_values]
    if snapshot_id:
        filters.append("snapshot_id = %s")
        values.append(snapshot_id)
    if repository:
        filters.append("repository = %s")
        values.append(repository)
    if branch:
        filters.append("branch = %s")
        values.append(branch)
    if path_prefix:
        filters.append("path LIKE %s")
        values.append(path_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if exact_path is not None:
        filters.append("path = %s")
        values.append(exact_path)
    where = "WHERE " + " AND ".join(filters)
    values.extend([candidate_limit, query_mode == "hybrid", candidate_limit,
                   RRF_K, RRF_K, bounded_limit])

    sql = f"""
        WITH search_input AS (
            SELECT %s::vector AS embedding, plainto_tsquery('simple', %s) AS keywords
        ), scoped AS NOT MATERIALIZED (
            SELECT * FROM source_chunks {where}
        ), semantic_candidates AS MATERIALIZED (
            SELECT id, embedding <=> (SELECT embedding FROM search_input) AS distance
            FROM scoped
            -- Keep the index-ordered pool distance-only. A secondary id sort
            -- here can consume unbounded equal-distance groups before LIMIT.
            ORDER BY embedding <=> (SELECT embedding FROM search_input)
            LIMIT %s
        ), semantic AS (
            -- Deterministic ties apply only after candidate materialization.
            SELECT id, row_number() OVER (ORDER BY distance, id) AS position
            FROM semantic_candidates
        ), lexical_candidates AS MATERIALIZED (
            SELECT id, ts_rank_cd(search_vector, (SELECT keywords FROM search_input)) AS rank
            FROM scoped
            WHERE %s AND search_vector @@ (SELECT keywords FROM search_input)
            ORDER BY rank DESC, id
            LIMIT %s
        ), lexical AS (
            SELECT id, row_number() OVER (ORDER BY rank DESC, id) AS position
            FROM lexical_candidates
        ), fused AS (
            SELECT coalesce(semantic.id, lexical.id) AS id,
                   semantic.position AS semantic_rank, lexical.position AS lexical_rank,
                   coalesce(1.0 / (%s + semantic.position), 0) +
                   coalesce(1.0 / (%s + lexical.position), 0) AS relevance
            FROM semantic FULL OUTER JOIN lexical USING (id)
        )
        SELECT scoped.id, repository, branch, commit_hash, snapshot_id, generation_id, metadata, path, language, symbol_name,
               chunk_index, start_line, end_line, content,
               1 - (embedding <=> (SELECT embedding FROM search_input)) AS similarity,
               relevance
        FROM fused JOIN scoped USING (id)
        ORDER BY relevance DESC, lexical_rank ASC NULLS LAST,
                 semantic_rank ASC NULLS LAST, scoped.id
        LIMIT %s
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            connection.execute("SET LOCAL statement_timeout = '5s'")
            connection.execute("SET LOCAL hnsw.iterative_scan = strict_order")
            connection.execute("SET LOCAL hnsw.ef_search = 100")
            rows = connection.execute(sql, values).fetchall()

    results: list[dict[str, Any]] = []
    consumed = 0
    for row in rows:
        content = row["content"]
        remaining = bounded_chars - consumed
        if remaining <= 0:
            break
        if len(content) > remaining:
            content = content[:remaining]
        item = dict(row)
        item["content"] = content
        item["similarity"] = float(item["similarity"])
        item["relevance"] = float(item["relevance"])
        results.append(item)
        consumed += len(content)
    return results


def get_chunk(settings: Settings, chunk_id: int) -> dict[str, Any] | None:
    from .ingestion import embedding_fingerprint

    fingerprint = embedding_fingerprint(settings, settings.embedding_artifact_id)
    filters, values = _chunk_scope(fingerprint)
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        row = connection.execute(
            f"""
            SELECT id, repository, branch, commit_hash, snapshot_id, generation_id, metadata, path, language, symbol_name,
                   chunk_index, start_line, end_line, content
            FROM source_chunks WHERE id = %s AND {' AND '.join(filters)}
            """,
            [chunk_id, *values],
        ).fetchone()
    return dict(row) if row else None


def observability_snapshot(settings: Settings) -> dict[str, float]:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        totals = connection.execute(
            """
            SELECT count(*) AS indexed_chunks,
                   count(DISTINCT (repository, branch, path)) AS indexed_files,
                   count(DISTINCT (repository, branch)) AS indexed_snapshots
            FROM source_chunks
            """
        ).fetchone()
        latest = connection.execute(
            """
            SELECT duration_ms, files_indexed, chunks_embedded, chunks_reused,
                   chunks_deleted, skipped_sensitive
            FROM ingestion_runs
            WHERE completed_at IS NOT NULL
            ORDER BY completed_at DESC
            LIMIT 1
            """
        ).fetchone()

    values = {key: float(value or 0) for key, value in dict(totals or {}).items()}
    for key, value in dict(latest or {}).items():
        values[f"last_{key}"] = float(value or 0)
    return values


def find_generation(settings: Settings, snapshot_id: str, fingerprint: str) -> dict | None:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        row = connection.execute('''SELECT g.id AS generation_id, s.id AS store_id,
            g.repository AS repository_id, g.snapshot_id FROM repository_generations g
            JOIN workspace_vector_stores s ON s.generation_id=g.id
            WHERE g.snapshot_id=%s AND g.embedding_fingerprint=%s ORDER BY g.created_at DESC LIMIT 1''',
            (snapshot_id, fingerprint)).fetchone()
    return dict(row) if row else None


def publish_generation(settings: Settings, manifest: dict, fingerprint: str,
                       records: list[ChunkRecord]) -> dict:
    """Commit all source rows, scope registration and current selection together."""
    from uuid import uuid4
    generation_id = 'gen_' + uuid4().hex
    store_id = 'vs_' + uuid4().hex
    repository, branch = manifest['repository_id'], manifest['git']['branch']
    with psycopg.connect(settings.database_url) as connection:
        with connection.transaction():
            connection.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (json.dumps([repository, branch]),))
            connection.execute('''INSERT INTO repository_generations
                (id,repository,branch,snapshot_id,embedding_fingerprint,manifest)
                VALUES (%s,%s,%s,%s,%s,%s::jsonb)''',
                (generation_id, repository, branch, manifest['snapshot_id'], fingerprint, json.dumps(manifest)))
            for record in records:
                connection.execute('''INSERT INTO source_chunks
                    (repository,branch,commit_hash,path,language,symbol_name,chunk_index,
                     start_line,end_line,chunk_hash,content,metadata,embedding,snapshot_id,generation_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::vector,%s,%s)''',
                    (repository, branch, record.commit_hash, record.path, record.language,
                     record.symbol_name, record.chunk_index, record.start_line, record.end_line,
                     record.chunk_hash, record.content, json.dumps(record.metadata),
                     vector_literal(record.embedding), manifest['snapshot_id'], generation_id))
            connection.execute('''INSERT INTO workspace_vector_stores
                (id,repository,branch,snapshot_id,generation_id) VALUES (%s,%s,%s,%s,%s)''',
                (store_id, repository, branch, manifest['snapshot_id'], generation_id))
            connection.execute('UPDATE repository_generations SET active=false WHERE repository=%s AND branch=%s AND active',
                               (repository, branch))
            connection.execute('UPDATE repository_generations SET active=true WHERE id=%s', (generation_id,))
    return {'repository_id': repository, 'snapshot_id': manifest['snapshot_id'],
            'generation_id': generation_id, 'store_id': store_id}


def activate_generation(settings: Settings, generation_id: str) -> None:
    with psycopg.connect(settings.database_url) as connection:
        with connection.transaction():
            row = connection.execute('SELECT repository, branch FROM repository_generations WHERE id=%s', (generation_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown repository generation')
            connection.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (json.dumps(list(row)),))
            connection.execute('UPDATE repository_generations SET active=false WHERE repository=%s AND branch=%s AND active', row)
            connection.execute('UPDATE repository_generations SET active=true WHERE id=%s', (generation_id,))


def compatible_scope_exists(settings: Settings, fingerprint: str, *, repository=None, branch=None, generation_id=None) -> bool:
    if not fingerprint:
        return False
    filters, values = _chunk_scope(fingerprint, generation_id)
    for field, value in [('repository', repository), ('branch', branch)]:
        if value is not None:
            filters.append(field + ' = %s')
            values.append(value)
    with psycopg.connect(settings.database_url) as connection:
        connection.execute("SET LOCAL statement_timeout = '5s'")
        return bool(connection.execute('SELECT EXISTS(SELECT 1 FROM source_chunks WHERE ' +
            ' AND '.join(filters) + ')', values).fetchone()[0])
