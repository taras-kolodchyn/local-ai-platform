# GitHub issue workflows

The Developer and Verifier queue takes a labeled issue through a linked PR,
review on the exact commit, and CI on `main`. Run the queue on the Mac and
confirm GitHub writes in private chats. [Queue setup](#configure-github-write-connections)

The optional foreground publisher runs on the Mac. It can turn one open issue
in a supported external repository into a draft PR using a checked Moyai patch.
A fresh installation has no publication target. Start the publisher separately
when you need it. [Draft publisher setup](#select-the-external-project)

For read-only GitHub access through LiteLLM, use [connected mode](connected.md).

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
Each chat and connection scope has a separate Linux login. Run commands from
the supplied working directory. Its parent is traversable but cannot be listed
by a session. The root-owned checkout grants access only to its assigned group.
The UID is retained on the workspace volume across container restarts. On first
access, older checkouts are adopted in place. A legacy tree with hard links to
files outside its checkout requires operator inspection.

The sandbox receives a Git bundle with regular tracked files. Git credentials
stay on the Mac. Use the GitHub connection for publication.

Select the same connection on the chat's first message or schedule. Prepare
a new chat for a different revision or connection. A used workspace keeps its
existing files.

## Configure GitHub write connections

Enable the [native GitHub connection](#enable-the-native-github-connection-on-the-mac).
Create separate GitHub Apps for Developer and Verifier. For each installation,
choose **Only select repositories** and select the target repository. Issue
installation access tokens scoped to that repository and to the role's
permissions. GitHub installation tokens expire after one hour.

| Role | GitHub repository permissions | Allowed tools in Moyai |
| --- | --- | --- |
| Developer | Contents, issues and pull requests write | `issue_read`, `add_issue_comment`, `create_branch`, `push_files`, `create_pull_request` |
| Verifier | Contents, issues and checks read, pull requests write | `issue_read`, `pull_request_read`, `pull_request_review_write` |

Enroll each role through the authenticated local `POST /api/connections`
endpoint. Use `kind: github`, `enabled: true`, `shared: false` and
`read_only: false`. Pass the role's installation token as `credential`,
set `policy.allowed_tools` to the tools in the table, and set
`policy.require_approval: true`. Record the returned connection UUID.
[Local API client](../integrations/moyai/client.py)

Set `MOYAI_VERIFIER_CONNECTION_ID` to the Verifier connection UUID in the
local `.env` and run `make up` to enable review publication. An unset value
keeps review publication disabled. Keep App private keys on the Mac and pass
installation tokens through the credential field.
[GitHub installation access tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)

To renew a write connection, issue a fresh token for the same App,
repository and role. In **Settings → Connections**, open **Edit** for that
connection, enter the **Replacement token**, and save. The connection UUID
stays the same, and the edit creates a new revision. Start a fresh private chat
or let the queue prepare one with that revision. Use the existing chat's
confirmation form while its token is current.

## Hand off a labeled issue to Developer

With the platform running, sign in with the GitHub CLI as the issue author
and select a private Developer connection for the same repository. Enable
native confirmations and allow `issue_read`, `add_issue_comment`,
`create_branch`, `push_files` and `create_pull_request`.

```sh
make moyai-developer-queue REPO=owner/project CONNECTION="$DEVELOPER_CONNECTION" WATCH=1
```

Leave the process running and add `moyai:ready` to an open issue. The queue
checks once per minute for the oldest eligible issue and prepares a private
checkout of verified `main` or the existing `issue-<number>` branch. Developer
reads the issue through the selected GitHub connection before using the local
checkout. Each repository on the Mac has one active handoff. The queue waits
while the conversation is running or awaiting input.
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
Before each inference request in a queue edit or repair turn, the controller
checks the tested index against the pinned commit. A ready index ends that
turn immediately. The queue then rechecks the checkout and advances to
confirmed publication on its next tick.
An unsupported snapshot or exhausted retries hold the issue for inspection.
Before opening the confirmation form, the publication step checks the tree
again. An empty or changed checkout, a missing test marker, or an unsupported
change stops the handoff. `push_files` sends the full UTF-8 contents of up to
64 added or modified regular, non-executable text files (128 KiB combined).

Check the target branch, file list and complete contents against the tested
checkout, including final newlines. If they match, choose **Allow this action**
and **Continue**. Decline a request with extra changes and follow the
[recovery steps](#recover-a-queued-issue).

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
The queue reports `ready_to_merge` after the Verifier App approves that SHA
and the required job succeeds on the PR.
Merge the linked PR in GitHub under the repository's merge rules. The queue
reports `main_green` when the merge commit passes the required push job on
`main` and the issue closes.

## Recover a queued issue

1. Inspect the saved activity and check GitHub before repeating an action.
   Compare the issue branch tip, any linked PR, and any review on the PR's
   current head. A failed turn can follow a successful write.
2. Once the prior turn has ended, renew the affected write connection if its
   token expired. Use a new private chat for the new revision. A confirmation
   from the old run belongs to its original connection revision.
3. If there is no published commit or PR, remove `moyai:ready`, let the queue
   check once to clear its hold, then reapply the label. Leave the existing
   issue branch in place. The queue pins its current tip for the fresh chat.
4. If a verified commit already exists on the issue branch, reuse that commit
   when opening or continuing the linked PR. The queue skips issues with an
   open PR during pickup. If the original queue entry has ended, use a fresh
   private Verifier chat for the existing PR and verify the review on its
   exact head and the required PR check before merging. Verify the push CI on
   the merge commit and check that the issue closes.

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

These commands enroll or refresh read-only connections. For Developer and
Verifier publication, use [write connection renewal](#configure-github-write-connections).

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

Select the [private Verifier connection](#configure-github-write-connections)
in the Verifier chat. Check the repository, PR number, review event and commit
SHA in the proposed action. Choose **Allow this action** and **Continue** to
submit it. Verify the published review on GitHub before merge.
