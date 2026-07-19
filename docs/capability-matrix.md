# Capability and version matrix

Research and live-verification date: 2026-07-19. Sources are upstream project
documentation or release metadata. Results below distinguish platform transport
checks from autonomous agent quality.

## Selected versions

| Component | Pinned/observed version | Relevant documented capability | Repository verification |
| --- | --- | --- | --- |
| Codex CLI | observed `0.144.6` | Custom `model_providers`, `base_url`, `env_key`, `wire_api = "responses"`, sandbox and approval configuration, `AGENTS.md` | Transport/tool bridge pass; autonomous Rust repair and model-selected retrieval MCP fail |
| LiteLLM | `1.93.0` | `/v1/responses`, explicit bridge to custom chat-completions providers, streaming, Redis cache, Prometheus, MCP and A2A gateways | Live pass for chat, embeddings, Responses, streaming, cache, MCP, metrics |
| Docker Desktop | observed `4.79.0` | Model Runner on macOS; Compose support | Live pass after recovering a stale backend |
| Docker Model Runner | observed `1.2.1` | llama.cpp, Metal on Apple Silicon, OpenAI-compatible API | Live pass with host Metal inference |
| Qwen3-Coder | `30B-A3B-UD-Q4_K_XL` artifact | Tool calling, 256K model context, 30B total/3.3B active MoE | 65,536 runtime context; transport pass, complex autonomous repair fail |
| Qwen3-Embedding | `0.6B-F16` artifact | 32K model context, 1024 dimensions, code/multilingual retrieval | Live 1024-dimension and pgvector search pass |
| Hermes Agent | release `2026.7.7.2`, package `0.18.2` | Custom OpenAI-compatible `/v1/chat/completions` provider, local endpoints, persisted base URL | One-shot shared-gateway pass; full coding workflow not claimed |
| PostgreSQL/pgvector | pgvector `0.8.5`, PostgreSQL 17 image | HNSW, cosine distance, metadata filtering patterns | Live ingestion, pruning/reuse, search and read-only MCP pass |
| Redis | `8.8.0` | LiteLLM response cache backend | Explicit miss/hit and bypass path pass |
| Prometheus | `3.13.1` | Metrics collection | All configured targets healthy; dashboard queries evaluated |
| Grafana | `13.1.0` | Dashboards | Provisioning, panels, and Playwright visual check pass |
| Loki | `3.7.3` | Local log store | Metadata-only streams query successfully |
| Grafana Alloy | `1.17.1` | Container/log collection pipeline | Local file pipeline pass; prompt sentinel absent |

## Compatibility conclusions

### Codex -> LiteLLM -> local chat-completions model

Live verified with an explicit bridge. LiteLLM requires
`use_chat_completions_api: true` when Codex calls `/v1/responses` while the
custom provider exposes `/chat/completions`. Tool results must be submitted
with `previous_response_id`; replaying the original function-call item caused
the local model template to reject the request.

### Hermes -> LiteLLM

Hermes documents a first-class custom provider with `model.default`,
`model.provider: custom`, and `model.base_url`. The one-shot shared-gateway path
passed. That is deliberately reported as chat compatibility only; no full
Hermes coding or tool workflow is claimed.

### MCP through LiteLLM

LiteLLM documents stdio, SSE, and Streamable HTTP transports, server and tool
allowlists, per-key/team/org permissions, and MCP use from both `/v1/responses`
and `/v1/chat/completions`. The gateway successfully executed real retrieval
and bounded read-only PostgreSQL calls. The local Qwen coding run did not select
the requested retrieval tool, so tool availability and agent behavior remain
separate results. External GitHub MCP is opt-in connected-mode work and was not
live-tested without a dedicated token.

Full observations: [agent validation](results/agent-validation-2026-07-19.md)
and [benchmark JSON](results/benchmark-2026-07-19.json).

### A2A

LiteLLM documents A2A 0.3 and 1.0 normalization, streaming, logging, and permissions. It is possible but not part of the first MVP; an upstream-capability checkbox is not a local compatibility result.

## Official sources

- Codex configuration reference: https://developers.openai.com/codex/config-reference/
- Codex `AGENTS.md`: https://developers.openai.com/codex/guides/agents-md/
- LiteLLM Responses API and bridge: https://docs.litellm.ai/docs/response_api
- LiteLLM caching: https://docs.litellm.ai/docs/proxy/caching
- LiteLLM MCP: https://docs.litellm.ai/docs/mcp
- LiteLLM MCP permissions: https://docs.litellm.ai/docs/mcp_control
- LiteLLM Prometheus: https://docs.litellm.ai/docs/proxy/prometheus
- LiteLLM A2A: https://docs.litellm.ai/docs/a2a
- LiteLLM 1.93.0 release: https://github.com/BerriAI/litellm/releases/tag/v1.93.0
- Hermes custom providers: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/integrations/providers.md
- Hermes release: https://github.com/NousResearch/hermes-agent/releases/tag/v2026.7.7.2
- Qwen3-Coder: https://qwenlm.github.io/blog/qwen3-coder/
- Qwen3-Embedding: https://qwenlm.github.io/blog/qwen3-embedding/
- Docker Model Runner: https://docs.docker.com/ai/model-runner/
- DMR inference engines: https://docs.docker.com/ai/model-runner/inference-engines/
- DMR Qwen3-Coder artifact: https://hub.docker.com/r/ai/qwen3-coder
- DMR Qwen3-Embedding artifact: https://hub.docker.com/r/ai/qwen3-embedding
- pgvector: https://github.com/pgvector/pgvector
- Ollama context length: https://docs.ollama.com/context-length
