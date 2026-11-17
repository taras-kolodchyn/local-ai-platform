-- PostgreSQL 17 built-in full-text search, without stemming source identifiers.
-- Include both literal paths/names and their components (e.g. retry_policy.py).
-- Stored generation indexes existing rows and tracks every snapshot upsert.
ALTER TABLE source_chunks ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('simple'::regconfig,
            path || ' ' || regexp_replace(path, '[/._-]+', ' ', 'g')), 'A') ||
        setweight(to_tsvector('simple'::regconfig,
            coalesce(symbol_name, '') || ' ' ||
            regexp_replace(coalesce(symbol_name, ''), '[_]+', ' ', 'g')), 'A') ||
        setweight(to_tsvector('simple'::regconfig, content), 'B')
    ) STORED;

CREATE INDEX IF NOT EXISTS source_chunks_search_vector_gin
    ON source_chunks USING gin (search_vector);
