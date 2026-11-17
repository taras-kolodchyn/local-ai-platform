# End to end local repository workflow

Status: approved by the user on 2026-11-17. Presentation changes are outside this work.

## Objective and acceptance scope

Connect the capabilities described in the technical-session agenda into one
supported local workflow: select a repository, retrieve cited context, analyze
and plan a task, edit an isolated copy, run trusted checks, repair a failed
attempt, and deliver a verified patch and report. Keep `make up` as the supported
platform entry point, the offline profile as default, and Moyai as the active
agent. All model requests continue to use LiteLLM aliases.

The first acceptance task uses the existing Rust service. Its documented retry
ceiling is 30,000 ms and its current helper permits 60,000 ms. The model must find
the relevant sources, write a native plan, implement the repair and a regression
test, and produce independently verified artifacts. This establishes one real
repository task; it does not establish arbitrary autonomous software development
or the separate full JWT/SQL/container/Helm repair exercise.

## Approaches

1. **Recommended: extend native Moyai capabilities.** Reuse its chat UI, native
   planning, Temporal execution, SSH workspace, progress and artifact delivery.
   Add a small repository capability, a scoped local MCP bridge, and a trusted
   task ledger/verifier. This connects the current platform without an upstream
   fork or a second agent orchestrator.
2. **External CLI orchestration.** A host runner could feed context and separate
   turns to Moyai. It would simplify some process supervision but introduce a
   second user workflow and duplicate task/plan state. Use a host CLI for
   operator preparation and acceptance automation, not as the primary agent UI.
3. **Restore separate legacy agents.** This would reintroduce profiles the
   current stack deliberately retired, without solving repository import,
   verification or provenance. It is outside the selected direction.

The user approved the recommended native Moyai direction on 2026-11-17.

## User and operator workflow

1. Start the platform with `make up`.
2. An operator command selects an explicit local `REPO`, creates a filtered
   immutable source snapshot, indexes it, and registers an opaque repository ID.
   A fixed verification profile is selected by the operator. Model-supplied
   host paths, shell command strings and credentials are never accepted.
3. In a fresh Moyai conversation, the user requests a task for that registered
   repository. A native tool attaches its current snapshot to this session and
   workspace authority. Subsequent indexing does not change that binding.
4. Moyai retrieves scoped source passages over MCP and records its analysis and
   native plan. Source text remains untrusted evidence, not tool policy.
5. After the plan gate, native file tools edit the conversation's project copy.
   Trusted validation executes the operator-selected checks, records failure or
   success, and permits bounded repair attempts.
6. Finalization requires current verification evidence. The controller produces
   a patch, test report and result manifest, delivered through native Files.
   Applying the patch to the original checkout is a separate explicit action.

The proposed initial operator surface is `make repository-add REPO=/path` and
an existing or new conversation invoking native repository tools. Existing
plain coding conversations and `make moyai-smoke` remain supported.

## Repository snapshots and provenance

Use host Git for repository root, selected subtree, branch/detached state, HEAD,
dirty status, and tracked/nonignored untracked paths. Support root checkouts,
subtrees and linked worktrees. Git failures in Git mode fail explicitly; plain
directories require an explicit unversioned mode.

Reuse the existing source guards, extending the source-only allowlist for
required build metadata such as Cargo.lock. Exclude credentials, generated
artifacts, symlinks, binary/oversized files and recognized secrets. Copy accepted
bytes to a staging directory before hashing and atomic publication. The manifest
records logical repository ID, subtree, descriptive Git metadata, per-file
hash/size, content snapshot digest and scanner version. No host paths or `.git`
directory enter the agent workspace.

Add immutable snapshot identity to indexed chunks and registered store scopes.
Keep the previous active generation until a new generation is completely
embedded and committed. Pin existing tasks to their original generation; legacy
unscoped searches use active generations to avoid duplicate historical hits.
Embedding reuse includes model artifact identity, dimensions, input format and
chunker version, rather than alias and source content alone. Citations identify
the actual snapshot, HEAD/dirty metadata, path and line range.

## MCP access and workspace boundary

Keep Moyai's inference credential restricted to `local-qwen`. Introduce an
internal, authenticated, read-only MCP context bridge with no published host
port. Its separate LiteLLM credential grants access only to registered source
stores; it cannot access global filesystem/Git/SQL tools or unscoped chunk IDs.

The controller binds each request to its server-owned session/snapshot scope.
The bridge validates scope before searching or reading a chunk. Model arguments
cannot override repository, branch or snapshot. Use fixed local origins, bounded
requests/results/timeouts, disabled environment proxies/redirects, explicit
read-only tool annotations, and metadata-only logs.

Copy the same immutable snapshot to a project subdirectory in the existing SSH
conversation workspace. Preserve the current workspace reference, do not
overwrite edits on follow-up/restart, and never mount host home, Docker socket,
host checkout or context credentials into the sandbox. Conversation directories
still share the single-user sandbox OS identity; this is not tenant isolation.

## Execution roles and trusted state

Analyst, planner, implementer and verifier are enforced phases of one agent.
Native planning remains visible and durable; its completion flags are not
validation evidence. A controller-owned ledger outside the writable sandbox
records the task contract, authority, pinned snapshot, phase, operation IDs,
attempts, deadlines, tree hashes, checks and terminal result.

| Phase | Allowed behavior | Transition evidence |
| --- | --- | --- |
| Preparing | Trusted snapshot attachment and baseline inspection | Workspace matches the selected snapshot |
| Analyzing and planning | Scoped MCP, source reads and native plan tools | Cited task context and a persisted plan with acceptance criteria |
| Implementing and repairing | Read/edit tools within the project copy | Attempt budget remains; protected verifier data unchanged |
| Verifying | Fixed trusted check command; source mutations denied | Actual exit, bounded output, final tree hash and oracle result |
| Completed | Read status and download results | Current verified tree and controller-generated artifacts match |
| Failed canceled timed out uncertain | Inspect persisted diagnostics | Terminal/reconciliation evidence; no blind replay |

Tool-definition filtering is backed by dispatch guards. Repository-task mode
does not expose generic shell, delegation, browser/desktop or external connected
tools; checks run through a controlled `run_checks` tool. Plain Moyai coding mode
retains its current native shell behavior. Enforce a task deadline, model/tool
call budget, validation timeout/output limit and repair-attempt budget independent
of upstream's unlimited-model-requests capability.

All I/O and ledger updates must respect Temporal durability: stable tool/activity
IDs, explicit idempotency and unknown-outcome reconciliation. Avoid nondeterministic
filesystem/network side effects inside workflow hooks. Do not replay an unknown
in-flight verification command after restart. Resume only from a reconciled
settled phase. A stop request is not terminal until tracked verification work is
confirmed stopped. Preserve existing native uncertain-effect protections.

## Trusted verification and artifacts

Use an operator-owned fixed argv, working directory, environment and timeout.
The agent cannot replace the verification command or protected oracle. A runner
tracks the process group and cleans it up on timeout/cancel. Record actual exits
and test identities/counts; an empty or skipped test run is not a pass.

For the initial Rust acceptance, build the pinned Rust 1.100.0 toolchain and
locked vendored crates into the prepared image while connected. Runtime checks
use offline Cargo with one build job and per-task writable build output. No host
Cargo cache or runtime downloads are required. Measure cold build time/memory
and adjust the sandbox budget only from observed requirements.

Independent acceptance must show that the baseline violates the retry policy,
the model adds a regression test and repairs the implementation, protected
checks are unchanged, the final checks pass, and host source hashes are unchanged.
Do not accept a printed test report, edited verification configuration or a
model's final success message as proof.

Any source mutation after a successful check invalidates that check. Finalization
compares the current tree with the verified tree, checks the allowed diff and
produces controller-authored patch/report/result hashes. Artifact downloads must
match those hashes. Native `save_artifact` remains the delivery mechanism.

## Observability and resources

Record end-to-end task and phase latency, queue wait, attempts, outcomes,
timeouts/cancellations and aggregate usage without request bodies. Prometheus
labels remain low-cardinality; task/session IDs, prompts and source paths belong
only in private receipts where needed, not metric labels.

An operator-side sampler can measure container CPU/memory and inference-process
RSS without mounting Docker control into the agent. Clearly separate measured
usage, host capacity, disk artifact size, RSS and Metal allocation. Report absent
measurements as unavailable. These task measurements complement, rather than
replace, the existing short endpoint benchmark.

## Verification matrix and completion criteria

- Unit and contract checks: manifest/path/secret guards, session binding,
  phase/tool authority, idempotency, stale verification, protected-test tampering,
  deadline/attempt limits and artifact hashes.
- Real PostgreSQL checks: multiple immutable snapshots, hybrid scope isolation,
  old-session pinning, embedding identity and failed-generation preservation.
- Runtime checks: real MCP retrieval, native plan, model edits, trusted failing
  baseline and passing repair, independently checked diff/artifacts, unchanged
  host source, fresh-process persistence, restart and canceled/timed-out work.
- Prepared-runtime offline checks: the complete task uses local models, source,
  vendored dependencies and tools; prove execution egress boundaries and record
  whether the actual host was disconnected. Do not disable the user's network
  silently or claim a disconnected rehearsal that did not happen.
- Run narrow checks first, then `make lint`, `make test`, `make smoke-test` and
  expanded real end-to-end acceptance through `make verify`. Preserve data and
  verify cleanup. Record exact versions/date and immutable source/model IDs.
- Repeat the bounded real-model acceptance on fresh conversations; retain
  unsuccessful runs as quality evidence instead of reporting only the best run.

Completion means the supported repository task passes through the single local
workflow with enforced boundaries and independent evidence. Known unsupported
task families and model limitations remain explicit. Neither the Google Slides
presentation nor its structure is changed by this implementation.

## Owning sources and compatibility baseline

Design inspection date: 2026-11-17. Existing baseline: LiteLLM 1.109.1, Docker
Model Runner 1.2.9, Moyai c375632aea66f6695b66158b5edf6ee4983dad72, Pydantic AI
2.55.0 / Harness 0.55.0. This design is not a claim that its new integrations have
already passed live verification.

- [Current platform operations](../../platform-operations.md)
- [Existing local Moyai boundary](../../moyai-local.md)
- [Pinned native agent capabilities](https://github.com/BerriAI/moyai/blob/c375632aea66f6695b66158b5edf6ee4983dad72/native/src/moyai/agent.py)
- [Pinned persistent stores](https://github.com/BerriAI/moyai/blob/c375632aea66f6695b66158b5edf6ee4983dad72/native/src/moyai/stores.py)
- [Pydantic capability hooks](https://github.com/pydantic/pydantic-ai/blob/v2.55.0/pydantic_ai_slim/pydantic_ai/capabilities/abstract.py)
- [Combined capabilities](https://github.com/pydantic/pydantic-ai/blob/v2.55.0/pydantic_ai_slim/pydantic_ai/capabilities/combined.py)
