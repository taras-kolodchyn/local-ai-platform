CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS source_chunks (
    id bigserial PRIMARY KEY,
    repository text NOT NULL,
    branch text NOT NULL,
    commit_hash text NOT NULL,
    path text NOT NULL,
    language text NOT NULL,
    symbol_name text,
    chunk_index integer NOT NULL CHECK (chunk_index >= 0),
    start_line integer NOT NULL CHECK (start_line > 0),
    end_line integer NOT NULL CHECK (end_line >= start_line),
    chunk_hash text NOT NULL,
    content text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(1024) NOT NULL,
    indexed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (repository, branch, path, chunk_index)
);

CREATE INDEX IF NOT EXISTS source_chunks_hnsw_cosine
    ON source_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS source_chunks_repository_branch
    ON source_chunks (repository, branch);
CREATE INDEX IF NOT EXISTS source_chunks_path_pattern
    ON source_chunks (path text_pattern_ops);
CREATE INDEX IF NOT EXISTS source_chunks_metadata_gin
    ON source_chunks USING gin (metadata);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id bigserial PRIMARY KEY,
    repository text NOT NULL,
    branch text NOT NULL,
    commit_hash text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    files_seen integer NOT NULL DEFAULT 0,
    files_indexed integer NOT NULL DEFAULT 0,
    chunks_embedded integer NOT NULL DEFAULT 0,
    chunks_reused integer NOT NULL DEFAULT 0,
    chunks_deleted integer NOT NULL DEFAULT 0,
    skipped_sensitive integer NOT NULL DEFAULT 0,
    duration_ms double precision,
    status text NOT NULL DEFAULT 'running',
    error_class text
);

ALTER TABLE ingestion_runs ADD COLUMN IF NOT EXISTS duration_ms double precision;
