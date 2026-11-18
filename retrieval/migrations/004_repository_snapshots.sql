-- Preserve legacy rows while retaining immutable, independently pinned generations.
CREATE TABLE IF NOT EXISTS repository_generations (
    id text PRIMARY KEY,
    repository text NOT NULL,
    branch text NOT NULL,
    snapshot_id text NOT NULL,
    embedding_fingerprint text NOT NULL,
    manifest jsonb NOT NULL,
    active boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS repository_generations_active
    ON repository_generations(repository, branch) WHERE active;
ALTER TABLE source_chunks ADD COLUMN IF NOT EXISTS snapshot_id text NOT NULL DEFAULT 'legacy';
ALTER TABLE source_chunks ADD COLUMN IF NOT EXISTS generation_id text NOT NULL DEFAULT 'legacy';
ALTER TABLE source_chunks DROP CONSTRAINT IF EXISTS source_chunks_repository_branch_path_chunk_index_key;
CREATE UNIQUE INDEX IF NOT EXISTS source_chunks_generation_path
    ON source_chunks(repository, branch, generation_id, path, chunk_index);
CREATE INDEX IF NOT EXISTS source_chunks_generation ON source_chunks(generation_id);
ALTER TABLE workspace_vector_stores ADD COLUMN IF NOT EXISTS snapshot_id text;
ALTER TABLE workspace_vector_stores ADD COLUMN IF NOT EXISTS generation_id text;
ALTER TABLE workspace_vector_stores DROP CONSTRAINT IF EXISTS workspace_vector_stores_repository_branch_key;
CREATE UNIQUE INDEX IF NOT EXISTS workspace_vector_stores_legacy
    ON workspace_vector_stores(repository, branch) WHERE generation_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS workspace_vector_stores_generation
    ON workspace_vector_stores(generation_id) WHERE generation_id IS NOT NULL;
