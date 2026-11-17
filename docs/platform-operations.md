# Model configuration, verification and the single-agent stack

Implemented and verified against the pinned stack on 2026-11-17: LiteLLM 1.109.1,
Docker Model Runner 1.2.9, PostgreSQL 17 / pgvector 0.8.7. Per-run local receipts
under `.local/verification/` record actual completion and failure status.

## One active agent

`make up` starts the core platform and local Moyai. It stops/removes previously
running legacy workspace containers and retires their platform-owned LiteLLM A2A
registrations. Existing independent gateway records are not deleted based on a
matching display name alone. Knowledge registration and scoped retrieval remain
available without the old Hermes-based development/review/research runtimes.

Moyai is currently connected through its local UI link and the `local-qwen`
inference alias. It does not pretend to expose an A2A endpoint. Additional agent
management through LiteLLM is future work. Codex/Hermes generated client examples
are optional clients, not extra active LiteLLM agents.

Ordinary startup does not delete data. An explicit one-time cleanup is available:

```sh
make up
make purge-legacy-agents
make down
```

The cleanup revokes only verified legacy profile/console keys, removes precisely
`workspace_development_state`, `workspace_review_state`, and
`workspace_research_state`, drops `workspace_memory` and its three private roles,
and removes those profiles' local state, memory passwords and execution locks.
Moyai conversations/files, the knowledge index and the provisioning key needed
for knowledge registration remain intact. Key ownership or path mismatches fail
the migration instead of guessing. This command permanently deletes legacy data.

## Resolved models and status

`.env` provides the selected runtime references and context. Missing model fields
use `.env.example` defaults, consistently in the shell lifecycle and Python
resolver; ambient variables cannot redirect one half of model management.
`scripts/model-config.py` renders the tracked LiteLLM template into the ignored
effective configuration. Both aliases stay stable. Current models are unchanged.
Descriptions and metadata follow the resolved reference; unknown models receive
honest generic metadata rather than the previous model's name or parameter size.

`make status FORMAT=json` is read-only and does not bootstrap secrets, start
containers or load weights. A stopped stack is a normal status. Model inspection
is restricted to safe metadata; tokenizer templates and credential values are
excluded. Artifact disk size, requested context, artifact context metadata, host
capacity and observed process RSS are distinct values. Runtime context and Metal
allocations are explicitly unavailable when the runtime does not expose them.

Docker documents that model inspection does not establish the effective
[runtime context](https://docs.docker.com/ai/model-runner/configuration/).
English descriptions are [LiteLLM deployment metadata](https://docs.litellm.ai/docs/proxy/ui_store_model_db_setting).

## Complete verification

```sh
make verify
```

The ordered phases are lint, required unit/DB/Rust tests, supported startup,
JSON status, endpoint smoke and real Moyai acceptance with a service restart.
The finalizer always attempts shutdown and independently checks owned project
containers and configured resident models. Failures, cleanup failures and
unavailable inventories are unsuccessful results. Unrelated Docker projects or
models are preserved. Private logs and JSON phase results are kept under
`.local/verification/`; do not commit this directory.

## Hybrid retrieval

The shared query takes bounded semantic candidates from pgvector and keyword
candidates from PostgreSQL full-text search with the `simple` configuration.
The lexical index includes paths, symbols and content and is updated by the
database when chunks change. Both candidate lists apply identical repository,
branch, exact-path and path-prefix restrictions before ranking.

Results are deduplicated and fused with reciprocal rank fusion (RRF, constant
60), with stable tie-breaking. The existing context-character and result-count
limits still apply. Raw `similarity` remains cosine similarity; `relevance` is
the raw RRF score. Vector-store API scores normalize RRF by `61 / 2`, capped to
`[0,1]`; a first-ranked hit from one channel scores 0.5. `score_threshold` applies
to that normalized hybrid ranking signal. Diagnostic attributes distinguish
`score_kind`, `rrf_relevance` and `cosine_similarity`.

Tests use deterministic vectors and real disposable PostgreSQL to verify exact
symbols/paths/errors beyond the semantic candidate pool, scope isolation,
semantic-only hits, bounded citations, stable ranking and migration idempotence.
They do not require model weights and are not skipped in CI.

Upstream: [PostgreSQL 17 text search](https://www.postgresql.org/docs/17/textsearch-controls.html),
[pgvector hybrid retrieval](https://github.com/pgvector/pgvector#hybrid-search).

## Verification record — 2026-11-17

The complete `make verify` run passed lint, 159 Python/DB checks and 2 Rust
tests, startup, live model-binding checks, JSON status, endpoint smoke and real
Moyai file/shell/pytest/artifact acceptance with restart persistence and separate
conversation directories. All seven phase exit codes were zero. Final independent
status showed no owned containers, no resident models and no inference-process
RSS entries.

The explicit cleanup removed the three legacy state volumes, `workspace_memory`
and its three roles, the three local profile directories and four dedicated keys.
Live queries confirmed zero legacy A2A registrations and retained scoped
knowledge access. No new model was downloaded or selected. Repository changes
remain local until separately committed/published.
