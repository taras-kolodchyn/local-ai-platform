# Publish a verified external change from a GitHub issue

The optional foreground publisher runs on the Mac. It can turn one open issue
in a supported external repository into a draft PR using a checked Moyai patch.
A fresh installation has no publication target. Start the publisher separately
when you need it.
For read-only GitHub access through MCP, use [connected mode](connected.md).

## Enable the native GitHub connection on the Mac

Set `MOYAI_GITHUB_CONNECTOR=1` in the local `.env` and run `make up`. Compose
starts the GitHub TLS relay and attaches the controller to its internal
network even when the controller is recreated. The relay forwards TLS to
`api.githubcopilot.com:443` with certificate validation performed by the
controller. It publishes no host ports, and the sandbox retains its internal
network. GitHub accounts and credentials are configured separately in Moyai.
The default value is `0`. To remove the relay and return to offline startup,
run `make down`, set the value to `0`, and run `make up`.

## Select the external project

1. Run `make bootstrap` on a fresh installation. In the Git-ignored `.env`, set
   `MOYAI_GITHUB_REPOSITORY` to the canonical `owner/name`. Set
   `MOYAI_GITHUB_PROJECT_PATH` to the crate's relative directory, or leave it
   empty for the repository root.
2. Check that `main` is publicly cloneable over HTTPS. The publisher clones it
   anonymously and pins the commit, even when the App is authenticated. The
   crate must match one of the two protected Rust profiles. A private local
   checkout can still be used for local search and registered tasks.
   [Repository profiles and local registration](repository-workflow.md)
3. Run `make up` to apply the target to the controller.

## Publish one draft PR

1. Run `make github-app-setup` and open the loopback URL it prints. Register the
   private App under the repository owner. Install it using **Only select
   repositories** and select exactly the target repository. The App requests
   read access to issues and write access to contents and pull requests. The Mac
   holds its private key and verifies the installation before obtaining a token.
   [Registration](../scripts/register-github-app.py) · [GitHub installation tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)
2. Start `make github-app-issues ISSUE=N`, substituting the issue number.
   The default profile repairs the retry policy. Add `PROFILE=rust-retry-tests`
   to add coverage for an already-correct implementation.
3. In a fresh **private** Moyai web conversation with the coding profile, submit
   only the full GitHub issue URL. Keep the host command running. It downloads
   the patch and receipts, checks the independent documentation review, and
   reproduces the verified tree in a temporary Git index before it opens a
   linked draft PR. Press Ctrl-C in the terminal to stop the publisher.

To use the operator's signed-in GitHub CLI account for one issue, use
`make github-issues ISSUE=N` in step 2. The publisher checks the authorized
issue, pinned base and selected repository. The execution sandbox receives
neither GitHub credential. The App client uses GitHub REST API version
`2026-03-10` in the configuration checked on 2026-11-20.
[Publisher implementation](../scripts/github-issues.py) · [App client](../scripts/github_app.py)

## Optional merge after CI

Grant `AUTO_MERGE=1` on the original single-issue App command only if the
destination's CI and branch rules support the publisher's merge checks.
Its `.github/workflows/ci.yml` must report a successful `pull_request` run with
the display title `CI PR <number> head <head-sha> base <base-sha>` and a
successful job named `test` on the verified head. The publisher rechecks the
issue, PR, base and tree, marks the draft ready, and requests a merge commit.
If interrupted after publication, restart with the same issue, profile and
merge grant to reconcile the saved job. [CI and merge checks](../scripts/github-issues.py)

## Prepare a Developer checkout

Create a private Developer chat and leave it unused while preparing its
checkout. Sign in with the GitHub CLI on the Mac before running the command.

```sh
make moyai-checkout REPO=owner/project BRANCH=main \
  SESSION="$DEVELOPER_SESSION" CONNECTION="$DEVELOPER_CONNECTION"
```

The command compares the fetched commit with the current branch tip and prints
the verified SHA. Add `SHA=<full-commit>` to require a particular revision.
It installs the checkout in the chat workspace for the selected connection.
The sandbox receives a Git bundle with regular tracked files. Git credentials
stay on the Mac. Use the GitHub connection for publication.

Select the same connection on the chat's first message or schedule. Prepare
a new chat for a different revision or connection. A used workspace keeps its
existing files.

## Hand off a labeled issue to Developer

With the platform running, sign in with the GitHub CLI as the issue author
and select a private Developer connection for the same repository. Enable
native confirmations and allow `issue_read`, `add_issue_comment`,
`create_branch`, `push_files` and `create_pull_request`.

```sh
make moyai-developer-queue REPO=owner/project CONNECTION="$DEVELOPER_CONNECTION" WATCH=1
```

Leave the process running and add `moyai:ready` to an open issue. It picks
the oldest eligible issue and prepares a fresh private checkout of verified
`main` or the existing `issue-<number>` branch. Developer reads the issue
through the selected GitHub connection before using the local checkout. Each
repository on the Mac has one active handoff. The queue waits while the
conversation is running or awaiting input.
It records the issue, session, revision and submission locally so restarts
can resume the same handoff. Publication uses the normal confirmation forms.

When the issue branch is missing, Developer reads the issue and requests
`create_branch` through the GitHub connection with native confirmation. The
queue checks that the remote branch points to the pinned main commit before
admitting edits. An existing branch goes directly to editing at its pinned tip.
Developer edits and tests in the local checkout, then publishes through a
separate `push_files` step. The queue opens a PR after GitHub shows one new commit on the pinned issue branch. A
branch change before submission or a missing commit after publication holds
the issue for inspection. The edit step carries the verified issue text as
task data and still reads the issue through the selected connection.

After a passing test run, Developer records the staged tree in the sandbox at
`refs/workflow/tested`. Before publication, the queue verifies that the index has
changes against the pinned revision, that tracked edits are staged, and that
the recorded tree matches the index. If staging or the test receipt is
incomplete, Developer gets up to two local repair turns in the same checkout
to stage the intended paths, rerun the issue tests and record the tree.
An unsupported snapshot or exhausted retries hold the issue for inspection.
The publication step checks the tree again before opening confirmation. It reads the changed files and supplies their exact UTF-8
contents to the native confirmation form, including final newlines. Inspect
the branch and proposed changes before choosing **Allow this action** and
**Continue**. An empty or changed checkout, a missing test marker, or an
unsupported change stops the handoff. This path supports up to 64 regular,
non-executable text files (128 KiB combined) added or modified on an existing
issue branch.

Omit `WATCH=1` for one check. The queue skips issues with an open PR from
`issue-<number>`. An issue whose turn finished without such a PR stays held.
To retry it, remove the label, wait for one check, and reapply the label.
Keep the connection token current and confirm writes before it expires.

## Hand off the published PR to Verifier

Enable review publication for a separate private Verifier connection as
described below. Pass both GitHub App slugs without the `[bot]` suffix.

```sh
make moyai-developer-queue REPO=owner/project CONNECTION="$DEVELOPER_CONNECTION" \
  VERIFIER="$VERIFIER_CONNECTION" DEVELOPER_APP="$DEVELOPER_APP_SLUG" \
  VERIFIER_APP="$VERIFIER_APP_SLUG" CI_WORKFLOW=test.yml CI_JOB=unit WATCH=1
```

Before confirmation, the queue formats `Closes #<number>` as the final Markdown
paragraph of the PR description. GitHub must list the issue in the PR’s
closing references before the queue starts review.

The queue sends a PR linked to the issue and opened by the Developer App to
a separate private Verifier chat. The review receives the exact head SHA and
uses the normal confirmation form. A new head SHA needs a new review.
If the previous chat is awaiting confirmation, resolve that prompt first.
The queue reports `ready_to_merge` after
the Verifier App approves that SHA and the required job succeeds on the PR.
After merging through GitHub, the queue reports `main_green` when the
merge commit passes the required push job on `main` and the issue is closed.

## Native schedules

The retired pilot used two native Moyai schedules, Developer and Verifier,
which were paused. This repository ships the foreground publisher and its own
platform CI. An external project needs its own schedule configuration, GitHub
permissions, CI and merge rules to run scheduled work. [Platform CI](../.github/workflows/ci.yml)

An interval tick records `skipped` in the schedule log while its conversation
has queued work, a run in progress or a run waiting for confirmation. A skipped
tick stays skipped on retry. The next scheduled tick can start when the
conversation is idle.

### Renew a private GitHub reader

From the repository root, configure the registered application for the selected repository.
Enroll a separate private connection for each role and select it on the first message in a new private chat.

```sh
bash scripts/github-command.sh scripts/github-reader.py --enroll issue
bash scripts/github-command.sh scripts/github-reader.py --enroll review
bash scripts/github-command.sh scripts/github-reader.py --refresh CONNECTION_UUID
```

The optional `--app-directory PATH` selects an existing registration. Each refresh is manual. If enrollment prints a connection ID and is interrupted, retry with
`--refresh` and that ID.
The controller keeps the current token encrypted in its data volume. Enrollment pins the application,
installation, owner and repository IDs, the installation permissions and the token permissions.
A change to any of these requires a new enrollment and chat. Ordinary connection edits continue to
invalidate the previous workspace binding. Renew before expiry to keep using the same chat.
GitHub installation tokens expire one hour after issuance.
[GitHub installation access tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)

### Confirmed pull request reviews

Set `MOYAI_VERIFIER_CONNECTION_ID` in the local `.env` to the UUID of the
dedicated private GitHub connection, then run `make up`. Leave it unset to
keep review publication disabled. Select that connection in the private
Verifier chat. Its GitHub installation needs pull request write access to
the target repository. Explicitly allow `pull_request_review_write` on the
connection and enable required approvals. Check the repository, PR number,
review event and commit SHA in the proposed action. Choose **Allow this action**
and **Continue** to submit it. Verify the published review on GitHub before merge.
