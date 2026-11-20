# Publish a verified change to an external repository

For ordinary access to GitHub, [connected mode](connected.md) exposes a read-only
MCP connector. The separate foreground publisher on the Mac can open a draft PR
for a supported external project. It checks the downloaded patch against the
verified tree and scopes GitHub writes to one configured repository.

## Select the project

In the local, Git-ignored `.env`, set `MOYAI_GITHUB_REPOSITORY` to the exact
`owner/name` on GitHub. Set `MOYAI_GITHUB_PROJECT_PATH` to the relative crate
directory if the crate lives below the repository root. Run `make up` to apply the
configuration to the controller. A fresh installation has no publication target.

The foreground publisher reads `main` through an anonymous HTTPS Git clone, so
the source repository must be publicly cloneable. A private local checkout can
still be indexed and registered through the local repository commands.
The selected project must match the
[protected verification profile](repository-workflow.md). It reads the issue,
pins the base commit, verifies the source snapshot and reproduces the patch in a
temporary Git index. Credentials stay on the Mac. For repository search alone,
index a local checkout with `make index REPO=/absolute/path/to/project`.

## Open a draft PR

1. Run `make github-app-setup` and open its loopback URL. Install the private App
   on the selected repository using **Only select repositories**. The publisher
   checks the installation's owner, permissions and repository before using it.
2. Run `make github-app-issues ISSUE=N`, replacing `N` with the target issue
   number. For the tests-only profile, add `PROFILE=rust-retry-tests`.
3. Paste the full GitHub issue URL into a new private Moyai chat. The Mac host
   checks the downloaded evidence, publishes the verified commit and returns
   the linked draft PR. Stop the foreground publisher to end the session.

To publish a single draft using the signed-in operator's GitHub CLI account,
use `make github-issues ISSUE=N`. The App additionally supports
`AUTO_MERGE=1` for a single issue when the destination supplies the expected
CI workflow and permits the merge. The merge gate checks the PR head, its base
and the completed CI run. [Publisher implementation](../scripts/github-issues.py)

## Developer and Verifier automations

For a scheduled issue-to-PR workflow, configure the two Moyai roles for the
destination repository. Install the GitHub App there, choose the permitted paths
and skills, and configure that repository's CI and merge rules before enabling
its queue label. The platform's own CI tests the platform. Its installation does
not enroll a project in the automation.

The retired docs-only pilot records the handoffs from issue intake to
independent review, merge and post-merge CI. Its Actions workflows and issue
form are preserved at the last platform revision that ran them. They identify
the original App and permitted path. [Completed pilot issue](https://github.com/taras-kolodchyn/local-ai-platform/issues/94) ·
[Versioned workflow configuration](https://github.com/taras-kolodchyn/local-ai-platform/tree/5029427f3b0d228b3eafe52f41f3d6337575e4a2/.github)

GitHub App authentication uses an installation token scoped to the selected
repository. [Manifest registration](https://docs.github.com/en/apps/sharing-github-apps/registering-a-github-app-from-a-manifest) ·
[Installation access tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)
