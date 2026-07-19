# Agent validation — 2026-07-19

This record separates transport compatibility from agent quality. A successful
HTTP response is not evidence that a coding agent followed its instructions or
produced correct code.

## Codex CLI through LiteLLM and local Qwen

Environment: Codex CLI 0.144.6, LiteLLM 1.93.0, Docker Model Runner 1.2.1,
`ai/qwen3-coder:30B-A3B-UD-Q4_K_XL`, 65,536-token configured context.

| Check | Result | Evidence observed |
| --- | --- | --- |
| Custom provider and Responses API | Pass | Strict-config run returned model output through `http://127.0.0.1:4000/v1/responses`. |
| Streaming | Pass | The smoke test consumed a complete Responses SSE stream. |
| Function calling | Pass | The model emitted a function call and accepted the result via `previous_response_id`. |
| AGENTS.md and workspace inspection | Pass | The coding run inspected the fixture and identified the main security gaps. |
| Shell and multi-file edits | Pass with guardrail intervention | The run edited Rust, Docker, Helm, tests, and docs; policy rejected destructive removal attempts. |
| Retrieval MCP selected by the model | Fail | The MCP servers were configured and reachable, but the model chose generic resource-list calls and then fell back to shell inspection instead of `search_code`. Direct gateway MCP calls passed. |
| Correct autonomous repair | Fail | Two attempts left compilation errors and one claimed success after failed tests. The run was stopped. |

The model correctly noticed SQL injection, absent JWT protection, an excessive
retry ceiling, root container execution, and weak Helm settings. It then added
an invalid middleware signature and an incorrect `jsonwebtoken` API import,
ignored the required patch workflow, and twice attempted a broad delete. This
is a behavioral failure, not a gateway failure.

A human-reviewed reference repair was applied only to an ignored working copy.
It passed seven tests covering health access, missing/valid/expired/bad-signature/
wrong-scope JWTs, SQL parameter separation, and bounded backoff, plus
`cargo fmt --check` and `git diff --check`. The committed fixture intentionally
remains vulnerable so the exercise stays reproducible. The reference repair is
not represented as model-generated work.

## Hermes through the same gateway

`make hermes-smoke` pulled the arm64 `nousresearch/hermes-agent:v2026.7.7.2`
image and completed a one-shot custom-provider request. Hermes returned the
required `HERMES-READY` marker through LiteLLM and `local-qwen`.

This verifies shared-gateway chat compatibility. It does not claim that Hermes
completed the full Rust repair or that its durable memory and every bundled
skill were validated.

## Platform checks

`make smoke-test` passed chat, embeddings (1024 dimensions), Responses function
calling, tool-result round trip, streaming, explicit Redis cache hit/miss,
incremental pgvector indexing, retrieval, real MCP retrieval and read-only SQL
calls through LiteLLM, Prometheus targets, Grafana health, and prompt-sentinel
absence from collected logs.

The provisioned Grafana dashboard was also opened in a real browser with
Playwright. Its panels rendered live request, latency, cache, retrieval, MCP,
indexing, service-health, and metadata-only log signals with zero browser-console
errors at capture time.
