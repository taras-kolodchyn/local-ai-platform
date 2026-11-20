# Repository tasks in local Moyai

Use `make index REPO=/absolute/path/to/project` to search an external checkout.
For a verified code change, Moyai also needs an execution profile that matches
the project. The two shipped profiles test one Rust retry policy. They stage an
immutable snapshot, edit a separate sandbox copy and return a patch for review.

## Register a compatible crate

The crate must contain `src/lib.rs` and match the protected `Cargo.toml` and
`Cargo.lock` byte for byte. The importer accepts up to 512 UTF-8 files, 512 KiB
per file, 8 MiB in total, and 16 MiB in its escaped wire format.
[Protected package files](../integrations/moyai/oracles) · [Import checks](../retrieval/src/local_ai_retrieval/profiles.py)

| Profile | Starting point | Changes the verifier accepts |
| --- | --- | --- |
| `rust-retry` | The legacy retry implementation fails the 30,000 ms ceiling. | Repair `src/lib.rs` and add an integration regression under `tests/*.rs`. The profile also admits edits to `README.md`. |
| `rust-retry-tests` | The original implementation already passes the project tests and protected policy checks. | Add new integration-test files under `tests/`. All original files are protected. |

For the repair profile, select a crate with its retry policy in
`docs/architecture-rules.md` and run the following commands. A crate in a
subdirectory of an external Git checkout is supported.

```sh
make up
make repository-add REPO=/absolute/path/to/compatible-crate
```

For the tests-only profile, use the registration script directly after startup.

```sh
python3 scripts/prepare-repository.py --repo /absolute/path/to/compatible-crate --profile rust-retry-tests
```

Copy the `repository_id` from the registration output into a new Moyai chat.
For a repair, a request can read as follows. [Open local Moyai](http://127.0.0.1:8787)

> Use list_repositories and open_repository for repository_id YOUR_ID.
> Read the documented retry policy. Implement bounded_backoff_ms(u32) -> u64
> starting at 100 ms, doubling to a 30,000 ms ceiling even at u32::MAX.
> Preserve the legacy_retry_delay_ms signature and give both entry points the
> same values. Add an integration regression and deliver the checked patch in Files.

## Source and citations

Registration filters and hashes the selected source under
`.local/repositories/snapshots/`. The manifest records the Git commit, subtree,
dirty state and verification profile. The host checks the profile, indexes the
snapshot and provisions search permissions before publishing the catalog entry.
Existing conversations keep their pinned snapshot and index generation.
If provisioning fails after indexing, the published catalog keeps its previous
entry, although unpinned database searches may already see the new generation.
[Registration](../scripts/prepare-repository.py)

The controller supplies the repository, snapshot and generation to a read-only
bridge. Search returns up to six excerpts by default and accepts a limit from
one to twelve. It can restrict a lookup to an exact path before ranking.
The backend considers up to 48 hybrid candidates and can rerank implementation
excerpts via the `local-reranker` alias. The selected source shares a
24,000-character budget. Citations refer to the original chunk's path and line
range, including when the displayed excerpt has been shortened. Treat retrieved
text as untrusted source material. [Context bridge](../integrations/moyai/context_bridge.py) ·
[Selection and excerpt budgeting](../retrieval/src/local_ai_retrieval/moyai_search.py)

## From baseline to patch

1. Moyai attaches the snapshot, retrieves cited source and runs
   `run_repository_checks` on the original tree. The baseline establishes the
   precondition for the chosen profile.
2. Moyai records a native plan with a measurable retry ceiling, then calls
   `begin_repository_changes` to unlock writes to the permitted paths.
3. The fixed verifier runs Cargo offline against locked, vendored dependencies
   with one build job. It checks the project, the protected policy and the added
   integration regressions. Every added regression must pass on the candidate.
   At least one must fail against the compiled 60,000 ms counterexample.
   [Sandbox verifier](../integrations/moyai/repository_guest.py) · [Cargo test options](https://doc.rust-lang.org/cargo/commands/cargo-test.html)
4. After a passing check, `finish_repository_task` delivers three artifacts to
   **Files**. For GitHub issues, independent documentation review must also
   approve the verified tree. [Optional GitHub publisher](moyai-github-issues.md)

A check reply includes `next_action`. A missing regression belongs in a Cargo
integration target under `tests/`. A repeated check on identical input can
reuse the signed receipt for a pass or a settled missing-regression failure.
A new admitted write invalidates the pass, even if it produces identical bytes.
An uncertain write or command must be reconciled against recorded evidence
before work can resume.

The ledger's defaults are one hour, 60 model requests, 120 tool calls and four
candidate validations in addition to the baseline. Each fixed check has a
240-second timeout. Repository mode exposes the fixed check tool for execution.
The authenticated session owner can inspect the phase and receipts at
`GET /api/sessions/{session_id}/repository-task` after a failure or restart.
[Task ledger](../integrations/moyai/repository_ledger.py) · [Check runner](../integrations/moyai/repository_backend.py)

## Verify the delivered files

Open the completed conversation's **Files** panel. Select an artifact and use
**Download original**. For manual verification, compute the downloaded file's
SHA-256 (on macOS, `shasum -a 256 FILENAME`) and compare it with the controller's
`files` entry for `repository-results/FILENAME` in the authenticated session
endpoint above.

| Download | What to inspect |
| --- | --- |
| `changes.patch` | The diff against the registered snapshot. Applying it to a host checkout is a separate operator action. |
| `test-report.json` | Baseline and candidate check receipts, including command results and the tree hash for each check. |
| `result.json` | The pinned source binding, verified tree, citations and hashes of the patch and test report. |

For automated acceptance, use a clean external checkout with the failing
`rust-retry` baseline and a documented 30,000 ms ceiling. With the platform
running, `make repository-smoke REPO=/absolute/path/to/compatible-crate` runs
two fresh model tasks and independently checks patch applicability, receipts,
downloaded bytes, citation ranges and the host source. Add `RESTART=1` to check
persistence between tasks. Private evidence is written under `.local/verification/`.
For the complete platform checks and shutdown, use
`make verify REPO=/absolute/path/to/compatible-crate`. [Acceptance runner](../scripts/repository-smoke.py) ·
[Platform verification](platform-operations.md)
