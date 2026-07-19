# Architecture

Status: accepted for MVP
Last verified: 2026-07-19

## Goals and boundaries

The platform gives local coding agents a stable OpenAI-compatible endpoint while keeping the inference runtime, caching, retrieval, tool access, and observability replaceable. The MVP targets Apple Silicon and defaults to no internet-dependent tools after model artifacts and container images have been pulled.

The platform does not grant a Linux container direct Metal access. Docker Model Runner executes llama.cpp through Docker Desktop's macOS host integration and exposes it to the Compose network.

## Component view

```mermaid
flowchart TB
    subgraph Host["Apple Silicon macOS host"]
        Codex["Codex CLI"]
        Hermes["Hermes Agent"]
        Other["Other OpenAI-compatible clients"]
        DMR["Docker Model Runner\nllama.cpp + Metal"]
        Qwen["Qwen3-Coder 30B-A3B\nQwen3-Embedding 0.6B"]
        DMR --> Qwen
    end

    subgraph Compose["Docker Compose - isolated Linux networks"]
        LiteLLM["LiteLLM AI Gateway\n127.0.0.1:4000"]
        Redis["Redis response cache"]
        Postgres["PostgreSQL + pgvector"]
        Ingestion["Ingestion CLI/worker"]
        Retrieval["Retrieval API + MCP server"]
        OfflineTools["Read-only filesystem/Git/Postgres MCP"]
        GitHubMCP["GitHub MCP\nconnected profile only"]
        Prometheus["Prometheus"]
        Loki["Loki"]
        Alloy["Grafana Alloy"]
        Grafana["Grafana"]

        LiteLLM --> Redis
        LiteLLM --> Postgres
        Ingestion --> LiteLLM
        Ingestion --> Postgres
        Retrieval --> LiteLLM
        Retrieval --> Postgres
        LiteLLM --> Retrieval
        LiteLLM --> OfflineTools
        LiteLLM -.-> GitHubMCP
        Prometheus --> LiteLLM
        Prometheus --> Retrieval
        Alloy --> Loki
        Grafana --> Prometheus
        Grafana --> Loki
    end

    Codex --> LiteLLM
    Hermes --> LiteLLM
    Other --> LiteLLM
    LiteLLM -->|"host.docker.internal:12434"| DMR
    GitHubMCP -. "explicit opt-in" .-> GitHub["GitHub API"]
```

## Request path

```mermaid
sequenceDiagram
    participant A as Coding agent
    participant L as LiteLLM
    participant C as Redis cache
    participant M as MCP Gateway
    participant R as Retrieval service
    participant P as pgvector
    participant Q as Qwen via DMR

    A->>L: POST /v1/responses model=local-qwen
    L->>C: Lookup response cache key
    alt safe exact cache hit
        C-->>L: Cached response
    else miss or cache disabled
        opt agent requests retrieval tool
            L->>M: MCP tool call under allowlist
            M->>R: search_code(query, filters, limits)
            R->>L: POST /v1/embeddings model=local-embeddings
            L->>Q: OpenAI-compatible embeddings request
            Q-->>L: 1024-dimensional vector
            L-->>R: vector
            R->>P: HNSW cosine search with metadata filters
            P-->>R: bounded chunks
            R-->>M: paths, metadata, excerpts
            M-->>L: tool result
        end
        L->>Q: bridged chat-completions request
        Q-->>L: streamed tokens / tool calls
        L-->>A: Responses API stream
    end
```

## Retrieval pipeline

```mermaid
flowchart LR
    Repo["Git repository or docs"] --> Scan["Scanner\n.gitignore + allowlist"]
    Scan --> Guard["Secret/generated-file exclusions"]
    Guard --> Chunk["Deterministic chunker"]
    Chunk --> Hash["Content + metadata hash"]
    Hash --> Embed["local-embeddings alias"]
    Embed --> Store["pgvector + metadata"]
    Store --> Search["HNSW cosine search"]
    Search --> Limit["Top-k + total-character budget"]
    Limit --> MCP["read-only retrieval MCP tool"]
```

Each chunk records repository, relative path, branch, commit, language, optional symbol, content hash, chunk ordinal, and ingestion timestamp. Reindexing upserts changed chunks and removes rows no longer present at the same repository/branch snapshot.

## Trust boundaries

```mermaid
flowchart LR
    User["Human approval boundary"] --> Agent["Codex or Hermes sandbox"]
    Agent --> Gateway["Authenticated LiteLLM boundary"]
    Gateway --> Tools["MCP allowlist boundary"]
    Tools --> Data["Untrusted repository content"]
    Gateway --> Model["Untrusted model output"]
    Model --> Agent
    Data --> Model
```

Repository text and retrieved chunks are data, even when they contain phrases that look like instructions. Shell and write actions remain subject to the client sandbox and approval policy. The gateway does not make model-generated commands safe.

## Network layout

- `gateway`: LiteLLM, Redis, retrieval, ingestion, exporters, and the optional connected GitHub MCP service.
- `data`: PostgreSQL, LiteLLM, retrieval, ingestion, and PostgreSQL exporter; this network is internal.
- `observability`: LiteLLM, retrieval, Prometheus, Loki, Alloy, and Grafana.
- `mcp-host`: a non-internal bridge used only by the offline MCP service so
  Docker Desktop can publish its loopback host port; data access still crosses
  the isolated `data` network.
- `observability-host`: a non-internal bridge for Prometheus and Grafana
  loopback publication. Docker Desktop does not publish ports from containers
  attached only to `internal: true` networks.
- Host ports bind to `127.0.0.1` only.
- No service mounts `/var/run/docker.sock`.
- The connected profile is separate and is not enabled by `make up`.

## Cache taxonomy

| Mechanism | Owner | What it stores | MVP status |
| --- | --- | --- | --- |
| Response cache | LiteLLM + Redis | Exact full LLM responses | Implemented with short TTL and per-request bypass |
| Semantic response cache | Optional LiteLLM backend | Similar prompt responses | Deferred; risky for stateful agent turns |
| KV/prompt cache | Inference runtime | Attention state for model prefixes | Runtime concern; not Redis |
| Embedding reuse | Ingestion/pgvector | Reuse stored vectors when the path/chunk content hash is unchanged | Implemented; not a separate Redis cache |
| Knowledge base | PostgreSQL + pgvector | Versioned source chunks and metadata | MVP |

## Responses tool-result detail

The verified Codex-compatible round trip sends the first tool result to
LiteLLM with `previous_response_id` and a `function_call_output` item only.
Replaying the original `function_call` alongside its output made the local
Qwen/llama.cpp message template reject the request. The smoke test locks in the
working sequence.

## Extensibility

Clients only know `local-qwen`, `local-embeddings`, and LiteLLM. A future Ollama or MLX-LM backend can replace DMR by changing the gateway configuration. LiteLLM 1.93.0 also has an A2A Agent Gateway; it is deliberately deferred until an end-to-end local A2A agent is tested and protocol version 1.0 is pinned.
