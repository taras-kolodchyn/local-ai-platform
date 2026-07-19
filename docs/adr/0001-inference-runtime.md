# ADR 0001: Use Docker Model Runner as the default macOS inference runtime

- Status: accepted
- Date: 2026-07-19
- Decision owners: repository maintainers

## Context

The platform must use Apple Silicon acceleration, keep `make up` as the entry point, and avoid the false claim that an ordinary Linux container can access Metal. It needs an OpenAI-compatible API for Qwen3-Coder and Qwen3-Embedding, streaming, tool calling, configurable context, pinned model artifacts, and connectivity from Docker Compose.

The target development machine used for the initial implementation is arm64 with 48 GiB unified memory, Docker Desktop 4.79.0, Docker Model Runner 1.2.1, and Ollama 0.32.1.

## Decision

Use Docker Model Runner (DMR) with its llama.cpp backend as the default runtime.

- On Apple Silicon, DMR enables Metal automatically.
- On macOS, the inference engine runs outside ordinary Linux containers in a Docker Desktop host sandbox.
- DMR exposes OpenAI-compatible chat-completions and embeddings endpoints to host and Compose clients.
- Compose can declare model dependencies, while the MVP uses explicit pinned artifact names and the documented host endpoint for transparent diagnostics.
- The generation artifact is `ai/qwen3-coder:30B-A3B-UD-Q4_K_XL` (about 16.5 GB, Q4 family quantization).
- The embedding artifact is `ai/qwen3-embedding:0.6B-F16` (1024 output dimensions in the upstream model).
- The coding context starts at 65,536 tokens. Higher values require measurement because KV-cache memory grows with context.

LiteLLM exposes the aliases `local-qwen` and `local-embeddings`. Because the local provider may only offer chat completions for all required request shapes, LiteLLM explicitly enables the Responses-to-Chat-Completions bridge.

## Alternatives

### Native Ollama

Ollama is the fallback runtime for Phase 2. It is mature, easy to install, supports tools and OpenAI-compatible APIs, and is already present on the initial test Mac. It stays secondary because DMR can now integrate model lifecycle with Docker Desktop and Compose while still using Metal outside Linux containers.

### MLX-LM

MLX-LM is optimized for Apple Silicon and may provide excellent performance. Its local server is maintained as an OpenAI-compatible endpoint, but tool-call parsing is a fast-moving compatibility surface. It is a benchmark candidate, not the default compatibility baseline.

### llama.cpp directly

llama.cpp treats Apple Silicon as a first-class Metal target and exposes an OpenAI-compatible server. It gives maximum control but makes installation, artifact lifecycle, server supervision, and model switching the repository's responsibility. DMR already uses llama.cpp and supplies those lifecycle pieces.

## Consequences

Positive:

- One Docker-oriented lifecycle for containers and model artifacts.
- Honest Metal boundary on macOS.
- Pinned, verified-publisher model artifacts.
- A direct path to Compose model declarations.

Negative:

- Docker Desktop 4.40+ and Model Runner must be enabled.
- The DMR API is unauthenticated, so it must remain loopback-scoped and behind LiteLLM for clients.
- Model startup and switching can add cold-start latency.
- The selected 30B model plus long KV cache may pressure 32 GiB machines; `make doctor` must fail or recommend a smaller profile rather than overpromise.

## Validation gates

The decision remains provisional in performance terms until all of these pass on an Apple Silicon Mac:

1. DMR reports the model available and Metal-backed inference in its logs.
2. LiteLLM `/v1/chat/completions` and `/v1/responses` both return a completion.
3. Responses streaming works through the bridge.
4. A tool call survives Qwen -> DMR -> LiteLLM -> client conversion.
5. Embeddings return exactly 1024 finite values.
6. Codex performs a sandboxed multi-file edit and runs tests.
7. Hermes completes a tool-using turn through the same alias.
8. Peak memory and latency are recorded for 32K and 64K contexts.

## Official references

- Docker Model Runner: https://docs.docker.com/ai/model-runner/
- DMR inference engines and Metal: https://docs.docker.com/ai/model-runner/inference-engines/
- Compose models: https://docs.docker.com/ai/compose/models-and-compose/
- Qwen3-Coder artifact: https://hub.docker.com/r/ai/qwen3-coder
- Qwen3-Embedding artifact: https://hub.docker.com/r/ai/qwen3-embedding
- Ollama OpenAI compatibility: https://docs.ollama.com/api/openai-compatibility
- MLX-LM: https://github.com/ml-explore/mlx-lm
- llama.cpp: https://github.com/ggml-org/llama.cpp
