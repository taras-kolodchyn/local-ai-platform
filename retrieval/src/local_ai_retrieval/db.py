from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row

from .config import Settings


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
        sql_path = Path(__file__).resolve().parents[3] / "migrations" / "001_knowledge.sql"
    sql = sql_path.read_text(encoding="utf-8")
    with psycopg.connect(settings.database_url, autocommit=True) as connection:
        connection.execute(sql)


def existing_hashes(settings: Settings, repository: str, branch: str) -> dict[tuple[str, int], str]:
    with psycopg.connect(settings.database_url) as connection:
        rows = connection.execute(
            "SELECT path, chunk_index, chunk_hash FROM source_chunks WHERE repository = %s AND branch = %s",
            (repository, branch),
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
                    ON CONFLICT (repository, branch, path, chunk_index) DO UPDATE SET
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
                WHERE repository = %s AND branch = %s
                  AND NOT EXISTS (
                    SELECT 1 FROM live_chunk_keys AS live
                    WHERE live.path = chunks.path AND live.chunk_index = chunks.chunk_index
                  )
                """,
                (repository, branch),
            ).rowcount
            connection.execute(
                "UPDATE source_chunks SET commit_hash = %s WHERE repository = %s AND branch = %s",
                (commit_hash, repository, branch),
            )
    return int(deleted or 0)


def delete_snapshot(settings: Settings, repository: str, branch: str) -> int:
    with psycopg.connect(settings.database_url) as connection:
        deleted = connection.execute(
            "DELETE FROM source_chunks WHERE repository = %s AND branch = %s",
            (repository, branch),
        ).rowcount
        connection.commit()
    return int(deleted or 0)


def search_chunks(
    settings: Settings,
    embedding: list[float],
    *,
    repository: str | None = None,
    branch: str | None = None,
    path_prefix: str | None = None,
    limit: int | None = None,
    max_chars: int | None = None,
) -> list[dict[str, Any]]:
    bounded_limit = min(limit or settings.default_limit, settings.max_limit)
    bounded_chars = min(max_chars or settings.max_context_chars, settings.max_context_chars)
    filters: list[str] = []
    values: list[Any] = [vector_literal(embedding)]
    if repository:
        filters.append("repository = %s")
        values.append(repository)
    if branch:
        filters.append("branch = %s")
        values.append(branch)
    if path_prefix:
        filters.append("path LIKE %s")
        values.append(path_prefix.replace("%", "\\%").replace("_", "\\_") + "%")
    where = "WHERE " + " AND ".join(filters) if filters else ""
    values.extend([vector_literal(embedding), bounded_limit])

    query = f"""
        SELECT id, repository, branch, commit_hash, path, language, symbol_name,
               chunk_index, start_line, end_line, content,
               1 - (embedding <=> %s::vector) AS similarity
        FROM source_chunks
        {where}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        with connection.transaction():
            connection.execute("SET LOCAL hnsw.iterative_scan = strict_order")
            connection.execute("SET LOCAL hnsw.ef_search = 100")
            rows = connection.execute(query, values).fetchall()

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
        results.append(item)
        consumed += len(content)
    return results


def get_chunk(settings: Settings, chunk_id: int) -> dict[str, Any] | None:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as connection:
        row = connection.execute(
            """
            SELECT id, repository, branch, commit_hash, path, language, symbol_name,
                   chunk_index, start_line, end_line, content
            FROM source_chunks WHERE id = %s
            """,
            (chunk_id,),
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
