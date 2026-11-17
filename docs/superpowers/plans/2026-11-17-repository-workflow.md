# End to end repository workflow implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete one local Moyai repository task from cited context and native plan through model edits, trusted offline Rust checks and verified downloadable results.

**Architecture:** Preserve the current Moyai UI, native planning/Temporal runtime and per-conversation SSH workspace. Introduce immutable registered source generations, a narrow internal MCP context bridge, and a controller-owned task ledger with phase/tool guards. Return a patch for review; do not write to the selected host checkout.

**Tech Stack:** Existing Python 3.14, MCP 2.3.0, PostgreSQL 17/pgvector 0.8.7, LiteLLM 1.109.1, pinned Moyai/Pydantic AI 2.55.0, Rust 1.100.0, Docker Model Runner/Metal.

**Spec:** [Approved design](../specs/2026-11-17-repository-workflow-design.md).

## Global constraints

- `make up` remains the supported entry point; offline is the default.
- All model calls use `local-qwen` or `local-embeddings` aliases.
- Keep host ports on `127.0.0.1`; no Docker socket, host home or writable host checkout in agents.
- Do not commit credentials, indexed source, model data or request/response bodies.
- Keep plain Moyai conversations and existing smoke checks working.
- Do not change the presentation or previous `docs/demo-runbook.md`.
- Implement in the current suitable checkout on `codex/repository-workflow`; preserve unrelated work.

## Review focus

1. Importing a Git subtree or linked worktree preserves actual staged-byte provenance and excludes nested ignored/secret paths.
2. A reindex/model change cannot silently change context in an existing task or destroy a previously usable generation.
3. Tool definitions cached before a phase transition cannot bypass the execution guard.
4. Lost/canceled check operations cannot be accepted as completed or blindly replayed after restart.
5. A model cannot manufacture success by changing tests/config, skipping all tests, changing files after verification, or returning fake artifacts.

## Shared interfaces

Repository catalog directory: `.local/repositories/`, mounted read-only into the controller and context bridge as `/run/local-repositories`. Immutable sources are `snapshots/<snapshot_id>/source/`; each snapshot has `manifest.json`. Catalog registration is atomic; snapshot contents never change.

Manifest version 1: `schema_version`, `repository_id`, `name`, `snapshot_id`, `git` (`branch`, `head`, `dirty`, `subtree`), `files` (`path`, `sha256`, `size`), `scanner_version`, `verification_profile`. The source snapshot ID covers sorted path/byte hashes; model/chunker identity is tracked separately in indexed generation metadata. Catalog rows add the registered `store_id` and current snapshot selection. Do not serialize host paths.

Default acceptance profile ID: `rust-retry`. It uses the existing Rust fixture and pinned offline dependencies. Its protected oracle checks shared `bounded_backoff_ms(u32) -> u64`, ceiling 30,000 ms including very large attempts, and retained compatibility of `legacy_retry_delay_ms`. Model edits may affect `src/lib.rs`, regression tests under `tests/`, and README; protected package/dependency/oracle data is not writable through workflow tools.

Guest helper protocol: `/opt/local-repository/guest.py` receives one bounded JSON object on stdin and returns one JSON object. Operations: `import` (conversation workspace plus manifest/files), `tree` (project file hashes/text with exclusions), `check` (fixed profile, task/operation IDs, timeout), `cancel`/`status` (tracked operation). It is called by trusted controller SSH code, never exposed as a generic model shell. Validation outcomes include actual commands/exits, test counts, timeout/cancel state, output and resource measurements when available.

Controller repository tools have stable IDs/names: `list_repositories`, `open_repository`, `search_repository`, `begin_repository_changes`, `run_repository_checks`, `finish_repository_task`, `repository_task_status`. Arguments selecting host paths, arbitrary commands, URLs or credentials are absent. `open_repository` pins the snapshot; reads/search/planning are allowed before changes. `begin_repository_changes` checks a native plan and cited context. `run_repository_checks` is the only command execution path in repository mode. `finish_repository_task` requires fresh trusted verification and creates the final artifact files.

## Task 1 Immutable source registration and retrieval generations

**Files:** create `scripts/repository-snapshot.py`, `scripts/prepare-repository.py`, `retrieval/migrations/004_repository_snapshots.sql`; modify ingestion/DB/registry/vector API/CLI and add focused script/retrieval tests. Makefile/Compose wiring is Task 4 ownership.

**Consumes:** explicit operator-selected REPO and profile, existing source guards and embedding alias.

**Produces:** versioned manifests/catalog above; immutable snapshot-aware registered vector stores; citations with snapshot provenance; public Python helper functions for preparing a source snapshot and atomically registering its indexed generation.

- [ ] Write failing tests for root/subtree/worktree/detached/dirty, nested ignores, symlinks/secrets, required Cargo.lock and content-stable staged hashes.
- [ ] Run the focused snapshot tests and confirm the missing implementation fails.
- [ ] Implement host preparation and atomic publication without mutating the old source snapshot helper's legacy behavior.
- [ ] Add real-DB failing tests for two conflicting generations, pinned scope in both search channels, old active generation surviving failed rebuild and model fingerprint invalidating reuse.
- [ ] Implement migration and bounded generation-aware ingestion/search. Preserve legacy active-generation queries and existing registry transport behavior.
- [ ] Run script and retrieval tests; report the exact operator/import API and catalog shape to Tasks 2/4.

## Task 2 Controller ledger capability and enforced phases

**Files:** create `integrations/moyai/repository_ledger.py`, `repository_backend.py`, `repository_workflow.py`, associated unit and pinned-SDK tests; modify `moyai_workspace.py`/`server.py` only for native capability composition. Do not edit Task 1 retrieval or Task 3 guest files.

**Consumes:** manifest/catalog above, guest protocol, fixed internal MCP client configured by Task 4. Reuse native `write_plan` and persisted plan store.

**Produces:** stable repository toolset, controller-owned `/data/local-tasks.db`, session/authority binding, idempotent operation/state gates, scoped context requests, fresh verification receipts and final patch/report/result artifact files in the conversation workspace.

- [ ] Write failing standalone tests for transitions, wrong authority, stale receipts, budgets, duplicate operations and uncertain recovery.
- [ ] Implement transactional ledger and controller backend with bounded SSH JSON transport and fixed context origin.
- [ ] Add pinned-SDK tests for stable toolset IDs, native tool filtering and dispatch-time denial of write/shell/delegation before or outside permitted phases.
- [ ] Implement native capability using durable operations/activities for I/O; enforce deadline/request/tool budgets independently of upstream unlimited-model configuration.
- [ ] Add tests rejecting empty/fake/tampered/stale verification and mismatched downloaded artifacts. Ensure plain chat behavior remains unchanged.
- [ ] Run standalone and pinned-image tests; report integration environment/secret needs to Task 4.

## Task 3 Offline Rust guest runner and independent oracle

**Files:** create `integrations/moyai/repository_guest.py`, `integrations/moyai/oracles/` and pure Python runner tests; modify `integrations/moyai/Dockerfile.sandbox`. Do not edit controller/Compose/retrieval files.

**Consumes:** shared guest protocol, existing Rust fixture and Cargo.lock.

**Produces:** bounded import/tree/check/cancel/status helper; pinned runtime toolchain/vendor; read-only oracle; observed baseline failure and repaired success independent of model text.

- [ ] Write failing tests for protected paths, malformed/oversized JSON, import hash mismatch, fixed argv, output bounds, process-group timeout/cancel and durable unknown-operation handling.
- [ ] Implement guest helper under the existing nonroot identity; prohibit arbitrary command selection and verification outside its project root.
- [ ] Add pinned Rust/linker/vendored dependencies at connected image build; run Cargo offline with one job and per-task build output.
- [ ] Implement the protected retry oracle and agent-added-test checks. Show the original fixture fails and a minimal reference repair passes without modifying the committed fixture.
- [ ] Run helper tests and image/build checks; record actual cold build/resource requirements. Leave model-driven acceptance to Task 5.

## Task 4 Local MCP bridge provisioning and lifecycle integration

**Files:** create `integrations/moyai/context_bridge.py`, context bridge/provisioning tests; modify `compose.moyai.yaml`, bootstrap/provisioning scripts, `Makefile`, relevant smoke/lifecycle assertions. Root owns these shared files.

**Consumes:** immutable registered store IDs, controller toolset and guest profile.

**Produces:** `make repository-add REPO=...`, authenticated internal read-only MCP service, separate scoped retrieval credential, fixed controller bridge configuration, current lifecycle and metrics integration.

- [ ] Write failing bridge tests for auth, token/snapshot mismatch, foreign store/chunk access, redirects/proxy handling and request/result limits.
- [ ] Implement a small native MCP server backed by the existing scoped vector API. Use read-only annotations and no host port; preserve the inference-only Moyai key.
- [ ] Wire source catalog and separate private context secrets without mounting them in the sandbox. Bind task scope on the trusted controller side; validate the same scope at the bridge.
- [ ] Wire operator registration, updated source ingestion, native runtime and guest profile into supported startup and shutdown.
- [ ] Add low-cardinality task/phase metrics and a host-side per-run resource sampler without exposing Docker control to agents.
- [ ] Run focused bridge/provision/lifecycle tests and pinned SDK/image checks.

## Task 5 Real local-model acceptance and final verification

**Files:** create `scripts/repository-smoke.py` and acceptance tests/docs; expand `scripts/verify.py` to include the repository path and preserve final cleanup. Root integrates after Tasks 1–4.

- [ ] Write acceptance assertions before running the model: real scoped MCP receipts and native plan, expected source citations, baseline oracle failure, allowed model diff and new regression test, protected checks unchanged, real passing validation, current tree/artifact hashes and unchanged host source.
- [ ] Run a fresh Moyai session for the existing Rust retry task through its actual API/tool path. Do not inject a reference repair or silently substitute deterministic code for the model output.
- [ ] Exercise actual failed validation/repair, fresh-process state, settled restart, cancellation/timeout, unknown-outcome reconciliation and cross-scope denial.
- [ ] Repeat the bounded model acceptance in fresh conversations; retain all outcomes and measured task/phase latency/resources in private receipts. Diagnose failures before changing code or retrying.
- [ ] Run `make lint`, `make test`, `make smoke-test` and expanded `make verify`; verify owned containers/models stop and data/artifacts remain.
- [ ] Review the complete patch independently, resolve findings and update public architecture/operating/compatibility documentation with exact tested scope, versions/date and remaining limits.

## Execution and review

The user approved implementation of the design. Use the existing parallel-agent
workflow for independent file ownership in Tasks 1–3; root implements Task 4 and
integrates Task 5. Report interface changes promptly, do not overwrite another
worker's files, and use fresh review of each integrated subsystem plus the final
patch. Make local checkpoints on the feature branch after meaningful passing
checks; no remote publishing or presentation edits are part of this plan.
