# Repository tasks in local Moyai

This workflow attaches a registered source snapshot to a Moyai conversation,
retrieves cited context, records a native plan, edits an isolated copy, and returns
a patch with independent verification evidence. The selected host checkout is
never the agent's writable workspace. Start the platform with `make up`.

## Register and run the first profile

```sh
make up
make repository-add REPO="$PWD/examples/rust-service"
```

Registration prints the opaque repository ID. In [Moyai](http://127.0.0.1:8787),
create a conversation and ask it to repair that registered repository's retry
policy: expose `bounded_backoff_ms(u32) -> u64`, start at 100 ms, increase
exponentially, cap at 30,000 ms even for extreme attempts, retain the legacy public
name/signature with identical corrected values, and add a regression test covering
both entry points. Ask it to complete the trusted checks and
deliver the patch through Files.

The first verification profile is `rust-retry`, anchored to the bundled fixture's
package and lockfile. It is a concrete acceptance case, rather than a general
Rust build service. The existing repository indexing commands remain available
for other source trees; an execution profile must be implemented and verified
before another project can use controlled repository execution.

The model's tools follow these gates:

1. `list_repositories` and `open_repository` select an operator registration.
2. `search_repository` retrieves passages from the pinned indexed generation.
   Native file tools inspect the corresponding isolated source copy.
3. `run_repository_checks` demonstrates the original policy violation. The
   native `write_plan` must include a measurable retry ceiling criterion.
4. `begin_repository_changes` admits edits. This profile permits `src/lib.rs`,
   `tests/*.rs`, and README; package/dependency and verifier inputs are protected.
5. `run_repository_checks` builds offline and runs project tests, protected
   policy tests and the added tests. All added tests must pass the repair, and
   their counterexamples must compile and complete. At least one added test must
   fail the 60,000-ms counterexample; supporting tests may pass both versions.
6. `finish_repository_task` creates and verifies three native Files entries:
   `changes.patch`, `test-report.json`, and `result.json`.

The controller enforces phases when a tool executes, including definitions cached
before attachment. Repository mode uses the fixed check tool for execution.
Ordinary unattached conversations retain the existing native shell workflow.

## Source, context and authority

The operator prepares filtered immutable source bytes under
`.local/repositories/snapshots/`. The snapshot records Git head, branch, dirty
state, subtree, exact file hashes and profile. The catalog publishes a generation
only after indexing and gateway registration. Existing conversations retain their
selected generation when another snapshot is registered.

Embedding provenance includes the configured alias, immutable DMR artifact,
dimensions, input format and chunker implementation. Incompatible generations
cannot silently reuse embeddings or enter unpinned searches. Native inference
still runs through Docker Model Runner on macOS; Linux containers do not have
direct Metal access.

Moyai's inference key remains restricted to `local-qwen`. A separate internal
read-only MCP bridge uses a distinct credential scoped to approved vector stores
and search routes. The controller supplies repository/snapshot/generation scope;
the model cannot select a gateway URL, store, host path or credential. Source and
retrieved passages remain untrusted data.

Source preparation, database activation, gateway grants and catalog publication
span different systems. Failed gateway provisioning preserves the published
catalog and prior pinned generations. A complete new database generation may
already be visible to legacy unpinned search before that failure; this is not a
transaction across the database and filesystem.

## Failure handling and evidence

The controller persists phases, budgets, operation identities, source hashes,
plans, citations and native artifact IDs in its own SQLite ledger. Default limits
are one hour, 60 model requests, 120 tool calls and four repair validations, with
a separate baseline check. The fixed command has its own bounded timeout.

Fresh check receipts are authenticated using a per-operation controller key sent
only over SSH stdin. Results identify actual executed tests and stopped process
groups. An unknown outcome stays fenced; a missing response does not authorize
replaying a command. A subsequent authorized turn can reconcile recorded check
evidence or a provable write postimage. Ambiguous writes remain uncertain.

Completed files and workspace data survive a service restart. Running process
memory does not. The authenticated owner endpoint
`GET /api/sessions/{session_id}/repository-task` provides persisted diagnostics
without consuming execution budget or starting remote work.

```sh
make repository-smoke
make repository-smoke RESTART=1
make verify
```

Repository acceptance uses actual local-model turns in fresh conversations. It
checks scoped context, native planning/edit/check tool receipts, signed baseline
and repaired results, downloaded hashes, patch applicability, current workspace
bytes and unchanged host source. Private evidence and operator-side resource
samples are retained under `.local/verification/`; generated source and request
bodies must not be committed. `make verify` also runs the original checks and
plain Moyai acceptance, then always shuts down and checks owned container/model
inventories.

Grafana includes repository task phases, mean phase duration, context outcomes
and telemetry availability. Labels omit conversation IDs, source paths and
queries. Host-side Docker statistics and native inference RSS observations belong
to individual acceptance receipts; container memory includes cache and filesystem
pages and is not equivalent to macOS inference memory.

## Execution limits

Runtime Cargo uses prepared vendored dependencies with `--offline --locked` and
one build job. Connected image/model preparation is separate from offline
execution. A container egress test does not establish a disconnected-host
rehearsal. No Docker socket, host home, writable host checkout or context key is
mounted in the execution sandbox.

This is a trusted single-user local platform. Conversations share one sandbox
UID; separate directories, phase guards and authenticated receipts do not
establish hostile-code or tenant isolation. The current task does not repair the
fixture's unrelated JWT, SQL or deployment examples. Artifact delivery returns a
reviewable patch; applying it to the host checkout is an explicit operator step.
Capacity measurements cover sequential repository tasks. Concurrent Rust checks
share the sandbox's memory limit and have not been capacity-tested.

Compatibility sources: [Cargo offline and locked execution](https://doc.rust-lang.org/cargo/commands/cargo-test.html),
[Cargo vendoring](https://doc.rust-lang.org/cargo/commands/cargo-vendor.html),
[LiteLLM virtual key permissions](https://docs.litellm.ai/docs/proxy/virtual_keys),
[pinned LiteLLM route checks](https://github.com/BerriAI/litellm/blob/v1.109.1/litellm/proxy/auth/route_checks.py).
See the [approved design](superpowers/specs/2026-11-17-repository-workflow-design.md)
for the implementation boundaries and the
[2026-11-17 verified results](results/repository-workflow-2026-11-17.md) for
three actual local-model cycles, lifecycle checks, measurements and limits.
