# From a GitHub issue to a Moyai draft PR

The first execution profile covers the retry policy in `examples/rust-service` in `taras-kolodchyn/local-ai-platform`. Moyai runs the model, retrieval, edits and trusted Rust checks locally. A separate foreground process on the Mac reads the issue and publishes the verified result to GitHub.

## Run it

1. Start the platform with `make up`.
2. For a single approved Rust retry issue, run `make github-issues ISSUE=6`, substituting the issue number. This explicitly authorizes the signed-in host `gh` account for that issue. Wait for “GitHub issue publisher ready”. First startup indexes the example at the current GitHub `main` commit. Enabling the extra mount can recreate the Moyai controller, so let active turns settle first.
3. Open a private chat at [local Moyai](http://127.0.0.1:8787). Paste the full GitHub issue URL as the entire message. The session owner must be the local operator. The initial chat message supplies the issue identity. The issue body and source files supply untrusted task data.
4. Follow the source search, baseline failure, plan, edits and validation in the chat. On success the final message contains the three native Files links and the GitHub draft PR. The PR targets `main` from `codex/issue-N`.
5. Press Ctrl+C in the publisher terminal when done. `make up` uses the offline Compose definition and restores the ordinary controller mount configuration. The default profile has no issue exchange, GitHub credentials or write-capable external MCP.

For a dedicated GitHub App, install it on this repository with Issues read, Contents write and Pull requests write. Supply an installation token in `.local/github-issues/installation-token` on the Mac with mode 0600. Run `make github-issues` without an issue number and select the verification profile by labeling the issue `moyai:rust-retry`. The operator still initiates each task with its URL. The publisher reads the token file for each request, so an operator or token provider can replace it at expiry. The connected read-only MCP setup has separate credentials. [GitHub installation tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)

## Publication and recovery

The host confirms the original URL against the authenticated owner's active web turn. It pins the issue, conversation, indexed snapshot and GitHub base commit in private host state. The Linux sandbox has access only to the local workspace. The controller exchanges small requests and responses with the host through a dedicated directory.

After local verification the publisher downloads the patch, result and test report through authenticated Moyai Files. It checks their hashes and the final trusted check receipt, compares the snapshot with Git blobs at the pinned base, applies the patch to a temporary Git index, and compares the resulting Rust files against the verified tree digest. GitHub must return the same full repository tree SHA as the local index. The publisher persists the commit SHA before creating the issue branch. It then reads back the draft and checks its repository, base branch, head SHA and state. [Git references API](https://docs.github.com/en/rest/git/refs#create-a-reference) · [Pull requests API](https://docs.github.com/en/rest/pulls/pulls#create-a-pull-request)

If publication loses its response, resend the same URL in the original chat with the publisher running. Recovery checks the stored commit, branch and PR before creating anything. A conflicting branch, a changed issue, or an advanced base requires operator review. The patch and reports remain available after successful local verification. GitHub review and merge are separate steps. Other repository tasks need their own execution and verification profile.

## Live verification, 2026-11-17

Using host GitHub CLI 2.104.0 and REST API version `2026-03-10`, the operator submitted [issue #6](https://github.com/taras-kolodchyn/local-ai-platform/issues/6) through a private Moyai chat. During development, two intake attempts failed before attachment. The controller now dispatches a standalone URL directly to its native issue tool, and the publisher checks ownership against the nested session object in the installed Moyai API.

The successful turn took 4m 54s. It reproduced the 60-second baseline, passed 7 project tests and 4 protected tests on the first repair validation, and delivered a patch with SHA-256 `01f2b8a7e57c85837ea7c3ae7d1b00cfd3611ca03b0a61f60903056916dcec3d`. Independent observation authenticated both verification receipts. GitHub recorded commit `4d4f4fe2b7c71e5c60282d6d026b9a21500d1eeb` on the issue branch. [Draft PR #7](https://github.com/taras-kolodchyn/local-ai-platform/pull/7) · [Successful CI for the published commit](https://github.com/taras-kolodchyn/local-ai-platform/actions/runs/44162713166)

After restarting the publisher, submitting the URL in the same chat returned the same PR and Files in 5 seconds. The persisted task, tool counters and validation receipts were identical. This live run used the single-issue host login. The dedicated installation-token path requires separate verification with a repository installation.
