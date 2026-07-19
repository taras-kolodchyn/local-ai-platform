from prometheus_client import Counter, Gauge, Histogram

EMBEDDING_REQUESTS = Counter(
    "local_ai_embedding_requests_total",
    "Embedding batches sent through LiteLLM",
    ["status"],
)
EMBEDDING_ITEMS = Counter(
    "local_ai_embedding_items_total",
    "Texts embedded through LiteLLM",
)
RETRIEVAL_REQUESTS = Counter(
    "local_ai_retrieval_requests_total",
    "Retrieval requests",
    ["status"],
)
RETRIEVAL_LATENCY = Histogram(
    "local_ai_retrieval_duration_seconds",
    "End-to-end retrieval latency",
)
VECTOR_SEARCH_LATENCY = Histogram(
    "local_ai_vector_search_duration_seconds",
    "PostgreSQL pgvector query latency",
)
INGESTION_FILES = Counter(
    "local_ai_ingestion_files_total",
    "Files processed by ingestion",
    ["status"],
)
INGESTION_CHUNKS = Counter(
    "local_ai_ingestion_chunks_total",
    "Chunks processed by ingestion",
    ["status"],
)
INGESTION_DURATION = Histogram(
    "local_ai_ingestion_duration_seconds",
    "Repository ingestion job duration",
    ["status"],
)
INDEXED_CHUNKS = Gauge("local_ai_indexed_chunks", "Chunks currently stored in pgvector")
INDEXED_FILES = Gauge("local_ai_indexed_files", "Distinct files currently stored in pgvector")
INDEXED_SNAPSHOTS = Gauge(
    "local_ai_indexed_snapshots", "Distinct repository and branch snapshots in pgvector"
)
LAST_INGESTION_DURATION = Gauge(
    "local_ai_last_ingestion_duration_seconds", "Duration of the most recently completed ingestion"
)
LAST_INGESTION_FILES = Gauge(
    "local_ai_last_ingestion_files", "Files accepted by the most recently completed ingestion"
)
LAST_INGESTION_CHUNKS = Gauge(
    "local_ai_last_ingestion_chunks", "Chunks embedded or reused by the most recent ingestion", ["status"]
)
LAST_INGESTION_SKIPPED_SENSITIVE = Gauge(
    "local_ai_last_ingestion_skipped_sensitive", "Sensitive files skipped by the most recent ingestion"
)
