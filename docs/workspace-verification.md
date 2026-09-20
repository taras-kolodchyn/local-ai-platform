# Workspace verification

Date: 2026-09-21. Local Apple Silicon environment, offline profile.

Versions: LiteLLM 1.102.0; Hermes v2026.9.14; Mem0 2.1.0;
PostgreSQL 17.11; pgvector 0.8.6. Runtime dependencies are pinned in
`execution/requirements.lock`; dependency consistency was checked during build.

## Deterministic checks

- `make lint`: passed shell syntax, ShellCheck, Python compilation, generated
  configuration validation, and repository secret-pattern checks.
- `make test`: 22 retrieval, 5 offline-tool, 7 execution, 11 script tests,
  and 2 Rust tests passed.
- One optional external-database test skipped because no test DSN was supplied.
  Actual per-profile database privileges were verified by live provisioning.
- Timeout, cancellation, bounded output, restart state, backend authorization,
  source-snapshot filtering, and permission reconciliation have regression tests.

## Live checks

- Two complete `make up` runs passed after independent review fixes.
- A separate `make smoke-test` passed gateway, indexing, tool, monitoring,
  loopback-listener, and synthetic-prompt log checks.
- Registered vector search returned source citations; unscoped access was denied.
- Mem0 add, fresh-process recall, cross-profile isolation, update, deletion,
  and fresh-process absence after deletion passed.
- Development, review, and research each completed a real gateway-dispatched turn.
- Unscoped invocation was rejected and did not create a persisted task.
- `make hermes-smoke` passed the existing one-shot client path.
- Installed skills were listed and opened through actual runtime tools.
- All three backends lacked `/workspace/.env`, `/workspace/.local`, and the old
  plaintext `caller.key`; only the caller verifier was present.
- Repeated provisioning preserved credential and registry file hashes.

## Review and boundaries

Independent review found and prompted fixes for source credential exposure,
shared caller credential exposure, input backpressure outside the execution
budget, and stale permissions after adding stores. Follow-up review confirmed
those fixes. The existing one-shot entrypoint also refreshes the source snapshot.

Generation is serialized by a shared lock. Each backend has bounded admission;
FIFO fairness and a single shared waiting queue are not implemented. Automated
notes are disabled so semantic deletion cannot leave a mirrored injected note.
Historical sessions and memory history are retained separately. These choices
are documented in the [operating guide](workspace-memory.md).

Verification establishes local integration and bounded fixtures, not arbitrary
long-running task quality, PDF parsing, background scheduling, or streaming.
