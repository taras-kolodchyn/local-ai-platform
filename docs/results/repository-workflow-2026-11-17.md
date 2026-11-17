# Local repository workflow — 2026-11-17

Implementation: `7e23ebb5831e9d871da9f1094244ac707ddc1a95`.
The supported case is the bundled Rust retry repair through native Moyai. The
model edits an isolated snapshot; the operator receives a patch through Files.
The presentation and the original Rust fixture were not changed.

## Observed end-to-end result

The first accepted fresh conversation's host-observed acceptance cycle took
**350.431 seconds**, including independent artifact checks. Native turn creation
to completion took 349.048 seconds; acceptance followed creation by 0.587 seconds.
It used
29 controller-counted model requests and 28 admitted tools, with three repair validations after the
separate baseline. Check outcomes were baseline failure, repair failure, pass,
pass. The final result ran seven project tests and all four protected oracle
tests. Of five added tests, three detected the compiled 60,000-ms counterexample
and two supported behavior that both implementations preserve.

The resulting patch changes only `src/lib.rs` and adds
`tests/retry_regression.rs`. It adds the bounded shared helper and makes the
legacy function call it. Independent acceptance authenticated every receipt,
checked the actual workspace, downloaded all three native Files objects, applied
the patch to a temporary source copy and compared its complete tree. Original
host file hashes remained unchanged. Controller state, workspace bytes and all
three downloads remained identical after restarting Moyai, SSH sandbox, Temporal
and the HTTP bridge.

The native artifacts were 2,378 bytes for the patch, 1,144,805 bytes for the test
report, and 2,088 bytes for the result manifest. These artifacts and request
history stay private; they are not checked into this repository.

Private first-run evidence: `.local/verification/20261117T154601Z-q7jibwjn/`.
Its measured checks took 29.794394, 30.053132, 40.053845 and 42.121982 seconds.
The final repeated-run measurements and full lifecycle receipt are recorded below.

## Runtime identity

Observed on Apple M5 Max, **128 GiB host capacity**. Model artifacts were already
prepared locally; model residency was not forced to a standardized cold or warm
state. Cargo targets were fresh for each operation. OS filesystem caches were
not reset. These are observed workflow timings, not controlled inference benchmarks.

| Component | Exact version or pin |
| --- | --- |
| Docker Model Runner client/server | `1.2.9`, Darwin arm64, Docker Desktop engine |
| Docker Compose | `5.7.0` |
| LiteLLM | `1.109.1` |
| Moyai upstream | `c375632aea66f6695b66158b5edf6ee4983dad72` |
| Controller Python | `3.13.16` |
| Pydantic AI / Harness | `2.55.0` / `0.55.0` |
| OpenAI SDK / Temporal SDK | `3.29.0` / `1.34.0` |
| Controller Starlette | `1.8.0` |
| Context Starlette | `1.10.0` |
| Temporal server CLI | `1.10.1`, OCI digest pinned in Compose |
| Prepared Rust / sandbox Python | `1.100.0` / `3.14.10` |
| PostgreSQL / pgvector / MCP | `17.12 (Debian 17.12-1.pgdg12+1)` / `0.8.7` / `2.3.0` |

Private runtime identities are in
`.local/verification/repository-runtime-versions.json`. Package and binary
observations are in the final repository evidence directory's
`controller-versions.json` and `software-version-observations.json`; those were
read from the tested images with networking disabled after shutdown. Compose,
the sandbox Dockerfile and the [Moyai integration notes](../moyai-local.md)
retain the image/toolchain/upstream pins. The pgvector extension version was
also queried from the running database before shutdown.

Inference remains behind `local-qwen` and `local-embeddings`. Configured coding
context is 65,536; the effective runtime context and Metal allocation were not
independently measured. Embeddings have 1,024 dimensions.

| Alias | Prepared reference | Immutable artifact |
| --- | --- | --- |
| `local-qwen` | `ai/qwen3-coder:30B-A3B-UD-Q4_K_XL` | `sha256:8fdf3f0840add2c226d01b25810ab37c2da82e48035bcc200fc2cdb654007b8e` |
| `local-embeddings` | `ai/qwen3-embedding:0.6B-F16` | `sha256:3e86173a1f6e9ab8e3c4a90f1ec00d80d4f07ecc532c42ff7b7f7d1419a828ab` |

The inference key's real vector-search/MCP requests returned 403. The separate
context key returned 200 for an approved source store and 403 for an unregistered
store, inference and management. Context MCP supplied the policy citation from
`docs/architecture-rules.md`, including line 7. A pre-final live Prometheus
spot-check returned task telemetry availability 1 and valid phase/duration
series. That raw query response was not archived in the final lifecycle receipt.

## Failure behavior and limits

Actual platform probes passed foreign-authority denial, observational pre-dispatch
status, lost SSH reply reconciliation through a fresh adapter, authenticated
ordinary failure followed by repair admission, Cargo timeout and cancellation.
Cleanup requires confirmed stopped work; private probe ledgers remain available
for failure reconciliation.

Earlier development runs exposed native plan refusals reported as completed,
overstrict treatment of supporting tests, lost late Cargo compiler diagnostics,
and ambiguous legacy compatibility wording. Their unsuccessful receipts were
retained. Final criteria keep both public entry points on the corrected policy
and require at least one genuinely detecting regression; supporting-only suites
remain rejected. An actual prepared-image Rust error after a 65,536-byte metadata
prefix remained visible as E0425 with file/line context after the diagnostic fix.

The sandbox used a 1 GiB / 2 CPU envelope and one Cargo build job. Dedicated
sequential checks reached the cgroup memory cap, which includes filesystem pages
and cache. This establishes no concurrency headroom. Host inference RSS, Docker
working-set samples, artifact disk size and Metal allocation are distinct; no
per-model Metal measurement or disconnected-host rehearsal is claimed. Runtime
execution egress was tested while the host remained online.

This verifies the trusted single-user `rust-retry` profile. It does not establish
hostile-code/tenant isolation, generic Rust project support, or the separate
JWT/SQL/container/Helm repair exercise.

## Final lifecycle verification

The expanded **`make verify` passed** on the implementation commit above. Its
private receipt directory is `.local/verification/20261117T155312Z-rlz8d4p5/`.
All eight phases returned zero: lint, test, up, status, smoke-test, Moyai acceptance
with restart, repository acceptance with restart, and down. The test phase passed
325 Python/real-database checks and two Rust tests. The native controller image
had separately passed its 13 pinned-SDK integration tests during the build.
No required Docker or model checks were skipped.

The final acceptance created two additional fresh local-model conversations.
Both failed the original baseline, produced a repair, passed independent checks
and delivered all three verified Files objects. Their private evidence is
`.local/verification/20261117T155641Z-_nxa8wa8/`.

| Observation | First accepted run | Final run 1 | Final run 2 |
| --- | ---: | ---: | ---: |
| Host acceptance cycle, seconds | 350.431 | 262.407 | 227.938 |
| Native created-to-completed, seconds | 349.048 | 260.529 | 226.305 |
| Native created-to-accepted, seconds | 0.587 | 0.543 | 0.542 |
| Ledger model requests after repository attachment | 29 | 27 | 23 |
| SDK requests for the entire native turn | 31 | 29 | 25 |
| Ledger admitted tools | 28 | 26 | 22 |
| Repair validations after baseline | 3 | 1 | 1 |
| Final project / protected oracle tests | 7 / 4 | 7 / 4 | 5 / 4 |
| Added detecting / supporting tests | 3 / 2 | 2 / 3 | 1 / 2 |

These are three accepted runs of the final implementation, not an overall
development success rate or latency distribution. Earlier unsuccessful runs
are described above. SDK and ledger counters have different scopes; queue
figures measure native acceptance delay, not an inference scheduler queue.
Each final patch modifies the retry helper/legacy delegation and adds only its
regression test file. Formatting was not an acceptance gate.

Between the two final tasks, Moyai, sandbox, Temporal and bridge restarted.
Persisted state, workspace bytes and downloaded artifacts remained identical.
Independent checks confirmed patch applicability and unchanged original source.
The eight phase durations sum to 762.703 seconds, including both model tasks,
the plain Moyai task, restarts and shutdown. Final inventory found no platform
containers and no configured resident models; downloaded artifacts were retained.
The platform was left stopped. Start it through `make up`.

## Resource observations

The final sampler recorded 71 observations across 497.571 seconds, approximately
7.1 seconds apart, including the inter-task restart. It observed two to three
native `llama-server` processes. Their summed sampled RSS ranged from
40,961,015,808 to 46,646,476,800 bytes (38.148–43.443 GiB). This is executable-name
inventory without per-model attribution; shared pages may be counted more than
once. It measures neither Metal allocation nor total platform memory.

| Container | Highest sampled Docker working set | Highest sampled CPU |
| --- | ---: | ---: |
| Moyai controller | 339.6 MiB | 54.98% |
| SSH/Rust sandbox | 298.4 MiB | 102.18% |
| Temporal | 167.5 MiB | 13.06% |
| HTTP bridge | 19.98 MiB | 1.07% |
| Repository context | 99.91 MiB | 13.39% |

These maxima need not occur together. Docker working set differs from cgroup
total memory and process RSS; these samples exclude other core containers.
They are sampled observations, not true peaks. The earlier first accepted run
had different residency (one to two native processes and 5.295–33.341 GiB summed
RSS), so it must not be treated as the same memory baseline. Raw observations
remain in each private evidence directory's `resources.jsonl`.

Private verifier development evidence for the independent cgroup-cap probes is
recorded in `.superpowers/sdd/2026-11-17-repository-workflow/task-3-report.md`.
Gateway access and execution guards are retained in the final directory's
`gateway-access.json` and `runtime-guards.json`; original Moyai egress/restart
acceptance is recorded in the full lifecycle's `moyai-smoke.log`.

## Coverage of the technical session

| Agenda | Demonstrable platform behavior |
| --- | --- |
| Architecture and trade-offs | Native macOS inference, containerized services, LiteLLM aliases, localhost access and measured sequential limits |
| Repository context | Filtered immutable source registration, indexing, scoped MCP search/read and source-line policy citations |
| Orchestration and tools | Persisted phases, bounded tool/model budgets, authority checks, independent trusted checks and restart reconciliation |
| Task to verified result | Fresh native Moyai conversation, failed baseline, model-generated patch/tests, protected oracle, compiled counterexample and hashed Files delivery |
| Results and discussion | Three accepted cycles, retained unsuccessful development runs, timing/resource samples and explicit scope limits |

This supplies a verified Rust demonstration for the agenda. A disconnected-host
rehearsal and broader language/project verification profiles remain separate work.

Compatibility references: [Cargo test/offline/locked](https://doc.rust-lang.org/cargo/commands/cargo-test.html),
[Cargo JSON compiler messages](https://doc.rust-lang.org/cargo/reference/external-tools.html#json-messages),
[LiteLLM route checks at the deployed version](https://github.com/BerriAI/litellm/blob/v1.109.1/litellm/proxy/auth/route_checks.py),
and [native Moyai integration notes](../moyai-local.md).
