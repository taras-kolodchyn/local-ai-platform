"""Register one unpinned vector store per indexed repository and branch."""
import json

import psycopg
from psycopg.rows import dict_row

from local_ai_retrieval.config import Settings


def main():
    with psycopg.connect(Settings.from_env().database_url, row_factory=dict_row) as connection:
        stores = connection.execute("""
            WITH registered AS (
                INSERT INTO workspace_vector_stores (id, repository, branch)
                SELECT 'vs_' || replace(gen_random_uuid()::text, '-', ''), repository, branch
                FROM (SELECT DISTINCT repository, branch FROM source_chunks) AS scopes
                -- Acquire conflicting rows in the same order across concurrent runs.
                ORDER BY repository, branch
                ON CONFLICT (repository, branch) WHERE generation_id IS NULL
                DO UPDATE SET repository = EXCLUDED.repository
                RETURNING id, repository, branch, snapshot_id, generation_id
            )
            SELECT * FROM registered ORDER BY repository, branch
        """).fetchall()
    print(json.dumps(stores))


if __name__ == '__main__':
    main()
