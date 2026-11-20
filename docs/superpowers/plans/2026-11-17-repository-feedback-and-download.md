# Repository feedback and bounded UI download verification

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Complete the Rust retry case from one ordinary task without operator hints, and bound UI download verification so it cannot silently wait for minutes.

**Architecture:** Keep the existing trusted verifier and phase limits. Explain its integration-test contract in controller-owned guidance, return actionable check feedback, and reuse the latest authenticated settled check only for the identical current tree in an active task. Browser download observation uses an overall deadline in addition to individual API timeouts.

**Tech stack:** Python, SQLite, existing native Moyai, CUA browser API.

**Spec:** Existing approved repository workflow design and the user's 2026-11-17 request to fix both live-demo failures.

## Constraints

- Preserve phase/authority/deadline checks, signed receipts, protected oracle and detecting regression requirement.
- Do not reopen failed/cancelled/timed-out tasks or increase budgets.
- Keep immutable source, offline execution, aliases and `make up`.
- Leave the presentation and unrelated working-tree edits unchanged.
- Do not attribute a historical browser delay to a server defect without evidence.

## Tasks

- [x] Add failing tests for useful missing-regression feedback and unchanged-tree reuse, including changed, pending, terminal, expired and foreign-authority cases.
- [x] Explain `tests/*.rs` discovery in native instructions and attached profile metadata. Return an actionable next step after failed or passed checks.
- [x] Reuse only the latest settled repair receipt after checking the actual tree; preserve receipt bytes and identify reuse explicitly. Do not consume another validation attempt or run Cargo on an unchanged retry.
- [x] Run focused tests, image SDK tests and required `make lint`, `make test`, `make smoke-test`.
- [x] Submit an ordinary Rust task through Moyai in one new conversation, with no integration-file hint or follow-up. Independently validate its delivered patch and source preservation.
- [x] Retest the actual download button with per-operation and total deadlines, verify bytes against the controller hash, and record timing and the bounded procedure.

Final evidence: [reliability results](https://github.com/taras-kolodchyn/local-ai-platform/blob/5029427f3b0d228b3eafe52f41f3d6337575e4a2/docs/results/repository-reliability-2026-11-17.md).
One native turn completed in 202.713 s; UI patch download completed in 275 ms
with matching controller and Files hashes. Completion fixes also cover native
authorization/checkpoint hooks, durable model-scope separation and Temporal's
runtime import boundary.

## Download diagnosis before changes

Three authenticated server downloads of the existing 1,476-byte patch took 18–20 ms; content length and attachment filename were correct. The unchanged UI button then downloaded the same bytes in 285 ms. The previous 19-minute browser wait was not reproduced. Use a 10-second overall browser-observation deadline so individual waits or permission handling cannot block the agent indefinitely; do not patch the working native file endpoint speculatively.
