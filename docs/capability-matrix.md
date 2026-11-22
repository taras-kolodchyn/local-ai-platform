# Platform capabilities and versions

Configuration checked on **2026-11-20** against the [core Compose file](../compose.yaml),
[Moyai Compose file](../compose.moyai.yaml), [model defaults](../.env.example)
and [gateway template](../litellm/config.yaml). The check column identifies a
repeatable local probe. Use its result on the target host when reporting runtime
compatibility.

| Component | Version or configured default | Capability and local check |
| --- | --- | --- |
| Docker (Desktop or Engine) / Compose / Model Runner | Reference Mac on 2026-11-20: Desktop 4.100.0, Compose 5.7.1, DMR client and server 1.2.9. Verified on Ubuntu x86_64 on 2026-11-22: Docker Engine 29.10.0, DMR client 1.2.9 | DMR runs the llama.cpp engine, using Metal on an Apple Silicon host or CPU (or NVIDIA CUDA / AMD ROCm when available) on Linux. On Docker Engine the DMR TCP port 12434 is enabled by default. Check the installation with `make doctor` and the configured models with `make status FORMAT=json`. [DMR inference engines](https://docs.docker.com/ai/model-runner/inference-engines/) · [DMR on Docker Engine](https://docs.docker.com/ai/model-runner/get-started/) |
| LiteLLM | 1.110.2 | Chat Completions, bridged Responses including tool results and streaming, embeddings, reranking and scoped tool access. Run `make smoke-test`. [Responses bridge](https://docs.litellm.ai/docs/response_api) · [MCP permissions](https://docs.litellm.ai/docs/mcp_control) |
| Qwen generation / embeddings | `ai/qwen3-coder:30B-A3B-UD-Q4_K_XL` / `ai/qwen3-embedding:0.6B-F16` | The default coding context is 65,536 tokens. The schema and smoke probe expect 1,024 embedding dimensions. Gateway clients use `local-qwen` and `local-embeddings`. |
| Headroom / CPU reranker | Headroom 0.44.0. MiniLM revision `df500e56a59fc34f37322735a40eff666200312e` | Two private services use the same image. One compresses eligible prompt history. The other loads an ARM64 INT8 model for `local-reranker`. Smoke tests cover both. [Compression integration](https://docs.litellm.ai/docs/proxy/headroom) · [MiniLM model](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2) |
| PostgreSQL / pgvector | `0.8.7-pg17-bookworm` | Hybrid source search and scoped vector stores. Run `make test` for database contracts and `make vector-smoke` for a live citation and authorization check. [pgvector](https://github.com/pgvector/pgvector#hybrid-search) |
| Redis | 8.10.3-alpine | Exact response caching for opted-in chat requests. `make smoke-test` repeats a unique request and checks the cache-hit header. [LiteLLM caching](https://docs.litellm.ai/docs/proxy/caching) |
| Moyai / Temporal | Moyai base image identified by SHA-256 in the [Dockerfile](../integrations/moyai/Dockerfile), Temporal CLI image 1.10.1 | Local chat, SSH workspace, workflow state and downloadable artifacts. Run `make moyai-smoke RESTART=1` on the active stack. [Moyai runbook](moyai-local.md) |
| Observability | Prometheus 3.16.0, Grafana 13.3.1, Loki 3.7.10, Alloy 1.22.0 | `bash scripts/check-observability.sh` checks retrieval metrics, Grafana's database and Prometheus readiness and targets. Inspect the Alloy and Loki services for log delivery. |
| GitHub MCP | 1.14.0, opt-in | Nine read-only tools. `make connected-smoke` checks discovery and inspects service logs for the supplied token. [Connected-mode setup](connected.md) |

The runner versions above describe the reference hosts. Compose image pins and model
defaults describe what the checkout requests. For the actual local model
selection, residency and resource observations, see [platform operations](platform-operations.md).
