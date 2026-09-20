CREATE TABLE IF NOT EXISTS workspace_vector_stores (
    id text PRIMARY KEY,
    repository text NOT NULL,
    branch text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(repository, branch)
);
