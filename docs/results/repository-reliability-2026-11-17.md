# Repository workflow reliability — 2026-11-17

Final acceptance passed: one ordinary user task completed in one native Moyai
turn in 202.713 seconds, without an integration-file hint or operator follow-up.
The delivered patch downloaded through the native UI in 275 ms and matched the
controller and Files SHA-256.

Implementation: `8c2cb43` (feedback and unchanged-check handling) and
`eea4c51a94baf88b1c34fe2a62b8490cbea288a4` (native turn completion), followed by
`d421e8b426dc334973c45c4f3f5fc91e0a4be0fb` (native authority without model lifecycle reentry)
and `5baccef` (explicit Temporal import boundary).
The presentation and original Rust fixture were not edited.

## Fixed workflow behavior

The live demonstration exposed a mismatch: new integration regressions were
discovered only in `tests/*.rs`, while the model added inline unit tests. Its
failure message did not explain that distinction. Four retries of the same tree
then exhausted its validation budget.

The controller now explains the integration-test contract at attachment and in
native instructions. Failed checks return an actionable next step. An identical
retry can reuse the latest signed regression-requirement failure when every
command and the oracle passed. It performs no Cargo work and consumes no further
validation attempt. Launch errors, nonzero exits and unclassified partial
failures remain eligible for a fresh bounded retry. Every admitted write still
invalidates a prior pass. Authority, deadline, pending-state and terminal fences
remain enforced.

A subsequent ordinary-task trial found a separate completion problem: valid
Files had been delivered, but the model continued invoking unavailable tools and
repeating its summary until its model budget ended. Ledger completion and native
conversation completion are therefore checked separately.

After verified delivery, the controller now returns a short final result for that
same delivery turn. It checks completed state, current turn, passed tree evidence
and all three native Files hashes. The registered native Authority's authorization
and workspace checkpoint hook still runs; missing or revoked authority blocks
the final response. No further inference
call or repository model-budget charge is needed. Later user turns and child runs
keep the normal model path. Both behavior changes have Temporal replay markers.
The SDK counts the synthetic final response as a request. The final UI trial of
`eea4c51` exposed an `AssertionError`: re-entering the entire SDK before-chain from
the synthetic wrapper reached Temporal's model repair without its model scope.
The controller now invokes only native Authority's hook, with the current durable
run context and a separate replay marker. A real SDK streaming test reproduced
the old failure before the fix and verifies both authorized completion with a
checkpoint and denial after authority revocation.

The next UI trial exposed the native module's runtime import boundary. A fast
live Temporal probe using the same Pydantic AI plugin reproduced the old hook's
`RestrictedWorkflowAccessError` on an import-time environment lookup. With the
native module explicitly imported through Temporal's pass-through boundary, the
same probe authorized the scope and saved a checkpoint without inference. The
fixture keeps native objects on the worker side, matching the real runtime.
Current and legacy import paths have sandbox regression coverage and a further
Temporal marker preserves older histories.

## Download diagnosis and bounded observation

Before modifying the workflow, three authenticated downloads of a 1,476-byte
existing patch took 18–20 ms. The unchanged native UI button completed in 285 ms
with the same SHA-256. A later 2.2-KiB patch control download completed in 3,854 ms.
The file endpoint's content length and attachment filename were correct.

No historical network trace proves the exact cause of the earlier 19-minute
wait; it was not reproduced. The working endpoint was preserved. UI verification
now uses a semantic link click, bounded individual operations and a 10-second
overall deadline. A simulated saved-path API that ignored its own timeout
returned control after 10,003 ms with the expected error. This deadline bounds
automation waiting rather than canceling an OS dialog. The
[documented procedure](../moyai-ui-verification.md) contains the tested code.

## Verification

`make lint`, `make test` and `make smoke-test` passed. The complete local suite
passed 334 Python/real-database checks and two Rust tests. The rebuilt native
controller image passed 24 tests against its actual pinned SDK, including
completion, native authorization denial, future-turn/child-run fences and
Temporal replay boundaries. No required Docker or model check was skipped.

Versions verified from the actual image: Pydantic AI `2.55.0`, Harness `0.55.0`
and Temporal SDK `1.34.0`. Runtime controller source hashes match the committed
files. The remaining platform/model pins are recorded in the
[earlier runtime identity](repository-workflow-2026-11-17.md).

The first ordinary no-hint conversation after adding deterministic completion
passed independent acceptance: one user task, one native turn, one repair
validation, seven project tests and four protected tests. Its artifacts, signed
receipts, independently observed source tree and applied patch matched. It did
not receive an integration-file hint or operator follow-up. Private evidence:
`.local/verification/feedback-ui-completion-20261117/`.

Final acceptance used the `5baccef` controller, with native authorization,
checkpoint preservation and the explicit import boundary. Runtime source hashes
matched the committed controller files. Both native turn status and repository
phase were `completed`.

| Observed result | Final UI acceptance |
| --- | --- |
| User messages / native turns | 1 / 1; original prompt unchanged |
| Operator follow-ups / chat recovery | 0 / 0 |
| Native elapsed time | 202.713 s; UI displayed 3m 22s |
| Baseline / repaired check | Failed in 29.510 s / passed in 40.266 s |
| Repair validations | 1 |
| Attached-task model / tool calls | 20 / 20 |
| SDK requests / tool calls | 23 / 22 |
| Project / protected oracle tests | 7 / 4 |
| Added integration tests | 5 included in project tests; 2 detecting, 3 supporting |
| Signed receipt authentication | Both receipts independently authenticated |
| Patch reproduction | Applied patch matched the independently observed verified tree |
| Original host source / protected inputs | Unchanged |
| Native Files | Patch, test report and result; all hashes matched |
| UI patch download | 2,216 bytes in 275 ms; 10-second observation deadline |
| Authenticated HTTP downloads | Patch 8.85 ms; 571,412-byte report 8.37 ms; result 6.49 ms |

Patch SHA-256:
`fb33611b934c3d53487ffb2f4d0eb8979f2d6472f42bbe748527797d23f18b25`.
Private evidence and the UI screenshot are retained under
`.local/verification/feedback-ui-accepted-20261117/`. The UI-downloaded bytes were
also compared with the independently downloaded native artifact. SDK usage and
attached-task ledger counters have different scopes; the SDK also counts the
synthetic completion request. These are observations from one warm local run,
not a latency or resource-use benchmark.

The generated patch retains a minor review issue: a test comment calls attempt
14 the first capped attempt, although exponential growth reaches the ceiling at
attempt 9. The executable assertions and independent checks passed; the
delivered artifact was kept byte-for-byte intact. Normal code review remains
part of assessing generated changes.

Earlier unsuccessful UI trials remain available for diagnosis, including
`.local/verification/ui-demo-20261117-failed-diagnosis.json` and
`.local/verification/feedback-ui-20261117/turn-outcome.json` and the failed native
completion trials in `.local/verification/feedback-ui-final-20261117/` and
`.local/verification/feedback-ui-native-final-20261117/`. They are not counted
as successful native conversations merely because a patch existed.

This verifies the trusted single-user `rust-retry` case, with a reviewable patch
and no host source writeback. Other project/language profiles and a
disconnected-host rehearsal retain the previously documented limits.
