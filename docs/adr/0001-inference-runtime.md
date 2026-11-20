# ADR 0001 — Docker Model Runner for macOS inference

Accepted on 2026-07-19. Configuration reviewed on 2026-11-20 with Docker
Desktop 4.100.0 and Model Runner 1.2.9 installed on the reference Mac.

## Decision

Use Docker Model Runner (DMR) as the default generation and embedding runtime
on Apple Silicon. Its llama.cpp backend can use Metal on the macOS host.
The Linux containers run the gateway, retrieval, agent and supporting services.
The CPU reranker runs in its own container. [DMR inference engines](https://docs.docker.com/ai/model-runner/inference-engines/) ·
[Platform architecture](../architecture.md)

Docker Desktop already manages the local services. DMR also provides the model
pull, configuration, inspection and unloading commands needed by the platform
lifecycle. Using a separate Ollama, MLX or standalone llama.cpp server would
require a second runtime to provision and supervise.

## Consequences

- `make up` enables DMR's host TCP endpoint on port 12434, prepares the selected
  artifacts and configures the context size and embedding mode. LiteLLM reaches
  DMR through the host bridge and publishes stable client aliases at
  `127.0.0.1:4000/v1`. [DMR setup](https://docs.docker.com/ai/model-runner/get-started/) ·
  [DMR API](https://docs.docker.com/ai/model-runner/api-reference/) ·
  [Model preparation](../../scripts/pull-model.sh)
- Authenticated clients use LiteLLM. DMR's host-side TCP endpoint is local to the
  Mac. `make smoke-test` checks its listener alongside the gateway listeners and
  sends inference requests through LiteLLM. [Endpoint checks](../../scripts/smoke-test.sh)
- The default profile requests 65,536 coding tokens. The host check requires
  at least 32 GiB of unified memory and recommends 32,768 tokens on Macs with
  32–47 GiB. Set `QWEN_CONTEXT_SIZE` in the generated `.env` before startup
  on those machines. [Host checks](../../scripts/doctor.sh) ·
  [DMR context configuration](https://docs.docker.com/ai/model-runner/configuration/)
- `make down` stops the clients and requests unloading of the two configured DMR
  models. Downloaded artifacts survive shutdown. Use `make benchmark` to
  measure cold and warm latency on a specific Mac.
  [Docker model unload](https://docs.docker.com/reference/cli/docker/model/unload/) ·
  [Benchmark method](../benchmarking.md)
