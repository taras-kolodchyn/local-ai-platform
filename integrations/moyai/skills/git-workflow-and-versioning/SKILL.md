---
name: git-workflow-and-versioning
description: Plan commits, branches, pull requests and release versions.
---

# Git workflow and versioning

Determine the current branch, base revision, working tree state and intended release line. Separate edits owned by the task from pre-existing local changes. Read the diff and affected callers before grouping changes into commits.

Group each behavior change with its verification. Resolve a conflict using the intent of both sides and check the resulting tree. For a version change, identify which consumers see a patch, compatible extension or breaking contract. Draft a concise change summary describing behavior and migration needs.

Use local history, status and diffs available in the current profile. For a proposed PR or release, report the base and head identities, checks and outstanding actions. Only record a commit, push, PR or tag as completed when the authorized operation returned evidence.
