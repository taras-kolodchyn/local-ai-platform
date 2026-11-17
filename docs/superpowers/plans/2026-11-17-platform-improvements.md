# Local Platform Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make model management consistent, verification complete, retrieval
hybrid and status useful while leaving Moyai as the default single agent.

**Architecture:** Preserve the existing environment, Compose, PostgreSQL and
LiteLLM boundaries. Generate model configuration from resolved settings; add
rank fusion within existing retrieval; retire owned legacy agent registrations.
Independent components have disjoint implementation ownership and are integrated
through the existing startup/smoke lifecycle.

**Tech Stack:** Python 3.11–3.14, Bash, Docker Model Runner 1.2.9, LiteLLM
1.109.1, PostgreSQL 17 / pgvector 0.8.7, existing Rust tests.

**Spec:** `docs/superpowers/specs/2026-11-17-platform-improvements-design.md`

## Global Constraints

- `make up` stays the supported entry point, default offline and loopback-only.
- Inference stays in Docker Model Runner's native macOS boundary.
- All LiteLLM comments/descriptions are English; preserve model aliases.
- Delete only authorized retired profile data via an explicit purge; preserve
  Moyai and knowledge data. Never commit secrets or generated private data.
- Keep the current models and embedding dimension 1024.
- Finish with the stack off and model allocations unloaded.

## Review Focus

- Invalid or overridden model references must not retain stale labels or route
  to a different model than the one pulled/unloaded.
- Host/process RSS and artifact sizes must not masquerade as Metal allocation.
- Lexical candidates must not bypass any scope or return unbounded context.
- Verification failure/signal and cleanup failure must remain observable errors.
- Legacy registration/data cleanup must preserve unrelated records, Moyai and
  the knowledge index, including when a key or filesystem path is unexpected.

### Task 1: Resolved model configuration and read-only status

**Files:** `litellm/`, model configuration/status helpers, `scripts/bootstrap.sh`,
`scripts/status.sh`, `scripts/doctor.sh`, `compose.yaml`, focused script tests.

**Interfaces:** Environment model references/context in; generated LiteLLM config
and safe status JSON/text out. Coordinate the generated mount path with startup.

- [x] Write behavior tests for overridden references/context/English metadata,
  safe rendering, missing artifacts and stopped-runtime status; observe RED.
- [x] Implement validation, rendering, effective metadata and observational
  status without starting services or loading model weights.
- [x] Exercise pinned LiteLLM's actual configuration/management boundary and
  run focused tests GREEN. Report changed files and status interfaces.

### Task 2: Hybrid retrieval on the existing database

**Files:** `retrieval/migrations/`, `retrieval/src/local_ai_retrieval/`,
`retrieval/tests/`, optional focused smoke helper.

**Interfaces:** Existing search inputs/citations retained; query text reaches
the SQL search alongside its embedding. Fusion relevance is distinguished from
raw cosine similarity.

- [x] Add failing real-DB tests for exact symbol/path retrieval missed by
  vector candidates, semantic-only results, stable ties, scope filters, bounded
  content and malicious/empty keyword inputs.
- [x] Add an idempotent full-text index migration and bounded rank-fusion SQL;
  connect HTTP, MCP and scoped vector-store search to the shared implementation.
- [x] Run the disposable-DB tests GREEN without any live platform dependency.

### Task 3: Complete verification command

**Files:** `Makefile`, new verification runner, focused lifecycle tests.

**Interfaces:** Runs existing make targets, records private phase outcomes and
always calls supported shutdown; no implementation of model routing or agent
registration inside this runner.

- [x] Exercise real make targets with controlled external boundaries; prove
  phase failures and cleanup failures cannot be reported as success.
- [x] Implement `make verify`: lint, test, up, smoke-test, moyai-smoke with
  restart persistence, down and final stopped-state inventory.
- [x] Verify failure cleanup and successful ordering with focused tests.

### Task 4: One default agent and complete integration

**Files:** startup/smoke/provisioning scripts, their tests, README and current docs.

**Interfaces:** Moyai UI link remains local; owned legacy A2A entries and grants
are removed, knowledge registration remains usable without legacy runtimes.

- [x] Write RED tests for removal of owned legacy entries, an explicit narrowly
  scoped profile-data purge, idempotence and prevention of startup re-registration.
- [x] Implement scoped reconciliation, omit legacy runtime startup from
  default `make up`, and add smoke checks for the single-agent configuration.
- [x] Review all component diffs, run the complete local suite via `make verify`,
  inspect actual model/agent configuration during the live cycle and ensure
  cleanup leaves zero owned containers and resident models.
- [x] Update documentation with exact versions/date, retain existing English
  descriptions, and perform a fresh independent whole-change review.

Commit/push/merge are not requested for this change; leave the result reviewable
in the existing checkout without publishing it automatically.

## Completion evidence

Completed 2026-11-17. Full `make verify` passed all seven phases, including
159 Python/DB checks, 2 Rust tests and real Moyai acceptance with restart. Final
independent status showed zero owned containers, zero resident models and no
inference-process RSS entries. The explicitly authorized legacy data purge
removed three volumes, the memory database/roles, profile directories and four
dedicated keys while live knowledge access remained valid.

Independent review found no remaining Important/Critical issue. Its documented
legacy-profile instructions were corrected; the HNSW concern was measured and
fixed with a real query-plan regression. Actual `/model/info` agreement is now
checked by smoke tests. No commit, push or merge was performed.
