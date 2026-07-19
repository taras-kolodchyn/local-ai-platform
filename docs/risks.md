# Risk register

Last reviewed: 2026-07-19

| ID | Risk | Impact | Likelihood | Mitigation / evidence gate |
| --- | --- | --- | --- | --- |
| R1 | Responses API bridge drops tool calls or stream events | High | Medium | Pin LiteLLM 1.93.0; contract-test non-streaming, streaming, and tool round trips with the actual Qwen runtime |
| R2 | 30B model plus 64K KV cache exhausts unified memory | High | Medium | `make doctor` checks memory; start at 64K only on 48 GiB+; record `ollama ps`/DMR metrics and provide a smaller profile |
| R3 | Qwen emits malformed or unreliable tool calls | High | Medium | Constrained tool schemas, low temperature, bounded turns, behavioral evals; never infer tool support from model card alone |
| R4 | Hermes custom provider chats but hangs on streamed tools | High | Medium | Version-pin Hermes and run a timed tool-use integration test; label chat-only success as partial |
| R5 | Response cache returns stale agent state | High | Medium | Short TTL; cache off for stateful/tool turns by default in client examples; explicit per-request `no-cache` test |
| R6 | Prompt injection in indexed repository controls the agent | Critical | High | Mark retrieval as untrusted data, bound chunks, separate system policy, approvals for writes/shell, adversarial fixtures |
| R7 | MCP exposes destructive tools | Critical | Medium | Offline default, `allowed_tools`, read-only retrieval API, scoped virtual keys, no Docker socket, connected profile opt-in |
| R8 | Logs leak source, tokens, or credentials | Critical | Medium | Disable prompt/response body persistence by default, redact headers, log metadata only, scan smoke-test logs for known sentinels |
| R9 | Unpinned image/model supply chain changes behavior | High | Medium | Exact image/model tags, arm64 manifest checks, release digest manifest, signature verification where upstream supplies it |
| R10 | Incremental indexing leaves stale or cross-branch chunks | Medium | Medium | Snapshot IDs, deterministic hashes, transactional upsert/delete, branch/repository filters, deletion tests |
| R11 | HNSW plus metadata filters returns too few results | Medium | Medium | pgvector 0.8.5 iterative scans, tune `hnsw.ef_search`, measure recall against an exact-search fixture |
| R12 | DMR unauthenticated API becomes reachable off-host | Critical | Low | Host-side loopback only, do not publish DMR on LAN, put client auth at LiteLLM, doctor checks listener addresses |
| R13 | `make up` mutates user-wide Codex/Hermes configuration | Medium | Medium | Generate files under `.local/`; show opt-in install commands; never overwrite `~/.codex` or `~/.hermes` automatically |
| R14 | Docker Desktop is absent, stopped, or Model Runner disabled | Medium | High | Doctor with exact remediation; `make up` can start the installed app and enable DMR, with timeouts and actionable failure |
| R15 | Article publishes benchmark or compatibility claims from a different commit | High | Medium | Store benchmark metadata with commit, versions, model digests, context, hardware, and commands; screenshots reference release tag |
