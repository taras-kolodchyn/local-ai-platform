# Implementation and milestone plan

Last updated: 2026-07-19

The plan separates a reproducible MVP from compatibility claims that require real agent workloads. A milestone is complete only when its acceptance checks are captured in tests or a dated benchmark record.

## Final MVP status

| Milestone | Status on 2026-07-19 |
| --- | --- |
| M0 architecture/evidence | Complete |
| M1 platform core | Complete on the reference Mac |
| M2 retrieval/MCP | Complete for offline read-only tools |
| M3 agent compatibility | Complete as a report: transports pass; Codex autonomous repair fails; Hermes chat-only passes |
| M4 demo | Complete as a reproducible failed-agent case plus human-verified reference repair |
| M5 connected/A2A | Connected profile implemented but not live-tested without a dedicated PAT; A2A deferred |
| M6 article/release | Article and evidence complete; publication is the final repository operation |

## M0 - Architecture and evidence

Deliverables:

- Architecture diagrams and trust boundaries.
- Inference ADR.
- Capability/version matrix sourced from official documentation.
- Threat model and risk register.

Exit criteria: every configuration-sensitive claim is linked to an official source and tagged as documented, locally verified, or unverified.

## M1 - Reproducible platform core

Deliverables:

- `make doctor`, `bootstrap`, `pull-model`, `up`, `down`, `status`, and `smoke-test`.
- DMR models, LiteLLM, Redis, pgvector, Prometheus, Loki, Alloy, and Grafana.
- Health checks, loopback bindings, named volumes, isolated networks, exact image tags.
- Generated Codex and Hermes config examples.

Exit criteria:

- `make up` succeeds from a clean clone on the reference Mac.
- Chat Completions, Responses API bridge, streaming, embeddings, Redis cache, pgvector, metrics, and log ingestion pass smoke tests.
- No external MCP or Docker socket is enabled.

## M2 - Local retrieval and MCP

Deliverables:

- Repository scanner respecting `.gitignore` and an explicit extension allowlist.
- Secret/generated-file exclusions, deterministic chunks, metadata, content hashes, incremental upsert, stale-chunk deletion.
- HNSW cosine index with bounded top-k and total-character context.
- Read-only retrieval MCP server registered with LiteLLM and an explicit tool allowlist.

Exit criteria:

- Reindexing unchanged content makes no embedding calls.
- Modifying and deleting files updates only the affected rows.
- Retrieval results respect repository/branch/path filters and context limits.
- MCP cannot read an arbitrary path or perform writes.

## M3 - Agent compatibility

Deliverables:

- Codex config with `wire_api = "responses"`, workspace-write sandbox, and on-request approvals.
- Hermes custom provider config targeting the same LiteLLM alias.
- Tests for tool calls, streaming, `AGENTS.md`, shell execution, multi-file edits, and test execution.

Exit criteria: a versioned compatibility report distinguishes pass, fail, degraded, and not tested for Codex and Hermes.

## M4 - Demonstration project

Deliverables:

- Intentionally imperfect Rust API with scoped security and operational defects.
- Internal architecture rules indexed into pgvector.
- A repeatable prompt and expected checkpoints for Codex, plus a shorter Hermes run.
- Grafana dashboard panels covering the entire flow.

Exit criteria: the demo records the initial failure, fix, tests, final diff, retrieval calls, cache behavior, latency, tokens, tool duration, and errors without logging source bodies or credentials.

## M5 - Connected mode and A2A

Deliverables:

- Opt-in Compose profile for GitHub or other external MCP servers.
- Per-server credentials, scoped keys, explicit egress, and tool allowlists.
- Evaluated LiteLLM A2A gateway with protocol version 1.0 pinned.

Exit criteria: offline remains the default, connected-mode credentials never enter container images/logs, and destructive tools are absent.

## M6 - DOU article and release

Deliverables:

- Ukrainian article with first-person introduction, task -> solution -> technology -> result case structure, diagrams, screenshots, measured benchmarks, failures, limitations, and conclusion.
- Public release tag and reproducibility record.

Exit criteria: every screenshot and benchmark maps to the released commit; the article contains no placeholder compatibility claims.

## Version policy

Runtime and container versions are pinned in source. Renovation is a deliberate PR that reruns smoke tests and updates `docs/capability-matrix.md`. Model artifacts use explicit tags and should additionally record resolved digests in a release manifest after the first successful pull.
