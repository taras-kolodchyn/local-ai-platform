# From a GitHub issue to a Moyai draft PR

The first execution profile covers the retry policy in `examples/rust-service` in `taras-kolodchyn/local-ai-platform`. Moyai runs the model, retrieval, edits and trusted Rust checks locally. A separate foreground process on the Mac reads the issue and publishes the verified result to GitHub.

## Run as a private GitHub App

1. Start the platform with `make up`. On the Mac run `make github-app-setup` and open the printed loopback URL in a browser signed in as the repository owner.
2. Confirm **Create GitHub App**, follow **Install GitHub App**, choose **Only select repositories**, and select **taras-kolodchyn/local-ai-platform**. The manifest requests Issues read, Contents write and Pull requests write. If GitHub first asks you to sign in, complete sign-in and reopen the local form if needed. The setup callback saves the signing key on the Mac under `.local/github-issues/app/` with mode 0600. The browser and Moyai receive no private key.
3. Start `make github-app-issues ISSUE=6`. This grants the publisher one issue under the App identity. For multiple eligible issues, omit `ISSUE` and apply the `moyai:rust-retry` label to the tickets. Wait for the ready message.
4. Paste the full issue URL into a new private Moyai chat. For the same issue, subsequent attempts use this chat. The host checks the actual installation selection, signs a short-lived RS256 JWT, exchanges it for a repository-scoped token and refreshes it near expiry. It calls GitHub REST directly. The model and sandbox receive neither GitHub credentials nor a GitHub CLI.
5. The draft targets `main` from `codex/app-<id>/issue-<number>`. Each App has its own local claims and branch namespace. Before returning the PR, the publisher checks its head and GitHub's recorded author type and login against the registered App bot. Stop the foreground publisher with Ctrl+C. Run `make up` to restore the offline controller.

The App transport uses GitHub REST API version `2026-03-10`. The registration and authentication flow was checked against GitHub documentation on 2026-11-18. [Manifest registration](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest) · [App JWTs](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app) · [Installation access tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)

### Add coverage to a passing Rust example

Run `make github-app-issues ISSUE=N PROFILE=rust-retry-tests` and submit the issue URL in an empty private chat. After intake Qwen sees the issue title and body and chooses any number of distinct guides from the local LiteLLM catalog pinned to the turn, including none. Moyai loads each selected guide through a native tool call before continuing repository analysis.

The coverage profile requires a passing original implementation and preserves every original file. The agent may add integration targets under `examples/rust-service/tests/`. Verification runs the project and protected tests. Each new test must pass on the candidate and complete on the compiled 60,000 ms counterexample. At least one must detect the counterexample. The App opens a draft at the pinned base. The operator reviews the assertions and CI before merging and checking issue closure and CI on `main`.

### Documentation review before publishing

After Rust verification, Moyai sends the candidate diff and numbered implementation and policy lines to a separate local `local-qwen` request. The reviewer has one structured reporting tool. It records a source citation and a judgment for each changed passage in comments, Rustdoc and Markdown. The controller also checks affirmative removal promises and explicit retry-ceiling claims against the pinned policy. Deprecation alone only establishes the current API status. A future removal requires an explicit policy.

When the reviewer requests changes, the editing agent receives the findings in the same chat. It must change the candidate, rerun the protected checks and obtain approval for the revised tree. The test report contains the review history. The host publisher checks the approval against the pinned snapshot, final Rust receipt, verified tree and downloaded patch hash before creating a draft. The report distinguishes model findings from policy checks and includes the source lines for inspection.

### Single-message App rehearsal, 2026-11-18

The operator pasted the URL of issue #10 into an empty private Moyai web chat. This was the only user message. In 2m 32s, one turn retrieved the retry policy, reproduced the 60,000 ms baseline, edited the Rust implementation and tests, delivered three Files links and returned the draft PR. GitHub identifies `moyai-local-348144ed[bot]` as the author of both the commit and the draft. [Issue #10](https://github.com/taras-kolodchyn/local-ai-platform/issues/10) · [Draft PR #11](https://github.com/taras-kolodchyn/local-ai-platform/pull/11) · [Published commit](https://github.com/taras-kolodchyn/local-ai-platform/commit/1f9cc341f2c9e967e5d63416f0b127884bd69074)

The first repair validation passed seven project tests and four protected tests. Two of the five new tests also failed against the old 60,000 ms behavior. An independent local observer authenticated both check receipts and reapplied the 1,757-byte patch to the pinned source. Its result matched the verified workspace. One authenticated localhost request fetched the completed patch in 7.46 ms. Its SHA-256 was `ba3254960ebdbc9a55c2c612c5fac30b16433d7667fc070baa7164b78de543bc`. GitHub Actions passed on the published commit. [CI for PR #11](https://github.com/taras-kolodchyn/local-ai-platform/actions/runs/44290097035)

### Earlier App run with a resumed chat, 2026-11-18

The private installation selected one repository. Moyai received [issue #6](https://github.com/taras-kolodchyn/local-ai-platform/issues/6) in a private web chat. The first turn exhausted its budget during planning. The controller now uses the Rust profile's native plan when the saved plan lacks the required acceptance criterion or marks every step complete before editing. Resubmitting the same URL can reopen an exhausted analysis once, within the original deadline, after the host confirms the claim and the controller verifies the pristine source tree.

After a controller update, the successful turn in the same chat took 3m 55s. The attached baseline recorded 60,000 ms against a 30,000 ms limit. The repaired tree passed five project tests, including three added regressions, and four protected tests on its first repair validation. Both signed check receipts and the downloaded patch were independently verified. GitHub recorded `moyai-local-348144ed[bot]` as the author of the commit and the open draft. [Draft PR #8](https://github.com/taras-kolodchyn/local-ai-platform/pull/8) · [CI on commit 20eb589](https://github.com/taras-kolodchyn/local-ai-platform/actions/runs/44278872210)

## Run with the operator account

1. Start the platform with `make up`.
2. For a single approved Rust retry issue, run `make github-issues ISSUE=6`, substituting the issue number. This explicitly authorizes the signed-in host `gh` account for that issue. Wait for “GitHub issue publisher ready”. First startup indexes the example at the current GitHub `main` commit. Enabling the extra mount can recreate the Moyai controller, so let active turns settle first.
3. Open a private chat at [local Moyai](http://127.0.0.1:8787). Paste the full GitHub issue URL as the entire message. The session owner must be the local operator. The initial chat message supplies the issue identity. The issue body and source files supply untrusted task data.
4. Follow the source search, baseline failure, plan, edits and validation in the chat. On success the final message contains the three native Files links and the GitHub draft PR. The PR targets `main` from `codex/issue-N`.
5. Press Ctrl+C in the publisher terminal when done. `make up` uses the offline Compose definition and restores the ordinary controller mount configuration. The default profile has no issue exchange, GitHub credentials or write-capable external MCP.

For a dedicated GitHub App, install it on this repository with Issues read, Contents write and Pull requests write. Supply an installation token in `.local/github-issues/installation-token` on the Mac with mode 0600. Run `make github-issues` without an issue number and select the verification profile by labeling the issue `moyai:rust-retry`. The operator still initiates each task with its URL. The publisher reads the token file for each request, so an operator or token provider can replace it at expiry. The connected read-only MCP setup has separate credentials. [GitHub installation tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)

## Publication and recovery

The host confirms the original URL against the authenticated owner's active web turn. It pins the issue, conversation, indexed snapshot and GitHub base commit in private host state. The Linux sandbox has access only to the local workspace. The controller exchanges small requests and responses with the host through a dedicated directory.

After local verification the publisher downloads the patch, result and test report through authenticated Moyai Files. It checks their hashes and the final trusted check receipt, compares the snapshot with Git blobs at the pinned base, applies the patch to a temporary Git index, and compares the resulting Rust files against the verified tree digest. GitHub must return the same full repository tree SHA as the local index. The publisher persists the commit SHA before creating the issue branch. It then reads back the draft and checks its repository, base branch, head SHA and state. [Git references API](https://docs.github.com/en/rest/git/refs#create-a-reference) · [Pull requests API](https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request)

If publication loses its response, resend the same URL in the original chat with the publisher running. The publisher checks the active owner turn before each GitHub write and rechecks the issue and base before creating a branch or draft. Recovery reads an existing PR before creating a branch, including closed and retargeted PRs. A deleted branch belonging to such a PR requires operator review. Publication succeeds when GitHub reports an open draft at the pinned base SHA and verified head SHA. The patch and reports remain available after successful local verification. GitHub review and merge are separate steps. Other repository tasks need their own execution and verification profile.

## Live verification, 2026-11-17

Using host GitHub CLI 2.104.0 and REST API version `2026-03-10`, the operator submitted [issue #6](https://github.com/taras-kolodchyn/local-ai-platform/issues/6) through a private Moyai chat. During development, two intake attempts failed before attachment. The controller now dispatches a standalone URL directly to its native issue tool, and the publisher checks ownership against the nested session object in the installed Moyai API.

The successful turn took 4m 54s. It reproduced the 60-second baseline, passed 7 project tests and 4 protected tests on the first repair validation, and delivered a patch with SHA-256 `01f2b8a7e57c85837ea7c3ae7d1b00cfd3611ca03b0a61f60903056916dcec3d`. Independent observation authenticated both verification receipts. GitHub recorded commit `4d4f4fe2b7c71e5c60282d6d026b9a21500d1eeb` on the issue branch. [Draft PR #7](https://github.com/taras-kolodchyn/local-ai-platform/pull/7) · [Successful CI for the published commit](https://github.com/taras-kolodchyn/local-ai-platform/actions/runs/44162713166)

After restarting the publisher, submitting the URL in the same chat returned the same PR and Files in 5 seconds. The persisted task, tool counters and validation receipts were identical. This live run used the single-issue host login. The dedicated installation-token path requires separate verification with a repository installation.
